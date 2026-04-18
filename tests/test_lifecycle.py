"""Tests for lifecycle module — DirewolfServices, startup, shutdown."""

import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from direwolf_dashboard.config import Config
from direwolf_dashboard.lifecycle import (
    DirewolfServices,
    ServiceContainer,
    startup_services,
    shutdown_services,
    resolve_my_position,
    enrich_with_bearing,
    broadcast_event,
)
from direwolf_dashboard.storage import Storage


class TestDirewolfServices:
    def test_get_stats_dict(self):
        """Verify stats response structure."""
        services = DirewolfServices(
            config=Config(),
            config_path=None,
            storage=MagicMock(),
            tile_proxy=MagicMock(),
            processor=MagicMock(),
            broadcast_queue=asyncio.Queue(),
            agw_reader=MagicMock(connected=True),
            log_tailer=MagicMock(active=True),
            start_time=time.time() - 100,
        )
        stats = services.get_stats_dict()
        assert "uptime_seconds" in stats
        assert stats["uptime_seconds"] >= 100
        assert stats["agw_connected"] is True
        assert stats["log_tailer_active"] is True
        assert "tile_cache" in stats

    def test_get_stats_dict_disconnected(self):
        """Stats when AGW is disconnected."""
        services = DirewolfServices(
            config=Config(),
            config_path=None,
            storage=MagicMock(),
            tile_proxy=MagicMock(),
            processor=MagicMock(),
            broadcast_queue=asyncio.Queue(),
            agw_reader=MagicMock(connected=False),
            log_tailer=MagicMock(active=False),
            start_time=time.time(),
        )
        stats = services.get_stats_dict()
        assert stats["agw_connected"] is False
        assert stats["log_tailer_active"] is False


class TestServiceContainer:
    def test_initial_state(self):
        """Container starts with no services."""
        container = ServiceContainer()
        assert container.services is None

    def test_set_services(self):
        """Container can be populated."""
        container = ServiceContainer()
        services = MagicMock(spec=DirewolfServices)
        container.services = services
        assert container.services is services


class TestStartupServices:
    @patch("direwolf_dashboard.lifecycle.LogTailer")
    @patch("direwolf_dashboard.lifecycle.AGWReader")
    @patch("direwolf_dashboard.lifecycle.PacketProcessor")
    @patch("direwolf_dashboard.lifecycle.TileProxy")
    @patch("direwolf_dashboard.lifecycle.Storage")
    async def test_startup_creates_all_services(
        self, MockStorage, MockTileProxy, MockProcessor, MockAGWReader, MockLogTailer
    ):
        """Verify startup_services returns a fully populated DirewolfServices."""
        # Setup mocks
        mock_storage = AsyncMock()
        MockStorage.return_value = mock_storage

        mock_tile_proxy = AsyncMock()
        MockTileProxy.return_value = mock_tile_proxy

        mock_processor = MagicMock()
        MockProcessor.return_value = mock_processor

        mock_agw = MagicMock()
        mock_agw.run = AsyncMock()
        MockAGWReader.return_value = mock_agw

        mock_tailer = MagicMock()
        mock_tailer.run = AsyncMock()
        MockLogTailer.return_value = mock_tailer

        config = Config()
        services = await startup_services(config, "/tmp/test.yaml")

        assert services.config is config
        assert services.config_path == "/tmp/test.yaml"
        assert services.storage is mock_storage
        assert services.tile_proxy is mock_tile_proxy
        assert services.processor is mock_processor
        assert services.agw_reader is mock_agw
        assert services.log_tailer is mock_tailer
        assert services.broadcast_queue is not None
        assert services.start_time > 0
        assert len(services.background_tasks) == 5  # agw, tailer, broadcast, housekeep, stats

        # Cleanup: cancel background tasks to avoid warnings
        for task in services.background_tasks:
            task.cancel()
        await asyncio.gather(*services.background_tasks, return_exceptions=True)

    @patch("direwolf_dashboard.lifecycle.Storage")
    async def test_startup_failure_propagates(self, MockStorage):
        """If Storage init fails, exception propagates."""
        mock_storage = AsyncMock()
        mock_storage.init.side_effect = RuntimeError("DB failure")
        MockStorage.return_value = mock_storage

        config = Config()
        with pytest.raises(RuntimeError, match="DB failure"):
            await startup_services(config)


class TestShutdownServices:
    async def test_shutdown_cleans_up(self):
        """Verify shutdown stops readers, cancels tasks, closes resources."""
        mock_agw = AsyncMock()
        mock_tailer = AsyncMock()
        mock_tile_proxy = AsyncMock()
        mock_storage = AsyncMock()

        # Create a real task to test cancellation
        async def dummy():
            await asyncio.sleep(100)

        task = asyncio.create_task(dummy())

        services = DirewolfServices(
            config=Config(),
            config_path=None,
            storage=mock_storage,
            tile_proxy=mock_tile_proxy,
            processor=MagicMock(),
            broadcast_queue=asyncio.Queue(),
            agw_reader=mock_agw,
            log_tailer=mock_tailer,
            start_time=time.time(),
            background_tasks=[task],
        )

        await shutdown_services(services)

        mock_agw.stop.assert_awaited_once()
        mock_tailer.stop.assert_awaited_once()
        # cancel() was called; await to let cancellation propagate
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()
        mock_tile_proxy.close.assert_awaited_once()
        mock_storage.close.assert_awaited_once()


class TestBroadcastEvent:
    async def test_broadcast_to_clients(self):
        """Broadcast sends to all clients."""
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        clients = {ws1, ws2}

        await broadcast_event("test", {"key": "val"}, clients)

        ws1.send_text.assert_awaited_once()
        ws2.send_text.assert_awaited_once()

    async def test_broadcast_removes_disconnected(self):
        """Disconnected clients are removed from the set."""
        ws_good = AsyncMock()
        ws_bad = AsyncMock()
        ws_bad.send_text.side_effect = Exception("disconnected")
        clients = {ws_good, ws_bad}

        await broadcast_event("test", {"key": "val"}, clients)

        assert ws_bad not in clients
        assert ws_good in clients

    async def test_broadcast_empty_clients(self):
        """No-op when no clients connected."""
        clients: set = set()
        await broadcast_event("test", {"key": "val"}, clients)  # Should not raise
