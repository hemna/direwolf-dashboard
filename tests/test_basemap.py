"""Tests for the MapLibre basemap foundation (issue #124).

Vendored surface: static/vendor/{maplibre-gl,pmtiles} + static/basemap/ (kit
pin f1227386d2, sprite/glyph/tile URLs rewritten to /static for LAN-offline).
Content-type expectations are MEASURED on the devbuild venv (Python 3.13,
starlette StaticFiles): .js -> text/javascript, .css -> text/css, .json ->
application/json, .png -> image/png; unknown extensions (.pbf, .pmtiles) fall
back to application/octet-stream.
"""

import hashlib
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles
from starlette.testclient import TestClient

import direwolf_dashboard
from direwolf_dashboard.basemap import (
    DEFAULT_REGION_ARCHIVE,
    DEFAULT_TILE_URL,
    region_url,
)
from direwolf_dashboard.config import Config

STATIC = Path(direwolf_dashboard.__file__).parent / "static"
BASEMAP = STATIC / "basemap"

SAMPLE_REGION_BYTES = 6_596_326
SAMPLE_REGION_SHA256 = "1efc31f57ce799eb0831ab82c3753160d4939f8888f9e601d3a1c261f4f1b450"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _static_app() -> Starlette:
    """Minimal app serving the real static dir (mirrors server.create_app)."""
    return Starlette(routes=[Mount("/static", StaticFiles(directory=str(STATIC)))])


@pytest.fixture
def client():
    with TestClient(_static_app()) as c:
        yield c


# ---------------------------------------------------------------------------
# Vendored JS libraries
# ---------------------------------------------------------------------------


def test_vendored_js_libraries_present():
    for name, min_size in (("maplibre-gl.js", 500_000), ("maplibre-gl.css", 10_000), ("pmtiles.js", 10_000)):
        f = STATIC / "vendor" / name
        assert f.exists(), f"missing vendored {name}"
        assert f.stat().st_size > min_size, f"{name} suspiciously small"


def test_vendored_js_libraries_served_with_content_types(client):
    cases = {
        "/static/vendor/maplibre-gl.js": "text/javascript",
        "/static/vendor/maplibre-gl.css": "text/css",
        "/static/vendor/pmtiles.js": "text/javascript",
    }
    for url, ctype in cases.items():
        r = client.get(url)
        assert r.status_code == 200, f"{url} -> {r.status_code}"
        assert r.headers.get("content-type", "").startswith(ctype), f"{url}: {r.headers.get('content-type')}"
        assert r.content, f"{url} served empty body"


@pytest.mark.parametrize(
    "name",
    ["maplibre-gl.js", "maplibre-gl.css", "pmtiles.js"],
)
def test_vendored_js_has_no_cdn_refs(name):
    """digipi is offline: vendored bundles must not reference CDNs."""
    text = (STATIC / "vendor" / name).read_text(encoding="utf-8")
    for needle in ("unpkg.com", "cdnjs.cloudflare.com", "jsdelivr.net"):
        assert needle not in text, f"{name} references {needle}"


# ---------------------------------------------------------------------------
# Kit surface: byte identity + LAN-offline URL rewrites
# ---------------------------------------------------------------------------


