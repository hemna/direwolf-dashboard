"""Tests for router factory functions."""

import pytest
from unittest.mock import MagicMock

from starlette.routing import Route

from direwolf_dashboard.lifecycle import ServiceContainer
from direwolf_dashboard.routers import create_api_routes, create_ws_handler, create_index_handler


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
