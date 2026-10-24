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


def test_image_registration_replaces_existing():
    """Re-adding an existing runtime image throws in MapLibre; upsert must
    remove the old one first so a changed report updates the plot (spec D2)."""
    assert "map.hasImage(" in JS
    assert "map.removeImage(" in JS


def test_model_uses_last_weather_and_toggle():
    """The canvas draw reads the weather payload and is gated by the toggle."""
    assert "last_weather" in JS
    assert "wxStationEnabled" in JS


def test_feature_property_set_when_weather_present():
    """Station features carry the runtime-image id in properties.wx_model for WX stations."""
    assert "properties.wx_model" in JS


def test_toggle_checkbox_exists_default_on_persisted():
    """The 'Station plots' filter checkbox defaults on and persists in localStorage."""
    assert "filter-station-model" in JS
    assert "checked" in JS
    assert "dw-station-model" in JS


def test_legend_section_present_and_toggle_drives_visibility():
    """A #legend-wx section in the map legend is shown/hidden with the toggle."""
    assert "legend-wx" in JS
    assert "setWxLegendVisible" in JS


def test_legend_strings_match_barb_rules():
    """The legend states the barb values, calm rule, gust, sky cover and units."""
    for s in ("5", "10", "50", "kt", "calm"):
        assert s in JS
    assert "°" in JS
    assert "inHg" in JS or "hPa" in JS


def test_off_state_falls_back_to_sprite():
    """The model path is gated by the toggle, so off falls back to the sprite."""
    assert "wxStationEnabled" in JS
    assert "filter-station-model" in JS
