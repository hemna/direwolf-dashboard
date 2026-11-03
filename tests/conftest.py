"""Shared test setup."""
import pytest

from direwolf_dashboard.basemap import BasemapSourceError


@pytest.fixture(autouse=True)
def _no_basemap_auto_fetch(monkeypatch, request):
    """Never let the startup region download hit the network in tests."""
    if request.node.get_closest_marker("real_auto_fetch"):
        return

    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr("direwolf_dashboard.lifecycle.auto_fetch_loop", _noop)


@pytest.fixture(autouse=True)
def _no_manifest_fetch(monkeypatch, request):
    """Never let the served region list hit the network in tests."""
    if request.node.get_closest_marker("real_manifest_fetch"):
        return

    def _offline(*_a, **_k):
        raise BasemapSourceError("offline in tests")

    monkeypatch.setattr(
        "direwolf_dashboard.basemap.fetch_region_manifest", _offline
    )


@pytest.fixture(autouse=True)
def _no_region_download(monkeypatch, request):
    """Never let a region archive download reach the network in tests."""
    if request.node.get_closest_marker("real_region_download"):
        return

    def _offline(*_a, **_k):
        raise BasemapSourceError("offline in tests")

    monkeypatch.setattr(
        "direwolf_dashboard.basemap._open_region_archive", _offline
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "real_auto_fetch: run the real basemap auto-fetch loop")
    config.addinivalue_line(
        "markers", "real_manifest_fetch: let the region-list loader reach the network")
    config.addinivalue_line(
        "markers", "real_region_download: let a kit region download reach the network")

