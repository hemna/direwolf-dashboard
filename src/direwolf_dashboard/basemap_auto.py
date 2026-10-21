"""Automatic region download around the station (worldwide offline maps).

On startup the dashboard checks whether ``<data_dir>/basemap/region.pmtiles``
covers the station position. If it doesn't (first run, or the station moved),
it extracts a square region around the station from the Protomaps planet build
in the background: with the ``pmtiles`` CLI when it's installed, otherwise
with the pure-Python extractor. Nothing needs to be hand-built.

Any failure (no position yet, no network, planet unreachable, region too big)
is logged and leaves the map on whatever it had. The world z0-4 fallback
(#144) always covers everywhere, so the map degrades gracefully instead of
going blank.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from haversine import Unit, haversine

from direwolf_dashboard import basemap_extract
from direwolf_dashboard.basemap import (
    DEFAULT_REGION_ARCHIVE,
    FETCHED_REGION_ARCHIVE,
    bbox_around,
    build_extract_command,
    default_planet_url,
)

LOG = logging.getLogger("direwolf_dashboard.basemap")

#: Sidecar describing what region.pmtiles covers.
META_NAME = "region.json"
#: Retry a failed download this often while the server keeps running.
RETRY_SECONDS = 6 * 3600


@dataclass
class AutoFetchStatus:
    state: str = "idle"  # idle|disabled|no_position|skipped|current|fetching|done|failed
    detail: str = ""
    bbox: list | None = None
    updated: float = field(default_factory=time.time)

    def set(self, state: str, detail: str = "", bbox=None) -> None:
        self.state, self.detail, self.updated = state, detail, time.time()
        if bbox is not None:
            self.bbox = [round(v, 6) for v in bbox]
        LOG.info("basemap auto-fetch: %s %s", state, detail)

    def to_dict(self) -> dict:
        return {"state": self.state, "detail": self.detail, "bbox": self.bbox}


def region_paths(data_dir: str) -> tuple[str, str]:
    region = os.path.join(data_dir, FETCHED_REGION_ARCHIVE)
    return region, os.path.join(os.path.dirname(region), META_NAME)


def read_meta(meta_path: str) -> dict | None:
    try:
        with open(meta_path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def needs_fetch(lat: float, lon: float, radius_km: float, maxzoom: int,
                region_path: str, meta: dict | None) -> tuple[bool, str]:
    """Decide whether the existing region still fits the station."""
    if not os.path.isfile(region_path):
        return True, "no region downloaded yet"
    if not meta:
        # A region someone copied in by hand: respect it.
        return False, "existing region.pmtiles without metadata (kept)"
    try:
        moved = haversine((lat, lon), (meta["lat"], meta["lon"]), unit=Unit.KILOMETERS)
    except (KeyError, TypeError):
        return True, "unreadable region metadata"
    if moved > max(5.0, meta.get("radius_km", radius_km) / 4):
        return True, f"station moved {moved:.0f} km from the region centre"
    if radius_km > meta.get("radius_km", 0) + 0.5 or maxzoom > meta.get("maxzoom", 0):
        return True, "configured radius/maxzoom grew"
    return False, "region covers the station"


def fetch_region(lat: float, lon: float, radius_km: float, maxzoom: int,
                 data_dir: str, planet_url: str | None = None,
                 use_cli: bool | None = None) -> dict:
    """Download the region now (blocking). Returns the written metadata."""
    region, meta_path = region_paths(data_dir)
    os.makedirs(os.path.dirname(region), exist_ok=True)
    bbox = bbox_around(lat, lon, radius_km)
    planet = planet_url or default_planet_url()
    tmp = region + ".tmp.pmtiles"
    cli = shutil.which("pmtiles") if use_cli in (None, True) else None
    if cli:
        subprocess.run(build_extract_command(planet, tmp, bbox, maxzoom) + ["--quiet"],
                       check=True, timeout=3600)
        method = "pmtiles-cli"
    else:
        get = (basemap_extract.file_get_bytes(planet) if os.path.isfile(planet)
               else basemap_extract.http_get_bytes(planet))
        basemap_extract.extract(get, tmp, bbox, maxzoom, log=LOG.debug)
        method = "python"
    os.replace(tmp, region)
    meta = {
        "lat": lat, "lon": lon, "radius_km": radius_km, "maxzoom": maxzoom,
        "bbox": list(bbox), "planet": planet, "method": method,
        "bytes": os.path.getsize(region),
        "fetched": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
    return meta


async def auto_fetch_once(config, status: AutoFetchStatus, position) -> bool:
    """One attempt. ``position`` is an async callable returning (lat, lon) or None.

    Returns True when no retry is needed (done / current / not applicable).
    """
    bm = config.basemap
    if not bm.auto_fetch:
        status.set("disabled", "basemap.auto_fetch is false")
        return True
    if (bm.region_archive or DEFAULT_REGION_ARCHIVE) != DEFAULT_REGION_ARCHIVE:
        status.set("skipped", f"basemap.region_archive is set to {bm.region_archive}")
        return True
    pos = await position()
    if not pos:
        status.set("no_position", "no station position yet; using the world map")
        return False
    lat, lon = pos
    region, meta_path = region_paths(config.data_dir)
    need, why = needs_fetch(lat, lon, bm.radius_km, bm.maxzoom, region, read_meta(meta_path))
    if not need:
        status.set("current", why)
        return True
    status.set("fetching", why, bbox_around(lat, lon, bm.radius_km))
    try:
        meta = await asyncio.to_thread(
            fetch_region, lat, lon, bm.radius_km, bm.maxzoom, config.data_dir,
            bm.planet_url or None)
    except Exception as e:  # network, CLI, size guard: never crash the server
        status.set("failed", f"{type(e).__name__}: {e}; using the world map")
        return False
    status.set("done", f"{meta['bytes']:,} bytes via {meta['method']}; reload the page")
    return True


#: Re-check for a position this often (e.g. waiting for the first beacon).
POSITION_POLL_SECONDS = 300


async def auto_fetch_loop(config, status: AutoFetchStatus, position,
                          retry_seconds: float = RETRY_SECONDS,
                          position_poll_seconds: float = POSITION_POLL_SECONDS) -> None:
    while True:
        if await auto_fetch_once(config, status, position):
            return
        await asyncio.sleep(position_poll_seconds if status.state == "no_position" else retry_seconds)
