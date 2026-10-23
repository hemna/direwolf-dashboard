"""Pure station-model math — barb decomposition, rotation, presence mask (issue #152)."""

import pytest

from direwolf_dashboard.station_model import (
    KT_PER_MPS,
    barb_rotation,
    build_station_model,
    wind_barb,
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
    assert barb_rotation(230) == 230


def test_build_station_model_aprs_only():
    m = build_station_model({"temperature": 20.0, "dewpoint": 9.3,
                             "wind_direction": 230, "wind_speed": 20.58, "humidity": 50})
    assert m == {"rotation_deg": 230, "calm": False, "feathers": ["full", "full", "full", "full"],
                 "has_temp": True, "has_dewpoint": True, "has_pressure": False}
    assert "gust_kt" not in m


def test_build_station_model_metar_style_full():
    m = build_station_model({"temperature": 25.0, "dewpoint": 15.0, "pressure": 1012.0,
                             "wind_direction": 90, "wind_speed": 38.57, "wind_gust": 30.88,
                             "visibility": 10.0, "sky_cover": 6, "wx_code": "RA"})
    assert m["calm"] is False
    assert m["feathers"] == ["pennant", "full", "full", "half"]  # ~75 kt
    assert m["gust_kt"] == pytest.approx(30.88 * KT, abs=0.01)
    assert m["has_pressure"] is True


def test_build_station_model_calm_vs_missing_wind():
    calm = build_station_model({"wind_direction": 0, "wind_speed": 0.0})
    assert calm["calm"] is True and calm["feathers"] == []
    none = build_station_model({})
    assert none is None
    nw = build_station_model({"temperature": 10.0})   # no wind at all
    assert nw["calm"] is False and nw["feathers"] == [] and nw["rotation_deg"] is None
    assert nw["has_temp"] is True and nw["has_dewpoint"] is False
