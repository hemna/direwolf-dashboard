"""direwolf_dashboard/basemap.py — MapLibre basemap foundation (issue #124).

The basemap surface is vendored into ``static/basemap/`` (kit pin
``f1227386d2``, URLs rewritten to ``/static`` so the map needs zero internet;
see ``static/basemap/SOURCE.md``). This module is the config seam: it owns the
default region archive path and the local URL the vendored styles use for
tiles, driven by the ``basemap.region_archive`` config setting.
"""

from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import os
import re
import struct
import threading
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from math import cos, radians
from pathlib import Path
from time import monotonic

import httpx

LOG = logging.getLogger(__name__)

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


# ============================================================================
# Issue #154 - seamless z11/z12 handoff + same-origin z12+ upstream proxy
# ----------------------------------------------------------------------------
# Port of the haminfo #269 design (PR hemna/haminfo#270) to this app. The
# browser never touches an external tile host directly: the served style
# points the z12+ source at the same-origin proxy route
# (:data:`UPSTREAM_PROXY_URL`), which passes HTTP byte ranges through to the
# resolved upstream (``basemap.sources``: cloud.hemna.com first, Protomaps
# daily fallback - or a single pinned :data:`UPSTREAM_URL_ENV` for dev) and
# caches the slices.
# ============================================================================

#: Style source id for the z12+ upstream archive. The kit's ``protomaps``
#: source keeps its id (JS consumers and the world fallback key on it) and now
#: points at the local region archive.
UPSTREAM_SOURCE_ID = "protomaps-hi"

#: Highest zoom the local region archive is asked for. The configured region
#: archive is z0-15 with real tiles at the station through z15, so the
#: station's z12-15 base map renders from the fast local archive instead of
#: the slow upstream proxy (issue #160). MapLibre overzooms the tiles past
#: this cap.
LOCAL_MAXZOOM = 15

#: Where the z12+ upstream band begins, decoupled from :data:`LOCAL_MAXZOOM`
#: (issue #160): areas outside the region archive (e.g. Richmond / DC) keep
#: upstream coverage from z12 even though the local source now reaches z15.
UPSTREAM_MINZOOM = 12

#: Minor-road (street) label parity gate (issue #157). The kit gates street
#: names to z15+, but OSM standard starts naming minor streets around z13-14
#: and the protomaps data carries named minor roads from z14 - served one
#: zoom earlier, app-side. The durable fix (denser z12+ tiles + aligned label
#: gates) belongs in hemna/basemap-kit, and is called out in the PR body.
MINOR_ROAD_LABEL_MINZOOM = 14

#: Same-origin path the served style points the z12+ upstream source at.
UPSTREAM_PROXY_URL = "/basemap/tiles/upstream"

#: How long a resolved upstream choice (URL + label + max_zoom) is kept before
#: the cloud-first probe chain runs again (per-tile proxy requests never
#: re-probe).
UPSTREAM_RESOLVE_TTL_SECONDS = 300.0

#: Upper bound on the in-memory byte-range cache entries for the proxy.
PROXY_CACHE_MAX_ENTRIES = 512

#: Largest single byte range the proxy will fetch and serve. pmtiles.js asks
#: for directory and tile ranges well below this; anything bigger is refused
#: before the upstream fetch so one anonymous request cannot pull a huge slice
#: (issue #169).
PROXY_RANGE_MAX_BYTES = 256 * 1024

#: Total bytes retained by the proxy cache across all entries. Bounds the
#: persistent memory one or more anonymous requests can occupy (issue #169).
PROXY_CACHE_MAX_BYTES = 16 * 1024 * 1024

#: Timeout for the proxy upstream ranged fetch (covers slow tile reads).
PROXY_FETCH_TIMEOUT_SECONDS = 30.0

#: Upstream max_zoom used when it cannot be probed (the single pinned URL path
#: never probes). Matches the Protomaps daily build / cloud planet.
FALLBACK_UPSTREAM_MAXZOOM = 15

#: Environment single-pin override for the z12+ upstream archive (dev/test).
#: A pinned value wins without probing; ``basemap.sources`` is the fallback.
UPSTREAM_URL_ENV = "DIREWOLF_MAP_TILES_URL"

