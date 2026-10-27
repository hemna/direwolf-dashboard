"""Tests for Starlette server REST endpoints."""

import asyncio
import time
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from direwolf_dashboard.config import Config
from direwolf_dashboard.lifecycle import DirewolfServices, ServiceContainer
from direwolf_dashboard.storage import Storage

# Module-level container reference for test access
_test_container: ServiceContainer | None = None


def _create_test_app(config: Config, config_path: str) -> tuple:
    """Create app and return (app, container) for test manipulation."""
    import os
    from contextlib import asynccontextmanager

    from starlette.applications import Starlette
    from starlette.exceptions import HTTPException
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route, WebSocketRoute
    from starlette.staticfiles import StaticFiles

    from direwolf_dashboard.lifecycle import ServiceContainer
    from direwolf_dashboard.routers import create_api_routes, create_index_handler, create_ws_handler

    container = ServiceContainer()

    @asynccontextmanager
    async def lifespan(app: Starlette):
        # Tests populate container.services manually, so lifespan is a no-op
        yield

    async def _json_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    static_dir = os.path.join(os.path.dirname(__file__), "..", "src", "direwolf_dashboard", "static")

    routes = [
        Mount("/api", routes=create_api_routes(container)),
        WebSocketRoute("/ws", create_ws_handler(container)),
    ]
    if os.path.exists(static_dir):
        routes.append(Mount("/static", StaticFiles(directory=static_dir)))
    routes.append(Route("/", create_index_handler(static_dir)))

    app = Starlette(
        lifespan=lifespan,
        routes=routes,
        exception_handlers={HTTPException: _json_error_handler},
    )
    app.state.container = container

    return app, container


@pytest.fixture
async def test_app(tmp_path):
    """Create a test app with real storage but mock AGW/log tailer."""
    config = Config()
    config.storage.db_path = str(tmp_path / "test.db")
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

    container.services = DirewolfServices(
        config=config,
        config_path=config_path,
        storage=storage,
        processor=MagicMock(),
        broadcast_queue=asyncio.Queue(),
        agw_reader=agw_reader,
        log_tailer=log_tailer,
        start_time=time.time(),
    )

    yield app, storage

    await storage.close()


