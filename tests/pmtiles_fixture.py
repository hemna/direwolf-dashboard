"""Tiny synthetic PMTiles 'planet' for extractor tests (no network)."""
from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import Writer


def tile_bytes(z, x, y):
    return f"tile {z}/{x}/{y}".encode()


def make_planet(path, maxzoom=6):
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
            {"tile_type": TileType.MVT, "tile_compression": Compression.NONE},
            {"name": "synthetic planet", "attribution": "test"},
        )
    return path
