"""Tile cache proxy for OpenStreetMap tiles."""

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Callable, Optional

import httpx

LOG = logging.getLogger(__name__)

# OSM tile usage policy: max 2 requests/second
RATE_LIMIT_DELAY = 0.5  # seconds between requests


class TileProxy:
    """Caching proxy for map tiles.

    Supports two modes:
    - Lazy: fetch tiles on demand, cache to disk
    - Preload: download tiles for a bounding box in the background
    """

    def __init__(
        self,
        cache_dir: str,
        tile_url_template: str = "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        max_cache_mb: int = 500,
    ):
        self.cache_dir = cache_dir
        self.tile_url_template = tile_url_template
        self.max_cache_mb = max_cache_mb
        self._preload_task: Optional[asyncio.Task] = None
        self._preload_cancel = False
        self._client: Optional[httpx.AsyncClient] = None

    async def init(self) -> None:
        """Initialize the tile proxy."""
        os.makedirs(self.cache_dir, exist_ok=True)
        self._client = httpx.AsyncClient(
            timeout=30.0,
            headers={"User-Agent": "DirewolfDashboard/0.1"},
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()

    def _tile_path(self, z: int, x: int, y: int) -> str:
        """Get the filesystem path for a cached tile."""
        return os.path.join(self.cache_dir, str(z), str(x), f"{y}.png")

    async def get_tile(self, z: int, x: int, y: int) -> Optional[bytes]:
        """Get a tile, serving from cache if available.

        Returns:
            PNG bytes, or None if fetch fails.
        """
        path = self._tile_path(z, x, y)

        # Check cache
        if os.path.exists(path):
            with open(path, "rb") as f:
                return f.read()

        # Fetch from upstream
        url = self.tile_url_template.format(z=z, x=x, y=y)
        try:
            response = await self._client.get(url)
            if response.status_code == 200:
                # Cache to disk
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as f:
                    f.write(response.content)

                # Check cache budget
                await self._check_cache_budget()

                return response.content
            else:
                LOG.warning(f"Tile fetch failed: {url} -> {response.status_code}")
                return None
        except Exception as e:
            LOG.error(f"Tile fetch error: {url} -> {e}")
            return None

    def get_cache_stats(self) -> dict:
        """Get cache statistics."""
        total_size = 0
        tile_count = 0
        for root, dirs, files in os.walk(self.cache_dir):
            for f in files:
                if f.endswith(".png"):
                    tile_count += 1
                    total_size += os.path.getsize(os.path.join(root, f))

        return {
            "tile_count": tile_count,
            "cache_size_mb": round(total_size / (1024 * 1024), 2),
            "max_cache_mb": self.max_cache_mb,
        }

    def estimate_preload(
        self, south: float, west: float, north: float, east: float,
        min_zoom: int, max_zoom: int
    ) -> dict:
        """Estimate how many tiles a preload would require.

        Returns:
            Dict with estimated_tiles and estimated_size_mb.
        """
        max_zoom = min(max_zoom, 16)  # Hard cap
        total_tiles = 0

        for z in range(min_zoom, max_zoom + 1):
            x_min, y_min = _deg2tile(north, west, z)
            x_max, y_max = _deg2tile(south, east, z)
            tiles_at_zoom = (x_max - x_min + 1) * (y_max - y_min + 1)
            total_tiles += tiles_at_zoom

        # Average tile is ~20KB
        estimated_mb = round(total_tiles * 20 / 1024, 2)

        return {
            "estimated_tiles": total_tiles,
            "estimated_size_mb": estimated_mb,
        }

    async def preload(
        self,
        south: float, west: float, north: float, east: float,
        min_zoom: int, max_zoom: int,
        progress_callback: Optional[Callable] = None,
    ) -> None:
        """Download tiles for a bounding box.

        Args:
            south, west, north, east: Bounding box coordinates.
            min_zoom, max_zoom: Zoom level range (max capped at 16).
            progress_callback: Async callable(done: int, total: int) for progress updates.
        """
        max_zoom = min(max_zoom, 16)  # Hard cap
        self._preload_cancel = False

        # Build tile list
        tiles = []
        for z in range(min_zoom, max_zoom + 1):
            x_min, y_min = _deg2tile(north, west, z)
            x_max, y_max = _deg2tile(south, east, z)
            for x in range(x_min, x_max + 1):
                for y in range(y_min, y_max + 1):
                    tiles.append((z, x, y))

        total = len(tiles)
        LOG.info(f"Preloading {total} tiles (zoom {min_zoom}-{max_zoom})")

        # Check budget
        estimate = self.estimate_preload(south, west, north, east, min_zoom, max_zoom)
        if estimate["estimated_size_mb"] > self.max_cache_mb:
            LOG.warning(
                f"Preload would exceed cache budget "
                f"({estimate['estimated_size_mb']}MB > {self.max_cache_mb}MB). Aborting."
            )
            return

        for i, (z, x, y) in enumerate(tiles):
            if self._preload_cancel:
                LOG.info(f"Preload cancelled at {i}/{total}")
                return

            # Skip if already cached
            if not os.path.exists(self._tile_path(z, x, y)):
                await self.get_tile(z, x, y)
                # Rate limit per OSM policy
                await asyncio.sleep(RATE_LIMIT_DELAY)

            if progress_callback and (i % 10 == 0 or i == total - 1):
                await progress_callback(i + 1, total)

        LOG.info(f"Preload complete: {total} tiles")

    def cancel_preload(self) -> None:
        """Cancel an in-progress preload."""
        self._preload_cancel = True

    async def _check_cache_budget(self) -> None:
        """Evict oldest tiles if cache exceeds budget."""
        stats = self.get_cache_stats()
        if stats["cache_size_mb"] <= self.max_cache_mb:
            return

        # Collect all tiles with modification time
        tile_files = []
        for root, dirs, files in os.walk(self.cache_dir):
            for f in files:
                if f.endswith(".png"):
                    path = os.path.join(root, f)
                    tile_files.append((path, os.path.getmtime(path)))

        # Sort by oldest first
        tile_files.sort(key=lambda x: x[1])

        # Delete oldest until under budget
        current_mb = stats["cache_size_mb"]
        for path, mtime in tile_files:
            if current_mb <= self.max_cache_mb * 0.9:  # Delete to 90% of budget
                break
            size_mb = os.path.getsize(path) / (1024 * 1024)
            os.remove(path)
            current_mb -= size_mb


def _deg2tile(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    """Convert lat/lon to tile coordinates at a given zoom level."""
    import math
    lat_rad = math.radians(lat)
    n = 2**zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y
