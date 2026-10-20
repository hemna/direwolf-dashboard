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
        assert style["sources"]["protomaps"]["url"] == DEFAULT_TILE_URL, (
            f"{theme} tile URL != config default {DEFAULT_TILE_URL}"
        )

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
# Done-when: no page consumes the new assets yet (that's slice 2)
# ---------------------------------------------------------------------------


def test_no_page_consumes_new_assets_yet():
    for rel in ("index.html", "app.js"):
        text = (STATIC / rel).read_text(encoding="utf-8")
        for needle in ("maplibre-gl", "pmtiles", "/static/basemap"):
            assert needle not in text, f"{rel} already references {needle}"
