"""Source chain behind the served region list (issue #212).

The kit list is fetched live from ``basemap.manifest_url`` with a timeout and
cached under ``data_dir`` for a short TTL. The vendored copy is only the
offline fallback, and the vendored world region is the last resort.
"""

import json
import os
import time
from types import SimpleNamespace

from direwolf_dashboard import basemap
from direwolf_dashboard.basemap import (
    FETCHED_MANIFEST,
    MANIFEST_CACHE_TTL,
    MANIFEST_FETCH_TIMEOUT,
    BasemapSourceError,
    kit_region_manifest,
    region_manifest,
)
from direwolf_dashboard.config import BasemapConfig, Config
from direwolf_dashboard.lifecycle import ServiceContainer
from direwolf_dashboard.routers import create_basemap_manifest_route
from starlette.applications import Starlette
from starlette.testclient import TestClient


def _manifest(region_ids):
    """A valid kit list holding ``region_ids``."""
    return {
        "schemaVersion": 1,
        "tileSchema": "v4",
        "buildDate": "2026-11-01T00:00:00Z",
        "groups": [{"id": "g", "name": "Group", "order": 0}],
        "regions": [
            {"id": rid, "name": rid, "group": "g",
             "url": f"/basemap/regions/{rid}.pmtiles", "bytes": 1,
             "sha256": "a" * 64, "maxzoom": 4, "bounds": [-1, -1, 1, 1]}
            for rid in region_ids
        ],
    }


def _write_cache(tmp_path, region_ids):
    path = tmp_path / FETCHED_MANIFEST
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_manifest(region_ids)), encoding="utf-8")
    return path


def _offline(*_a, **_k):
    raise BasemapSourceError("offline in test")


def test_fresh_cache_is_served_without_fetching(tmp_path, monkeypatch):
    _write_cache(tmp_path, ["cached"])
    calls = []
    monkeypatch.setattr(basemap, "fetch_region_manifest",
                        lambda *a, **k: calls.append(a))
    doc = region_manifest(str(tmp_path), "https://example.test/m.json")
    assert [r["id"] for r in doc["regions"]] == ["cached"]
    assert calls == []


def test_live_fetch_wins_and_populates_the_cache(tmp_path, monkeypatch):
    seen = {}

    def fake(url, timeout=MANIFEST_FETCH_TIMEOUT):
        seen["url"], seen["timeout"] = url, timeout
        return _manifest(["live"])

    monkeypatch.setattr(basemap, "fetch_region_manifest", fake)
    doc = region_manifest(str(tmp_path), "https://dev.test/m.json")
    assert [r["id"] for r in doc["regions"]] == ["live"]
    assert seen == {"url": "https://dev.test/m.json",
                    "timeout": MANIFEST_FETCH_TIMEOUT}
    cached = json.loads((tmp_path / FETCHED_MANIFEST).read_text(encoding="utf-8"))
    assert [r["id"] for r in cached["regions"]] == ["live"]


def test_second_request_reuses_the_cache(tmp_path, monkeypatch):
    calls = []

    def fake(url, timeout=MANIFEST_FETCH_TIMEOUT):
        calls.append(url)
        return _manifest(["live"])

    monkeypatch.setattr(basemap, "fetch_region_manifest", fake)
    region_manifest(str(tmp_path), "https://dev.test/m.json")
    region_manifest(str(tmp_path), "https://dev.test/m.json")
    assert calls == ["https://dev.test/m.json"]


def test_old_cache_is_refetched(tmp_path, monkeypatch):
    path = _write_cache(tmp_path, ["stale"])
    old = time.time() - MANIFEST_CACHE_TTL - 1
    os.utime(path, (old, old))

    def fake(url, timeout=MANIFEST_FETCH_TIMEOUT):
        return _manifest(["live"])

    monkeypatch.setattr(basemap, "fetch_region_manifest", fake)
    doc = region_manifest(str(tmp_path), "https://dev.test/m.json")
    assert [r["id"] for r in doc["regions"]] == ["live"]


def test_stale_cache_is_served_when_the_fetch_fails(tmp_path, monkeypatch, caplog):
    path = _write_cache(tmp_path, ["stale"])
    old = time.time() - MANIFEST_CACHE_TTL - 1
    os.utime(path, (old, old))
    monkeypatch.setattr(basemap, "fetch_region_manifest", _offline)
    with caplog.at_level("WARNING", logger="direwolf_dashboard.basemap"):
        doc = region_manifest(str(tmp_path), "https://dev.test/m.json")
    assert [r["id"] for r in doc["regions"]] == ["stale"]
    assert "offline in test" in caplog.text


def test_bundled_copy_is_the_offline_fallback(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(basemap, "fetch_region_manifest", _offline)
    with caplog.at_level("WARNING", logger="direwolf_dashboard.basemap"):
        doc = region_manifest(str(tmp_path), "https://dev.test/m.json")
    assert doc == kit_region_manifest()
    assert "bundled offline copy" in caplog.text
    cached = json.loads((tmp_path / FETCHED_MANIFEST).read_text(encoding="utf-8"))
    assert cached == kit_region_manifest()


def test_fallback_is_cached_so_an_offline_host_retries_once_per_ttl(
    tmp_path, monkeypatch
):
    calls = []

    def fake(url, timeout=MANIFEST_FETCH_TIMEOUT):
        calls.append(url)
        raise BasemapSourceError("offline in test")

    monkeypatch.setattr(basemap, "fetch_region_manifest", fake)
    region_manifest(str(tmp_path), "https://dev.test/m.json")
    region_manifest(str(tmp_path), "https://dev.test/m.json")
    assert calls == ["https://dev.test/m.json"]


def test_world_region_is_the_last_resort(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(basemap, "fetch_region_manifest", _offline)
    monkeypatch.setattr(
        "direwolf_dashboard.basemap.REGION_MANIFEST_FILE", tmp_path / "nope.json"
    )
    with caplog.at_level("WARNING", logger="direwolf_dashboard.basemap"):
        doc = region_manifest(str(tmp_path), "https://dev.test/m.json")
    assert [r["id"] for r in doc["regions"]] == ["world"]
    assert doc["regions"][0]["maxzoom"] == 4
    assert "nope.json" in caplog.text


def test_route_uses_the_configured_manifest_url(monkeypatch):
    seen = {}

    def fake(url, timeout=MANIFEST_FETCH_TIMEOUT):
        seen["url"] = url
        return _manifest(["world"])

    monkeypatch.setattr(basemap, "fetch_region_manifest", fake)
    container = ServiceContainer()
    container.services = SimpleNamespace(
        config=Config(basemap=BasemapConfig(
            manifest_url="http://dev.test/basemap/maps-manifest.json"))
    )
    app = Starlette(routes=[create_basemap_manifest_route(container)])
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get("/basemap/maps-manifest.json")
    assert r.status_code == 200
    assert seen["url"] == "http://dev.test/basemap/maps-manifest.json"
    assert [reg["id"] for reg in r.json()["regions"]] == ["world"]
