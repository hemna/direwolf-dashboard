"""Pure-Python bbox extract of a PMTiles v3 archive (worldwide offline maps).

Reads only the directories and tiles for a bbox from a (remote) planet archive
via HTTP Range requests and writes a standalone region .pmtiles, the same job
as ``pmtiles extract`` but without needing the go-pmtiles binary on the Pi.
Adjacent tile byte ranges are coalesced so a region costs a handful of
requests, not one per tile.
"""

from __future__ import annotations

import gzip
import math
import os
from typing import Callable, Iterable

from pmtiles.tile import (
    Compression,
    deserialize_directory,
    deserialize_header,
    tileid_to_zxy,
    zxy_to_tileid,
)
from pmtiles.writer import Writer

GetBytes = Callable[[int, int], bytes]

#: Refuse extracts bigger than this many tiles (keeps a Pi from filling up).
MAX_TILES = 60_000
#: Merge tile reads whose gap is below this many bytes into one request.
COALESCE_GAP = 256 * 1024


class ExtractError(RuntimeError):
    pass


def _lon_to_x(lon: float, z: int) -> int:
    n = 1 << z
    return min(n - 1, max(0, int((lon + 180.0) / 360.0 * n)))


def _lat_to_y(lat: float, z: int) -> int:
    n = 1 << z
    lat = max(-85.0511, min(85.0511, lat))
    r = math.radians(lat)
    return min(n - 1, max(0, int((1.0 - math.asinh(math.tan(r)) / math.pi) / 2.0 * n)))


def tiles_in_bbox(bbox, minzoom: int, maxzoom: int) -> list[int]:
    """Sorted tile ids covering ``bbox`` (min_lon, min_lat, max_lon, max_lat)."""
    min_lon, min_lat, max_lon, max_lat = bbox
    ids = []
    for z in range(minzoom, maxzoom + 1):
        x0, x1 = _lon_to_x(min_lon, z), _lon_to_x(max_lon, z)
        y0, y1 = _lat_to_y(max_lat, z), _lat_to_y(min_lat, z)
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                ids.append(zxy_to_tileid(z, x, y))
    ids.sort()
    return ids


def estimate_tile_count(bbox, maxzoom: int) -> int:
    min_lon, min_lat, max_lon, max_lat = bbox
    total = 0
    for z in range(0, maxzoom + 1):
        total += (_lon_to_x(max_lon, z) - _lon_to_x(min_lon, z) + 1) * (
            _lat_to_y(min_lat, z) - _lat_to_y(max_lat, z) + 1
        )
    return total


def _find(entries, tile_id):
    lo, hi = 0, len(entries) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        d = tile_id - entries[mid].tile_id
        if d > 0:
            lo = mid + 1
        elif d < 0:
            hi = mid - 1
        else:
            return entries[mid]
    if hi >= 0:
        e = entries[hi]
        if e.run_length == 0 or tile_id - e.tile_id < e.run_length:
            return e
    return None


class _Dirs:
    """Directory reader with a cache, so each leaf is fetched once."""

    def __init__(self, get_bytes: GetBytes, header: dict):
        self.get = get_bytes
        self.h = header
        self.cache: dict[tuple[int, int], list] = {}

    def _dir(self, offset, length):
        key = (offset, length)
        if key not in self.cache:
            # deserialize_directory gunzips (pmtiles writes gzip internally).
            self.cache[key] = deserialize_directory(self.get(offset, length))
        return self.cache[key]

    def lookup(self, tile_id):
        off, length = self.h["root_offset"], self.h["root_length"]
        for _ in range(4):
            e = _find(self._dir(off, length), tile_id)
            if e is None:
                return None
            if e.run_length == 0:
                off, length = self.h["leaf_directory_offset"] + e.offset, e.length
                continue
            return e
        return None


def _coalesce(spans: Iterable[tuple[int, int]], gap: int):
    """Group (offset, length) spans (sorted) into merged read ranges."""
    groups = []
    for off, length in spans:
        if groups and off - (groups[-1][0] + groups[-1][1]) <= gap:
            start = groups[-1][0]
            groups[-1][1] = max(groups[-1][1], off + length - start)
            groups[-1][2].append((off, length))
        else:
            groups.append([off, length, [(off, length)]])
    return groups


