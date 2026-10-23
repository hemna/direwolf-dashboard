"""MapLibre WX station plots — per-station runtime images (issue #152).

Static analysis of app.js: the repo has no JS test runner, so frontend
behavior is asserted over the source text (pattern from test_maplibre_main_map.py
and test_maplibre_station_label.py).
"""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "src" / "direwolf_dashboard" / "static"
JS = (STATIC / "app.js").read_text(encoding="utf-8")


def _layer(layer_id: str) -> str:
    m = re.search(r"id: '" + re.escape(layer_id) + r"',(.*?)\n        \}\);", JS, re.S)
    assert m, layer_id
    return m.group(1)


def test_symbol_base_icons_pick_runtime_image_when_present():
    """stations-symbol-base chooses the wx_model runtime image when present,
    otherwise falls back to the APRS sprite."""
    base = _layer("stations-symbol-base")
    assert "wx_model" in base            # icon-image case references the runtime key
    assert "'icon-allow-overlap': true" in base


def test_model_image_registered_on_style_load():
    """Models are re-registered on style.load (theme switches drop runtime images)."""
    assert "reRegisterWxStationImages" in JS
    assert "map.addImage" in JS
    assert "upsertWxStationImage" in JS


def test_model_uses_last_weather_and_toggle():
    """The canvas draw reads the weather payload and is gated by the toggle."""
    assert "last_weather" in JS
    assert "wxStationEnabled" in JS


def test_feature_property_set_when_weather_present():
    """Station features carry a wmodel prop (the runtime image id) for WX stations."""
    assert "props.wx_model" in JS
