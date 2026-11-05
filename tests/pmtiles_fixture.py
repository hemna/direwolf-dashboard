"""Tiny synthetic PMTiles 'planet' for extractor tests (no network)."""
from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import Writer


def tile_bytes(z, x, y):
    return f"tile {z}/{x}/{y}".encode()


def make_planet(path, maxzoom=6, bounds=None):
    """Write a tiny planet archive.

    ``bounds`` is ``(west, south, east, north)`` for the header; the default
    (whole world) matches what the writer emits when no bounds are given.
    """
    header = {"tile_type": TileType.MVT, "tile_compression": Compression.NONE}
    if bounds is not None:
        west, south, east, north = bounds
        header.update({
            "min_lon_e7": int(west * 10_000_000),
            "min_lat_e7": int(south * 10_000_000),
            "max_lon_e7": int(east * 10_000_000),
            "max_lat_e7": int(north * 10_000_000),
        })
    ids = []
    for z in range(maxzoom + 1):
        n = 1 << z
        for x in range(n):
            for y in range(n):
                ids.append((zxy_to_tileid(z, x, y), z, x, y))
    ids.sort()
    with open(path, "wb") as f:
        w = Writer(f)
        for tid, z, x, y in ids:
            w.write_tile(tid, tile_bytes(z, x, y))
        w.finalize(
            header,
            {"name": "synthetic planet", "attribution": "test"},
        )
    return path
