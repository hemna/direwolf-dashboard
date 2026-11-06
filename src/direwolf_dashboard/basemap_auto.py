"""Automatic region download around the station (worldwide offline maps).

On startup the dashboard checks whether ``<data_dir>/basemap/region.pmtiles``
covers the station position. If it doesn't (first run, or the station moved),
it extracts a square region around the station in the background from the
first working entry of ``basemap.sources``: by default the basemap-kit planet
on aprslive.com (haminfo #221), then the Protomaps daily build: with the ``pmtiles`` CLI when it's installed, otherwise
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
    source: str | None = None
    updated: float = field(default_factory=time.time)

    def set(self, state: str, detail: str = "", bbox=None) -> None:
        self.state, self.detail, self.updated = state, detail, time.time()
        if bbox is not None:
            self.bbox = [round(v, 6) for v in bbox]
        LOG.info("basemap auto-fetch: %s %s", state, detail)

    def to_dict(self) -> dict:
        return {"state": self.state, "detail": self.detail, "bbox": self.bbox,
                "source": self.source}


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


#: basemap-kit publish layout (CONTRACT.md / scripts/publish.py):
#: https://aprslive.com/basemap-kit/archives/<id>.pmtiles. The kit's
#: capped-maxzoom planet archive is published as id "planet" (haminfo #221).
CLOUD_PLANET_URL = "https://aprslive.com/basemap-kit/archives/planet.pmtiles"
#: Keyword for yesterday's Protomaps daily build.
PROTOMAPS = "protomaps"
DEFAULT_SOURCES = [CLOUD_PLANET_URL, PROTOMAPS]


def resolve_source(entry: str) -> str:
    """Map a ``basemap.sources`` entry to a concrete URL or local path."""
    entry = (entry or "").strip()
    return default_planet_url() if entry.lower() == PROTOMAPS else entry


def _reader(src: str):
    return (basemap_extract.file_get_bytes(src) if os.path.isfile(src)
            else basemap_extract.http_get_bytes(src))


def probe_source(src: str, get=None) -> dict:
    """Read the 127-byte header; raise if missing/unreachable/not a vector PMTiles v3."""
    from pmtiles.tile import TileType, deserialize_header

    get = get or _reader(src)
    try:
        raw = get(0, 127)
        header = deserialize_header(raw)
    except basemap_extract.ExtractError:
        raise
    except Exception as e:  # MagicNumberNotFound, short read, spec version...
        raise basemap_extract.ExtractError(f"invalid archive: {type(e).__name__} {e}".strip()) from e
    if header["tile_type"] != TileType.MVT:
        raise basemap_extract.ExtractError(f"invalid archive: tile type {header['tile_type']}, need MVT")
    return header


def _extract_from(src: str, tmp: str, bbox, maxzoom: int, use_cli) -> str:
    cli = shutil.which("pmtiles") if use_cli in (None, True) else None
    if cli:
        subprocess.run(build_extract_command(src, tmp, bbox, maxzoom) + ["--quiet"],
                       check=True, timeout=3600)
        return "pmtiles-cli"
    basemap_extract.extract(_reader(src), tmp, bbox, maxzoom, log=LOG.debug)
    return "python"


def fetch_region(lat: float, lon: float, radius_km: float, maxzoom: int,
                 data_dir: str, sources=None, use_cli: bool | None = None) -> dict:
    """Download the region now (blocking), trying ``sources`` in order.

    Each source is probed first (header must be a vector PMTiles v3). A 404,
    network error, invalid archive or failed extract moves on to the next
    source. Raises ExtractError listing every attempt if all fail.
    """
    if isinstance(sources, str):
        sources = [sources]
    sources = list(sources or DEFAULT_SOURCES)
    region, meta_path = region_paths(data_dir)
    os.makedirs(os.path.dirname(region), exist_ok=True)
    bbox = bbox_around(lat, lon, radius_km)
    tmp = region + ".tmp.pmtiles"
    src, method, tried = extract_with_fallback(sources, tmp, bbox, maxzoom, use_cli)
    os.replace(tmp, region)
    LOG.info("basemap region downloaded from %s via %s", src, method)
    meta = {
        "lat": lat, "lon": lon, "radius_km": radius_km, "maxzoom": maxzoom,
        "bbox": list(bbox), "source": src, "planet": src, "method": method,
        "tried": tried, "bytes": os.path.getsize(region),
        "fetched": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
    return meta


def extract_with_fallback(sources, tmp: str, bbox, maxzoom: int, use_cli=None):
    """Extract ``bbox`` into ``tmp`` from the first working source.

    A source whose archive stops below ``maxzoom`` (e.g. a low-zoom hosted
    world planet) is deferred: later sources with full street detail are
    tried first, and the low-zoom one is used only if none of them work.
    Returns ``(source, method, tried)``; ``tried`` lists skipped sources.
    Raises ExtractError listing every attempt if all fail.
    """
    tried = []
    deferred = []
    for entry in sources:
        src = resolve_source(entry)
        try:
            header = probe_source(src)
        except Exception as e:  # noqa: BLE001 - any failure means "next source"
            tried.append(f"{src}: {type(e).__name__}: {e}")
            LOG.warning("basemap source failed, trying next: %s (%s)", src, e)
            continue
        hz = (header or {}).get("max_zoom", maxzoom)
        if hz < maxzoom:
            deferred.append(src)
            tried.append(f"{src}: only z{hz} < maxzoom {maxzoom}; deferred")
            continue
        got = _try_extract(src, tmp, bbox, maxzoom, use_cli, tried)
        if got:
            return src, got, tried
    for src in deferred:
        got = _try_extract(src, tmp, bbox, maxzoom, use_cli, tried)
        if got:
            return src, got, tried
    raise basemap_extract.ExtractError("all basemap sources failed: " + " | ".join(tried))


def _try_extract(src, tmp, bbox, maxzoom, use_cli, tried):
    try:
        return _extract_from(src, tmp, bbox, maxzoom, use_cli)
    except Exception as e:  # noqa: BLE001
        tried.append(f"{src}: {type(e).__name__}: {e}")
        LOG.warning("basemap source failed, trying next: %s (%s)", src, e)
        if os.path.exists(tmp):
            os.remove(tmp)
        return None


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
        status.source = (read_meta(meta_path) or {}).get("source")
        status.set("current", why)
        return True
    status.set("fetching", why, bbox_around(lat, lon, bm.radius_km))
    try:
        meta = await asyncio.to_thread(
            fetch_region, lat, lon, bm.radius_km, bm.maxzoom, config.data_dir,
            bm.sources or DEFAULT_SOURCES)
    except Exception as e:  # network, CLI, size guard: never crash the server
        status.set("failed", f"{type(e).__name__}: {e}; using the world map")
        return False
    status.source = meta["source"]
    status.set("done", f"{meta['bytes']:,} bytes from {meta['source']} via {meta['method']}; reload the page")
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
