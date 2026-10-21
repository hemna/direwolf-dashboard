"""GPX overlay on MapLibre: GeoJSON source + layers, no leaflet-gpx (issue #127)."""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "src" / "direwolf_dashboard" / "static"
JS = (STATIC / "app.js").read_text(encoding="utf-8")
HTML = (STATIC / "index.html").read_text(encoding="utf-8")


def _layer(layer_id: str) -> str:
    m = re.search(r"id: '" + layer_id + r"',(.*?)\n        \}, before\);", JS, re.S)
    assert m, layer_id
    return m.group(1)


def test_leaflet_gpx_plugin_removed():
    assert not (STATIC / "leaflet" / "gpx.js").exists()
    assert "leaflet/gpx.js" not in HTML
    assert "L.GPX" not in JS


def test_gpx_not_guarded_off_for_maplibre():
    assert "TODO(#127)" not in JS
    body = JS[JS.index("function initGpxOverlay()") :]
    assert "if (!mainMapIsMaplibre || !map) return;" in body[:200]
    assert "map.addControl(mlControl(buildGpxControl, 'top-left'))" in body


def test_gpx_parsed_with_domparser_to_geojson():
    fn = JS[JS.index("function parseGpxToGeoJSON") :]
    fn = fn[: fn.index("\n    }\n")]
    for tag in ("'trk'", "'trkseg'", "'trkpt'", "'rte'", "'rtept'", "'wpt'"):
        assert tag in fn, tag
    assert "DOMParser" in fn
    assert "parsererror" in fn
    assert "kind: 'start'" in fn and "kind: 'end'" in fn


def test_gpx_layers_match_leaflet_styles():
    trk = _layer("gpx-track")
    assert "'line-width': 3" in trk and "'line-opacity': 0.8" in trk
    assert "themeColor('--gpx-accent')" in trk
    rte = _layer("gpx-route")
    # Leaflet dashArray '8,6' at weight 2 -> MapLibre dash in line widths.
    assert "'line-dasharray': [4, 3]" in rte and "'line-width': 2" in rte
    pts = _layer("gpx-points")
    for var in ("--gpx-start", "--gpx-end", "--gpx-waypoint"):
        assert f"themeColor('{var}')" in pts
    assert "'circle-stroke-color': '#ffffff'" in pts


def test_gpx_layers_survive_theme_switch():
    assert "map.on('style.load', addGpxLayers)" in JS
    assert "data: (gpxLayer && gpxLayer.fc) || GPX_EMPTY_FC" in JS


def test_gpx_fit_and_clear_use_maplibre():
    assert "fitGpxBounds(gpxLayer.bounds)" in JS
    assert "fitGpxBounds(gpx.bounds)" in JS
    clear = JS[JS.index("function clearGpxOverlay") :][:400]
    assert "setGpxData(GPX_EMPTY_FC)" in clear
