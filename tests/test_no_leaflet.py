"""Leaflet is fully retired: no assets, no references, no L.* calls (#146)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src" / "direwolf_dashboard" / "static"
JS = (STATIC / "app.js").read_text(encoding="utf-8")
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")


def test_vendored_leaflet_dir_removed():
    assert not (STATIC / "leaflet").exists()


def test_no_leaflet_asset_references():
    for name, text in (("index.html", HTML), ("app.js", JS), ("style.css", CSS)):
        assert "/static/leaflet" not in text, name
        assert "leaflet.js" not in text and "leaflet.css" not in text, name


def test_no_leaflet_api_calls_in_app_js():
    calls = re.findall(r"\bL\.[A-Za-z]+", JS)
    assert calls == [], calls
    assert "removeLayer(" not in JS, "Leaflet map.removeLayer fallback must be gone"
    assert "createSymbolIcon(" not in JS, "Leaflet divIcon builder must be gone"


def test_no_leaflet_css_selectors():
    assert ".leaflet-" not in CSS


def test_no_static_file_under_leaflet_anywhere():
    hits = [p for p in STATIC.rglob("*") if "leaflet" in p.name.lower()]
    assert hits == [], hits


def test_my_position_pin_is_bottom_anchored_maplibre_marker():
    fn = JS[JS.index("function updateMyPositionMarker()"):]
    fn = fn[: fn.index("async function saveMyPosition")]
    assert "new maplibregl.Marker({ element: el, anchor: 'bottom' })" in fn
    assert "el.style.width = '24px'" in fn and "el.style.height = '24px'" in fn
    assert "new maplibregl.Popup({ offset: 24 })" in fn
    assert "myPositionPinMarker.remove();" in fn
