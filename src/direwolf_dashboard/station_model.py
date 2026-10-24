"""Pure geometry math for the NWS surface-analysis station plot (issues #152, #156).
No I/O — keeps the feather math, rotation, presence mask and colour math
pytest-testable (the repo has no JS test runner). Wind on the model is always
in knots; the stored/app wind stays m/s (see the spec's D1 / D1a).

Issue #156 fixes (mirroring the haminfo #273 implementation, the reference):
- The station circle is ALWAYS filled and colored by temperature with the
  rounded value and unit inside; no temperature -> a clear gray "N/D" disc,
  never an empty circle.
- Circle fill follows a continuous warmth scale (purple/blue cold ->
  green/yellow mild -> orange/red hot); the wind barb staff, feathers and
  calm ring are colored by wind speed on a continuous calm-to-strong scale.
  Both scales are computed here in Python (single source of truth) and
  shipped to the frontend as hex colours; the legend shows both.
- Every value carries a unit: temperature degF/degC, pressure hPa,
  wind/gust kt. Each field has its own position so nothing overlaps.
"""
import math

KT_PER_MPS = 1.94384  # 1 m/s = 1.94384 kt (approx 2)
# NWS barb values.
PENNANT_KT = 50
FULL_KT = 10
HALF_KT = 5
#: Gray "no data" disc when a station sends no temperature (issue #156 fix 4) —
#: never an empty circle. Same value the haminfo #273 reference uses.
NO_TEMP_GRAY = "#6b7280"
# Continuous colour scales (issue #156, copied from the haminfo #273 fix so all
# platforms render the same colours). Temperatures are keyed on Fahrenheit so
# colour is unit-independent; wind on knots.
# (temp_f, hex): cold purple/blue -> mild green/yellow -> hot orange/red.
TEMP_COLOR_STOPS = (
    (-40.0, "#3b0d70"),
    (0.0, "#4c1d95"),
    (20.0, "#6d28d9"),
    (35.0, "#3b82f6"),
    (50.0, "#22c55e"),
    (60.0, "#84cc16"),
    (70.0, "#facc15"),
    (82.0, "#f59e0b"),
    (95.0, "#f97316"),
    (110.0, "#ef4444"),
)
# (knots, hex): calm green -> moderate yellow -> strong red.
WIND_COLOR_STOPS = (
    (0.0, "#22c55e"),
    (10.0, "#84cc16"),
    (20.0, "#facc15"),
    (35.0, "#f59e0b"),
    (50.0, "#ef4444"),
)


def _rgb(hex_color):
    """(r, g, b) ints from a #rrggbb string; (0,0,0) for anything else."""
    if not isinstance(hex_color, str) or not hex_color.startswith("#") or len(hex_color) != 7:
        return (0, 0, 0)
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))


def _lerp_hex(value, stops):
    """Continuous linear RGB interpolation across (value, hex) stops.
    Clamps to the first/last stop outside the range; None for None.
    """
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v <= stops[0][0]:
        return stops[0][1]
    if v >= stops[-1][0]:
        return stops[-1][1]
    # strict=False: the two sequences differ in length by design.
    for (v0, c0), (v1, c1) in zip(stops, stops[1:], strict=False):
        if v0 <= v <= v1:
            if v1 == v0:
                return c0
            f = (v - v0) / (v1 - v0)
            r0, g0, b0 = _rgb(c0)
            r1, g1, b1 = _rgb(c1)
            r = round(r0 + (r1 - r0) * f)
            g = round(g0 + (g1 - g0) * f)
            b = round(b0 + (b1 - b0) * f)
            return f"#{r:02x}{g:02x}{b:02x}"
    return stops[-1][1]


def temperature_color(temp_f):
    """Continuous warmth-scale hex for a Fahrenheit temperature; None when no
    temperature is reported (the frontend draws the gray "N/D" disc, never an
    empty circle)."""
    return _lerp_hex(temp_f, TEMP_COLOR_STOPS)


def wind_color(knots):
    """Continuous calm-to-strong hex for a wind speed in knots; None when no
    wind speed is reported."""
    return _lerp_hex(knots, WIND_COLOR_STOPS)


