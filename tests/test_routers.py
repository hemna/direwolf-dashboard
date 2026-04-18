"""Tests for router factory functions."""

import pytest
from unittest.mock import MagicMock

from direwolf_dashboard.lifecycle import ServiceContainer
from direwolf_dashboard.routers import create_api_router, create_ws_router, create_index_router


class TestCreateApiRouter:
    def test_all_endpoints_registered(self):
        """API router has all expected routes."""
        container = ServiceContainer()
        router = create_api_router(container)

        paths = {route.path for route in router.routes}
        expected = {
            "/packets",
            "/stations",
            "/station/{callsign}",
            "/stations/positions",
            "/stations/tracks",
            "/stats",
            "/config",
            "/tiles/{z}/{x}/{y}.png",
            "/tiles/preload",
        }
        assert expected.issubset(paths), f"Missing routes: {expected - paths}"

    def test_routes_have_no_api_prefix(self):
        """Routes should NOT have /api/ prefix — that's added by the mounting app."""
        container = ServiceContainer()
        router = create_api_router(container)

        for route in router.routes:
            assert not route.path.startswith("/api/"), f"Route has /api/ prefix: {route.path}"


class TestCreateWsRouter:
    def test_default_path(self):
        """WS router defaults to /ws."""
        container = ServiceContainer()
        router = create_ws_router(container)

        paths = {route.path for route in router.routes}
        assert "/ws" in paths

    def test_custom_path(self):
        """WS router accepts custom path."""
        container = ServiceContainer()
        router = create_ws_router(container, path="/ws/direwolf")

        paths = {route.path for route in router.routes}
        assert "/ws/direwolf" in paths
        assert "/ws" not in paths


class TestCreateIndexRouter:
    def test_index_route_registered(self, tmp_path):
        """Index router has a / route."""
        router = create_index_router(tmp_path)

        paths = {route.path for route in router.routes}
        assert "/" in paths