#: Per-source probe timeout for the serve-time HTTP Range header check.
PROBE_TIMEOUT_SECONDS = 5.0

#: Ordered upstream sources used when no config is available (the config
#: ``basemap.sources`` default is identical): cloud.hemna.com first, Protomaps
#: daily build as the fallback (Hemna tile-source rule, aprs-chat-project).
DEFAULT_UPSTREAM_SOURCES = [
    "https://cloud.hemna.com/basemap-kit/archives/planet.pmtiles",
    "protomaps",
]

#: The served local region archive URL (same-origin, HTTP Range) the style
#: points the local z0-11 source at (issue #154).
LOCAL_REGION_URL = "/basemap/region.pmtiles"

#: On-disk location of the vendored kit surface (styles, sprites, glyphs).
BASEMAP_DIR = Path(__file__).resolve().parent / "static" / "basemap"

#: Vendored sample fallback used when every upstream source fails (overzoom
#: keeps the viewport non-blank rather than 502-ing the tile proxy). This is a
#: filesystem path: the proxy serves local archives straight from disk, so a
#: browser-style URL here would send every range to httpx and 502 (issue #171).
DEFAULT_TILE_ARCHIVE_PATH = str(BASEMAP_DIR / "regions" / "sample.pmtiles")

#: Directory holding region archives a client may download (issue #204).
REGIONS_DIR = BASEMAP_DIR / "regions"

#: The kit's published region list (hemna/basemap-kit builds and owns
#: ``maps-manifest.json``); this host serves it as published so ids, names,
#: groups, sizes, digests, maxzoom and bounds cannot drift (issue #204).
REGION_MANIFEST_FILE = BASEMAP_DIR / "maps-manifest.json"

#: Served URLs for the client-facing region list and per-region archives
#: (issue #204; same values as hemna/basemap-kit's nginx config).
REGION_MANIFEST_ROUTE = "/basemap/maps-manifest.json"
REGION_ARCHIVE_ROUTE = "/basemap/regions/{name}.pmtiles"

#: Same values as the kit's published CORS block (docker/basemap-kit/cors.conf);
#: pmtiles:// downloaders range-request the archives cross-origin.
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
    "Access-Control-Allow-Headers": "Range, If-Match, If-None-Match, If-Modified-Since",
    "Access-Control-Expose-Headers": "Content-Range, Content-Length, Accept-Ranges, ETag",
}

#: Internal zoom-11 gap filler, not a region an app may choose. The loader
#: never lets these through into the list clients read.
INTERNAL_REGION_IDS = frozenset({"low-fallback"})

#: Vector PMTiles v3 header is a fixed 127-byte struct (pmtiles spec v3).
_PMTILES_MAGIC = b"PMTiles"
_PMTILES_HEADER_SIZE = 127
_PMTILES_SPEC_VERSION = 3
#: ``TileType.Mvt == 1`` in the spec; mirrors the vendored ``pmtiles.js``.
_PMTILES_TILE_TYPE_MVT = 1


class BasemapSourceError(RuntimeError):
    """A tile source failed its probe (404, network error, invalid archive)."""


class UpstreamRangeError(RuntimeError):
    """The upstream marked the byte range unsatisfiable (HTTP 416)."""

    def __init__(self, total: int):
        super().__init__(f"upstream range unsatisfiable (total {total})")
        self.total = total


