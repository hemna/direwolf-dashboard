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


def test_tracks_fetched_by_maplibre():
    """loadTracks must no longer short-circuit before /stations/tracks (#126)."""
    js = APP_JS.read_text(encoding="utf-8")
    body = js[js.index("async function loadTracks()"):]
    assert "/stations/tracks" in body, "loadTracks must still fetch /stations/tracks"
    assert "if (mainMapIsMaplibre) return;" not in body, "loadTracks must not short-circuit on maplibre"


def test_tracks_extended_live_without_guard():
    """updateStationTrack must not be dead-guarded on the maplibre flag (#126)."""
    js = APP_JS.read_text(encoding="utf-8")
    body = js[js.index("function updateStationTrack("):]
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
    body = js[js.index("function createArcMarker("):]
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
    assert "stationVisibleByFilters(" in _function_body(js, "refreshStationTrackSource", "function refreshOverlaySource")


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
    body = _function_body(js, "clearStationsAndPackets", "// --- Tile Preload")
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
