"""Wind extraction from aprslib-parse output (issue #152).

aprslib leaves `_` WX packets' wind as top-level course (deg) / speed (km/h)
instead of wind_direction/wind_speed; these tests pin the decode-side mapping.
"""

import pytest

import aprslib
from direwolf_dashboard.decoder import extract_wind, _categorize_sections

RAW = "TEST1>APRS,TCPXX*:!4713.58N/08724.97W_230/040g055t068r000p000P000h50b10051.DsVP"


def test_wind_extraction_from_underscore_packet():
    parsed = aprslib.parse(RAW)
    assert parsed.get("wind_direction") is None
    assert parsed.get("wind_speed") is None         # the gap being fixed
    direction, speed = extract_wind(parsed)
    assert direction == 230
    assert speed == pytest.approx(74.08 * 1000 / 3600, abs=0.01)  # 20.58 m/s


def test_wind_extraction_prefers_weather_dict():
    parsed = {"weather": {"wind_direction": 90, "wind_speed": 5.0}}
    assert extract_wind(parsed) == (90, 5.0)


def test_wind_extraction_none_when_absent():
    assert extract_wind({"weather": {}}) == (None, None)


def test_categorize_sections_includes_wind_for_underscore():
    parsed = aprslib.parse(RAW)
    w = _categorize_sections(parsed)["weather"]
    assert w["wind_direction"] == 230
    assert w["wind_speed"] == pytest.approx(74.08 * 1000 / 3600, abs=0.01)