def kit_region_manifest(path: Path | None = None) -> dict:
    """The kit's published region list, read exactly as published.

    Raises :class:`BasemapSourceError` with a message a log reader can act on
    when the file is missing, is not the shape clients read, or lists a region
    under a group the list does not define. Serving a differently-named or
    partly-filled list would be worse than an error. Regions named in
    :data:`INTERNAL_REGION_IDS` never appear in the returned ``regions`` list.
    """
    p = Path(path) if path is not None else REGION_MANIFEST_FILE
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except OSError as e:
        raise BasemapSourceError(f"region list unreadable at {p}: {e}") from e
    except ValueError as e:
        raise BasemapSourceError(f"region list is not valid JSON at {p}: {e}") from e
    try:
        regions, groups = doc["regions"], doc["groups"]
        if not regions or not groups:
            raise KeyError("a non-empty regions and groups array")
        if type(doc["schemaVersion"]) is not int:
            raise BasemapSourceError(
                f"region list at {p}: schemaVersion is not an int")
        for key in ("tileSchema", "buildDate"):
            if key not in doc:
                raise KeyError(key)
        for g in groups:
            if not isinstance(g, dict):
                raise BasemapSourceError(
                    f"region list at {p}: groups entry is not an object: {g!r}")
            for key in ("id", "name", "order"):
                if key not in g:
                    raise KeyError(f"group {g.get('id')!r}: {key}")
            if type(g["order"]) is not int:
                raise BasemapSourceError(
                    f"region list at {p}: group {g['id']!r} order is not an int")
        for r in regions:
            if not isinstance(r, dict):
                raise BasemapSourceError(
                    f"region list at {p}: regions entry is not an object: {r!r}")
            for key in ("id", "name", "group", "url", "bytes", "sha256",
                        "maxzoom", "bounds"):
                if key not in r:
                    raise KeyError(f"region {r.get('id')!r}: {key}")
            if type(r["maxzoom"]) is not int:
                raise BasemapSourceError(
                    f"region list at {p}: region {r['id']!r} maxzoom is not an int")
        declared = {g["id"] for g in groups}
        unknown = sorted({r["group"] for r in regions if r["group"] not in declared})
        if unknown:
            raise KeyError(f"a groups entry for {unknown}")
    except (KeyError, TypeError) as e:
        raise BasemapSourceError(f"region list at {p} is missing {e}") from e
    visible = [r for r in regions if r["id"] not in INTERNAL_REGION_IDS]
    return {**doc, "regions": visible}


def parse_pmtiles_header(raw: bytes) -> dict:
    """Validate a PMTiles v3 header (127 bytes) and return its parsed fields.

    Raises :class:`BasemapSourceError` if the bytes are not a vector (MVT)
    PMTiles v3 header.
    """
    if len(raw) < _PMTILES_HEADER_SIZE:
        raise BasemapSourceError(
            f"archive header too short ({len(raw)} < {_PMTILES_HEADER_SIZE} bytes)"
        )
    if raw[:7] != _PMTILES_MAGIC:
        raise BasemapSourceError("archive missing PMTiles magic")
    version = raw[7]
    if version != _PMTILES_SPEC_VERSION:
        raise BasemapSourceError(
            f"archive spec version {version}, expected {_PMTILES_SPEC_VERSION} (v3)"
        )
    tile_type = raw[99]
    if tile_type != _PMTILES_TILE_TYPE_MVT:
        raise BasemapSourceError(
            f"archive tile type {tile_type}, expected {_PMTILES_TILE_TYPE_MVT} (MVT/vector)"
        )
    min_lon_e7, min_lat_e7, max_lon_e7, max_lat_e7 = struct.unpack("<4i", raw[102:118])
    return {
        "version": version,
        "tile_type": tile_type,
        "min_zoom": raw[100],
        "max_zoom": raw[101],
        "min_lon": min_lon_e7 / 1e7,
        "min_lat": min_lat_e7 / 1e7,
        "max_lon": max_lon_e7 / 1e7,
        "max_lat": max_lat_e7 / 1e7,
    }


def probe_source(src: str, timeout: float = PROBE_TIMEOUT_SECONDS) -> dict:
    """Probe one tile source; return its parsed v3 header or raise.

    HTTP(S) sources are probed with a ``Range: bytes=0-126`` request (the
    127-byte header only). A 404, network error, short body, or invalid header
    raises :class:`BasemapSourceError` - the caller moves on to the next
    source. Local file paths are read directly.
    """
    if os.path.isfile(src):
        with open(src, "rb") as f:
            return parse_pmtiles_header(f.read(_PMTILES_HEADER_SIZE))
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(
                src, headers={"Range": f"bytes=0-{_PMTILES_HEADER_SIZE - 1}"}
            )
    except httpx.HTTPError as e:
        raise BasemapSourceError(f"network error: {type(e).__name__}: {e}") from e
    if resp.status_code == 404:
        raise BasemapSourceError("HTTP 404 not found")
    if resp.status_code not in (200, 206):
        raise BasemapSourceError(f"HTTP {resp.status_code}")
    return parse_pmtiles_header(bytes(resp.content[:_PMTILES_HEADER_SIZE]))


