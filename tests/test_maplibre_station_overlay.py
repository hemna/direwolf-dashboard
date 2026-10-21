"""MapLibre station selection overlay parity with Leaflet (issue #134)."""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "src" / "direwolf_dashboard" / "static"
JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")


def _fn(name: str) -> str:
    m = re.search(r"function " + name + r"\(.*?\n    \}\n", JS, re.S)
    assert m, name
    return m.group(0)


def test_hop_dot_layer_matches_leaflet_circle_marker():
    m = re.search(r"id: 'station-overlay-hops',(.*?)\}, 'stations-symbol-base'\);", JS, re.S)
    assert m
    layer = m.group(1)
    assert "type: 'circle'" in layer
    assert "['==', ['get', 'kind'], 'hop']" in layer
    assert "'circle-radius': 5" in layer
    assert "'#ff9500'" in layer


def test_path_and_distance_line_styles_kept():
    assert "'line-dasharray': [6, 4]" in JS
    assert "'line-dasharray': [4, 8]" in JS


def test_hops_emitted_for_labelled_waypoints():
    body = _fn("showStationOverlay")
    assert "kind: 'hop', label: waypoints[i + 1].label" in body
    assert "type: 'Point'" in body


def test_hover_tooltip_is_closebuttonless_popup_escaped():
    body = _fn("onHopEnter")
    assert "closeButton: false" in body
    assert "class=\"path-tooltip\"" in body
    assert "escHtml(" in body
    assert "map.on('mouseenter', 'station-overlay-hops', onHopEnter)" in JS
    assert "map.on('mouseleave', 'station-overlay-hops', onHopLeave)" in JS
    assert ".path-tooltip-popup .maplibregl-popup-content" in CSS


def test_distance_label_midpoint_marker_km_and_mi():
    body = _fn("showStationOverlay")
    assert "distanceLabelHtml(haversineKm(stLat, stLng, myPos.lat, myPos.lng))" in body
    assert "(stLng + myPos.lng) / 2, (stLat + myPos.lat) / 2" in body
    html = _fn("distanceLabelHtml")
    assert 'class="distance-label"' in html
    assert "0.621371" in html


def test_clear_removes_label_and_tooltip():
    body = _fn("clearStationOverlay")
    assert "_distanceLabelMarker.remove()" in body
    assert "_hopTooltip.remove()" in body
