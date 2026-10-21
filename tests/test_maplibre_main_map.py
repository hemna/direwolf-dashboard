"""Tests for the MapLibre main map (issue #125).

The main map in app.js was swapped from Leaflet (L.map('map') + L.tileLayer of
/api/tiles/{z}/{x}/{y}.png) to MapLibre GL JS using the assets vendored in #124:
static/vendor/maplibre-gl.{js,css}, static/vendor/pmtiles.js and the basemap kit
sprites/glyphs in static/basemap (local, LAN-offline).

Leaflet itself is gone since #146.
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


def test_index_no_longer_loads_leaflet():
    """Leaflet is fully retired (#146); see test_no_leaflet.py."""
    html = INDEX.read_text(encoding="utf-8")
    assert "/static/leaflet/" not in html


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


def test_tile_proxy_preload_ui_removed():
    """The OSM tile-proxy management UI is retired with the proxy (#129).

    The Settings 'Map Tiles' panel, the /tiles/preload fetches and the
    stats-overlay tile cell all serviced the Leaflet tile-cache proxy, which
    is gone once the map is on the LAN-offline kit basemap. They must not
    come back.
    """
    js = APP_JS.read_text(encoding="utf-8")
    html = INDEX.read_text(encoding="utf-8")
    for needle in ("/tiles/preload", "stats.tile_cache", "cfg-tile-mode",
                   "preload-section", "tile-cache-stats", "estimatePreload",
                   "cache_mode", "max_cache_mb"):
        assert needle not in js, f"app.js must not reference tile-proxy UI: {needle}"
    for needle in ("cfg-tile-mode", "preload-section", "tile-cache-stats",
                   "btn-estimate", "btn-download", "btn-cancel-download"):
        assert needle not in html, f"index.html must not reference tile-proxy UI: {needle}"


def test_no_leaflet_tile_layer_left():
    """No map uses L.tileLayer('/api/tiles/...') any more: the main map moved
    in #125 and the decode mini-map in #128."""
    js = APP_JS.read_text(encoding="utf-8")
    assert not re.search(r"L\.tileLayer\(", js)


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


def test_tracks_fetched_by_maplibre():
    """loadTracks must no longer short-circuit before /stations/tracks (#126)."""
    js = APP_JS.read_text(encoding="utf-8")
    body = js[js.index("async function loadTracks()") :]
    assert "/stations/tracks" in body, "loadTracks must still fetch /stations/tracks"
    assert "if (mainMapIsMaplibre) return;" not in body, "loadTracks must not short-circuit on maplibre"


def test_tracks_extended_live_without_guard():
    """updateStationTrack must not be dead-guarded on the maplibre flag (#126)."""
    js = APP_JS.read_text(encoding="utf-8")
    body = js[js.index("function updateStationTrack(") :]
    assert "if (mainMapIsMaplibre) return;" not in body


# ---------------------------------------------------------------------------
# app.js adds the ported GeoJSON sources/layers (issue #126)
# ---------------------------------------------------------------------------


def test_app_js_defines_tracks_source_and_line_layer():
    js = APP_JS.read_text(encoding="utf-8")
    assert "map.addSource('station-tracks'" in js, "must add a 'station-tracks' geojson source"
    assert "id: 'station-tracks-line'" in js, "must add the 'station-tracks-line' line layer"
    assert "'line-color': themeColor('--station-track')" in js, "tracks must reuse the station-track color"


def test_app_js_defines_overlay_source_and_line_layers():
    js = APP_JS.read_text(encoding="utf-8")
    assert "map.addSource('station-overlay'" in js, "must add a 'station-overlay' geojson source"
    assert "id: 'station-overlay-path'" in js, "must add the path line layer"
    assert "id: 'station-overlay-distance'" in js, "must add the distance line layer"
    assert "line-dasharray': [6, 4]" in js, "path lines keep the Leaflet dash 6,4"
    assert "line-dasharray': [4, 8]" in js, "distance lines keep the Leaflet dash 4,8"


def test_app_js_defines_tx_arcs_source_and_line_layer():
    js = APP_JS.read_text(encoding="utf-8")
    assert "map.addSource('tx-arcs'" in js, "must add a 'tx-arcs' geojson source"
    assert "id: 'tx-arcs-line'" in js, "must add the 'tx-arcs-line' routing line layer"


def test_app_js_radds_overlay_sources_on_style_load():
    """All dynamic sources/layers must be re-added on style.load (theme switch)."""
    js = APP_JS.read_text(encoding="utf-8")
    assert "map.on('style.load', addStationLayers)" in js
    assert "map.on('style.load', addOverlaySources)" in js
    assert "function addOverlaySources()" in js


def test_no_remaining_todo_126_guards():
    js = APP_JS.read_text(encoding="utf-8")
    assert "TODO(#126)" not in js, "all issue #126 TODO guards must be ported/removed"


def test_arc_animation_markers_are_maplibre():
    """The arc effect must use maplibre markers, not Leaflet L.marker icons."""
    js = APP_JS.read_text(encoding="utf-8")
    body = js[js.index("function createArcMarker(") :]
    assert "new maplibregl.Marker" in body
    assert "createTransmitArcIcon" not in js, "dead Leaflet arc icon builders must be removed"
    assert "createReceiveArcIcon" not in js, "dead Leaflet arc icon builders must be removed"


def test_popup_open_handler_registered_before_add():
    """maplibre fires 'open' synchronously inside addTo(); bind first."""
    js = APP_JS.read_text(encoding="utf-8")
    body = js[js.index("function openStationPopup(") :]
    assert body.index("activePopup.on('open'") < body.index("activePopup.addTo(map)")


def test_open_popup_content_refreshed_on_update():
    js = APP_JS.read_text(encoding="utf-8")
    assert "activePopup.setDOMContent(stations[cs].popupDiv)" in js


# ---------------------------------------------------------------------------
# issue #135: absolute sprite/glyph URLs + zoom parity (-1 vs Leaflet)
# ---------------------------------------------------------------------------


def test_absolutize_style_urls_helper_present():
    """fetchStyleForTheme must rewrite sprite array entries + glyphs to
    absolute URLs (MapLibre rejects relative sprite URLs, issue #135)."""
    js = APP_JS.read_text(encoding="utf-8")
    assert "function absolutizeStyleUrls(style, base)" in js
    assert "return absolutizeStyleUrls(style, window.location.href);" in js
    assert "Array.isArray(style.sprite)" in js, "must handle sprite as array of {id,url}"
    assert "typeof style.sprite === 'string'" in js, "must handle string sprite"
    assert "style.glyphs = abs(style.glyphs)" in js
    # glyph template placeholders must survive URL parsing unescaped
    assert "replace(/%7Bfontstack%7D/g, '{fontstack}')" in js
    assert "replace(/%7Brange%7D/g, '{range}')" in js


def test_overlay_layer_uses_non_null_filter_and_omits_property():
    """#135: ['has','overlay_hex'] matched explicit nulls; require a real
    value, and stop emitting the property at all when there is no overlay."""
    js = APP_JS.read_text(encoding="utf-8")
    assert "['has', 'overlay_hex']" not in js
    assert "filter: ['!=', ['get', 'overlay_hex'], null]" in js
    assert "overlay_hex: (symbolTable" not in js, "must not set overlay_hex to null"
    assert "props.overlay_hex = hex2(symbolTable);" in js


def test_to_ml_zoom_helper_defined():
    js = APP_JS.read_text(encoding="utf-8")
    assert "function toMlZoom(leafletZoom) {" in js
    assert "Math.max(0, leafletZoom - 1)" in js


def test_to_ml_zoom_applied_on_every_maplibre_zoom_entry_point():
    """MapLibre (512px tiles) zoom == Leaflet zoom - 1, so every configured /
    Leaflet-semantic zoom passed to the maplibre map must go through toMlZoom."""
    js = APP_JS.read_text(encoding="utf-8")
    # Every mapFlyTo/mapSetView line must wrap its zoom in toMlZoom; only the
    # two passthrough helper definitions themselves are exempt.
    for line in js.splitlines():
        if ("mapFlyTo(" in line or "mapSetView(" in line) and "toMlZoom" not in line:
            assert line.strip().startswith("function mapSetView(") or line.strip().startswith(
                "function mapFlyTo("
            ), f"zoom not mapped to toMlZoom: {line.strip()}"
    # initial map view, center FAB, my-location fly-to, auto-follow, focus,
    # saveMyPosition fly-to, and every initMapCenter branch use the helper.
    for needle in (
        # initMap initial view (a #150 URL zoom is already MapLibre zoom)
        "zoom: urlMapView ? urlMapView.zoom : toMlZoom(zoom),",
        "mapFlyTo(pos.lat, pos.lng, toMlZoom(zoom));",  # center FAB + my-location
        "mapFlyTo(packet.latitude, packet.longitude, toMlZoom(zoom));",  # auto-follow
        "mapFlyTo(s.data.latitude, s.data.longitude, Math.max(map.getZoom(), toMlZoom(12)));",  # focus
        "mapSetView(0, 0, toMlZoom(3));",  # cold start
    ):
        assert needle in js, f"missing zoom mapping: {needle}"


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
                return src[start : j + 1]
    raise AssertionError(f"unbalanced braces in {name}")


def test_absolutize_style_urls_and_zoom_mapping_in_node():
    """Run the two helpers, verbatim from app.js, under node (issue #135)."""
    if not shutil.which("node"):
        pytest.skip("node not installed")
    js = APP_JS.read_text(encoding="utf-8")
    absolutize = _extract_js_function(js, "absolutizeStyleUrls")
    to_ml_zoom = _extract_js_function(js, "toMlZoom")

    fstack = "{fontstack}"
    range_ph = "{range}"
    harness = (
        absolutize
        + "\n"
        + to_ml_zoom
        + "\n"
        + "const arr = absolutizeStyleUrls({ sprite: ["
        + "{ id: 'default', url: '/static/basemap/sprites/dark' },"
        + "{ id: 'aprs', url: '/static/basemap/sprites/aprs' }],"
        + 'glyphs: "/static/basemap/fonts/'
        + fstack
        + "/"
        + range_ph
        + '.pbf" },'
        + "'http://localhost:8080/');\n"
        + "console.log('ARR', arr.sprite[0].url, '|', arr.sprite[1].url, '|', arr.glyphs);\n"
        + "const str = absolutizeStyleUrls({ sprite: '/static/basemap/sprites/dark' }, 'http://localhost:8080/');\n"
        + "console.log('STR', str.sprite);\n"
        + "const absInput = absolutizeStyleUrls("
        + "{ sprite: 'http://cdn.example.com/sprites/dark', glyphs: 'http://cdn.example.com/{fontstack}/{range}.pbf' },"
        + "'http://localhost/');\n"
        + "console.log('ABS', absInput.sprite, '|', absInput.glyphs);\n"
        + "console.log('ZOOM', toMlZoom(13), toMlZoom(12), toMlZoom(1), toMlZoom(0));\n"
    )
    result = subprocess.run(["node", "-e", harness], capture_output=True, text=True)
    assert result.returncode == 0, f"node failed:\n{result.stderr}\n{result.stdout}"
    out = result.stdout
    assert "ARR http://localhost:8080/static/basemap/sprites/dark" in out
    assert "http://localhost:8080/static/basemap/sprites/aprs" in out
    assert "http://localhost:8080/static/basemap/fonts/{fontstack}/{range}.pbf" in out, (
        "glyph placeholders must stay literal: " + out
    )
    assert "STR http://localhost:8080/static/basemap/sprites/dark" in out
    assert "ABS http://cdn.example.com/sprites/dark" in out, "absolute input must pass through unchanged"
    assert "http://cdn.example.com/{fontstack}/{range}.pbf" in out
    assert "ZOOM 12 11 0 0" in out, "Leaflet z maps to max(0, z-1): " + out


# Issue #126 review fixes: filtered tracks (2), wipe clears line sources (3),
# route distance labels (4)
# ---------------------------------------------------------------------------


def _function_body(js, name, stop_at):
    """Slice of app.js from `function <name>(` up to the next marker."""
    start = js.index("function " + name + "(")
    end = js.index(stop_at, start)
    return js[start:end]


def test_station_filter_predicate_shared():
    """Markers and track lines must share one callsign/type/txrx predicate."""
    js = APP_JS.read_text(encoding="utf-8")
    assert "function stationVisibleByFilters(" in js
    assert "stationVisibleByFilters(" in _function_body(js, "buildStationFeatureCollection", "// --- Map view")
    assert "stationVisibleByFilters(" in _function_body(
        js, "refreshStationTrackSource", "function refreshOverlaySource"
    )


def test_tracks_source_respects_station_filters():
    """Filtered-out stations' tracks must drop out of the track GeoJSON source."""
    js = APP_JS.read_text(encoding="utf-8")
    body = _function_body(js, "refreshStationTrackSource", "function refreshOverlaySource")
    assert "stationVisibleByFilters(cs, stations[cs])" in body
    body = _function_body(js, "applyFilters", "function applyFilterToRow")
    assert "refreshStationTrackSource()" in body, "applyFilters must refresh the track source"


def test_clear_stations_and_packets_clears_line_sources():
    """Wiping the DB must clear track lines and the overlay/tx-arc sources."""
    js = APP_JS.read_text(encoding="utf-8")
    body = _function_body(js, "clearStationsAndPackets", "// --- Log View Toggle")
    assert "delete stationTracks" in body
    assert "_overlayFC = { type: 'FeatureCollection', features: [] }" in body
    assert "_arcFC = { type: 'FeatureCollection', features: [] }" in body
    for call in ("refreshStationTrackSource()", "refreshOverlaySource()", "refreshArcSource()"):
        assert call in body, f"clearStationsAndPackets must call {call}"


def test_route_distance_labels_ported():
    """showRouteDistances draws per-hop labels with the Leaflet text/units/class."""
    js = APP_JS.read_text(encoding="utf-8")
    body = _function_body(js, "createRoutingPolylines", "// --- Animation Lifecycle")
    assert "showRouteDistances" in body
    assert "route-distance-label" in body
    assert "${distKm.toFixed(2)} km / ${distMiles.toFixed(2)} mi" in body
    assert "new maplibregl.Marker({ element: el, anchor: 'center' })" in body

    tx = _function_body(js, "playTransmitAnimation", "// --- WebSocket")
    assert "removeRouting" in tx, "route lines + labels must be removed together"
    assert "label.remove()" in tx