def _resolve_style(sources=None, probe=None,
                   planet_factory=default_planet_url) -> tuple[str, str, dict | None]:
    """Resolve the **z12+ upstream** tile archive for served styles.

    Precedence:

    1. ``DIREWOLF_MAP_TILES_URL`` (single, pinned) wins without probing.
    2. Otherwise each entry of ``sources`` (default
       :data:`DEFAULT_UPSTREAM_SOURCES`) is probed in order and the first with
       a valid vector PMTiles v3 header **and ``max_zoom`` >=
       :data:`UPSTREAM_MINZOOM`** is used. The zoom gate (issue #154) exists
       because a planet archive that does not reach the z12+ band cannot fill it.
    3. If every source fails, the vendored sample is used as a last resort
       (overzoom keeps the viewport non-blank) and a warning is logged.

    Returns ``(url, source_label, header)`` where ``header`` is ``None`` for
    the single pinned-URL path (which never probes).
    """
    single = os.environ.get(UPSTREAM_URL_ENV, "").strip()
    if single:
        return single, UPSTREAM_URL_ENV, None
    probe = probe or probe_source
    ordered = list(sources) if sources is not None else list(DEFAULT_UPSTREAM_SOURCES)
    tried = []
    for entry in ordered:
        src = planet_factory() if str(entry).strip().lower() == "protomaps" else entry
        try:
            header = probe(src)
        except BasemapSourceError as e:
            tried.append(f"{src}: {e}")
            continue
        if header["max_zoom"] < UPSTREAM_MINZOOM:
            tried.append(f"{src}: max_zoom {header['max_zoom']} < {UPSTREAM_MINZOOM}")
            continue
        LOG.info("basemap z12+ tiles served from %s", src)
        return src, f"source:{entry}", header
    LOG.warning(
        "all basemap tile sources failed (%s); falling back to the vendored sample",
        " | ".join(tried),
    )
    return DEFAULT_TILE_ARCHIVE_PATH, "local-fallback", None


def resolve_style_url(sources=None, probe=None) -> tuple[str, str]:
    """Return ``(url, source_label)`` for the z12+ upstream archive.

    See :func:`_resolve_style` for the cloud-first precedence; this wrapper
    drops the probed header so callers keep the two-tuple contract.
    """
    url, label, _header = _resolve_style(sources, probe)
    return url, label


def upstream_sources(config) -> list:
    """Ordered upstream source list from ``basemap.sources`` (cloud-first)."""
    basemap = getattr(config, "basemap", None)
    if basemap is not None and getattr(basemap, "sources", None):
        return list(basemap.sources)
    return list(DEFAULT_UPSTREAM_SOURCES)


