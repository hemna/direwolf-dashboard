"""Tests for FastAPI server REST endpoints."""

import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from direwolf_dashboard.config import Config
from direwolf_dashboard.server import create_app, state
from direwolf_dashboard.storage import Storage


@pytest.fixture
async def test_app(tmp_path):
    """Create a test app with real storage but mock AGW/log tailer."""
    config = Config()
    config.storage.db_path = str(tmp_path / "test.db")
    config.tiles.cache_dir = str(tmp_path / "tiles")
    config.station.latitude = 37.75
    config.station.longitude = -77.45
    config_path = str(tmp_path / "config.yaml")

    app = create_app(config, config_path)

    # We need to manually init storage for tests since lifespan won't run with TestClient sync
    storage = Storage(config.storage.db_path)
    await storage.init()
    state.storage = storage
    state.config = config
    state.config_path = config_path
    state.start_time = time.time()
    state.agw_reader = MagicMock()
    state.agw_reader.connected = False
    state.log_tailer = MagicMock()
    state.log_tailer.active = False
    state.tile_proxy = MagicMock()
    state.tile_proxy.get_cache_stats.return_value = {"tile_count": 0, "cache_size_mb": 0}

    yield app, storage

    await storage.close()


def _make_packet(**overrides) -> dict:
    packet = {
        "timestamp": time.time(),
        "type": "GPSPacket",
        "tx": False,
        "from_call": "WB4BOR",
        "to_call": "APRS",
        "path": ["WIDE1-1"],
        "msg_no": "23",
        "latitude": 37.80,
        "longitude": -77.40,
        "symbol": ">",
        "symbol_table": "/",
        "human_info": "38.50mph",
        "comment": "Test",
        "audio_level": 42,
        "raw_log": ["[0 L>R] WB4BOR>APRS"],
        "compact_log": "<span>RX↓ GPSPacket</span>",
        "raw_packet": "WB4BOR>APRS,WIDE1-1:!3745.00N/07730.00W>",
    }
    packet.update(overrides)
    return packet


class TestPacketsEndpoint:
    async def test_get_packets_empty(self, test_app):
        app, storage = test_app
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/packets")
            assert response.status_code == 200
            assert response.json() == []

    async def test_get_packets_with_data(self, test_app):
        app, storage = test_app
        await storage.insert_packet(_make_packet(from_call="WB4BOR"))
        await storage.insert_packet(_make_packet(from_call="N3LLO"))

        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/packets")
            assert response.status_code == 200
            data = response.json()
            assert len(data) == 2

    async def test_get_packets_filter_callsign(self, test_app):
        app, storage = test_app
        await storage.insert_packet(_make_packet(from_call="WB4BOR"))
        await storage.insert_packet(_make_packet(from_call="N3LLO"))

        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/packets?callsign=WB4BOR")
            assert response.status_code == 200
            data = response.json()
            assert len(data) == 1
            assert data[0]["from_call"] == "WB4BOR"


class TestStationsEndpoint:
    async def test_get_stations_empty(self, test_app):
        app, storage = test_app
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/stations")
            assert response.status_code == 200
            assert response.json() == []

    async def test_get_stations_with_data(self, test_app):
        app, storage = test_app
        await storage.upsert_station("WB4BOR", time.time(), 37.75, -77.45)
        await storage.upsert_station("N3LLO", time.time(), 38.0, -78.0)

        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/stations")
            assert response.status_code == 200
            data = response.json()
            assert len(data) == 2


class TestStationDetailEndpoint:
    async def test_get_station_not_found(self, test_app):
        app, storage = test_app
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/station/UNKNOWN")
            assert response.status_code == 404

    async def test_get_station_with_track(self, test_app):
        app, storage = test_app
        t = time.time()
        await storage.upsert_station("WB4BOR", t, 37.75, -77.45)
        for i in range(3):
            await storage.insert_packet(
                _make_packet(from_call="WB4BOR", timestamp=t + i, latitude=37.75 + i * 0.01)
            )

        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/station/WB4BOR")
            assert response.status_code == 200
            data = response.json()
            assert data["station"]["callsign"] == "WB4BOR"
            assert len(data["track"]) == 3


class TestStatsEndpoint:
    async def test_get_stats(self, test_app):
        app, storage = test_app
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/stats")
            assert response.status_code == 200
            data = response.json()
            assert "packets_total" in data
            assert "uptime_seconds" in data
            assert "agw_connected" in data


class TestConfigEndpoint:
    async def test_get_config(self, test_app):
        app, storage = test_app
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/config")
            assert response.status_code == 200
            data = response.json()
            assert "station" in data
            assert "server" in data
            assert data["station"]["callsign"] == "N0CALL"

    async def test_put_config_hot_reload(self, test_app):
        app, storage = test_app
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.put(
                "/api/config",
                json={"storage": {"retention_days": 14}},
            )
            assert response.status_code == 200
            data = response.json()
            assert "storage.retention_days" in data["updated"]
            assert data["restart_required"] is False

    async def test_put_config_restart_required(self, test_app):
        app, storage = test_app
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.put(
                "/api/config",
                json={"server": {"port": 9090}},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["restart_required"] is True


class TestIndexEndpoint:
    async def test_index_page(self, test_app):
        app, storage = test_app
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/")
            assert response.status_code == 200
            # Should return HTML
            assert "html" in response.headers.get("content-type", "").lower()
