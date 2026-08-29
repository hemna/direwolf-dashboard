"""Tests for TileProxy — async get_cache_stats with TTL caching."""

import time
import pytest

from direwolf_dashboard.tile_proxy import TileProxy


@pytest.fixture
def tile_proxy(tmp_path):
    """Return a TileProxy pointed at a temp directory."""
    return TileProxy(cache_dir=str(tmp_path / "tiles"), max_cache_mb=100)


class TestGetCacheStatsKeys:
    async def test_returns_expected_keys(self, tile_proxy):
        """get_cache_stats() returns tile_count, cache_size_mb, max_cache_mb."""
        stats = await tile_proxy.get_cache_stats()
        assert "tile_count" in stats
        assert "cache_size_mb" in stats
        assert "max_cache_mb" in stats

    async def test_empty_cache_dir(self, tile_proxy, tmp_path):
        """Empty cache dir → tile_count=0, cache_size_mb=0."""
        # cache_dir does not exist yet; the walk should still succeed
        stats = await tile_proxy.get_cache_stats()
        assert stats["tile_count"] == 0
        assert stats["cache_size_mb"] == 0.0

    async def test_max_cache_mb_reflects_config(self, tmp_path):
        """max_cache_mb in stats matches the constructor argument."""
        proxy = TileProxy(cache_dir=str(tmp_path / "tiles"), max_cache_mb=250)
        stats = await proxy.get_cache_stats()
        assert stats["max_cache_mb"] == 250

    async def test_counts_png_files(self, tile_proxy, tmp_path):
        """Stats count .png files placed in the cache dir."""
        cache = tmp_path / "tiles"
        cache.mkdir(parents=True, exist_ok=True)
        sub = cache / "10" / "512"
        sub.mkdir(parents=True)
        (sub / "300.png").write_bytes(b"\x89PNG" + b"\x00" * 100)
        (sub / "301.png").write_bytes(b"\x89PNG" + b"\x00" * 100)

        # Override the proxy's cache_dir to point at our created structure
        tile_proxy.cache_dir = str(cache)
        tile_proxy._stats_cache = None  # force refresh

        stats = await tile_proxy.get_cache_stats()
        assert stats["tile_count"] == 2


class TestGetCacheStatsTTL:
    async def test_result_is_cached(self, tile_proxy):
        """Second call within TTL returns same object without re-walking."""
        stats1 = await tile_proxy.get_cache_stats()
        stats2 = await tile_proxy.get_cache_stats()
        assert stats1 is stats2  # Same dict object — cached

    async def test_cache_expires_after_ttl(self, tile_proxy):
        """After TTL expires the cache is refreshed (new dict object returned)."""
        stats1 = await tile_proxy.get_cache_stats()
        # Force expiry by back-dating the cache timestamp
        tile_proxy._stats_cache_time = time.time() - tile_proxy._stats_cache_ttl - 1
        stats2 = await tile_proxy.get_cache_stats()
        assert stats1 is not stats2  # Different object — re-fetched

    async def test_invalidated_cache_refreshes(self, tile_proxy):
        """Setting _stats_cache = None forces a fresh walk on next call."""
        await tile_proxy.get_cache_stats()
        tile_proxy._stats_cache = None
        stats2 = await tile_proxy.get_cache_stats()
        assert stats2 is not None
