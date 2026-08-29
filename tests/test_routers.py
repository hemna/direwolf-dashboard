"""Tests for router factory functions."""

import time
import unittest.mock as mock

import pytest
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

from direwolf_dashboard.lifecycle import ServiceContainer
from direwolf_dashboard.routers import create_api_routes, create_index_handler, create_ws_handler


class TestCreateApiRoutes:
    def test_all_endpoints_registered(self):
        """API routes list has all expected paths."""
        container = ServiceContainer()
        routes = create_api_routes(container)

        paths = {route.path for route in routes}
        expected = {
            "/packets",
            "/messages",
            "/stations",
            "/station/{callsign}",
            "/stations/positions",
            "/stations/tracks",
            "/stats",
            "/config",
            "/storage",
            "/tiles/{z}/{x}/{y}.png",
            "/tiles/preload",
            "/health",
        }
        assert expected.issubset(paths), f"Missing routes: {expected - paths}"

    def test_routes_have_no_api_prefix(self):
        """Routes should NOT have /api/ prefix — that's added by the mounting app."""
        container = ServiceContainer()
        routes = create_api_routes(container)

        for route in routes:
            assert not route.path.startswith("/api/"), f"Route has /api/ prefix: {route.path}"


class TestCreateWsHandler:
    def test_returns_callable(self):
        """WS handler factory returns a callable."""
        container = ServiceContainer()
        handler = create_ws_handler(container)
        assert callable(handler)

    def test_custom_container(self):
        """WS handler is bound to the given container."""
        container = ServiceContainer()
        handler = create_ws_handler(container)
        # Handler is a closure — just verify it's a distinct callable per container
        container2 = ServiceContainer()
        handler2 = create_ws_handler(container2)
        assert handler is not handler2


class TestCreateIndexHandler:
    def test_returns_callable(self, tmp_path):
        """Index handler factory returns a callable."""
        handler = create_index_handler(tmp_path)
        assert callable(handler)


def _build_test_app(container: ServiceContainer) -> Starlette:
    """Build a minimal Starlette app with just the API routes — no lifespan."""
    return Starlette(routes=[Mount("/api", routes=create_api_routes(container))])


class TestHealthEndpoint:
    """Tests for the /api/health endpoint."""

    def _make_services(self, agw_connected=True, log_active=True):
        """Build a minimal mock DirewolfServices."""
        services = mock.MagicMock()
        services.start_time = time.time() - 60
        agw = mock.MagicMock()
        agw.connected = agw_connected
        services.agw_reader = agw
        log = mock.MagicMock()
        log.active = log_active
        services.log_tailer = log
        return services

    def test_health_ok_when_all_connected(self):
        """Returns 200 with status=ok when AGW connected and log tailer active."""
        container = ServiceContainer()
        container.services = self._make_services(agw_connected=True, log_active=True)
        client = TestClient(_build_test_app(container), raise_server_exceptions=False)
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "uptime_seconds" in data
        assert data["uptime_seconds"] >= 0

    def test_health_degraded_when_agw_disconnected(self):
        """Returns 503 with agw issue when AGW reader is not connected."""
        container = ServiceContainer()
        container.services = self._make_services(agw_connected=False, log_active=True)
        client = TestClient(_build_test_app(container), raise_server_exceptions=False)
        resp = client.get("/api/health")
        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "degraded"
        assert any("agw" in issue for issue in data["issues"])

    def test_health_degraded_when_log_tailer_inactive(self):
        """Returns 503 with log tailer issue when log tailer is not active."""
        container = ServiceContainer()
        container.services = self._make_services(agw_connected=True, log_active=False)
        client = TestClient(_build_test_app(container), raise_server_exceptions=False)
        resp = client.get("/api/health")
        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "degraded"
        assert any("log" in issue for issue in data["issues"])

    def test_health_503_when_services_not_initialized(self):
        """Returns 503 when services container has no services yet."""
        container = ServiceContainer()
        # container.services is None by default
        client = TestClient(_build_test_app(container), raise_server_exceptions=False)
        resp = client.get("/api/health")
        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "degraded"