def render_style(theme: str,
                 proxy_url: str = UPSTREAM_PROXY_URL,
                 upstream_max_zoom: int = FALLBACK_UPSTREAM_MAXZOOM) -> str:
    """Return a kit style split into local z0-15 + same-origin upstream
    (issues #154, #160).

    The kit's ``protomaps`` vector source is pointed at the served local
    region archive (:data:`LOCAL_REGION_URL`) and gets ``maxzoom``
    :data:`LOCAL_MAXZOOM` so the station's z12-15 tiles render locally (fast,
    LAN-local) instead of through the upstream proxy. A second vector source
    (:data:`UPSTREAM_SOURCE_ID`) points at the **same-origin** proxy
    (:data:`UPSTREAM_PROXY_URL`) for the upstream band; it gets ``minzoom``
    :data:`UPSTREAM_MINZOOM` and ``maxzoom`` ``upstream_max_zoom`` so areas
    outside the region archive keep coverage and the top archive zoom
    overzooms - no integer zoom 0-16 is ever left without a drawing layer.

    Layer split (the seamless local/upstream handoff):

    * Layers whose ``minzoom`` is at or above :data:`UPSTREAM_MINZOOM` are
      upstream-only: they keep their id and zooms but draw from the upstream
      source.
    * Every other layer stays on the local source; its ``maxzoom`` is at
      least ``LOCAL_MAXZOOM + 1`` (so it draws through the whole of zoom
      :data:`LOCAL_MAXZOOM`), keeping any own min zoom the kit declared. A
      layer that carries its **own** maxzoom keeps only its local copy - its
      zoom-12+ range belongs to a counterpart layer in the kit (e.g. the
      ``_early``/``_late`` casing pairs). A layer without its own maxzoom also
      gets an ``<id>-hi`` twin on the upstream source starting at
      :data:`UPSTREAM_MINZOOM`, so local (in-region) and upstream
      (out-of-region) ranges both have coverage.
      Note: in-region, local layers and their ``-hi`` twins overlap at
      z12-15 (same Protomaps data, redrawn) - acceptable, flagged for review.

    Args:
        theme: ``"light"`` or ``"dark"`` (the two kit styles).
        proxy_url: same-origin path the upstream source is pointed at.
        upstream_max_zoom: the upstream archive's top zoom; the upstream
            source gets this ``maxzoom`` so beyond it tiles are overzoomed.

    Raises:
        ValueError: for an unsupported theme.
    """
    if theme not in ("light", "dark"):
        raise ValueError(f"unsupported basemap theme: {theme!r}")
    style = json.loads((BASEMAP_DIR / f"style.{theme}.json").read_text(encoding="utf-8"))

    local_src = dict(style["sources"]["protomaps"])
    local_src["url"] = LOCAL_REGION_URL
    local_src["maxzoom"] = LOCAL_MAXZOOM  # overzoom the z15 tiles past z15
    style["sources"]["protomaps"] = local_src

    upstream_src = {k: v for k, v in local_src.items() if k != "maxzoom"}
    upstream_src["url"] = proxy_url
    upstream_src["minzoom"] = UPSTREAM_MINZOOM
    upstream_src["maxzoom"] = upstream_max_zoom
    style["sources"][UPSTREAM_SOURCE_ID] = upstream_src

    split_layers = []
    for layer in style["layers"]:
        if layer.get("source") != "protomaps":
            split_layers.append(layer)
            continue
        if layer.get("minzoom", 0) >= UPSTREAM_MINZOOM:
            layer["source"] = UPSTREAM_SOURCE_ID
            # parity gate (#157): name minor streets one zoom earlier than the
            # kit's z15 - protomaps data carries named minor roads at z14.
            if (layer.get("id") == "roads_labels_minor"
                    and layer["minzoom"] > MINOR_ROAD_LABEL_MINZOOM):
                layer["minzoom"] = MINOR_ROAD_LABEL_MINZOOM
            split_layers.append(layer)
            continue
        has_own_maxzoom = "maxzoom" in layer
        if not has_own_maxzoom:
            layer["maxzoom"] = LOCAL_MAXZOOM + 1  # draw through the whole of z15
        elif layer["maxzoom"] < LOCAL_MAXZOOM + 1:
            layer["maxzoom"] = LOCAL_MAXZOOM + 1  # never leave a hole below z16
        split_layers.append(layer)
        if has_own_maxzoom:
            continue  # own-maxzoom layer: no -hi twin (z12+ counterpart exists)
        twin = dict(layer)
        twin["id"] = f"{layer['id']}-hi"
        twin["source"] = UPSTREAM_SOURCE_ID
        twin["minzoom"] = max(layer.get("minzoom", 0), UPSTREAM_MINZOOM)
        twin.pop("maxzoom", None)
        split_layers.append(twin)
    style["layers"] = split_layers
    return json.dumps(style)


#: Last resolved upstream: ``[monotonic, url, label, max_zoom]`` or ``[]``.
_UPSTREAM_RESOLVE: list = []
_UPSTREAM_RESOLVE_LOCK = threading.Lock()


