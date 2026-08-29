"""Tests for router factory functions."""

import time
import unittest.mock as mock

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
