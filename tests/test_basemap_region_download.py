"""Downloaded kit regions behind the Settings picker (issue #212).

The kit region list lives in Settings (Hemna's call on PR #213): the user picks
which regions this host downloads and caches for offline map use. The download
takes its URL, size and digest from the kit list, verifies both, writes
atomically under ``data_dir`` and is served from there. Never touches the
network: ``_open_region_archive`` is faked or blocked by conftest.
"""

import hashlib
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

import direwolf_dashboard
from direwolf_dashboard.basemap import (
    BasemapSourceError,
    download_region,
    downloaded_region_path,
    publish_url,
    region_archive_path,
    remove_region,
    resolve_region_archive,
)
from direwolf_dashboard.config import BasemapConfig, Config
from direwolf_dashboard.lifecycle import ServiceContainer
from direwolf_dashboard.routers import (
    create_api_routes,
    create_basemap_manifest_route,
    create_basemap_region_archive_route,
)

PAYLOAD = b"PMTiles" + b"x" * 5000


class _FakeStream:
    """Stand-in for ``httpx.stream``'s context manager."""

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        return None

    def iter_bytes(self, chunk_size=65536):
        for i in range(0, len(self.payload), chunk_size):
            yield self.payload[i:i + chunk_size]


@pytest.fixture
def served(monkeypatch):
    """Serve ``PAYLOAD`` from the fake stream and record the URL requested."""
    seen = {}

    def fake(url, timeout):
        seen["url"] = url
        return _FakeStream(PAYLOAD)

    monkeypatch.setattr("direwolf_dashboard.basemap._open_region_archive", fake)
    return seen


def _region(rid="canada", payload=PAYLOAD, **over):
    region = {
        "id": rid,
        "name": rid.title(),
        "group": "north-america",
        "url": f"pmtiles://https://kit.test/archives/{rid}.pmtiles",
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "maxzoom": 15,
        "bounds": [-1, -1, 1, 1],
    }
    region.update(over)
    return region


def _manifest(*regions):
    return {
        "schemaVersion": 1,
        "tileSchema": "v4",
        "buildDate": "2026-11-01T00:00:00Z",
        "groups": [{"id": "g", "name": "Group", "order": 0}],
        "regions": [dict(r, group="g") for r in regions],
    }


# ---------------------------------------------------------------------------
# download_region / remove_region / region_archive_path
# ---------------------------------------------------------------------------


def test_download_verifies_and_stores_the_archive(tmp_path, served):
    info = download_region(_region(), str(tmp_path))

    dest = tmp_path / "basemap" / "regions" / "canada.pmtiles"
    assert dest.read_bytes() == PAYLOAD
    assert info == {"id": "canada", "size": len(PAYLOAD),
                    "sha256": hashlib.sha256(PAYLOAD).hexdigest()}
    assert not (tmp_path / "basemap" / "regions" / "canada.pmtiles.tmp").exists()
    # the pmtiles:// prefix is stripped before the GET
    assert served["url"] == "https://kit.test/archives/canada.pmtiles"


def test_download_rejects_a_digest_mismatch(tmp_path, served):
    with pytest.raises(BasemapSourceError, match="does not match the kit list"):
        download_region(_region(sha256="0" * 64), str(tmp_path))
    assert not (tmp_path / "basemap" / "regions" / "canada.pmtiles").exists()
    assert not (tmp_path / "basemap" / "regions" / "canada.pmtiles.tmp").exists()


def test_download_rejects_a_size_mismatch(tmp_path, served):
    with pytest.raises(BasemapSourceError, match="does not match the kit list"):
        download_region(_region(bytes=len(PAYLOAD) + 1), str(tmp_path))
    assert not (tmp_path / "basemap" / "regions" / "canada.pmtiles").exists()


def test_download_never_opens_a_stream_for_a_bad_id(tmp_path):
    for rid in ("low-fallback", "not a slug", "../escape"):
        with pytest.raises(BasemapSourceError, match="invalid kit region id"):
            download_region(_region(rid=rid), str(tmp_path))


