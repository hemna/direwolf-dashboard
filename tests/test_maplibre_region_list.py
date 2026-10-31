"""Tests for the kit region-list client view (issue #204).

app.js shows the kit's region list (id + name + bytes) fetched same-origin
from the served ``/basemap/maps-manifest.json`` (Task 2 route), cached in
localStorage per calendar day under ``dw-region-list-<YYYY-MM-DD>`` keys with
older-day keys pruned. The list is read-only: no download button or UI. The
manifest's own ``bytes``/``sha256`` fields do NOT match the vendored files on
disk (measured on this branch), so they are display text only; the ACTIVE
region's row shows the real on-disk size from ``region_archive_info`` in
``GET /api/config`` (``config.basemap.region``: ``{name, size}``).

Style mirrors tests/test_maplibre_main_map.py (source asserts on app.js and
index.html; node --check plus small node harnesses for pure helpers).
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

import direwolf_dashboard

STATIC = Path(direwolf_dashboard.__file__).parent / "static"
INDEX = STATIC / "index.html"
APP_JS = STATIC / "app.js"


def _region_section(js):
    """Slice of app.js: the kit region-list section next to the basemap setup."""
    start = js.index("// --- Kit region list (issue #204)")
    end = js.index("/** One GeoJSON source + symbol layers for all stations", start)
    return js[start:end]


def _extract_js_function(src, name):
    """Copy a named function (top-level in the IIFE) out of app.js by regex."""
    m = re.search(r"function " + re.escape(name) + r"\(", src)
    if not m:
        raise AssertionError(f"function {name} not found")
    start = m.start()
    i = src.index("{", start)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError(f"unbalanced braces in {name}")


# ---------------------------------------------------------------------------
# app.js fetches the manifest same-origin; no upstream host string ever
# ---------------------------------------------------------------------------


def test_app_js_fetches_kit_manifest_same_origin():
    js = APP_JS.read_text(encoding="utf-8")
    assert "const REGION_MANIFEST_URL = '/basemap/maps-manifest.json'" in js, \
        "app.js must name the same-origin manifest route"
    assert "await fetch(REGION_MANIFEST_URL)" in js, "app.js must fetch the manifest"
    # Offline DigiPi constraint: the upstream kit host must never be emitted.
    assert "cloud.hemna.com" not in js, "app.js must not reference the upstream host"


# ---------------------------------------------------------------------------
# per-day localStorage cache (dw-region-list-* keys, older days pruned)
# ---------------------------------------------------------------------------


def test_app_js_caches_region_list_per_day():
    sect = _region_section(APP_JS.read_text(encoding="utf-8"))
    assert "const REGION_LIST_KEY_PREFIX = 'dw-region-list-'" in sect, \
        "cache keys must be prefixed dw-region-list-"
    assert "new Date().toISOString().slice(0, 10)" in sect, "cache key day is UTC today"
    assert "localStorage.setItem(regionListDayKey(), JSON.stringify(doc));" in sect, \
        "the fetched manifest must be stored under today's dw-region-list- key"
    assert "const raw = localStorage.getItem(today);" in sect, \
        "the cached list must be read back from localStorage"
    assert ("if (k.indexOf(REGION_LIST_KEY_PREFIX) === 0 && k !== today) "
            "localStorage.removeItem(k);") in sect, "older-day dw-region-list- keys must be pruned"


# ---------------------------------------------------------------------------
# rendered list shows id/name/bytes; active size from /api/config, never
# manifest bytes/sha256 validation; no download UI anywhere
# ---------------------------------------------------------------------------


def test_region_list_renders_id_name_and_bytes():
    sect = _region_section(APP_JS.read_text(encoding="utf-8"))
    assert r"label.textContent = r.id + ' \u2014 ' + (r.name || r.id)" in sect, \
        "rows must show id + name"
    assert "value.textContent = formatBytes(isActive ? active.size : r.bytes);" in sect, \
        "rows must show bytes (manifest value, display text only)"
    assert "function formatBytes(bytes) {" in sect, "app.js must format byte sizes"
    assert "map.addControl(mlControl(function () { return container; }, 'bottom-right'));" in sect, \
        "the region list renders as a read-only map control panel"


def test_active_region_size_from_api_config():
    sect = _region_section(APP_JS.read_text(encoding="utf-8"))
    assert "config.basemap?.region" in sect, \
        "the active region data comes from region_archive_info in GET /api/config"
    assert "active.name === r.id + '.pmtiles'" in sect, "active row matches by region id"
    assert "active.size" in sect, "the active row's size is the on-disk size from the server"
    js = APP_JS.read_text(encoding="utf-8")
    # Manifest bytes/sha256 must stay informational: app.js never sees the digest.
    assert "sha256" not in js, "app.js must never validate against manifest sha256"


def test_region_list_has_no_download_ui():
    sect = _region_section(APP_JS.read_text(encoding="utf-8"))
    for needle in ("download", "addEventListener", "<button", "createElement('button'"):
        assert needle not in sect, f"region list must not add a download UI: {needle}"
    html = INDEX.read_text(encoding="utf-8")
    assert "btn-region" not in html, "index.html must not gain a region download button"


# ---------------------------------------------------------------------------
# wiring: initMap kicks the panel; index.html stays untouched (script order)
# ---------------------------------------------------------------------------


def test_region_panel_wired_into_init_map():
    js = APP_JS.read_text(encoding="utf-8")
    body = js[js.index("async function initMap("):js.index("function absolutizeStyleUrls(")]
    assert "showKitRegionList();" in body, "initMap must kick off the region list"
    assert js.count("showKitRegionList()") == 2, "defined once, called once"


def test_index_html_script_order_unchanged():
    """Task 4 adds no script and no markup to index.html (all JS-built DOM)."""
    html = INDEX.read_text(encoding="utf-8")
    srcs = re.findall(r'<script src="([^"]+)"', html)
    assert srcs == [
        "/static/vendor/maplibre-gl.js",
        "/static/vendor/pmtiles.js",
        "/static/vendor/chart.min.js",
        "/static/app.js",
    ], f"index.html script order changed: {srcs}"
    assert "maps-manifest" not in html, "index.html must not hardcode the manifest list"


# ---------------------------------------------------------------------------
# syntax check: node --check when node is available
# ---------------------------------------------------------------------------


def test_app_js_passes_node_syntax_check():
    if not shutil.which("node"):
        pytest.skip("node not installed")
    result = subprocess.run(["node", "--check", str(APP_JS)], capture_output=True, text=True)
    assert result.returncode == 0, f"node --check failed:\n{result.stderr}"


def test_region_cache_and_format_bytes_in_node():
    """cachedRegionList + formatBytes, verbatim from app.js, under node: today's
    key serves the cached doc, an older dw-region-list- key is pruned, foreign
    keys survive."""
    if not shutil.which("node"):
        pytest.skip("node not installed")
    js = APP_JS.read_text(encoding="utf-8")
    harness = (
        "const REGION_LIST_KEY_PREFIX = 'dw-region-list-';\n"
        "const _store = {};\n"
        "const localStorage = new Proxy(_store, {\n"
        "  get: function (t, k) {\n"
        "    if (k === 'getItem') return function (key) { return t[key]; };\n"
        "    if (k === 'setItem') return function (key, value) { t[key] = value; };\n"
        "    if (k === 'removeItem') return function (key) { delete t[key]; };\n"
        "    return t[k];\n"
        "  },\n"
        "  set: function (t, k, v) { t[k] = v; return true; },\n"
        "  deleteProperty: function (t, k) { delete t[k]; return true; },\n"
        "});\n"
        + _extract_js_function(js, "regionListDayKey")
        + "\n"
        + _extract_js_function(js, "cachedRegionList")
        + "\n"
        + _extract_js_function(js, "formatBytes")
        + "\n"
        "const today = REGION_LIST_KEY_PREFIX + new Date().toISOString().slice(0, 10);\n"
        "localStorage.setItem(today, JSON.stringify({ groups: [], "
        "regions: [{ id: 'sample', name: 'Sample', bytes: 999 }] }));\n"
        "localStorage.setItem(REGION_LIST_KEY_PREFIX + '2025-01-01', 'stale');\n"
        "localStorage.setItem('dw-map-lat', '37.5');\n"
        "const doc = cachedRegionList();\n"
        "console.log('DOC', doc ? doc.regions[0].id : 'MISS');\n"
        "console.log('KEYS', Object.keys(localStorage).sort().join(','));\n"
        "console.log('BYTES', formatBytes(0) + ' ' + formatBytes(1024) + ' '"
        "+ formatBytes(6041449));\n"
    )
    result = subprocess.run(["node", "-e", harness], capture_output=True, text=True)
    assert result.returncode == 0, f"node failed:\n{result.stderr}\n{result.stdout}"
    out = result.stdout
    assert "DOC sample" in out, "today's cached manifest must be returned: " + out
    keys_line = [line for line in out.splitlines() if line.startswith("KEYS ")][0]
    assert "2025-01-01" not in keys_line, "older-day key must be pruned: " + out
    assert "dw-map-lat" in keys_line, "foreign keys must survive pruning: " + out
    assert "dw-region-list-" in keys_line, "today's key must stay: " + out
    assert "BYTES 0 B 1.0 KiB 5.8 MiB" in out, "formatBytes humanizes bytes: " + out
