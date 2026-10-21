"""Tests for the MapLibre main map (issue #125).

The main map in app.js was swapped from Leaflet (L.map('map') + L.tileLayer of
/api/tiles/{z}/{x}/{y}.png) to MapLibre GL JS using the assets vendored in #124:
static/vendor/maplibre-gl.{js,css}, static/vendor/pmtiles.js and the basemap kit
sprites/glyphs in static/basemap (local, LAN-offline).

The decode mini-map (decodeMiniMap) intentionally stays Leaflet until #128, so
index.html keeps loading the vendored leaflet assets too.
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


# ---------------------------------------------------------------------------
# index.html: loads the vendored maplibre/pmtiles assets, no external URLs
# ---------------------------------------------------------------------------


def test_index_loads_maplibre_vendor_assets():
    html = INDEX.read_text(encoding="utf-8")
    for needle in (
        '<link rel="stylesheet" href="/static/vendor/maplibre-gl.css">',
        '<script src="/static/vendor/maplibre-gl.js"></script>',
        '<script src="/static/vendor/pmtiles.js"></script>',
    ):
        assert needle in html, f"index.html missing {needle}"


def test_index_keeps_leaflet_loaded_for_decode_minimap():
    """The decode mini-map keeps Leaflet until #128, so leaflet.js stays."""
    html = INDEX.read_text(encoding="utf-8")
    assert '<script src="/static/leaflet/leaflet.js"></script>' in html


def test_index_has_no_external_asset_urls():
    """Offline DigiPi constraint: script/link assets must be local /static paths."""
    html = INDEX.read_text(encoding="utf-8")
    for match in re.findall(r'<(?:script|link)[^>]+(?:src|href)="([^"]+)"', html):
        assert not match.startswith(("http://", "https://", "//")), f"index.html loads external asset: {match}"


# ---------------------------------------------------------------------------
# app.js main map uses MapLibre
# ---------------------------------------------------------------------------


def test_app_js_uses_maplibre_for_main_map():
    js = APP_JS.read_text(encoding="utf-8")
    assert "map = new maplibregl.Map({" in js, "main map must be created via maplibregl.Map"
    assert "new maplibregl.NavigationControl" in js


def test_app_js_registers_pmtiles_protocol():
    js = APP_JS.read_text(encoding="utf-8")
    assert "maplibregl.addProtocol('pmtiles', protocol.tile)" in js
    assert "new pmtiles.Protocol()" in js


def test_app_js_uses_aprs_sprite_icon_names():
    """Icon names follow the basemap-kit contract: aprs:aprs-p-XX / -a-XX / -o-YY."""
    js = APP_JS.read_text(encoding="utf-8")
    for needle in ("'aprs:aprs-p-'", "'aprs:aprs-a-'", "'aprs:aprs-o-'"):
        assert needle in js, f"missing {needle} icon name"


def test_app_js_has_webgl2_check():
    js = APP_JS.read_text(encoding="utf-8")
    assert "getContext('webgl2')" in js, "app.js must probe for WebGL2"
    assert "map-webgl-message" in js, "app.js must render the no-WebGL2 message"
    assert "no legacy fallback" in js


def test_main_map_no_longer_uses_leaflet_tile_layer():
    """The main map must not use L.tileLayer('/api/tiles/...').

    The only remaining L.tileLayer is inside the decode mini-map path (Leaflet
    until #128), so every occurrence must reference decodeMiniMap, never the
    main `map` object.
    """
    js = APP_JS.read_text(encoding="utf-8")
    occurrences = [m.start() for m in re.finditer(r"L\.tileLayer\(", js)]
    assert occurrences, "expected the decode mini-map to keep its Leaflet tile layer"
    for pos in occurrences:
        tail = js[pos : pos + 400]
        assert "decodeMiniMap" in tail, (
            "L.tileLayer on the main map must be removed (only the decode "
            "mini-map may use it) at byte offset " + str(pos)
        )


def test_app_js_exposes_aprs_icon_sprite_usage():
    """Station symbol layers must reference the 'aprs' sprite scope."""
    js = APP_JS.read_text(encoding="utf-8")
    assert "sources.protomaps" in js, "app.js must rewrite the protomaps source url"
    assert "pmtiles://" in js, "app.js must prepend the pmtiles:// protocol"


# ---------------------------------------------------------------------------
# Syntax check: node --check when node is available
# ---------------------------------------------------------------------------


def test_app_js_passes_node_syntax_check():
    if not shutil.which("node"):
        pytest.skip("node not installed")
    result = subprocess.run(
        ["node", "--check", str(APP_JS)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"node --check failed:\n{result.stderr}"


def test_map_init_failure_does_not_block_boot():
    """A map/style failure must not stop websocket/filters/settings boot."""
    js = APP_JS.read_text(encoding="utf-8")
    assert "try {\n            await initMap();\n        } catch (e) {" in js


def test_tracks_not_fetched_until_ported():
    """loadTracks must return before hitting /stations/tracks (TODO #126)."""
    js = APP_JS.read_text(encoding="utf-8")
    body = js[js.index("async function loadTracks()") :]
    assert body.index("if (mainMapIsMaplibre) return;") < body.index("/stations/tracks")


def test_popup_open_handler_registered_before_add():
    """maplibre fires 'open' synchronously inside addTo(); bind first."""
    js = APP_JS.read_text(encoding="utf-8")
    body = js[js.index("function openStationPopup(") :]
    assert body.index("activePopup.on('open'") < body.index("activePopup.addTo(map)")


def test_open_popup_content_refreshed_on_update():
    js = APP_JS.read_text(encoding="utf-8")
    assert "activePopup.setDOMContent(stations[cs].popupDiv)" in js