def resolve_upstream_cached(sources=None, probe=None,
                            ttl_seconds: float = UPSTREAM_RESOLVE_TTL_SECONDS
                            ) -> tuple[str, str, int]:
    """Return ``(upstream_url, source_label, upstream_max_zoom)``, TTL-cached.

    Runs the cloud-first probe chain (:func:`_resolve_style`) at most once per
    ``ttl_seconds`` so per-tile proxy requests never re-probe. The label is the
    same one the style route surfaces in ``X-Basemap-Source``.
    """
    with _UPSTREAM_RESOLVE_LOCK:
        if _UPSTREAM_RESOLVE and (monotonic() - _UPSTREAM_RESOLVE[0]) < ttl_seconds:
            return _UPSTREAM_RESOLVE[1], _UPSTREAM_RESOLVE[2], _UPSTREAM_RESOLVE[3]
    url, label, header = _resolve_style(sources, probe)
    max_zoom = header["max_zoom"] if header else FALLBACK_UPSTREAM_MAXZOOM
    with _UPSTREAM_RESOLVE_LOCK:
        _UPSTREAM_RESOLVE[:] = [monotonic(), url, label, max_zoom]
    return url, label, max_zoom


def _content_range_total(headers) -> int:
    """Parse the upstream 206 ``Content-Range`` for the archive's total size."""
    value = (headers.get("Content-Range") or "").strip()
    match = re.fullmatch(r"bytes \d+-\d+/(\d+)", value)
    if not match:
        raise BasemapSourceError(
            f"upstream returned unparsable Content-Range: {value!r}"
        )
    return int(match.group(1))


#: In-memory byte-range cache for the proxy: key ``(url, start, length)``.
_PROXY_CACHE: OrderedDict[tuple, tuple] = OrderedDict()
_PROXY_CACHE_LOCK = threading.Lock()


def proxy_fetch_range(url: str, start: int, length: int,
                      client=None) -> tuple[bytes, int]:
    """Fetch one byte range from the upstream archive, caching the result.

    Returns ``(data, total_archive_bytes)``. Raises
    :class:`UpstreamRangeError` (416) or :class:`BasemapSourceError` (network
    error / non-range response) so the route can answer 416 / 502
    respectively.

    Local file paths (a dev-hosted basemap archive, or a Pi's on-disk region
    copy - the same ``os.path.isfile`` case :func:`probe_source` handles) are
    served straight from disk: no network, nothing cached.
    """
    if os.path.isfile(url):
        total = os.path.getsize(url)
        if start >= total:
            raise UpstreamRangeError(total)
        end = min(start + length - 1, total - 1)
        with open(url, "rb") as f:
            f.seek(start)
            data = f.read(end - start + 1)
        return data, total
    key = (url, start, length)
    with _PROXY_CACHE_LOCK:
        hit = _PROXY_CACHE.get(key)
    if hit is not None:
        return hit
    end = start + length - 1
    try:
        if client is not None:
            resp = client.get(url, headers={"Range": f"bytes={start}-{end}"})
        else:
            with httpx.Client(timeout=PROXY_FETCH_TIMEOUT_SECONDS) as c:
                resp = c.get(url, headers={"Range": f"bytes={start}-{end}"})
    except httpx.HTTPError as e:
        raise BasemapSourceError(
            f"proxy upstream network error: {type(e).__name__}: {e}"
        ) from e
    if resp.status_code == 416:
        match = re.match(
            r"bytes \*/(\d+)",
            (resp.headers.get("Content-Range") or "").strip(),
        )
        total = int(match.group(1)) if match else 0
        raise UpstreamRangeError(total)
    if resp.status_code != 206:
        raise BasemapSourceError(
            f"proxy upstream HTTP {resp.status_code} (want 206 range response)"
        )
    total = _content_range_total(resp.headers)
    data = bytes(resp.content)
    cached = (data, total)
    with _PROXY_CACHE_LOCK:
        _PROXY_CACHE[key] = cached
        _PROXY_CACHE.move_to_end(key)
        # Bound both the entry count and the retained bytes so anonymous
        # requests cannot pin an unbounded amount of memory (issue #169).
        while _PROXY_CACHE and (
            len(_PROXY_CACHE) > PROXY_CACHE_MAX_ENTRIES
            or sum(len(entry[0]) for entry in _PROXY_CACHE.values())
            > PROXY_CACHE_MAX_BYTES
        ):
            _PROXY_CACHE.popitem(last=False)
    return cached