def test_publish_url_requires_a_web_url():
    with pytest.raises(BasemapSourceError, match="no downloadable URL"):
        publish_url({"id": "x", "url": "/basemap/regions/x.pmtiles"})


def test_remove_region_only_deletes_a_downloaded_copy(tmp_path, served):
    download_region(_region(), str(tmp_path))
    assert remove_region("canada", str(tmp_path)) is True
    assert not (tmp_path / "basemap" / "regions" / "canada.pmtiles").exists()
    assert remove_region("canada", str(tmp_path)) is False


def test_region_archive_path_prefers_the_downloaded_copy(tmp_path):
    static = tmp_path / "static"
    (static / "basemap" / "regions").mkdir(parents=True)
    (static / "basemap" / "regions" / "world.pmtiles").write_bytes(b"V")
    data = tmp_path / "data"
    data.mkdir()

    assert region_archive_path("world", str(data), str(static)) == (
        static / "basemap" / "regions" / "world.pmtiles")

    downloaded = data / "basemap" / "regions" / "world.pmtiles"
    downloaded.parent.mkdir(parents=True)
    downloaded.write_bytes(b"D")
    assert region_archive_path("world", str(data), str(static)) == downloaded


def test_resolve_region_archive_serves_a_downloaded_kit_id(tmp_path):
    """A region downloaded in Settings is what `region_archive: <id>` serves."""
    data = tmp_path / "data"
    downloaded = data / "basemap" / "regions" / "canada.pmtiles"
    downloaded.parent.mkdir(parents=True)
    downloaded.write_bytes(b"D")
    static = tmp_path / "static"
    static.mkdir()

    assert resolve_region_archive("canada", str(data), str(static)) == str(downloaded)


# ---------------------------------------------------------------------------
# Served manifest + archive route
# ---------------------------------------------------------------------------


def _container(tmp_path, manifest_url="https://kit.test/m.json"):
    container = ServiceContainer()
    config = Config(basemap=BasemapConfig(manifest_url=manifest_url))
    config.data_dir = str(tmp_path)
    container.services = type("S", (), {"config": config})()
    return container, config


@pytest.fixture
def offline_manifest(monkeypatch):
    """Serve a two-region list (world is vendored, canada is not)."""
    doc = _manifest(_region("world"), _region("canada"))
    monkeypatch.setattr(
        "direwolf_dashboard.basemap.fetch_region_manifest", lambda *a, **k: doc
    )
    return doc


def test_manifest_lists_a_downloaded_region_and_rewrites_its_url(
    tmp_path, offline_manifest, served
):
    container, config = _container(tmp_path)
    app = Starlette(routes=[create_basemap_manifest_route(container)])
    with TestClient(app, raise_server_exceptions=False) as client:
        before = client.get("/basemap/maps-manifest.json")
    assert [r["id"] for r in before.json()["regions"]] == ["world"]  # vendored only

    download_region(_region("canada"), config.data_dir)
    with TestClient(app, raise_server_exceptions=False) as client:
        after = client.get("/basemap/maps-manifest.json")
    regions = {r["id"]: r for r in after.json()["regions"]}
    assert set(regions) == {"world", "canada"}
    assert regions["canada"]["url"] == "/basemap/regions/canada.pmtiles"


def test_archive_route_serves_a_downloaded_region(tmp_path, served):
    container, config = _container(tmp_path)
    download_region(_region("canada"), config.data_dir)
    app = Starlette(routes=[create_basemap_region_archive_route(container)])
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get("/basemap/regions/canada.pmtiles")
    assert r.status_code == 200
    assert r.content == PAYLOAD
    assert r.headers["accept-ranges"] == "bytes"


def test_archive_route_404_for_an_id_that_is_not_downloaded(tmp_path):
    container, _config = _container(tmp_path)
    app = Starlette(routes=[create_basemap_region_archive_route(container)])
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/basemap/regions/canada.pmtiles").status_code == 404


# ---------------------------------------------------------------------------
# /api/basemap/regions — the Settings picker's endpoints
# ---------------------------------------------------------------------------


