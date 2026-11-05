"""Downloaded kit regions behind the Settings picker (issue #212).

The kit region list lives in Settings (Hemna's call on PR #213): the user picks
which regions this host downloads and caches for offline map use. The download
takes its URL, size and digest from the kit list, verifies both, writes
atomically under ``data_dir`` and is served from there. Never touches the
network: ``_open_region_archive`` is faked or blocked by conftest.
"""

import hashlib
import json
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

import direwolf_dashboard
from direwolf_dashboard.basemap import (
    BasemapSourceError,
    _download_kit_archive,
    download_region,
    downloaded_region_path,
    publish_url,
    region_archive_path,
    region_meta,
    remove_region,
    resolve_region_archive,
)
from direwolf_dashboard.basemap_extract import ExtractError
from direwolf_dashboard.config import BasemapConfig, Config
from direwolf_dashboard.lifecycle import ServiceContainer
from direwolf_dashboard.routers import (
    create_api_routes,
    create_basemap_manifest_route,
    create_basemap_region_archive_route,
)
from tests.pmtiles_fixture import make_planet
import direwolf_dashboard.basemap_auto as basemap_auto

#: The real source chain, for tests that exercise the extractor end to end
#: (the autouse fixture replaces the attribute, not this reference).
_REAL_EXTRACT = basemap_auto.extract_with_fallback

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


@pytest.fixture(autouse=True)
def _offline_extractor(monkeypatch):
    """No test reaches the network for a fallback extraction by default.

    A test that wants the fallback supplies its own ``extract_with_fallback``;
    fixtures run before the test body, so that fake replaces this one.
    """
    def _offline(*_a, **_k):
        raise ExtractError("offline in tests")

    monkeypatch.setattr(
        "direwolf_dashboard.basemap_auto.extract_with_fallback", _offline)


@pytest.fixture
def kit_down(monkeypatch):
    """The publish host is unreachable, as on 2026-11-05 (issue #214)."""
    def fake(url, timeout):
        raise httpx.ConnectError(
            "[SSL: TLSV1_UNRECOGNIZED_NAME] tlsv1 unrecognized name")

    monkeypatch.setattr("direwolf_dashboard.basemap._open_region_archive", fake)


@pytest.fixture
def extraction(monkeypatch):
    """Record the fallback chain and write a valid vector PMTiles to ``tmp``."""
    calls = {}

    def fake(sources, tmp, bbox, maxzoom, use_cli=None):
        calls.update(sources=list(sources), bbox=tuple(bbox), maxzoom=maxzoom)
        make_planet(tmp, maxzoom=1)
        calls["src"] = "https://planet.test/p.pmtiles"
        # the world source could only supply z0-1, so that is the effective cap
        return calls["src"], "python", ["https://cloud.hemna.com/x: down"], 1

    monkeypatch.setattr(
        "direwolf_dashboard.basemap_auto.extract_with_fallback", fake)
    return calls


def _stored(tmp_path, rid="canada"):
    return tmp_path / "basemap" / "regions" / f"{rid}.pmtiles"


def _sidecar(tmp_path, rid="canada"):
    return tmp_path / "basemap" / "regions" / f"{rid}.pmtiles.json"


def test_download_verifies_and_stores_the_archive(tmp_path, served, extraction):
    info = download_region(_region(), str(tmp_path))

    assert _stored(tmp_path).read_bytes() == PAYLOAD
    assert info == {"id": "canada", "size": len(PAYLOAD),
                    "sha256": hashlib.sha256(PAYLOAD).hexdigest(),
                    "source": "https://kit.test/archives/canada.pmtiles",
                    "method": "kit", "maxzoom": 15, "bounds": [-1, -1, 1, 1]}
    assert not (tmp_path / "basemap" / "regions" / "canada.pmtiles.tmp").exists()
    # the pmtiles:// prefix is stripped before the GET
    assert served["url"] == "https://kit.test/archives/canada.pmtiles"
    # a verified kit download never reaches the extractor
    assert extraction == {}
    meta = json.loads(_sidecar(tmp_path).read_text())
    assert (meta["source"], meta["method"]) == (
        "https://kit.test/archives/canada.pmtiles", "kit")