def extract(get_bytes: GetBytes, out_path: str, bbox, maxzoom: int,
            log: Callable[[str], None] = lambda _m: None) -> dict:
    """Extract ``bbox`` up to ``maxzoom`` into ``out_path``. Returns stats."""
    header = deserialize_header(get_bytes(0, 127))
    maxzoom = min(maxzoom, header["max_zoom"])
    minzoom = header["min_zoom"]
    est = estimate_tile_count(bbox, maxzoom)
    if est > MAX_TILES:
        raise ExtractError(f"region too large: ~{est} tiles > {MAX_TILES}; lower radius or maxzoom")
    meta_raw = get_bytes(header["metadata_offset"], header["metadata_length"])
    if header["internal_compression"] == Compression.GZIP:
        meta_raw = gzip.decompress(meta_raw)
    import json
    metadata = json.loads(meta_raw or b"{}")

    dirs = _Dirs(get_bytes, header)
    wanted = []  # (tile_id, abs_offset, length)
    for tid in tiles_in_bbox(bbox, minzoom, maxzoom):
        e = dirs.lookup(tid)
        if e is not None:
            wanted.append((tid, header["tile_data_offset"] + e.offset, e.length))
    if not wanted:
        raise ExtractError("no tiles in bbox (is the planet URL right?)")
    log(f"{len(wanted)} tiles, {len(dirs.cache)} directories read")

    data: dict[tuple[int, int], bytes] = {}
    spans = sorted({(o, n) for _t, o, n in wanted})
    groups = _coalesce(spans, COALESCE_GAP)
    for i, (start, length, members) in enumerate(groups):
        blob = get_bytes(start, length)
        for off, n in members:
            data[(off, n)] = blob[off - start: off - start + n]
        if i % 20 == 0:
            log(f"read {i + 1}/{len(groups)} ranges")

    tmp = out_path + ".part"
    with open(tmp, "wb") as f:
        w = Writer(f)
        for tid, off, n in wanted:
            w.write_tile(tid, data[(off, n)])
        out_header = {
            "tile_type": header["tile_type"],
            "tile_compression": header["tile_compression"],
            "min_lon_e7": int(bbox[0] * 1e7), "min_lat_e7": int(bbox[1] * 1e7),
            "max_lon_e7": int(bbox[2] * 1e7), "max_lat_e7": int(bbox[3] * 1e7),
            "center_zoom": max(minzoom, min(maxzoom, 10)),
            "center_lon_e7": int((bbox[0] + bbox[2]) / 2 * 1e7),
            "center_lat_e7": int((bbox[1] + bbox[3]) / 2 * 1e7),
        }
        w.finalize(out_header, metadata)
    os.replace(tmp, out_path)
    return {"tiles": len(wanted), "requests": len(groups) + len(dirs.cache) + 2,
            "bytes": os.path.getsize(out_path), "maxzoom": maxzoom}


def http_get_bytes(url: str, client=None) -> GetBytes:
    """Range reader for a remote archive (httpx; one keep-alive connection)."""
    import httpx

    c = client or httpx.Client(timeout=60.0, follow_redirects=True)

    def get(offset: int, length: int) -> bytes:
        if length <= 0:
            return b""
        r = c.get(url, headers={"Range": f"bytes={offset}-{offset + length - 1}"})
        if r.status_code not in (200, 206):
            raise ExtractError(f"HTTP {r.status_code} for {url}")
        body = r.content
        if r.status_code == 200:  # server ignored Range
            body = body[offset: offset + length]
        return body

    return get


def file_get_bytes(path: str) -> GetBytes:
    def get(offset: int, length: int) -> bytes:
        with open(path, "rb") as f:
            f.seek(offset)
            return f.read(length)
    return get


def _iter_entries(get_bytes: GetBytes, header: dict):
    """Yield every tile entry in the archive's directories (not its header)."""
    stack = list(deserialize_directory(
        get_bytes(header["root_offset"], header["root_length"])))
    while stack:
        e = stack.pop()
        if e.run_length == 0:
            stack.extend(deserialize_directory(
                get_bytes(header["leaf_directory_offset"] + e.offset, e.length)))
        else:
            yield e


def archive_inventory(path: str) -> dict:
    """The tiles a local archive actually holds, read from its directories.

    Returns ``{"min_zoom", "max_zoom", "tiles"}``. The header alone cannot be
    trusted after an extract: the extractor stamps the requested bbox into it
    (#214 review B-2), so a coverage check has to look at the tiles present.
    """
    get = file_get_bytes(path)
    header = deserialize_header(get(0, 127))
    tiles: set[int] = set()
    for e in _iter_entries(get, header):
        tiles.update(range(e.tile_id, e.tile_id + e.run_length))
    zooms = [tileid_to_zxy(tid)[0] for tid in (min(tiles), max(tiles))] if tiles else []
    return {"min_zoom": zooms[0] if zooms else None,
            "max_zoom": zooms[1] if zooms else None, "tiles": tiles}


def require_tile_coverage(path: str, bbox, maxzoom: int) -> None:
    """Reject an archive that does not hold the tiles ``bbox`` needs at ``maxzoom``.

    ``maxzoom`` is the zoom the extraction was expected to reach (the request,
    capped by the source's own header), not the archive's claim. An archive
    with only coarse tiles cannot stand in for a region entry promising street
    detail: the client would show a blank map offline (issue #214).
    """
    try:
        inv = archive_inventory(path)
    except Exception as e:  # noqa: BLE001 - an unreadable archive is unusable
        raise ExtractError(
            f"archive unreadable: {type(e).__name__}: {e}") from e
    needed = set(tiles_in_bbox(tuple(bbox), maxzoom, maxzoom))
    missing = needed - inv["tiles"]
    if inv["max_zoom"] is None or inv["max_zoom"] < maxzoom or missing:
        raise ExtractError(
            f"archive holds tiles to z{inv['max_zoom']}, need z{maxzoom} "
            f"({len(needed) - len(missing)} of {len(needed)} tiles present)")
