"""Decode mini-map on MapLibre (issue #128)."""

from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "src" / "direwolf_dashboard" / "static"
JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")


def _fn(name: str) -> str:
    start = JS.index(f"function {name}(")
    return JS[start : JS.index("\n    }\n", start)]


def test_minimap_is_maplibre_on_kit_style():
    fn = _fn("initDecodeMiniMap")
    assert "new maplibregl.Map(" in fn
    assert "fetchStyleForTheme(_currentTheme)" in fn
    assert "L.map(" not in fn
    assert "L.tileLayer" not in fn
    assert "/api/tiles/" not in fn


def test_minimap_markers_and_path_line():
    fn = _fn("initDecodeMiniMap")
    assert "new maplibregl.Marker({ element: createSymbolIconElement(" in fn
    assert "new maplibregl.Popup(" in fn and "escHtml(call)" in fn
    assert "'line-dasharray': [3, 4]" in fn
    assert "themeColor('--segment-path-fg')" in fn


def test_minimap_fit_matches_leaflet_zoom():
    fn = _fn("initDecodeMiniMap")
    assert "maxZoom: toMlZoom(14)" in fn
    assert "zoom: toMlZoom(13)" in fn


def test_minimap_webgl_fallback_and_cleanup():
    fn = _fn("initDecodeMiniMap")
    assert "getContext('webgl2')" in fn
    assert "decode-map-unavailable" in fn
    assert "decodeMiniMap.remove()" in _fn("closeDecodeModal")


def test_icon_markup_shared_with_leaflet_divicon():
    assert "html: createSymbolIconHtml(symbolTable, symbolChar, callsign)" in _fn("createSymbolIcon")
    assert "createSymbolIconHtml(symbolTable, symbolChar, callsign)" in _fn("createSymbolIconElement")


def test_minimap_css_targets_maplibre():
    assert "#decode-mini-map .maplibregl-ctrl-attrib" in CSS
    assert "#decode-mini-map .leaflet-tile" not in CSS
