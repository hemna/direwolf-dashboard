"""Wind extraction from aprslib-parse output (issue #152).

aprslib leaves `_` WX packets' wind as top-level course (deg) / speed (km/h)
— it scales the raw position-suffix digits by x1.852 as if they were knots,
but the `_` comment's speed digits are mph per the APRS weather-field spec.
These tests pin the decode-side mapping (recover the digits, mph -> m/s) and
that plain course/speed movement is NOT treated as wind.
"""

import aprslib
import pytest

from direwolf_dashboard.decoder import _categorize_sections, extract_wind

# Canonical `_` WX packet: position, symbol `_`, wind 230 deg / 40 in the
# position suffix, weather comment (gust 55, temp 68 F, ...).
RAW = "TEST1>APRS,TCPXX*:!4713.58N/08724.97W_230/040g055t068r000p000P000h50b10051.DsVP"

# The raw suffix digits are mph (40); aprslib has already x1.852'd them.
EXPECTED_MPS = 40 * 0.44704  # 17.88 m/s ~= 34.7 kt


def test_wind_extraction_from_underscore_packet():
    parsed = aprslib.parse(RAW)
    assert parsed.get("symbol") == "_"           # the gate extract_wind relies on
    assert parsed.get("wind_direction") is None
    assert parsed.get("wind_speed") is None         # the gap being fixed
    direction, speed = extract_wind(parsed)
    assert direction == 230
    assert speed == pytest.approx(EXPECTED_MPS, abs=0.01)


def test_wind_extraction_prefers_weather_dict():
    parsed = {"weather": {"wind_direction": 90, "wind_speed": 5.0}}
    assert extract_wind(parsed) == (90, 5.0)


def test_wind_extraction_none_when_absent():
    assert extract_wind({"weather": {}}) == (None, None)


def test_plain_movement_is_not_wind():
    """Course/speed on a non-`_` packet is course-over-ground, not wind."""
    parsed = aprslib.parse("TEST2>APRS:!4713.58N/08724.97W>135/040")
    assert parsed.get("symbol") != "_"
    assert extract_wind(parsed) == (None, None)
    assert "weather" not in _categorize_sections(parsed)


def test_categorize_sections_includes_wind_for_underscore():
    parsed = aprslib.parse(RAW)
    w = _categorize_sections(parsed)["weather"]
    assert w["wind_direction"] == 230
    assert w["wind_speed"] == pytest.approx(EXPECTED_MPS, abs=0.01)