def _num(value, digits: int = 1):
    """Round a numeric value, returning None for None/NaN/inf/non-numeric."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, digits)


def wind_barb(speed_kt: float) -> list[str]:
    """Feather kinds for a speed in knots, ordered tip-first.
    Index 0 (pennant) is outermost at the shaft tip; the last element sits
    nearest the station circle — the NWS order. Rounds to the nearest 5 kt
    (half-up), then splits into pennants (50), full barbs (10) and half
    barbs (5). Returns ``[]`` for a speed that rounds to 0 kt (the calm
    case is the caller's flag).
    """
    kt = int(speed_kt / HALF_KT + 0.5) * HALF_KT
    if kt == 0:
        return []
    feathers: list[str] = ["pennant"] * (kt // PENNANT_KT)
    remainder = kt % PENNANT_KT
    feathers += ["full"] * (remainder // FULL_KT)
    if remainder % FULL_KT >= HALF_KT:
        feathers.append("half")
    return feathers


def barb_rotation(direction_deg: float) -> float:
    """Compass degrees the shaft points (toward where the wind comes FROM).
    Identity of the APRS meteorological direction, normalized to ``[0, 360)``
    so 0 and 360 both plot straight up.
    """
    return direction_deg % 360


def build_station_model(last_weather: dict | None) -> dict | None:
    """Geometry + presence mask for a station's plot, or ``None`` without weather.
    Input is the stored APRS weather (temperature/dewpoint degC, pressure hPa,
    wind m/s, gust m/s). Adds the server-computed colour hexes (temp_color,
    wind_color) and converted fields (temp_f, dewpoint_f, pressure_hpa,
    gust_kt) so the frontend renders hexes verbatim and keeps no math.
    """
    if not last_weather:
        return None

    def has(key: str) -> bool:
        return last_weather.get(key) is not None

    direction = last_weather.get("wind_direction")
    model: dict = {
        "rotation_deg": barb_rotation(direction) if direction is not None else None,
        "calm": False,
        "feathers": [],
        "has_temp": has("temperature"),
        "has_dewpoint": has("dewpoint"),
        "has_pressure": has("pressure"),
    }
    # Temperature (stored degC). Colour is computed from degF so it is
    # unit-independent; the frontend shows the value in the app's unit.
    temp_c = _num(last_weather.get("temperature"))
    temp_f = round(temp_c * 9.0 / 5.0 + 32.0, 1) if temp_c is not None else None
    model["temp_c"] = temp_c
    model["temp_f"] = temp_f
    model["temp_color"] = temperature_color(temp_f)
    dew_c = _num(last_weather.get("dewpoint"))
    model["dewpoint_c"] = dew_c
    model["dewpoint_f"] = round(dew_c * 9.0 / 5.0 + 32.0, 1) if dew_c is not None else None
    pressure = last_weather.get("pressure")
    if pressure is not None:
        model["pressure_hpa"] = int(float(pressure))

    speed_mps = last_weather.get("wind_speed")
    if speed_mps is not None:
        speed_kt = float(speed_mps) * KT_PER_MPS
        feathers = wind_barb(speed_kt)
        model["feathers"] = feathers
        # calm = there IS a wind speed but it rounds to 0 kt (0 kt).
        model["calm"] = feathers == []
        if model["calm"]:
            # NWS calm = outer ring only; the shaft is not drawn.
            model["rotation_deg"] = None
        # Continuous wind colour from the un-rounded knots (rounding to 5 kt
        # would step the calm-to-strong scale in bands, not continuous).
        model["wind_color"] = wind_color(speed_kt)
    gust_mps = last_weather.get("wind_gust")
    if gust_mps is not None:
        model["gust_kt"] = float(gust_mps) * KT_PER_MPS
    return model


def legend_lines(units: str) -> list:
    """Legend text lines for one unit system ('imperial' or 'metric') —
    every shown value carries its unit and both colour scales are named.
    """
    t_unit = "\u00B0F" if units == "imperial" else "\u00B0C"
    return [
        "Wind barb: the staff points toward where the wind comes FROM",
        "Half barb = 5 kt, full barb = 10 kt, pennant = 50 kt",
        "Calm (0 kt) = ring around the station circle",
        f"Circle ALWAYS filled by temperature ({t_unit}); no data = gray disc",
        "Circle color scale (degF): purple/blue (cold) -> green/yellow (mild) -> orange/red (hot)",
        "Wind barb color scale (kt): green (calm) -> yellow -> red (strong)",
        f"Temperature {t_unit} inside the circle, dewpoint {t_unit} bottom-left",
        "Pressure top-right in hPa; gust G<kt> near the barb tip",
        f"Units: temperature {t_unit}, pressure hPa, wind/gust kt",
    ]


def legend_data() -> dict:
    """Server-provided legend payload: lines per unit system, both colour
    scales as hex lists and a fixed sample spec — so the legend example and
    the markers are rendered by the same frontend code path (issue #156 fixes
    5 & 6)."""
    return {
        "lines_imperial": legend_lines("imperial"),
        "lines_metric": legend_lines("metric"),
        "temp_scale": [c for _, c in TEMP_COLOR_STOPS],
        "wind_scale": [c for _, c in WIND_COLOR_STOPS],
        "sample": build_station_model(
            {
                "temperature": 22.2,   # degC
                "dewpoint": 12.0,
                "humidity": 52,
                "wind_speed": 20.6,    # m/s -> ~40 kt
                "wind_direction": 315.0,
                "wind_gust": 18.0,     # m/s -> ~35 kt
                "pressure": 1013.0,
            }
        ),
    }
