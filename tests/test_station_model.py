"""Pure station-model math — barb decomposition, rotation, presence mask,
continuous colour scales and the presence-mask build (issues #152, #156)."""
import pytest

from direwolf_dashboard.station_model import (
    KT_PER_MPS,
    TEMP_COLOR_STOPS,
    WIND_COLOR_STOPS,
    barb_rotation,
    build_station_model,
    legend_data,
    temperature_color,
    wind_barb,
    wind_color,
)

KT = KT_PER_MPS


def test_barb_matrix():
    assert wind_barb(0) == []            # calm handled by caller
    assert wind_barb(3) == ["half"]      # nonzero < 5 -> one half barb
    assert wind_barb(5) == ["half"]      # rounds to 5 -> 1 half
    assert wind_barb(7) == ["half"]      # rounds to 5
    assert wind_barb(25) == ["full", "full", "half"]
    assert wind_barb(50) == ["pennant"]
    assert wind_barb(75) == ["pennant", "full", "full", "half"]
    assert wind_barb(40) == ["full", "full", "full", "full"]


def test_rotation_directions():
    assert barb_rotation(0) == 0
    assert barb_rotation(360) == 0       # 0-vs-360 equivalence
    assert barb_rotation(90) == 90
    assert barb_rotation(180) == 180     # N/E/S/W matrix
    assert barb_rotation(270) == 270
    assert barb_rotation(230) == 230


def test_temperature_color_stops_and_clamps():
    # Exact stop colours at the stop temperatures.
    assert temperature_color(-40) == "#3b0d70"   # coldest purple
    assert temperature_color(60) == "#84cc16"
    assert temperature_color(95) == "#f97316"
    assert temperature_color(110) == "#ef4444"   # hottest red
    # Outside the range clamps to the first/last stop.
    assert temperature_color(-50) == TEMP_COLOR_STOPS[0][1]
    assert temperature_color(200) == TEMP_COLOR_STOPS[-1][1]
    # Continuous interpolation between stops (50F -> 60F midpoint).
    assert temperature_color(55) == "#53c83a"


def test_temperature_color_none():
    assert temperature_color(None) is None
    assert temperature_color("abc") is None


def test_wind_color_continuous():
    assert wind_color(0) == "#22c55e"    # calm = green, exact stop
    assert wind_color(50) == "#ef4444"   # strong = red, exact stop
    assert wind_color(5) == "#53c83a"    # 0 -> 10 kt midpoint shows a blend
    assert wind_color(None) is None


def test_build_station_model_aprs_only():
    m = build_station_model({"temperature": 20.0, "dewpoint": 9.3,
                             "wind_direction": 230, "wind_speed": 20.58, "humidity": 50})
    assert m["rotation_deg"] == 230
    assert m["calm"] is False
    assert m["feathers"] == ["full", "full", "full", "full"]
    assert m["has_temp"] is True and m["has_dewpoint"] is True and m["has_pressure"] is False
    # Issue #156: server-computed colour + converted fields with units.
    assert m["temp_c"] == 20.0
    assert m["temp_f"] == 68.0
    assert m["temp_color"] == "#e2cc15"
    assert m["dewpoint_f"] == 48.7
    assert m["wind_color"] == "#f3801e"   # 20.58 m/s = 40 kt
    assert "gust_kt" not in m


def test_build_station_model_no_temperature_gives_no_data_disc():
    """No temperature -> no temp_color, so the frontend draws the gray
    'N/D' disc (fix 4), never an empty circle."""
    m = build_station_model({"wind_direction": 90, "wind_speed": 3.0})
    assert m["has_temp"] is False
    assert m["temp_c"] is None and m["temp_f"] is None and m["temp_color"] is None
    assert m["wind_color"] is not None


def test_build_station_model_pressure_and_units():
    m = build_station_model({"temperature": 25.0, "pressure": 1012.0})
    assert m["pressure_hpa"] == 1012
    assert m["has_pressure"] is True


def test_build_station_model_metar_style_full():
    m = build_station_model({"temperature": 25.0, "dewpoint": 15.0, "pressure": 1012.0,
                             "wind_direction": 90, "wind_speed": 38.57, "wind_gust": 30.88})
    assert m["calm"] is False
    assert m["feathers"] == ["pennant", "full", "full", "half"]  # ~75 kt
    assert m["gust_kt"] == pytest.approx(30.88 * KT, abs=0.01)
    assert m["has_pressure"] is True
    assert m["pressure_hpa"] == 1012
    assert m["temp_f"] == 77.0


def test_build_station_model_calm_vs_missing_wind():
    calm = build_station_model({"wind_direction": 0, "wind_speed": 0.0})
    assert calm["calm"] is True and calm["feathers"] == []
    assert calm["rotation_deg"] is None   # calm = outer ring only, no shaft (NWS)
    assert calm["wind_color"] == "#22c55e"  # 0 kt = calm green
    none = build_station_model({})
    assert none is None
    nw = build_station_model({"temperature": 10.0})   # no wind at all
    assert nw["calm"] is False and nw["feathers"] == [] and nw["rotation_deg"] is None
    assert "wind_color" not in nw
    assert nw["has_temp"] is True and nw["has_dewpoint"] is False


def test_build_station_model_direction_without_speed():
    """Direction without a speed is a bare shaft — not calm, no feathers."""
    m = build_station_model({"wind_direction": 90})
    assert m["rotation_deg"] == 90 and m["calm"] is False and m["feathers"] == []
    assert "wind_color" not in m


def test_build_station_model_gust_without_wind():
    """A gust without wind is preserved in the model but plots no barb."""
    m = build_station_model({"wind_gust": 10.0, "temperature": 10.0})
    assert m["rotation_deg"] is None and m["calm"] is False
    assert m["gust_kt"] == pytest.approx(10.0 * KT, abs=0.01)


def test_legend_data_has_both_scales_sample_and_units():
    """Issue #156 fixes 5 & 6: the legend payload lists both colour scales,
    per-unit lines and a sample spec rendered by the same code path."""
    d = legend_data()
    assert d["temp_scale"] == [c for _, c in TEMP_COLOR_STOPS]
    assert d["wind_scale"] == [c for _, c in WIND_COLOR_STOPS]
    for units in ("imperial", "metric"):
        lines = d[f"lines_{units}"]
        assert any("hPa" in line for line in lines)
        assert any("kt" in line for line in lines)
    sample = d["sample"]
    assert sample["temp_c"] == 22.2 and sample["temp_color"] is not None
    assert sample["pressure_hpa"] == 1013
    assert sample["wind_color"] is not None
    assert sample["gust_kt"] == pytest.approx(18.0 * KT, abs=0.05)
