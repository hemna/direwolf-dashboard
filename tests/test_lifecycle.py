"""Tests for lifecycle module — DirewolfServices, startup, shutdown."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from direwolf_dashboard.config import Config
from direwolf_dashboard.lifecycle import (
    DirewolfServices,
    ServiceContainer,
    _broadcast_consumer,
    broadcast_event,
    resolve_my_position,
    shutdown_services,
    startup_services,
)


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
        # tile_cache is no longer in get_stats_dict() — it is fetched
        # asynchronously by callers via await tile_proxy.get_cache_stats()

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


class TestBroadcastConsumerRollback:
    """Tests for storage-error isolation in _broadcast_consumer."""

    def _make_services(self, storage, queue):
        return DirewolfServices(
            config=Config(),
            config_path=None,
            storage=storage,
            tile_proxy=MagicMock(),
            processor=MagicMock(),
            broadcast_queue=queue,
            agw_reader=MagicMock(connected=False),
            log_tailer=MagicMock(active=False),
            start_time=time.time(),
        )

    async def test_rollback_called_on_insert_failure(self):
        """When insert_packet raises, rollback() is called."""
        mock_storage = AsyncMock()
        mock_storage.insert_packet.side_effect = RuntimeError("disk full")

        queue = asyncio.Queue()
        await queue.put({"from_call": "TEST", "type": "GPSPacket", "timestamp": time.time()})

        services = self._make_services(mock_storage, queue)

        async def run_one():
            task = asyncio.create_task(_broadcast_consumer(services))
            await asyncio.sleep(0.05)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        await run_one()

        mock_storage.rollback.assert_awaited_once()

    async def test_consumer_continues_after_storage_error(self):
        """Consumer processes subsequent packets even after a storage failure."""
        call_count = 0

        async def flaky_insert(packet):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("transient disk error")
            return call_count  # row_id

        mock_storage = AsyncMock()
        mock_storage.insert_packet.side_effect = flaky_insert
        mock_storage.get_station.return_value = None

        queue = asyncio.Queue()
        pkt1 = {"from_call": "CALL1", "type": "GPSPacket", "timestamp": time.time()}
        pkt2 = {"from_call": "CALL2", "type": "GPSPacket", "timestamp": time.time()}
        await queue.put(pkt1)
        await queue.put(pkt2)

        services = self._make_services(mock_storage, queue)

        async def run_two():
            task = asyncio.create_task(_broadcast_consumer(services))
            await asyncio.sleep(0.1)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        await run_two()

        assert mock_storage.insert_packet.await_count == 2
        mock_storage.rollback.assert_awaited_once()


class TestResolveMyPositionCache:
    """Tests for the my_position caching logic in resolve_my_position."""

    def _make_services(self, storage):
        return DirewolfServices(
            config=Config(),
            config_path=None,
            storage=storage,
            tile_proxy=MagicMock(),
            processor=MagicMock(),
            broadcast_queue=asyncio.Queue(),
            agw_reader=MagicMock(connected=False),
            log_tailer=MagicMock(active=False),
            start_time=time.time(),
        )

    async def test_cache_hit_skips_db(self):
        """When cache is warm (dirty=False), DB is not called."""
        mock_storage = AsyncMock()
        services = self._make_services(mock_storage)

        services._my_position_cache = (37.75, -77.45)
        services._my_position_dirty = False

        result = await resolve_my_position(services)

        assert result == (37.75, -77.45)
        mock_storage.get_my_position.assert_not_called()

    async def test_cache_miss_calls_db(self):
        """When dirty=True, the DB is queried and result is cached."""
        mock_storage = AsyncMock()
        mock_storage.get_my_position.return_value = {
            "type": "pin", "latitude": 38.5, "longitude": -78.0
        }
        services = self._make_services(mock_storage)
        assert services._my_position_dirty is True

        result = await resolve_my_position(services)

        assert result == (38.5, -78.0)
        mock_storage.get_my_position.assert_awaited_once()
        assert services._my_position_cache == (38.5, -78.0)
        assert services._my_position_dirty is False

    async def test_second_call_uses_cache(self):
        """Second call after warm-up does not hit the DB."""
        mock_storage = AsyncMock()
        mock_storage.get_my_position.return_value = {
            "type": "pin", "latitude": 38.5, "longitude": -78.0
        }
        services = self._make_services(mock_storage)

        await resolve_my_position(services)
        await resolve_my_position(services)

        assert mock_storage.get_my_position.await_count == 1

    async def test_dirty_flag_causes_refresh(self):
        """Setting dirty=True triggers a fresh DB read."""
        mock_storage = AsyncMock()
        mock_storage.get_my_position.return_value = {
            "type": "pin", "latitude": 38.5, "longitude": -78.0
        }
        services = self._make_services(mock_storage)

        await resolve_my_position(services)
        services._my_position_dirty = True
        mock_storage.get_my_position.return_value = {
            "type": "pin", "latitude": 39.0, "longitude": -79.0
        }
        result = await resolve_my_position(services)

        assert result == (39.0, -79.0)
        assert mock_storage.get_my_position.await_count == 2

    async def test_no_my_position_caches_none(self):
        """When no my_position is set and config has no coords, None is cached."""
        mock_storage = AsyncMock()
        mock_storage.get_my_position.return_value = None
        services = self._make_services(mock_storage)

        result = await resolve_my_position(services)

        assert result is None
        assert services._my_position_cache is None
        assert services._my_position_dirty is False


class TestHousekeepingJitter:
    """Tests for jitter in _housekeeping_loop sleep interval."""

    async def test_housekeeping_sleep_has_jitter(self):
        """Sleep duration must be within 3420–3780 s (3600 ± 5%)."""
        from direwolf_dashboard.lifecycle import _housekeeping_loop

        mock_storage = AsyncMock()
        mock_storage.housekeep.return_value = 0

        services = DirewolfServices(
            config=MagicMock(),
            config_path=None,
            storage=mock_storage,
            tile_proxy=MagicMock(),
            processor=MagicMock(),
            broadcast_queue=asyncio.Queue(),
            agw_reader=MagicMock(connected=False),
            log_tailer=MagicMock(active=False),
            start_time=time.time(),
        )

        sleep_durations = []

        async def capture_sleep(duration):
            sleep_durations.append(duration)
            raise asyncio.CancelledError()

        with patch("direwolf_dashboard.lifecycle.asyncio.sleep", side_effect=capture_sleep):
            with pytest.raises(asyncio.CancelledError):
                await _housekeeping_loop(services, retention_days=7)

        assert len(sleep_durations) == 1
        duration = sleep_durations[0]
        assert 3420 <= duration <= 3780, f"Sleep duration {duration} outside jitter range [3420, 3780]"
