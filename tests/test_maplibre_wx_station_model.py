"""MapLibre WX station plots — per-station runtime images (issues #152, #156).
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
    """The model draw reads the weather payload and is gated by the toggle."""
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


def test_legend_is_data_driven_from_server():
    """Issue #156 fixes 5 & 6: the legend fetches both colour scales, the
    per-unit lines and the sample from the server (station_model.legend_data)
    so legend and markers always agree."""
    assert "legend-wx-body" in JS
    assert "/weather/stations/legend" in JS
    assert "temp_scale" in JS
    assert "wind_scale" in JS
    assert "lines_imperial" in JS
    assert "lines_metric" in JS


def test_circle_always_filled_and_temp_colored():
    """Issue #156 fixes 4 & 5: the circle is ALWAYS filled using the server
    temperature colour, never an empty circle."""
    assert "model.temp_color || NO_TEMP_GRAY" in JS
    assert "temp_color" in JS


def test_wind_staff_and_calm_ring_colored():
    """Issue #156 fix 6: staff, feathers and calm ring colored by the server
    wind-speed hex."""
    assert "model.wind_color" in JS
    assert "model.calm" in JS
    assert "wind_color || themeColor" in JS


def test_no_data_disc_marker():
    """No temperature -> gray 'N/D' disc instead of an empty circle."""
    assert "N/D" in JS
    assert "NO_TEMP_GRAY" in JS
    assert "'#6b7280'" in JS


def test_units_on_every_value():
    """Issue #156 fix 3: temperature, pressure (hPa) and gust (kt) carry units
    on the model and in the legend."""
    assert "hPa" in JS
    assert "+ 'kt'" in JS
    assert "\\u00B0F" in JS
    assert "\\u00B0C" in JS


def test_callsign_drawn_with_station_model():
    """Issue #156 fix 2: the callsign is drawn with the station model."""
    assert "drawStationModelCanvas" in JS
    assert "drawModelText(ctx, callsign" in JS


def test_off_state_falls_back_to_sprite():
    """The model path is gated by the toggle, so off falls back to the sprite."""
    assert "wxStationEnabled" in JS
    assert "filter-station-model" in JS