class TestStationsEndpointWeather:
    """Tests that /api/stations augments weather stations with last_weather."""

    def _make_services(self, stations, weather_by_cs):
        """Build a mock services where storage returns given data."""
        services = mock.MagicMock()
        storage = mock.MagicMock()
        storage.get_stations = mock.AsyncMock(return_value=stations)
        storage.get_latest_weather_by_callsign = mock.AsyncMock(return_value=weather_by_cs)
        services.storage = storage
        return services

    def test_stations_with_no_weather(self):
        """When no weather reports exist, stations are returned without last_weather."""
        container = ServiceContainer()
        container.services = self._make_services(
            stations=[{"callsign": "W1ABC", "symbol": ">"}],
            weather_by_cs={},
        )
        client = TestClient(_build_test_app(container), raise_server_exceptions=False)
        resp = client.get("/api/stations")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert "last_weather" not in data[0]

    def test_stations_weather_station_gets_last_weather(self):
        """A weather station callsign present in latest_weather gets last_weather injected."""
        wx = {"callsign": "W1WX", "temperature": 72.5, "wind_speed": 10.0, "timestamp": 1234567890.0}
        container = ServiceContainer()
        container.services = self._make_services(
            stations=[{"callsign": "W1WX", "symbol": "_"}],
            weather_by_cs={"W1WX": wx},
        )
        client = TestClient(_build_test_app(container), raise_server_exceptions=False)
        resp = client.get("/api/stations")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert "last_weather" in data[0]
        assert data[0]["last_weather"]["temperature"] == pytest.approx(72.5)

    def test_stations_non_weather_not_affected(self):
        """Stations without weather data are not modified even when other stations have weather."""
        wx = {"callsign": "W1WX", "temperature": 72.5, "timestamp": 1234567890.0}
        container = ServiceContainer()
        container.services = self._make_services(
            stations=[
                {"callsign": "W1WX", "symbol": "_"},
                {"callsign": "W1ABC", "symbol": ">"},
            ],
            weather_by_cs={"W1WX": wx},
        )
        client = TestClient(_build_test_app(container), raise_server_exceptions=False)
        resp = client.get("/api/stations")
        assert resp.status_code == 200
        data = resp.json()
        by_cs = {s["callsign"]: s for s in data}
        assert "last_weather" in by_cs["W1WX"]
        assert "last_weather" not in by_cs["W1ABC"]


class TestMessagesEndpoint:
    """Tests for the /api/messages endpoint."""

    def _make_services(self, packets):
        services = mock.MagicMock()
        storage = mock.MagicMock()
        storage.query_packets = mock.AsyncMock(return_value=packets)
        services.storage = storage
        return services

    def test_messages_route_registered(self):
        """The /messages route must be registered."""
        container = ServiceContainer()
        routes = create_api_routes(container)
        paths = {route.path for route in routes}
        assert "/messages" in paths, "/messages route must be registered"

    def test_messages_returns_200_empty(self):
        """Returns 200 with empty list when no messages exist."""
        container = ServiceContainer()
        container.services = self._make_services(packets=[])
        client = TestClient(_build_test_app(container), raise_server_exceptions=False)
        resp = client.get("/api/messages")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_messages_returns_message_packets(self):
        """Returns message packets from storage."""
        msg = {
            "timestamp": 1234567890.0,
            "type": "MessagePacket",
            "from_call": "W1ABC",
            "to_call": "W2DEF",
            "comment": "Hello World",
            "msg_no": "001",
            "tx": False,
        }
        container = ServiceContainer()
        container.services = self._make_services(packets=[msg])
        client = TestClient(_build_test_app(container), raise_server_exceptions=False)
        resp = client.get("/api/messages")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["from_call"] == "W1ABC"
        assert data[0]["comment"] == "Hello World"

    def test_messages_calls_query_packets_with_message_type(self):
        """The endpoint passes packet_type='MessagePacket' to query_packets."""
        container = ServiceContainer()
        container.services = self._make_services(packets=[])
        client = TestClient(_build_test_app(container), raise_server_exceptions=False)
        client.get("/api/messages")
        container.services.storage.query_packets.assert_called_once()
        _, kwargs = container.services.storage.query_packets.call_args
        assert kwargs.get("packet_type") == "MessagePacket" or \
               container.services.storage.query_packets.call_args[0][2] == "MessagePacket" or \
               "MessagePacket" in str(container.services.storage.query_packets.call_args)
