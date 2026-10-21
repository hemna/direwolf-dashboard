"""Shared test setup."""
import pytest


@pytest.fixture(autouse=True)
def _no_basemap_auto_fetch(monkeypatch, request):
    """Never let the startup region download hit the network in tests."""
    if request.node.get_closest_marker("real_auto_fetch"):
        return

    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr("direwolf_dashboard.lifecycle.auto_fetch_loop", _noop)


def pytest_configure(config):
    config.addinivalue_line("markers", "real_auto_fetch: run the real basemap auto-fetch loop")