def test_unreachable_kit_extracts_the_region_instead(tmp_path, kit_down, extraction):
    info = download_region(_region(), str(tmp_path), sources=["cloud", "protomaps"])

    assert _stored(tmp_path).stat().st_size > 0
    assert (info["source"], info["method"]) == (
        "https://planet.test/p.pmtiles", "python")
    assert extraction["bbox"] == (-1.0, -1.0, 1.0, 1.0)
    assert extraction["maxzoom"] == 15
    assert extraction["sources"] == ["cloud", "protomaps"]
    meta = json.loads(_sidecar(tmp_path).read_text())
    assert (meta["source"], meta["method"]) == (
        "https://planet.test/p.pmtiles", "python")
    assert not (tmp_path / "basemap" / "regions" / "canada.pmtiles.tmp").exists()


def test_wrong_digest_falls_back_and_never_installs_the_bad_file(
        tmp_path, served, extraction):
    info = download_region(_region(sha256="0" * 64), str(tmp_path))

    stored = _stored(tmp_path).read_bytes()
    assert stored != PAYLOAD  # the mismatched bytes are not what landed
    assert info["method"] == "python"
    assert hashlib.sha256(stored).hexdigest() == info["sha256"]


def test_short_read_falls_back_and_never_installs_the_bad_file(
        tmp_path, served, extraction):
    info = download_region(_region(bytes=len(PAYLOAD) + 1), str(tmp_path))

    assert _stored(tmp_path).read_bytes() != PAYLOAD
    assert info["method"] == "python"


def test_error_names_the_kit_and_every_fallback_source(tmp_path, kit_down, monkeypatch):
    def fake(sources, tmp, bbox, maxzoom, use_cli=None):
        raise ExtractError(
            "all basemap sources failed: "
            "https://cloud.test/planet.pmtiles: ConnectError: down | "
            "https://build.protomaps.com/20261104.pmtiles: HTTP 404")

    monkeypatch.setattr(
        "direwolf_dashboard.basemap_auto.extract_with_fallback", fake)
    with pytest.raises(BasemapSourceError) as excinfo:
        download_region(_region(), str(tmp_path))

    message = str(excinfo.value)
    assert "https://kit.test/archives/canada.pmtiles" in message
    assert "TLSV1_UNRECOGNIZED_NAME" in message
    # NB-3: the kit's prefix is stated once, not echoed by its own cause
    assert message.count("download failed from") == 1
    assert "https://cloud.test/planet.pmtiles" in message
    assert "https://build.protomaps.com/20261104.pmtiles" in message
    assert not _stored(tmp_path).exists()
    assert not (tmp_path / "basemap" / "regions" / "canada.pmtiles.tmp").exists()


@pytest.mark.parametrize("payload", [b"", b"not a pmtiles archive" * 10])
def test_fallback_output_must_be_a_vector_pmtiles(
        tmp_path, kit_down, monkeypatch, payload):
    def fake(sources, tmp, bbox, maxzoom, use_cli=None):
        with open(tmp, "wb") as f:
            f.write(payload)
        return "https://planet.test/p.pmtiles", "python", [], maxzoom

    monkeypatch.setattr(
        "direwolf_dashboard.basemap_auto.extract_with_fallback", fake)
    with pytest.raises(BasemapSourceError, match="fallback extraction failed"):
        download_region(_region(), str(tmp_path))

    assert not _stored(tmp_path).exists()
    assert not (tmp_path / "basemap" / "regions" / "canada.pmtiles.tmp").exists()
    assert not _sidecar(tmp_path).exists()


def test_no_bounds_means_no_fallback(tmp_path, kit_down, extraction):
    region = _region()
    del region["bounds"]
    with pytest.raises(BasemapSourceError, match="no usable bounds/maxzoom"):
        download_region(region, str(tmp_path))

    assert extraction == {}
    assert not _stored(tmp_path).exists()


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