def test_kit_surface_sprites_fonts_byte_identical_to_kit():
    """Every file in the vendored assets.sha256 hashes to the kit pin value."""
    manifest = BASEMAP / "assets.sha256"
    assert manifest.exists()
    entries = [ln.split(None, 1) for ln in manifest.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(entries) >= 780  # 13 sprites + 768 glyphs + OFL.txt
    bad = []
    for digest, relpath in entries:
        p = BASEMAP / relpath
        if not p.exists():
            bad.append(f"{relpath}: MISSING")
        elif _sha256(p) != digest:
            bad.append(f"{relpath}: hash mismatch")
    assert not bad, "byte-identity drift vs kit assets.sha256:\n" + "\n".join(bad[:20])


def test_styles_rewritten_to_local_urls(client):
    """LAN-offline: the styles must reference only /static paths for assets."""
    import json

    for theme in ("light", "dark"):
        text = (BASEMAP / f"style.{theme}.json").read_text(encoding="utf-8")
        assert "cloud.hemna.com" not in text, f"style.{theme}.json still references cloud.hemna.com"
        assert "__MAP_TILES_URL__" not in text, f"style.{theme}.json still has the tile token"

        style = json.loads(text)
        glyphs = style["glyphs"]
        assert glyphs.startswith("/static/basemap/fonts/"), f"{theme} glyphs not local: {glyphs}"
        for s in style["sprite"]:
            assert s["url"].startswith("/static/basemap/sprites/"), f"{theme} sprite not local: {s['url']}"
        assert (
            style["sources"]["protomaps"]["url"] == DEFAULT_TILE_URL
        ), f"{theme} tile URL != config default {DEFAULT_TILE_URL}"

        r = client.get(f"/static/basemap/style.{theme}.json")
        assert r.status_code == 200
        assert r.headers.get("content-type", "").startswith("application/json")


def test_sprites_and_glyphs_served_with_content_types(client):
    cases = {
        "/static/basemap/sprites/light.png": "image/png",
        "/static/basemap/sprites/aprs.png": "image/png",
        "/static/basemap/sprites/aprs.json": "application/json",
        "/static/basemap/fonts/NotoSansRegular/0-255.pbf": "application/octet-stream",
    }
    for url, ctype in cases.items():
        r = client.get(url)
        assert r.status_code == 200, f"{url} -> {r.status_code}"
        assert r.headers.get("content-type", "").startswith(ctype), f"{url}: {r.headers.get('content-type')}"
        assert r.content, f"{url} served empty body"


# ---------------------------------------------------------------------------
# Config seam (region archive path)
# ---------------------------------------------------------------------------


def test_region_archive_config_default():
    assert Config().basemap.region_archive == DEFAULT_REGION_ARCHIVE
    assert region_url() == DEFAULT_TILE_URL


def test_region_url_custom_archive():
    assert region_url("areas/europe.pmtiles") == "/static/areas/europe.pmtiles"


@pytest.mark.parametrize(
    "bad",
    ["../secret.pmtiles", "a/../../secret.pmtiles", "/etc/passwd"],
)
def test_region_url_rejects_escape(bad):
    with pytest.raises(ValueError):
        region_url(bad)


# ---------------------------------------------------------------------------
# Sample region archive + HTTP Range support (the design's builder check)
# ---------------------------------------------------------------------------


def test_sample_region_vendored(client):
    f = BASEMAP / "regions" / "sample.pmtiles"
    assert f.exists(), "sample region archive missing"
    assert f.stat().st_size == SAMPLE_REGION_BYTES, f"size {f.stat().st_size}"
    assert _sha256(f) == SAMPLE_REGION_SHA256, "sample region hash mismatch"

    r = client.get("/static/basemap/regions/sample.pmtiles")
    assert r.status_code == 200
    assert r.headers.get("content-type", "").startswith("application/octet-stream")
    assert r.content == f.read_bytes(), "served bytes differ from on-disk archive"


def test_region_pmtiles_range_support(client):
    """StaticFiles must answer Range with 206 + Content-Range (measured baseline)."""
    f = BASEMAP / "regions" / "sample.pmtiles"
    total = f.stat().st_size
    r = client.get("/static/basemap/regions/sample.pmtiles", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206, f"expected 206 Partial Content, got {r.status_code}"
    assert r.headers.get("content-range") == f"bytes 0-99/{total}"
    assert r.headers.get("accept-ranges") == "bytes"
    assert len(r.content) == 100
    disk = f.read_bytes()
    assert r.content == disk[:100]

    mid = client.get(
        "/static/basemap/regions/sample.pmtiles",
        headers={"Range": f"bytes={total - 50}-{total - 1}"},
    )
    assert mid.status_code == 206
    assert mid.headers.get("content-range") == f"bytes {total - 50}-{total - 1}/{total}"
    assert len(mid.content) == 50
    assert mid.content == disk[-50:]


# ---------------------------------------------------------------------------
# Done-when: #125 (MapLibre main map) now consumes the new assets
# ---------------------------------------------------------------------------


def test_pages_consume_new_assets():
    """The MapLibre main map (issue #125) loads the vendored assets."""
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "maplibre-gl.js" in index
    assert "pmtiles.js" in index
    assert "maplibre-gl.css" in index
    assert "/basemap/style/" in app
    assert "new maplibregl.Map({" in app


# ---------------------------------------------------------------------------
# Issue #154: z11/z12 handoff + same-origin z12+ upstream proxy
# ---------------------------------------------------------------------------


def _split_style(theme, upstream_max=16):
    import json

    from direwolf_dashboard.basemap import render_style

    return json.loads(render_style(theme, upstream_max_zoom=upstream_max))


def test_render_style_handoff_local_through_z15_upstream_from_z12():
    """Local source draws through all of z15 (overzoom); upstream starts at z12."""
    from direwolf_dashboard.basemap import (
        LOCAL_MAXZOOM,
        LOCAL_REGION_URL,
        UPSTREAM_MINZOOM,
        UPSTREAM_PROXY_URL,
        UPSTREAM_SOURCE_ID,
    )

    for theme in ("light", "dark"):
        style = _split_style(theme)
        sources = style["sources"]
        assert sources["protomaps"]["url"] == LOCAL_REGION_URL
        assert sources["protomaps"]["maxzoom"] == LOCAL_MAXZOOM
        assert sources[UPSTREAM_SOURCE_ID]["url"] == UPSTREAM_PROXY_URL
        assert sources[UPSTREAM_SOURCE_ID]["minzoom"] == UPSTREAM_MINZOOM
        assert sources[UPSTREAM_SOURCE_ID]["maxzoom"] == 16

        by_id = {layer["id"]: layer for layer in style["layers"]}
        # layers starting inside the upstream band render upstream under their original id
        assert by_id["roads_taxiway"]["source"] == UPSTREAM_SOURCE_ID
        assert by_id["water_stream"]["source"] == UPSTREAM_SOURCE_ID
        # a plain local layer gets maxzoom LOCAL_MAXZOOM+1 (draws through z15) and a -hi twin
        earth = by_id["earth"]
        assert earth["source"] == "protomaps"
        assert earth["maxzoom"] == LOCAL_MAXZOOM + 1
        assert by_id["earth-hi"]["source"] == UPSTREAM_SOURCE_ID
        assert by_id["earth-hi"]["minzoom"] == UPSTREAM_MINZOOM
        assert "maxzoom" not in by_id["earth-hi"]
        # a layer with its own maxzoom (the z12 early casing) keeps its local copy,
        # now drawn through the whole local range
        assert by_id["roads_major_casing_early"]["maxzoom"] == LOCAL_MAXZOOM + 1


def test_no_blank_integer_zoom_0_to_16_both_themes():
    """Issue #154: every integer zoom 0-16 has a visible basemap layer.

    For each zoom 0..LOCAL_MAXZOOM there must be a local-source layer whose
    [minzoom, maxzoom) window covers it, and for each zoom 12..16 an
    upstream-source layer. Fails on any seam/handoff gap at z11.
    """
    from direwolf_dashboard.basemap import LOCAL_MAXZOOM, UPSTREAM_SOURCE_ID

    for theme in ("light", "dark"):
        style = _split_style(theme)
        local_cover = dict.fromkeys(range(0, LOCAL_MAXZOOM + 1), False)
        upstream_cover = dict.fromkeys(range(LOCAL_MAXZOOM + 1, 17), False)
        for layer in style["layers"]:
            if layer.get("type") == "background" or layer.get("source") is None:
                continue
            src = layer.get("source")
            if src == "protomaps":
                table = local_cover
            elif src == UPSTREAM_SOURCE_ID:
                table = upstream_cover
            else:
                continue
            lo = layer.get("minzoom", 0)
            hi = layer.get("maxzoom", 17)  # MapLibre maxzoom is exclusive
            for z in range(lo, min(hi, 17)):
                if z in table:
                    table[z] = True
        for z in range(0, LOCAL_MAXZOOM + 1):
            assert local_cover[z], f"{theme}: no local basemap layer at zoom {z}"
        for z in range(LOCAL_MAXZOOM + 1, 17):
            assert upstream_cover[z], f"{theme}: no upstream basemap layer at zoom {z}"


def test_render_style_validates_theme():
    import pytest

    from direwolf_dashboard.basemap import render_style

    with pytest.raises(ValueError):
        render_style("sepia")


def test_parse_pmtiles_header_accepts_vector_v3():
    import struct as _struct

    from direwolf_dashboard.basemap import parse_pmtiles_header

    raw = bytearray(127)
    raw[:7] = b"PMTiles"
    raw[7] = 3            # spec version 3
    raw[99] = 1           # MVT / vector
    raw[100] = 0          # min zoom
    raw[101] = 15         # max zoom
    raw[102:118] = _struct.pack(
        "<4i", -789_000_000, 373_500_000, -783_000_000, 379_000_000
    )
    h = parse_pmtiles_header(bytes(raw))
    assert h["min_zoom"] == 0 and h["max_zoom"] == 15
    assert h["min_lon"] == -78.9
    assert h["max_lat"] == 37.9


def test_parse_pmtiles_header_rejects_short_header():
    import pytest

    from direwolf_dashboard.basemap import BasemapSourceError, parse_pmtiles_header

    with pytest.raises(BasemapSourceError):
        parse_pmtiles_header(b"PMTiles")


def test_upstream_single_env_pin_wins_without_probe(monkeypatch):
    from direwolf_dashboard import basemap as basemap_module

    monkeypatch.setenv(basemap_module.UPSTREAM_URL_ENV, "/srv/planet.pmtiles")
    monkeypatch.setattr(basemap_module, "_UPSTREAM_RESOLVE", [])

    def _no_probe(*_a, **_k):
        raise AssertionError("pinned path must not probe")

    url, label = basemap_module.resolve_style_url(sources=[], probe=_no_probe)
    assert url == "/srv/planet.pmtiles"
    assert label == basemap_module.UPSTREAM_URL_ENV


def test_upstream_sources_from_config_cloud_first():
    from direwolf_dashboard.basemap import upstream_sources

    class _Basemap:
        sources = [
            "https://cloud.hemna.com/basemap-kit/archives/planet.pmtiles",
            "protomaps",
        ]

    class _Cfg:
        basemap = _Basemap()

    srcs = upstream_sources(_Cfg())
    assert srcs[0].startswith("https://cloud.hemna.com")
    assert srcs[1] == "protomaps"


def test_resolve_upstream_primary_cloud_wins(monkeypatch):
    from direwolf_dashboard import basemap as basemap_module

    calls = []

    def _probe(src):
        calls.append(src)
        assert src.startswith("https://cloud.hemna.com"), src
        return {"max_zoom": 15}

    monkeypatch.delenv(basemap_module.UPSTREAM_URL_ENV, raising=False)
    monkeypatch.setattr(basemap_module, "_UPSTREAM_RESOLVE", [])
    url, _label, header = basemap_module._resolve_style(
        basemap_module.DEFAULT_UPSTREAM_SOURCES, probe=_probe
    )
    assert url.startswith("https://cloud.hemna.com")
    assert header["max_zoom"] == 15
    assert len(calls) == 1  # cloud wins; protomaps never probed


def test_resolve_upstream_falls_back_to_protomaps(monkeypatch):
    from direwolf_dashboard import basemap as basemap_module

    calls = []

    def _probe(src):
        calls.append(src)
        if src.startswith("https://cloud.hemna.com"):
            raise basemap_module.BasemapSourceError("HTTP 404 not found")
        return {"max_zoom": 15}

    monkeypatch.delenv(basemap_module.UPSTREAM_URL_ENV, raising=False)
    monkeypatch.setattr(basemap_module, "_UPSTREAM_RESOLVE", [])
    url, _label, header = basemap_module._resolve_style(
        basemap_module.DEFAULT_UPSTREAM_SOURCES, probe=_probe
    )
    assert "build.protomaps.com" in url
    assert calls[1] == url
    assert header["max_zoom"] == 15


def test_resolve_upstream_both_down_falls_back_to_sample(monkeypatch):
    from direwolf_dashboard import basemap as basemap_module

    def _probe(src):
        raise basemap_module.BasemapSourceError("HTTP 404 not found")

    monkeypatch.delenv(basemap_module.UPSTREAM_URL_ENV, raising=False)
    monkeypatch.setattr(basemap_module, "_UPSTREAM_RESOLVE", [])
    url, label, header = basemap_module._resolve_style(
        basemap_module.DEFAULT_UPSTREAM_SOURCES, probe=_probe
    )
    assert url == basemap_module.DEFAULT_TILE_ARCHIVE_URL
    assert label == "local-fallback"
    assert header is None


def test_proxy_fetch_range_passes_through_and_caches():
    from direwolf_dashboard import basemap as basemap_module

    class _Resp:
        status_code = 206
        headers = {"Content-Range": "bytes 0-2/1000"}
        content = b"\x01\x02\x03"

    calls = []

    class _Client:
        def get(self, url, headers=None, timeout=None):
            calls.append((url, headers))
            return _Resp()

    data, total = basemap_module.proxy_fetch_range(
        "https://tiles.example/u.pmtiles", 0, 3, client=_Client()
    )
    assert data == b"\x01\x02\x03"
    assert total == 1000
    assert len(calls) == 1

    # second identical range is served from the in-memory cache (no new fetch)
    data2, total2 = basemap_module.proxy_fetch_range(
        "https://tiles.example/u.pmtiles", 0, 3, client=_Client()
    )
    assert data2 == b"\x01\x02\x03" and total2 == 1000
    assert len(calls) == 1
    basemap_module._PROXY_CACHE.clear()


def test_style_route_serves_split_with_source_header():
    from unittest.mock import MagicMock, patch

    from starlette.applications import Starlette
    from starlette.testclient import TestClient

    from direwolf_dashboard.lifecycle import ServiceContainer
    from direwolf_dashboard.routers import create_basemap_style_route

    container = ServiceContainer()
    services = MagicMock()
    services.config.basemap.sources = ["https://e.example/planet.pmtiles"]
    container.services = services
    app = Starlette(routes=[create_basemap_style_route(container)])

    with patch(
        "direwolf_dashboard.routers.resolve_upstream_cached",
        return_value=("https://e.example/planet.pmtiles", "source:test", 15),
    ):
        with TestClient(app, raise_server_exceptions=False) as c:
            r = c.get("/basemap/style/light.json")
    assert r.status_code == 200, r.text
    assert r.headers.get("content-type", "").startswith("application/json")
    assert r.headers.get("x-basemap-source") == "source:test"
    style = r.json()
    assert style["sources"]["protomaps-hi"]["url"] == "/basemap/tiles/upstream"
    assert style["sources"]["protomaps"]["maxzoom"] == 15
    assert style["sources"]["protomaps-hi"]["minzoom"] == 12


def test_style_route_unknown_theme_404():
    from unittest.mock import MagicMock, patch

    from starlette.applications import Starlette
    from starlette.testclient import TestClient

    from direwolf_dashboard.lifecycle import ServiceContainer
    from direwolf_dashboard.routers import create_basemap_style_route

    container = ServiceContainer()
    container.services = MagicMock()
    app = Starlette(routes=[create_basemap_style_route(container)])

    with patch(
        "direwolf_dashboard.routers.resolve_upstream_cached",
        return_value=("x", "source:test", 15),
    ):
        with TestClient(app, raise_server_exceptions=False) as c:
            r = c.get("/basemap/style/sepia.json")
    assert r.status_code == 404


def test_upstream_tile_proxy_route_serves_ranges_from_local_file(tmp_path):
    from unittest.mock import MagicMock, patch

    from starlette.applications import Starlette
    from starlette.testclient import TestClient

    from direwolf_dashboard.lifecycle import ServiceContainer
    from direwolf_dashboard.routers import create_basemap_tiles_route

    archive = tmp_path / "up.pmtiles"
    archive.write_bytes(b"0" * 2000)

    container = ServiceContainer()
    services = MagicMock()
    services.config.basemap.sources = ["https://e.example/planet.pmtiles"]
    container.services = services
    app = Starlette(routes=[create_basemap_tiles_route(container)])

    with patch(
        "direwolf_dashboard.routers.resolve_upstream_cached",
        return_value=(str(archive), "local-file", 15),
    ):
        with TestClient(app, raise_server_exceptions=False) as c:
            r = c.get("/basemap/tiles/upstream", headers={"Range": "bytes=0-99"})
            r2 = c.get("/basemap/tiles/upstream", headers={"Range": "bytes=100-199"})
            r416 = c.get(
                "/basemap/tiles/upstream", headers={"Range": "bytes=3000-3100"}
            )
            r_bad = c.get("/basemap/tiles/upstream", headers={"Range": "bytes=5-1"})
    assert r.status_code == 206
    assert r.headers.get("content-range") == "bytes 0-99/2000"
    assert r.headers.get("accept-ranges") == "bytes"
    assert r.headers.get("x-basemap-source") == "local-file"
    assert len(r.content) == 100
    assert r2.status_code == 206 and r2.content == b"0" * 100
    assert r416.status_code == 416
    assert r_bad.status_code == 416
