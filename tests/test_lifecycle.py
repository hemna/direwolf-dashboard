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
    _store_weather_report,
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

    def test_get_stats_dict_disconnected(self):
        """Stats when AGW is disconnected."""
        services = DirewolfServices(
            config=Config(),
            config_path=None,
            storage=MagicMock(),
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
    @patch("direwolf_dashboard.lifecycle.Storage")
    async def test_startup_creates_all_services(
        self, MockStorage, MockProcessor, MockAGWReader, MockLogTailer
    ):
        """Verify startup_services returns a fully populated DirewolfServices."""
        # Setup mocks
        mock_storage = AsyncMock()
        MockStorage.return_value = mock_storage

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
        assert services.processor is mock_processor
        assert services.agw_reader is mock_agw
        assert services.log_tailer is mock_tailer
        assert services.broadcast_queue is not None
        assert services.start_time > 0
        assert len(services.background_tasks) == 6  # agw, tailer, broadcast, housekeep, stats, basemap auto-fetch

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
        mock_storage = AsyncMock()

        # Create a real task to test cancellation
        async def dummy():
            await asyncio.sleep(100)

        task = asyncio.create_task(dummy())

        services = DirewolfServices(
            config=Config(),
            config_path=None,
            storage=mock_storage,
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
            processor=MagicMock(),
            broadcast_queue=queue,
            agw_reader=MagicMock(connected=False),
            log_tailer=MagicMock(active=False),
            start_time=time.time(),
        )

    async def test_consumer_does_not_rollback_a_storage_transaction(self):
        """The consumer leaves transaction rollback to the storage method."""
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

        mock_storage.rollback.assert_not_awaited()

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
        mock_storage.rollback.assert_not_awaited()

    async def test_zero_coordinates_upsert_station(self):
        """A packet at lat=0.0/lon=0.0 (equator/prime meridian) is treated as
        having a position — regression for issue #71 (falsy 0.0 check)."""
        mock_storage = AsyncMock()
        mock_storage.insert_packet.return_value = 1
        mock_storage.get_my_position.return_value = None

        queue = asyncio.Queue()
        pkt = {
            "from_call": "EQ0",
            "type": "GPSPacket",
            "timestamp": time.time(),
            "latitude": 0.0,
            "longitude": 0.0,
            "symbol": ">",
            "symbol_table": "/",
        }
        await queue.put(pkt)

        services = self._make_services(mock_storage, queue)

        async def run_one():
            task = asyncio.create_task(_broadcast_consumer(services))
            await asyncio.sleep(0.05)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        await run_one()

        # The station upsert must have been called WITH position data.
        mock_storage.upsert_station.assert_awaited()
        call_kwargs = mock_storage.upsert_station.await_args.kwargs
        assert call_kwargs["latitude"] == 0.0
        assert call_kwargs["longitude"] == 0.0
        # The packet must NOT have been back-filled from the stations table
        # (get_station is only used for packets without a position).
        mock_storage.get_station.assert_not_awaited()


class TestObjectItemNotStations:
    """Object/item packets report a named location, not the reporting
    station's own position — they must not update the stations table
    (issue #170)."""

    def _make_services(self, storage, queue):
        return DirewolfServices(
            config=Config(),
            config_path=None,
            storage=storage,
            processor=MagicMock(),
            broadcast_queue=queue,
            agw_reader=MagicMock(connected=False),
            log_tailer=MagicMock(active=False),
            start_time=time.time(),
        )

    async def _run_one(self, services):
        task = asyncio.create_task(_broadcast_consumer(services))
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def test_object_packet_does_not_update_station(self):
        """An APRS object must not overwrite the reporting station's row."""
        mock_storage = AsyncMock()
        mock_storage.insert_packet.return_value = 1
        mock_storage.get_my_position.return_value = None

        queue = asyncio.Queue()
        await queue.put({
            "from_call": "WB4BOR",
            "type": "ObjectPacket",
            "timestamp": time.time(),
            "latitude": 37.75,
            "longitude": -77.45,
            "symbol": ">",
            "symbol_table": "/",
            "object_name": "OBJONE",
        })
        services = self._make_services(mock_storage, queue)
        await self._run_one(services)

        mock_storage.insert_packet.assert_awaited_once()
        mock_storage.upsert_station.assert_not_awaited()
        mock_storage.get_station.assert_not_awaited()

    async def test_item_packet_does_not_update_station(self):
        """An APRS item must not overwrite the reporting station's row."""
        mock_storage = AsyncMock()
        mock_storage.insert_packet.return_value = 1
        mock_storage.get_my_position.return_value = None

        queue = asyncio.Queue()
        await queue.put({
            "from_call": "WB4BOR",
            "type": "ItemPacket",
            "timestamp": time.time(),
            "latitude": 37.75,
            "longitude": -77.45,
            "symbol": ">",
            "symbol_table": "/",
            "item_name": "FIRE",
        })
        services = self._make_services(mock_storage, queue)
        await self._run_one(services)

        mock_storage.insert_packet.assert_awaited_once()
        mock_storage.upsert_station.assert_not_awaited()
        mock_storage.get_station.assert_not_awaited()

    async def test_gps_packet_still_updates_station(self):
        """Ordinary station position packets keep updating the station table."""
        mock_storage = AsyncMock()
        mock_storage.insert_packet.return_value = 1
        mock_storage.get_my_position.return_value = None

        queue = asyncio.Queue()
        await queue.put({
            "from_call": "WB4BOR",
            "type": "GPSPacket",
            "timestamp": time.time(),
            "latitude": 37.75,
            "longitude": -77.45,
            "symbol": ">",
            "symbol_table": "/",
        })
        services = self._make_services(mock_storage, queue)
        await self._run_one(services)

        mock_storage.upsert_station.assert_awaited_once()


class TestResolveMyPositionCache:
    """Tests for the my_position caching logic in resolve_my_position."""

    def _make_services(self, storage):
        return DirewolfServices(
            config=Config(),
            config_path=None,
            storage=storage,
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


class TestMyPositionCallsignCache:
    """Cached tracked callsign avoids per-packet DB reads (issue #76)."""

    def _make_services(self, storage):
        return DirewolfServices(
            config=Config(),
            config_path=None,
            storage=storage,
            processor=MagicMock(),
            broadcast_queue=asyncio.Queue(),
            agw_reader=MagicMock(connected=False),
            log_tailer=MagicMock(active=False),
            start_time=time.time(),
        )

    async def test_resolve_populates_callsign(self):
        """resolve_my_position caches the tracked station callsign."""
        mock_storage = AsyncMock()
        mock_storage.get_my_position.return_value = {
            "type": "station", "callsign": "TRACKED"
        }
        mock_storage.get_station.return_value = {
            "callsign": "TRACKED", "latitude": 37.75, "longitude": -77.45
        }
        services = self._make_services(mock_storage)

        result = await resolve_my_position(services)

        assert result == (37.75, -77.45)
        assert services._my_position_callsign == "TRACKED"

    async def test_consumer_invalidates_tracked_callsign(self):
        """A position packet from the tracked callsign invalidates the cache
        so the next resolve picks up the moved station — without polling
        get_my_position on every packet (issue #76)."""
        mock_storage = AsyncMock()
        mock_storage.insert_packet.return_value = 1
        mock_storage.get_my_position.return_value = {
            "type": "station", "callsign": "TRACKED"
        }

        station_state = {"callsign": "TRACKED", "latitude": 37.75, "longitude": -77.45}

        async def fake_get_station(callsign):
            return station_state

        mock_storage.get_station.side_effect = fake_get_station

        services = self._make_services(mock_storage)

        # Warm the cache with the station's old position
        result = await resolve_my_position(services)
        assert result == (37.75, -77.45)
        assert services._my_position_dirty is False
        calls_after_warm = mock_storage.get_my_position.call_count

        # Station moves; a new packet arrives (the DB upsert already happened)
        station_state = {"callsign": "TRACKED", "latitude": 37.9, "longitude": -77.5}

        pkt = {
            "from_call": "TRACKED",
            "type": "GPSPacket",
            "timestamp": time.time(),
            "latitude": 37.9,
            "longitude": -77.5,
        }
        await services.broadcast_queue.put(pkt)

        async def run_one():
            task = asyncio.create_task(_broadcast_consumer(services))
            await asyncio.sleep(0.2)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        await run_one()

        assert mock_storage.insert_packet.await_count == 1
        # The tracked station's own packet triggers exactly ONE refresh read
        # during processing (inside enrich_with_bearing). Previously the
        # invalidation path added a SECOND read on every packet.
        assert mock_storage.get_my_position.call_count == calls_after_warm + 1

        # Cache was invalidated and refreshed during processing: resolve
        # returns the new position with no additional DB read.
        result = await resolve_my_position(services)
        assert result == (37.9, -77.5)
        assert mock_storage.get_my_position.call_count == calls_after_warm + 1

    async def test_consumer_ignores_other_callsigns(self):
        """Packets from other callsigns do not touch the cache."""
        mock_storage = AsyncMock()
        mock_storage.insert_packet.return_value = 1
        mock_storage.get_my_position.return_value = {
            "type": "station", "callsign": "TRACKED"
        }
        mock_storage.get_station.return_value = {
            "callsign": "TRACKED", "latitude": 37.75, "longitude": -77.45
        }

        services = self._make_services(mock_storage)
        await resolve_my_position(services)
        assert services._my_position_dirty is False

        pkt = {
            "from_call": "OTHER",
            "type": "GPSPacket",
            "timestamp": time.time(),
            "latitude": 38.0,
            "longitude": -77.0,
        }
        await services.broadcast_queue.put(pkt)

        async def run_one():
            task = asyncio.create_task(_broadcast_consumer(services))
            await asyncio.sleep(0.05)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        await run_one()

        assert services._my_position_dirty is False


class TestStoreWeatherReport:
    """Tests for _store_weather_report zero-value handling (issue #72)."""

    def _make_services(self):
        services = MagicMock()
        services.storage = AsyncMock()
        return services

    async def test_zero_values_preserved(self):
        """Valid 0.0 readings (0C, 0% RH, 0-deg wind, 0 m/s) are stored, not
        dropped by an `or` fallback — regression for issue #72."""
        parsed = {
            "weather": {
                "temperature": 0.0,
                "humidity": 0.0,
                "wind_direction": 0.0,
                "wind_speed": 0.0,
                "pressure": 1013.0,
            }
        }
        services = self._make_services()
        with patch("direwolf_dashboard.lifecycle.aprslib.parse", return_value=parsed):
            await _store_weather_report(
                {"from_call": "WX0", "timestamp": 123.0, "raw_packet": "WX0>APRS:@..."},
                services,
            )
        report = services.storage.insert_weather_report.await_args.args[0]
        assert report["temperature"] == 0.0
        assert report["humidity"] == 0.0
        assert report["wind_direction"] == 0.0
        assert report["wind_speed"] == 0.0
        assert report["pressure"] == 1013.0

    async def test_top_level_fallback_still_works(self):
        """When weather is empty, top-level parsed keys are used."""
        parsed = {"temperature": 0.0, "humidity": 50.0}
        services = self._make_services()
        with patch("direwolf_dashboard.lifecycle.aprslib.parse", return_value=parsed):
            await _store_weather_report(
                {"from_call": "WX1", "timestamp": 123.0, "raw_packet": "WX1>APRS:@..."},
                services,
            )
        report = services.storage.insert_weather_report.await_args.args[0]
        assert report["temperature"] == 0.0
        assert report["humidity"] == 50.0


    async def test_underscore_packet_stores_wind(self):
        """_ WX packets carry wind as top-level course/speed (raw digits mph);
        they must reach storage as wind_direction/wind_speed (m/s)."""
        services = self._make_services()
        await _store_weather_report(
            {"from_call": "TEST1", "timestamp": 123.0,
             "raw_packet": "TEST1>APRS,TCPXX*:!4713.58N/08724.97W_230/040g055t068"
                           "r000p000P000h50b10051.DsVP"},
            services,
        )  # does NOT patch aprslib.parse — drives the real parser
        report = services.storage.insert_weather_report.await_args.args[0]
        assert report["wind_direction"] == 230
        assert report["wind_speed"] == pytest.approx(40 * 0.44704, abs=0.01)  # 40 mph
        # temp 20C / rh 50%  ->  dewpoint ~9.25C (also pins AC3)
        assert report["dewpoint"] == pytest.approx(9.25, abs=0.05)


class TestBroadcastConcurrent:
    """Concurrent WebSocket broadcast (issue #84)."""

    async def test_slow_client_does_not_block_others(self):
        """A slow client must not delay sends to other clients."""
        ws_fast = AsyncMock()

        async def slow_send(message):
            await asyncio.sleep(0.2)

        ws_slow = AsyncMock()
        ws_slow.send_text.side_effect = slow_send
        clients = {ws_fast, ws_slow}

        t0 = time.time()
        await broadcast_event("packet", {"id": 1}, clients)
        elapsed = time.time() - t0

        # Concurrent dispatch: total time ~ the slow client only, not the sum.
        assert elapsed < 0.3, f"sequential broadcast took {elapsed:.2f}s"
        ws_fast.send_text.assert_awaited_once()

    async def test_single_client_ok(self):
        """Single-client path still works."""
        ws = AsyncMock()
        await broadcast_event("test", {"k": "v"}, {ws})
        ws.send_text.assert_awaited_once()


class TestBroadcastStalledClient:
    """A client whose send never completes must not block the broadcast —
    and therefore must not block packet storage (issue #166)."""

    class _StalledWS:
        """Fake WebSocket whose send_text never completes until cancelled."""

        def __init__(self):
            self.closed = False

        async def send_text(self, message):
            await asyncio.sleep(3600)  # never completes on its own

        async def close(self):
            self.closed = True

    async def test_stalled_client_removed_others_served(self, monkeypatch):
        """A stalled client is dropped; healthy clients still get the event
        and the broadcast returns promptly instead of hanging forever."""
        monkeypatch.setattr("direwolf_dashboard.lifecycle.WS_SEND_TIMEOUT", 0.05)
        monkeypatch.setattr("direwolf_dashboard.lifecycle.WS_CLOSE_TIMEOUT", 0.2)

        stalled = self._StalledWS()
        healthy = AsyncMock()
        clients = {stalled, healthy}

        t0 = time.time()
        await broadcast_event("packet", {"id": 1}, clients)
        elapsed = time.time() - t0

        assert elapsed < 1.0, f"broadcast blocked on stalled client: {elapsed:.2f}s"
        assert stalled not in clients
        assert stalled.closed  # dropped socket was closed
        assert healthy in clients
        healthy.send_text.assert_awaited_once()

    async def test_consumer_storage_progresses_with_stalled_client(self, monkeypatch):
        """Regression for issue #166: packet storage keeps progressing while
        one browser client is stalled, instead of queueing behind it."""
        monkeypatch.setattr("direwolf_dashboard.lifecycle.WS_SEND_TIMEOUT", 0.05)
        monkeypatch.setattr("direwolf_dashboard.lifecycle.WS_CLOSE_TIMEOUT", 0.2)

        stalled = self._StalledWS()
        mock_storage = AsyncMock()
        mock_storage.insert_packet.return_value = 1
        mock_storage.get_my_position.return_value = None

        queue = asyncio.Queue()
        for i in range(3):
            await queue.put({
                "from_call": f"ST{i}",
                "type": "GPSPacket",
                "timestamp": time.time(),
                "latitude": 37.75,
                "longitude": -77.45,
                "symbol": ">",
                "symbol_table": "/",
            })

        services = DirewolfServices(
            config=Config(),
            config_path=None,
            storage=mock_storage,
            processor=MagicMock(),
            broadcast_queue=queue,
            agw_reader=MagicMock(connected=False),
            log_tailer=MagicMock(active=False),
            start_time=time.time(),
        )
        services.ws_clients.add(stalled)

        async def run_three():
            task = asyncio.create_task(_broadcast_consumer(services))
            await asyncio.sleep(0.3)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        await run_three()

        # All packets stored even though the first broadcast had to time out
        # the stalled client; the stalled client is gone after the first one.
        assert mock_storage.insert_packet.await_count == 3
        assert stalled not in services.ws_clients
        assert stalled.closed