# ---------------------------------------------------------------------------
# #214 review round: the list must describe the file the host serves, and a
# fallback archive must be checked against the region it stands in for
# ---------------------------------------------------------------------------
def _extraction_writing(bounds=None, maxzoom=1, tile_type=None, effective=None):
    """Fake the world extractor, writing an archive with the given header.

    ``effective`` is the zoom the source promised (and so what the extract was
    expected to reach); by default the archive's own max zoom, i.e. a source
    that really only had those tiles.
    """
    def fake(sources, tmp, bbox, maxzoom_arg, use_cli=None):
        make_planet(tmp, maxzoom=maxzoom, bounds=bounds)
        if tile_type is not None:
            raw = bytearray(Path(tmp).read_bytes())
            raw[99] = tile_type  # PMTiles v3 header tile_type byte
            Path(tmp).write_bytes(raw)
        cap = min(maxzoom_arg, maxzoom) if effective is None else effective
        return "https://planet.test/p.pmtiles", "python", [], cap
    return fake


@pytest.fixture
def fallback_extract(monkeypatch):
    """The kit host is down and the extractor writes a zoom 0-1 archive."""
    monkeypatch.setattr(
        "direwolf_dashboard.basemap_auto.extract_with_fallback",
        _extraction_writing(maxzoom=1),
    )


def test_manifest_publishes_the_installed_file_not_the_kit_numbers(
        tmp_path, offline_manifest, kit_down, fallback_extract
):
    """B-1: a fallback install is a different file, so the list must describe
    that file. A client verifies both values and deletes the part on a
    mismatch (basemap-kit docs/CLIENTS.md), so the kit's numbers would throw
    away a working download."""
    container, config = _container(tmp_path)
    region = next(r for r in offline_manifest["regions"] if r["id"] == "canada")
    download_region(region, config.data_dir)
    app = Starlette(routes=[
        create_basemap_manifest_route(container),
        create_basemap_region_archive_route(container),
    ])
    with TestClient(app, raise_server_exceptions=False) as client:
        regions = client.get("/basemap/maps-manifest.json").json()["regions"]
        served = client.get("/basemap/regions/canada.pmtiles").content
    entry = {r["id"]: r for r in regions}["canada"]
    assert entry["bytes"] == len(served)
    assert entry["sha256"] == hashlib.sha256(served).hexdigest()
    assert entry["bytes"] != region["bytes"]
    assert entry["sha256"] != region["sha256"]
    # the kit's declaration stays readable, under its own keys
    assert entry["kit_bytes"] == region["bytes"]
    assert entry["kit_sha256"] == region["sha256"]
    # a zoom 0-1 extract is not the zoom 15 region the entry promises
    assert entry["maxzoom"] == 1
    assert entry["kit_maxzoom"] == 15


def test_fallback_sidecar_records_the_file_not_the_request(
        tmp_path, kit_down, fallback_extract
):
    """B-2: the sidecar and the download response stated the requested maxzoom
    (15) for a zoom 0-1 file."""
    info = download_region(_region(), str(tmp_path))
    meta = region_meta("canada", str(tmp_path))
    assert meta["maxzoom"] == 1
    assert meta["bounds"] == [-180.0, -90.0, 180.0, 90.0]
    assert meta["requested_maxzoom"] == 15
    assert meta["requested_bounds"] == [-1, -1, 1, 1]
    assert (info["maxzoom"], info["bounds"]) == (
        1, [-180.0, -90.0, 180.0, 90.0])


def test_fallback_archive_must_cover_the_region_bounds(
        tmp_path, kit_down, monkeypatch
):
    """An archive missing the area the region promises is not installed."""
    monkeypatch.setattr(
        "direwolf_dashboard.basemap_auto.extract_with_fallback",
        _extraction_writing(bounds=(20, 40, 30, 50)),
    )
    with pytest.raises(BasemapSourceError, match="not the region bounds"):
        download_region(_region(bounds=[-1, -1, 1, 1]), str(tmp_path))
    assert not _stored(tmp_path).exists()
    assert not _sidecar(tmp_path).exists()
    assert not Path(str(_stored(tmp_path)) + ".tmp").exists()


def test_bounds_comparison_keeps_longitude_and_latitude_apart(
        tmp_path, kit_down, monkeypatch
):
    """A region south of the equator and east of Greenwich: the box handed to
    the extractor and the coverage check must both keep the axes apart, or the
    region is cut from the wrong part of the world."""
    seen = {}

    def fake(sources, tmp, bbox, maxzoom, use_cli=None):
        seen["bbox"] = tuple(bbox)
        make_planet(tmp, maxzoom=1, bounds=(125, -25, 145, -5))
        return "https://planet.test/p.pmtiles", "python", [], maxzoom

    monkeypatch.setattr(
        "direwolf_dashboard.basemap_auto.extract_with_fallback", fake)
    region = _region(rid="australia", bounds=[130, -20, 140, -10], maxzoom=1)
    assert download_region(region, str(tmp_path))["method"] == "python"
    # west, south, east, north
    assert seen["bbox"] == (130.0, -20.0, 140.0, -10.0)