@pytest.fixture
async def test_app_with_token(tmp_path):
    """Test app with server.api_token configured (issue #73)."""
    config = Config()
    config.server.api_token = "sekrit-token"
    config.storage.db_path = str(tmp_path / "test.db")
    config_path = str(tmp_path / "config.yaml")

    app, container = _create_test_app(config, config_path)

    storage = Storage(config.storage.db_path)
    await storage.init()

    container.services = DirewolfServices(
        config=config,
        config_path=config_path,
        storage=storage,
        processor=MagicMock(),
        broadcast_queue=asyncio.Queue(),
        agw_reader=MagicMock(connected=False),
        log_tailer=MagicMock(active=False),
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

    async def test_negative_limit_is_clamped(self, test_app):
        """A negative limit must not bypass the response cap (issue #177)."""
        app, storage = test_app
        for i in range(3):
            await storage.insert_packet(_make_packet(from_call=f"CALL{i}"))

        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/packets?limit=-1")

        assert response.status_code == 200
        assert len(response.json()) == 1

    async def test_negative_limit_is_clamped_for_messages(self, test_app):
        app, storage = test_app
        for i in range(3):
            await storage.insert_packet(
                _make_packet(from_call=f"CALL{i}", type="MessagePacket")
            )

        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/messages?limit=-1")

        assert response.status_code == 200
        assert len(response.json()) == 1

    async def test_negative_limit_is_clamped_for_csv_export(self, test_app):
        app, storage = test_app
        for i in range(3):
            await storage.insert_packet(_make_packet(from_call=f"CALL{i}"))

        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/packets/export?limit=-1")

        assert response.status_code == 200
        rows = [line for line in response.text.splitlines() if line.strip()]
        assert len(rows) == 2  # header + one packet


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

    async def test_rejected_mixed_update_preserves_position_and_cache(self, test_app):
        app, storage = test_app
        services = app.state.container.services
        position = {"type": "pin", "latitude": 37.75, "longitude": -77.45}
        await storage.set_my_position(position)
        services._my_position_cache = (37.75, -77.45)
        services._my_position_dirty = False

        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.put(
                "/api/config",
                json={
                    "station": {"my_position": None},
                    "storage": {"retention_days": "bad"},
                },
            )

        assert response.status_code == 400
        assert await storage.get_my_position() == position
        assert services._my_position_cache == (37.75, -77.45)
        assert services._my_position_dirty is False

    @pytest.mark.parametrize(
        "position",
        [{"type": "pin", "latitude": 38.0, "longitude": -78.0}, None],
    )
    async def test_successful_position_update_invalidates_cache(self, test_app, position):
        app, _storage = test_app
        services = app.state.container.services
        services._my_position_dirty = False

        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.put(
                "/api/config", json={"station": {"my_position": position}}
            )

        assert response.status_code == 200
        assert services._my_position_dirty is True


class TestTileProxyRetired:
    """The OSM tile-cache proxy routes are gone (issue #129).

    /api/tiles/{z}/{x}/{y}.png and /api/tiles/preload existed only for the
    Leaflet basemap. Once the map moved to the LAN-offline MapLibre kit
    basemap, the proxy is retired outright: the routes must 404 and /api/stats
    must no longer carry a tile_cache block.
    """

    async def test_tiles_route_404(self, test_app):
        app, _storage = test_app
        with TestClient(app, raise_server_exceptions=False) as client:
            assert client.get("/api/tiles/1/2/3.png").status_code == 404
            assert client.post("/api/tiles/preload", json={
                "bbox": [37.0, -78.0, 38.0, -77.0],
            }).status_code == 404
            assert client.delete("/api/tiles/preload").status_code == 404

    async def test_stats_has_no_tile_cache(self, test_app):
        app, _storage = test_app
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/stats")
            assert response.status_code == 200
            data = response.json()
            assert "tile_cache" not in data

class TestApiTokenAuth:
    """API token gating on state-changing endpoints (issue #73)."""

    async def test_read_endpoints_need_no_token(self, test_app_with_token):
        app, _storage = test_app_with_token
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get("/api/config")
            assert r.status_code == 200
            r = client.get("/api/health")
            assert r.status_code != 401

    async def test_config_get_never_exposes_api_token(self, test_app_with_token):
        """Public /api/config must never include server.api_token (issue #162)."""
        app, _storage = test_app_with_token
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get("/api/config")
            assert r.status_code == 200
            assert "api_token" not in r.json()["server"]
            # The token value itself must not appear anywhere in the body.
            assert "sekrit-token" not in r.text

    async def test_config_get_has_no_token_key_when_unset(self, test_app):
        """No api_token key leaks even when token auth is disabled (issue #162)."""
        app, _storage = test_app
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get("/api/config")
            assert r.status_code == 200
            assert "api_token" not in r.json()["server"]

    async def test_token_survives_save_without_leaked_value(self, test_app_with_token):
        """Redacting the public config must not wipe the stored token (issue #162)."""
        app, _storage = test_app_with_token
        with TestClient(app, raise_server_exceptions=False) as client:
            # The browser round-trips the public config back into a PUT; the
            # api_token key is now absent from what it sends.
            cfg = client.get("/api/config").json()
            r = client.put(
                "/api/config",
                json={"server": cfg["server"]},
                headers={"X-API-Token": "sekrit-token"},
            )
            assert r.status_code == 200
            # The token still authenticates after the save...
            r = client.put(
                "/api/config",
                json={"storage": {"retention_days": 14}},
                headers={"X-API-Token": "sekrit-token"},
            )
            assert r.status_code == 200
            # ...and a wrong token is still rejected.
            r = client.put(
                "/api/config",
                json={"storage": {"retention_days": 14}},
                headers={"X-API-Token": "wrong"},
            )
            assert r.status_code == 401

    async def test_config_put_requires_token(self, test_app_with_token):
        app, _storage = test_app_with_token
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.put("/api/config", json={"storage": {"retention_days": 14}})
            assert r.status_code == 401
            r = client.put(
                "/api/config",
                json={"storage": {"retention_days": 14}},
                headers={"X-API-Token": "wrong"},
            )
            assert r.status_code == 401
            r = client.put(
                "/api/config",
                json={"storage": {"retention_days": 14}},
                headers={"X-API-Token": "sekrit-token"},
            )
            assert r.status_code == 200

    async def test_storage_delete_requires_token(self, test_app_with_token):
        app, _storage = test_app_with_token
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.request("DELETE", "/api/storage", json={"confirm": True})
            assert r.status_code == 401
            r = client.request(
                "DELETE",
                "/api/storage",
                json={"confirm": True},
                headers={"X-API-Token": "sekrit-token"},
            )
            assert r.status_code == 200

    async def test_no_token_configured_stays_open(self, test_app):
        """When no api_token is configured, behavior is unchanged."""
        app, _storage = test_app
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.put("/api/config", json={"storage": {"retention_days": 14}})
            assert r.status_code == 200


class TestChangelogContentType:
    """Changelog is served as markdown (issue #95)."""

    async def test_changelog_served_as_markdown(self, test_app):
        app, _storage = test_app
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get("/api/changelog")
            if r.status_code == 200:
                assert "text/markdown" in r.headers.get("content-type", "").lower()


class TestConfigTypeValidation:
    """PUT /config validates scalar types (issue #91)."""

    async def test_non_numeric_retention_rejected(self, test_app):
        app, _storage = test_app
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.put(
                "/api/config",
                json={"storage": {"retention_days": "abc"}},
            )
            assert r.status_code == 400

    async def test_numeric_string_coerced(self, test_app):
        app, _storage = test_app
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.put(
                "/api/config",
                json={"storage": {"retention_days": "14"}},
            )
            assert r.status_code == 200



class TestIndexCaching:
    """index.html is read once at startup, not per request (issue #87)."""

    async def test_index_content_cached_at_startup(self, tmp_path):
        """Modifying index.html after handler creation must not change output."""
        from direwolf_dashboard.routers import create_index_handler

        static_dir = tmp_path
        index_path = static_dir / "index.html"
        index_path.write_text("<h1>original</h1>", encoding="utf-8")

        handler = create_index_handler(str(static_dir))
        first = await handler(None)
        assert first.body == b"<h1>original</h1>"

        # Change the file on disk — the handler must still serve the cached copy
        index_path.write_text("<h1>changed</h1>", encoding="utf-8")
        second = await handler(None)
        assert second.body == b"<h1>original</h1>"
