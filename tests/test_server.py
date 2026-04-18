"""Tests for FastAPI server REST endpoints."""

import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from direwolf_dashboard.config import Config
from direwolf_dashboard.server import create_app
from direwolf_dashboard.lifecycle import ServiceContainer, DirewolfServices
from direwolf_dashboard.storage import Storage


# Module-level container reference for test access
_test_container: ServiceContainer | None = None


def _create_test_app(config: Config, config_path: str) -> tuple:
    """Create app and return (app, container) for test manipulation."""
    from direwolf_dashboard.lifecycle import ServiceContainer
    from direwolf_dashboard.routers import create_api_router, create_ws_router, create_index_router

    import os
    from contextlib import asynccontextmanager
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles

    container = ServiceContainer()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Tests populate container.services manually, so lifespan is a no-op
        yield

    app = FastAPI(title="Direwolf Dashboard", lifespan=lifespan)
    app.include_router(create_api_router(container), prefix="/api")
    app.include_router(create_ws_router(container, path="/ws"))

    static_dir = os.path.join(os.path.dirname(__file__), "..", "src", "direwolf_dashboard", "static")
    if os.path.exists(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")
    app.include_router(create_index_router(static_dir))

    return app, container


@pytest.fixture
async def test_app(tmp_path):
    """Create a test app with real storage but mock AGW/log tailer."""
    config = Config()
    config.storage.db_path = str(tmp_path / "test.db")
    config.tiles.cache_dir = str(tmp_path / "tiles")
    config.station.latitude = 37.75
    config.station.longitude = -77.45
    config_path = str(tmp_path / "config.yaml")

    app, container = _create_test_app(config, config_path)

    # Init real storage for tests
    storage = Storage(config.storage.db_path)
    await storage.init()

    # Create a mock DirewolfServices-like object via the container
    agw_reader = MagicMock()
    agw_reader.connected = False
    log_tailer = MagicMock()
    log_tailer.active = False
    tile_proxy = MagicMock()
    tile_proxy.get_cache_stats.return_value = {
        "tile_count": 0,
        "cache_size_mb": 0,
    }

    container.services = DirewolfServices(
        config=config,
        config_path=config_path,
        storage=storage,
        tile_proxy=tile_proxy,
        processor=MagicMock(),
        broadcast_queue=asyncio.Queue(),
        agw_reader=agw_reader,
        log_tailer=log_tailer,
        start_time=time.time(),
    )

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
            await storage.insert_packet(_make_packet(from_call="WB4BOR", timestamp=t + i, latitude=37.75 + i * 0.01))

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


class TestStationPositionsEndpoint:
    async def test_get_positions_empty(self, test_app):
        app, storage = test_app
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/api/stations/positions")
            assert resp.status_code == 200
            assert resp.json() == {}

    async def test_get_positions_with_data(self, test_app):
        app, storage = test_app
        await storage.upsert_station("N3ABC", 100.0, latitude=40.0, longitude=-75.0)
        await storage.upsert_station("N3DEF", 100.0, latitude=39.0, longitude=-76.0)
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/api/stations/positions")
            assert resp.status_code == 200
            data = resp.json()
            assert "N3ABC" in data
            assert data["N3ABC"]["lat"] == 40.0
            assert data["N3ABC"]["lng"] == -75.0
            assert "N3DEF" in data


class TestStationTracksEndpoint:
    async def test_get_tracks_empty(self, test_app):
        app, storage = test_app
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/api/stations/tracks?hours=1")
            assert resp.status_code == 200
            assert resp.json() == {}

    async def test_get_tracks_with_data(self, test_app):
        app, storage = test_app
        t = time.time()
        for i in range(3):
            await storage.insert_packet(
                _make_packet(
                    from_call="WB4BOR",
                    timestamp=t - 1800 + i * 100,
                    latitude=37.75 + i * 0.01,
                    longitude=-77.45,
                )
            )
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/api/stations/tracks?hours=1")
            assert resp.status_code == 200
            data = resp.json()
            assert "WB4BOR" in data
            assert len(data["WB4BOR"]) == 3
            # Each point is [lat, lon, timestamp]
            assert len(data["WB4BOR"][0]) == 3


class TestConfigEndpoint:
    async def test_get_config(self, test_app):
        app, storage = test_app
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/config")
            assert response.status_code == 200
            data = response.json()
            assert "station" in data
            assert "server" in data
            assert data["station"]["latitude"] == 37.75

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


class TestMyPositionValidation:
    """Test my_position validation in PUT /api/config."""

    async def test_set_station_type(self, test_app):
        app, storage = test_app
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.put(
                "/api/config",
                json={"station": {"my_position": {"type": "station", "callsign": "WB4BOR"}}},
            )
            assert response.status_code == 200

    async def test_set_pin_type(self, test_app):
        app, storage = test_app
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.put(
                "/api/config",
                json={
                    "station": {
                        "my_position": {
                            "type": "pin",
                            "latitude": 37.75,
                            "longitude": -77.45,
                        }
                    }
                },
            )
            assert response.status_code == 200

    async def test_clear_my_position(self, test_app):
        app, storage = test_app
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.put(
                "/api/config",
                json={"station": {"my_position": {"type": None}}},
            )
            assert response.status_code == 200

    async def test_station_type_requires_callsign(self, test_app):
        app, storage = test_app
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.put(
                "/api/config",
                json={"station": {"my_position": {"type": "station", "callsign": ""}}},
            )
            assert response.status_code == 400

    async def test_pin_type_requires_valid_coords(self, test_app):
        app, storage = test_app
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.put(
                "/api/config",
                json={"station": {"my_position": {"type": "pin", "latitude": 999}}},
            )
            assert response.status_code == 400

    async def test_pin_type_requires_longitude(self, test_app):
        app, storage = test_app
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.put(
                "/api/config",
                json={"station": {"my_position": {"type": "pin", "latitude": 37.75}}},
            )
            assert response.status_code == 400

    async def test_invalid_type(self, test_app):
        app, storage = test_app
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.put(
                "/api/config",
                json={"station": {"my_position": {"type": "invalid"}}},
            )
            assert response.status_code == 400