@pytest.mark.parametrize("rid", ["sample", "world"])
def test_manifest_describes_the_vendored_file_it_serves(
        tmp_path, rid, monkeypatch
):
    """B-1: the image ships ``sample`` and ``world`` under kit ids whose list
    numbers differ from the bytes on disk. The list must describe the file the
    archive route serves, exactly as it does for a downloaded copy, or a client
    that verifies the digest deletes the region."""
    kit = _region(rid, payload=b"kit's own archive, never served here")
    doc = _manifest(kit)
    monkeypatch.setattr(
        "direwolf_dashboard.basemap.fetch_region_manifest", lambda *a, **k: doc
    )
    container, _config = _container(tmp_path)
    app = Starlette(routes=[
        create_basemap_manifest_route(container),
        create_basemap_region_archive_route(container),
    ])
    with TestClient(app, raise_server_exceptions=False) as client:
        entry = client.get("/basemap/maps-manifest.json").json()["regions"][0]
        served = client.get(f"/basemap/regions/{rid}.pmtiles").content
    assert entry["url"] == f"/basemap/regions/{rid}.pmtiles"
    assert entry["bytes"] == len(served)
    assert entry["sha256"] == hashlib.sha256(served).hexdigest()
    assert entry["bytes"] != kit["bytes"]
    assert entry["kit_bytes"] == kit["bytes"]
    assert entry["kit_sha256"] == kit["sha256"]


def _broken_planet(tmp_path, maxzoom=0, claimed=2):
    """A source whose header promises ``claimed`` but holds only z0-``maxzoom``."""
    src = tmp_path / "broken-planet.pmtiles"
    make_planet(src, maxzoom=maxzoom)
    raw = bytearray(src.read_bytes())
    raw[101] = claimed  # PMTiles v3 header max_zoom byte
    src.write_bytes(raw)
    return str(src)


def test_fallback_archive_missing_the_needed_zoom_is_refused(
        tmp_path, kit_down, monkeypatch
):
    """B-2: the extractor stamps the requested box into its own header, so a
    source that claims z2 but holds only the z0 tile used to install a tiny
    archive covering nothing. The guard must read the tiles present."""
    src = _broken_planet(tmp_path)
    # the pure-Python extractor runs regardless of whether the CLI is installed
    monkeypatch.setattr("direwolf_dashboard.basemap_auto.shutil.which",
                        lambda _name: None)
    monkeypatch.setattr(
        "direwolf_dashboard.basemap_auto.extract_with_fallback", _REAL_EXTRACT)
    with pytest.raises(BasemapSourceError, match="need z2"):
        download_region(_region(bounds=[2, 44, 4, 46], maxzoom=2),
                        str(tmp_path), sources=[src])
    assert not _stored(tmp_path).exists()
    assert not _sidecar(tmp_path).exists()
    assert not Path(str(_stored(tmp_path)) + ".tmp").exists()


def test_fallback_archive_missing_a_needed_tile_is_refused(
        tmp_path, kit_down, monkeypatch
):
    """The archive reaches z2 but lacks the one z2 tile the region needs."""
    src = tmp_path / "partial-planet.pmtiles"
    make_planet(src, maxzoom=2, omit={(2, 2, 1)})  # the tile over 2,44-4,46
    monkeypatch.setattr("direwolf_dashboard.basemap_auto.shutil.which",
                        lambda _name: None)
    monkeypatch.setattr(
        "direwolf_dashboard.basemap_auto.extract_with_fallback", _REAL_EXTRACT)
    with pytest.raises(BasemapSourceError, match="0 of 1 tiles present"):
        download_region(_region(bounds=[2, 44, 4, 46], maxzoom=2),
                        str(tmp_path), sources=[str(src)])
    assert not _stored(tmp_path).exists()