def _app(container):
    return Starlette(routes=[Mount("/api", routes=create_api_routes(container))])


def test_api_lists_every_region_with_its_download_state(
    tmp_path, offline_manifest, served
):
    container, config = _container(tmp_path)
    download_region(_region("canada"), config.data_dir)
    with TestClient(_app(container), raise_server_exceptions=False) as client:
        r = client.get("/api/basemap/regions")
    assert r.status_code == 200
    regions = {x["id"]: x for x in r.json()["regions"]}
    assert set(regions) == {"world", "canada"}
    assert regions["canada"]["downloaded"] is True
    assert regions["canada"]["downloaded_bytes"] == len(PAYLOAD)
    assert regions["world"]["downloaded"] is False
    assert regions["world"]["bytes"] == len(PAYLOAD)


def test_api_download_then_remove(tmp_path, offline_manifest, served):
    container, config = _container(tmp_path)
    with TestClient(_app(container), raise_server_exceptions=False) as client:
        r = client.post("/api/basemap/regions/canada/download")
        assert r.status_code == 200
        assert r.json()["size"] == len(PAYLOAD)
        dest = downloaded_region_path("canada", config.data_dir)
        assert dest.endswith("canada.pmtiles")

        r = client.delete("/api/basemap/regions/canada")
        assert r.status_code == 200
        assert r.json() == {"removed": True}
        r = client.delete("/api/basemap/regions/canada")
        assert r.json() == {"removed": False}


def test_api_download_404_for_an_unknown_region(tmp_path, offline_manifest):
    container, _config = _container(tmp_path)
    with TestClient(_app(container), raise_server_exceptions=False) as client:
        r = client.post("/api/basemap/regions/atlantis/download")
    assert r.status_code == 404


def test_api_download_requires_the_api_token(tmp_path, offline_manifest, served):
    container, config = _container(tmp_path)
    config.server.api_token = "sekrit"
    with TestClient(_app(container), raise_server_exceptions=False) as client:
        denied = client.post("/api/basemap/regions/canada/download")
        assert denied.status_code == 401
        assert not (tmp_path / "basemap" / "regions" / "canada.pmtiles").exists()

        ok = client.post(
            "/api/basemap/regions/canada/download",
            headers={"X-API-Token": "sekrit"},
        )
    assert ok.status_code == 200


def test_api_download_reports_a_failed_fetch_as_502(tmp_path, offline_manifest):
    container, _config = _container(tmp_path)  # conftest blocks the network
    with TestClient(_app(container), raise_server_exceptions=False) as client:
        r = client.post("/api/basemap/regions/canada/download")
    assert r.status_code == 502
    assert "offline in tests" in r.json()["detail"]


def test_api_remove_rejects_an_internal_region_id(tmp_path, offline_manifest):
    container, _config = _container(tmp_path)
    with TestClient(_app(container), raise_server_exceptions=False) as client:
        r = client.delete("/api/basemap/regions/low-fallback")
    assert r.status_code == 404


def test_api_lists_a_downloaded_region_the_kit_no_longer_publishes(
    tmp_path, offline_manifest
):
    """A retired region's archive stays visible so the user can delete it."""
    container, _config = _container(tmp_path)
    orphan = tmp_path / "basemap" / "regions" / "retired.pmtiles"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"OLD")
    with TestClient(_app(container), raise_server_exceptions=False) as client:
        r = client.get("/api/basemap/regions")
    regions = {x["id"]: x for x in r.json()["regions"]}
    assert regions["retired"]["downloaded"] is True
    assert regions["retired"]["downloaded_bytes"] == 3


def test_the_list_lives_in_settings_not_on_the_map():
    """Hemna's call on PR #213: Settings hosts the list; the map panel stays gone."""
    static = Path(direwolf_dashboard.__file__).parent / "static"
    html = (static / "index.html").read_text(encoding="utf-8")
    assert 'id="basemap-region-list"' in html
    assert "Offline Map Regions" in html
    app_js = (static / "app.js").read_text(encoding="utf-8")
    assert "loadBasemapRegions" in app_js
    assert "showKitRegionList" not in app_js
