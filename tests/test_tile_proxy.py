"""Tests for TileProxy — async get_cache_stats with TTL caching."""

import asyncio
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


class TestCheckCacheBudget:
    async def test_under_budget_does_not_evict(self, tile_proxy):
        """When cache is under budget _check_cache_budget() completes without error."""
        # cache_dir doesn't exist yet → cache_size_mb == 0, well under 100 MB limit
        await tile_proxy._check_cache_budget()
        # Stats should now be populated (no eviction needed)
        assert tile_proxy._stats_cache is not None
        assert tile_proxy._stats_cache["cache_size_mb"] == 0.0

    async def test_over_budget_triggers_eviction(self, tmp_path):
        """When cache exceeds max_cache_mb, _check_cache_budget() calls eviction."""
        # Use max_cache_mb=0; write a file large enough that cache_size_mb rounds
        # to > 0.0 so the budget check actually triggers eviction.
        proxy = TileProxy(cache_dir=str(tmp_path / "tiles"), max_cache_mb=0)
        cache = tmp_path / "tiles"
        cache.mkdir(parents=True)
        # 1 MiB + 1 byte → cache_size_mb = 1.0, which is > 0
        (cache / "tile.png").write_bytes(b"\x89PNG" + b"\x00" * (1024 * 1024))
        await proxy._check_cache_budget()
        # After eviction the stats cache is invalidated
        assert proxy._stats_cache is None

    async def test_runs_inside_running_event_loop(self, tile_proxy):
        """get_cache_stats and _check_cache_budget use get_running_loop() — they must
        succeed when called from within an already-running event loop (as pytest-asyncio
        provides), and must not raise DeprecationWarning or RuntimeError."""
        # asyncio.get_running_loop() raises RuntimeError if there is no running loop;
        # the fact that this test executes successfully proves we are inside one.
        loop = asyncio.get_running_loop()
        assert loop is not None
        stats = await tile_proxy.get_cache_stats()
        assert isinstance(stats, dict)


class TestGetTile:
    async def test_serves_cached_tile_from_executor(self, tile_proxy, tmp_path):
        """get_tile returns cached PNG bytes without hitting the network.

        The read is executor-offloaded (issue #86) — verify it works and
        that the network client is never touched on a cache hit.
        """
        from unittest.mock import patch

        await tile_proxy.init()
        # Seed a cached tile
        cache = tmp_path / "tiles" / "1" / "1"
        cache.mkdir(parents=True)
        (cache / "1.png").write_bytes(b"\x89PNG fake tile")

        with patch.object(tile_proxy._client, "get") as mock_get:
            data = await tile_proxy.get_tile(1, 1, 1)

        assert data == b"\x89PNG fake tile"
        mock_get.assert_not_called()
        await tile_proxy.close()

    async def test_missing_tile_returns_none(self, tile_proxy, tmp_path):
        """A missing cached tile that fails to fetch returns None."""
        from unittest.mock import AsyncMock

        await tile_proxy.init()
        tile_proxy._client.get = AsyncMock(return_value=None)
        # Force a 404-like failure
        class _R:
            status_code = 404
            content = b""
        tile_proxy._client.get = AsyncMock(return_value=_R())

        data = await tile_proxy.get_tile(9, 1, 1)
        assert data is None
        await tile_proxy.close()