def test_fallback_that_claims_the_zoom_but_lacks_the_tiles_is_refused(
        tmp_path, kit_down, monkeypatch
):
    """A chain result that reports z2 but wrote only z0-1 is refused by the
    installer itself, not only by the source-chain check."""
    monkeypatch.setattr(
        "direwolf_dashboard.basemap_auto.extract_with_fallback",
        _extraction_writing(maxzoom=1, effective=2),
    )
    with pytest.raises(BasemapSourceError, match="need z2"):
        download_region(_region(bounds=[-1, -1, 1, 1], maxzoom=2), str(tmp_path))
    assert not _stored(tmp_path).exists()


def test_a_source_cut_short_is_skipped_for_the_next(tmp_path, monkeypatch):
    """The chain must move on when the first source's extract is short."""
    good = tmp_path / "good-planet.pmtiles"
    make_planet(good, maxzoom=2)
    monkeypatch.setattr("direwolf_dashboard.basemap_auto.shutil.which",
                        lambda _name: None)
    out = tmp_path / "out.pmtiles"
    src, method, tried, effective = _REAL_EXTRACT(
        [_broken_planet(tmp_path), str(good)], str(out), (2, 44, 4, 46), 2)
    assert (src, method, effective) == (str(good), "python", 2)
    assert any("need z2" in t for t in tried)


def test_fallback_output_must_be_vector_tiles(tmp_path, kit_down, monkeypatch):
    """A v3 archive carrying raster tiles parses as PMTiles but cannot render."""
    monkeypatch.setattr(
        "direwolf_dashboard.basemap_auto.extract_with_fallback",
        _extraction_writing(tile_type=2),
    )
    with pytest.raises(BasemapSourceError, match="tile type 2"):
        download_region(_region(), str(tmp_path))
    assert not _stored(tmp_path).exists()


def test_a_kit_download_that_fails_verification_leaves_no_partial_file(
        tmp_path, served
):
    """The kit path's own discard: a wrong digest must not leave ``.tmp``."""
    tmp = str(_stored(tmp_path)) + ".tmp"
    Path(tmp).parent.mkdir(parents=True)
    # the same byte count but the wrong digest is the digest-discard branch
    with pytest.raises(BasemapSourceError,
                       match=r"does not match the kit list.*sha256"):
        _download_kit_archive(
            _region(sha256="0" * 64), "https://kit.test/x.pmtiles", tmp, 5)
    assert not Path(tmp).exists()


class _PartialThenFail(_FakeStream):
    """A stream that writes some bytes, then the connection dies."""

    def iter_bytes(self, chunk_size=65536):
        yield self.payload[:100]
        raise httpx.ReadError("connection reset")


def test_a_kit_stream_that_breaks_midway_leaves_no_partial_file(
        tmp_path, monkeypatch
):
    """The kit path's ``except`` discard: a reset after 100 bytes of a 5 KB
    archive must not leave ``.tmp`` behind for a later run to trip over."""
    monkeypatch.setattr(
        "direwolf_dashboard.basemap._open_region_archive",
        lambda url, timeout: _PartialThenFail(PAYLOAD),
    )
    tmp = str(_stored(tmp_path)) + ".tmp"
    Path(tmp).parent.mkdir(parents=True)
    with pytest.raises(BasemapSourceError, match="download failed from"):
        _download_kit_archive(
            _region(), "https://kit.test/x.pmtiles", tmp, 5)
    assert not Path(tmp).exists()


def test_api_download_uses_the_configured_sources(tmp_path, offline_manifest,
                                                  monkeypatch):
    """The router must hand the operator's source chain to the downloader."""
    container, config = _container(tmp_path)
    config.basemap.sources = ["https://world.test/planet.pmtiles"]
    seen = {}

    def fake(region, data_dir, timeout=0, sources=None):
        seen["sources"] = sources
        return {"id": region["id"], "size": 1, "sha256": "0" * 64,
                "source": "fake", "method": "kit"}

    monkeypatch.setattr("direwolf_dashboard.routers.download_region", fake)
    with TestClient(_app(container), raise_server_exceptions=False) as client:
        assert client.post("/api/basemap/regions/canada/download").status_code == 200
    assert seen["sources"] == ["https://world.test/planet.pmtiles"]
