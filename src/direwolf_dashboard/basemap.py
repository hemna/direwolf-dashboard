"""direwolf_dashboard/basemap.py — MapLibre basemap foundation (issue #124).

The basemap surface is vendored into ``static/basemap/`` (kit pin
``f1227386d2``, URLs rewritten to ``/static`` so the map needs zero internet;
see ``static/basemap/SOURCE.md``). This module is the config seam: it owns the
default region archive path and the local URL the vendored styles use for
tiles, driven by the ``basemap.region_archive`` config setting.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
from datetime import UTC, datetime, timedelta
from math import cos, radians

# MapLibre glyph (.pbf) and tile-archive (.pmtiles) assets are binary; without
# these registrations starlette StaticFiles serves unknown extensions as
# text/plain. Register them once (this module is imported by config.py, which
# server.py imports, so the override applies to the /static mount too).
mimetypes.add_type("application/octet-stream", ".pmtiles")
mimetypes.add_type("application/octet-stream", ".pbf")

#: Default region archive path, relative to the static mount. Matches the
#: vendored Florence sample and the ``basemap.region_archive`` config default.
DEFAULT_REGION_ARCHIVE = "basemap/regions/sample.pmtiles"

#: The tile URL baked into the vendored styles at pin time (LAN-offline).
DEFAULT_TILE_URL = "/static/" + DEFAULT_REGION_ARCHIVE

#: Where ``direwolf-dashboard basemap fetch`` writes by default, relative to
#: ``data_dir``. Picked up automatically when ``region_archive`` is the default.
FETCHED_REGION_ARCHIVE = "basemap/region.pmtiles"


def region_url(region_archive: str | None = None) -> str:
    """Return the served URL for a region archive under the static mount.

    ``region_archive`` names a file inside the static tree (the config value
    ``basemap.region_archive``), so it must be a relative path without ``..``
    segments — an absolute or upward path would escape the static mount.
    """
    rel = (region_archive or DEFAULT_REGION_ARCHIVE).strip()
    if not rel or rel.startswith("/") or rel.startswith("..") or "/../" in "/" + rel:
        raise ValueError(f"region_archive must stay inside the static mount: {region_archive!r}")
    return f"/static/{rel}"


# ---------------------------------------------------------------------------
# Region fetch (issue #132): pure bbox / command building, no I/O side effects
# ---------------------------------------------------------------------------


def default_planet_url(now: datetime | None = None) -> str:
    """Protomaps planet build URL for yesterday's UTC date (builds land daily)."""
    d = (now or datetime.now(UTC)).date() - timedelta(days=1)
    return f"https://build.protomaps.com/{d:%Y%m%d}.pmtiles"


def bbox_around(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    """Bounding box ``(min_lon, min_lat, max_lon, max_lat)`` around a point.

    Uses a flat-square approximation: 1 deg lat ~ 111 km; deg lon scaled by
    cos(lat). Clamped to valid lon/lat ranges.
    """
    lat_delta = radius_km / 111.0
    lon_delta = radius_km / (111.0 * max(cos(radians(lat)), 1e-9))
    return (
        max(-180.0, min(180.0, lon - lon_delta)),
        max(-90.0, min(90.0, lat - lat_delta)),
        max(-180.0, min(180.0, lon + lon_delta)),
        max(-90.0, min(90.0, lat + lat_delta)),
    )


def parse_bbox(text: str) -> tuple[float, float, float, float]:
    """Parse ``--bbox`` text into ``(min_lon, min_lat, max_lon, max_lat)``."""
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 4:
        raise ValueError("bbox must be 4 comma-separated numbers: MIN_LON,MIN_LAT,MAX_LON,MAX_LAT")
    try:
        min_lon, min_lat, max_lon, max_lat = (float(p) for p in parts)
    except ValueError:
        raise ValueError("bbox must be 4 comma-separated numbers: MIN_LON,MIN_LAT,MAX_LON,MAX_LAT") from None
    if not (
        -180 <= min_lon <= 180
        and -180 <= max_lon <= 180
        and -90 <= min_lat <= 90
        and -90 <= max_lat <= 90
    ):
        raise ValueError("bbox coordinates out of range (lon in [-180,180], lat in [-90,90])")
    if min_lon > max_lon or min_lat > max_lat:
        raise ValueError("bbox requires min_lon <= max_lon and min_lat <= max_lat")
    return (min_lon, min_lat, max_lon, max_lat)


def build_extract_command(
    planet_url: str, output: str, bbox: tuple[float, float, float, float], maxzoom: int
) -> list[str]:
    """Build the ``pmtiles extract`` argv for a region of a planet archive."""
    bbox_arg = ",".join(f"{v:.6f}" for v in bbox)
    return ["pmtiles", "extract", planet_url, output, f"--bbox={bbox_arg}", f"--maxzoom={maxzoom}"]


def sha256_file(path: str) -> str:
    """Hex sha256 of a file, streamed in 64 KiB chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Active region archive resolution (issue #132)
# ---------------------------------------------------------------------------


def resolve_region_archive(region_archive: str, data_dir: str, static_dir: str) -> str:
    """Return the filesystem path of the region archive to serve.

    Priority:
      1. ``region_archive`` is an absolute path -> serve exactly that file.
      2. ``region_archive`` is the default and ``<data_dir>/basemap/region.pmtiles``
         (the ``basemap fetch`` output) exists -> serve that.
      3. ``<data_dir>/<region_archive>`` exists (operator copied a real region
         into the writable data dir) -> serve that.
      4. Otherwise -> the static-relative vendored path (existing semantics,
         the Florence sample by default).

    Returns a path even when the file is missing; callers answer 404.
    """
    arc = (region_archive or DEFAULT_REGION_ARCHIVE).strip()
    if os.path.isabs(arc):
        return arc
    if arc == DEFAULT_REGION_ARCHIVE:
        fetched = os.path.join(data_dir, FETCHED_REGION_ARCHIVE)
        if os.path.isfile(fetched):
            return fetched
    data_candidate = os.path.join(data_dir, arc)
    if os.path.isfile(data_candidate):
        return data_candidate
    return os.path.join(static_dir, arc)


def region_archive_info(path: str) -> dict | None:
    """Return ``{'name': basename, 'size': bytes}`` for an archive, or None if missing."""
    if not path or not os.path.isfile(path):
        return None
    return {"name": os.path.basename(path), "size": os.path.getsize(path)}
