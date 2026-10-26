"""Map source resolution must not block unrelated async work (issue #180).

``resolve_upstream_cached`` probes upstream archives with blocking HTTP calls.
Both map handlers must offload it to a worker thread, otherwise a slow probe
stalls every other coroutine in the process.
"""

import asyncio
import threading
from unittest.mock import MagicMock

import httpx
from starlette.applications import Starlette

from direwolf_dashboard import routers as routers_module
from direwolf_dashboard.lifecycle import ServiceContainer


def _app(route_factory):
    container = ServiceContainer()
    container.services = MagicMock()
    container.services.config.basemap.sources = ["https://e.example/planet.pmtiles"]
    return Starlette(routes=[route_factory(container)])


async def _assert_heartbeat_runs_during_probe(app, path, monkeypatch, headers=None):
    """Fail if the handler runs the blocking probe on the event loop."""
    started = threading.Event()
    release = threading.Event()
    order = []

    def slow_resolve(sources):
        order.append("probe-start")
        started.set()
        release.wait(5)
        order.append("probe-end")
        return ("https://e.example/planet.pmtiles", "source:test", 15)

    monkeypatch.setattr(routers_module, "resolve_upstream_cached", slow_resolve)

    async def heartbeat():
        while not started.is_set():
            await asyncio.sleep(0.005)
        order.append("heartbeat")
        release.set()

    def watchdog():
        # Frees a handler that blocks the loop, so the test fails instead of hangs
        if started.wait(5) and not release.wait(0.5):
            release.set()

    threading.Thread(target=watchdog, daemon=True).start()

    task = asyncio.create_task(heartbeat())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(path, headers=headers or {})
    await asyncio.wait_for(task, timeout=5)

    assert response.status_code in (200, 206), response.text
    assert "heartbeat" in order
    assert order.index("heartbeat") < order.index("probe-end"), order


async def test_style_route_offloads_source_probe(monkeypatch):
    app = _app(routers_module.create_basemap_style_route)
    await _assert_heartbeat_runs_during_probe(
        app, "/basemap/style/light.json", monkeypatch
    )


async def test_tiles_route_offloads_source_probe(monkeypatch):
    app = _app(routers_module.create_basemap_tiles_route)
    monkeypatch.setattr(
        routers_module, "proxy_fetch_range", lambda url, start, length: (b"x", 2000)
    )
    await _assert_heartbeat_runs_during_probe(
        app,
        "/basemap/tiles/upstream",
        monkeypatch,
        headers={"Range": "bytes=0-9"},
    )
