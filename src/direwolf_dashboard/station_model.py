"""Pure geometry math for the NWS surface-analysis station plot (issue #152).

No I/O — keeps the feather math, rotation and presence mask pytest-testable
(the repo has no JS test runner). Wind on the model is always in knots; the
stored/app wind stays m/s (see the spec's D1 / D1a).
"""

KT_PER_MPS = 1.94384  # 1 m/s = 1.94384 kt (approx 2)

# NWS barb values.
PENNANT_KT = 50
FULL_KT = 10
HALF_KT = 5


def wind_barb(speed_kt: float) -> list[str]:
    """Feather kinds for a speed in knots, innermost (nearest the circle) first.

    Rounds to the nearest 5 kt (half-up), then splits into pennants (50),
    full barbs (10) and half barbs (5). Returns ``[]`` for a speed that
    rounds to 0 kt (the calm case is the caller's flag).
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
    """Geometry + presence mask for a station's plot, or ``None`` without weather."""
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

    speed_mps = last_weather.get("wind_speed")
    if speed_mps is not None:
        speed_kt = float(speed_mps) * KT_PER_MPS
        feathers = wind_barb(speed_kt)
        model["feathers"] = feathers
        # calm = there IS a wind speed but it rounds to 0 kt (0 kt).
        model["calm"] = feathers == []

    gust_mps = last_weather.get("wind_gust")
    if gust_mps is not None:
        model["gust_kt"] = float(gust_mps) * KT_PER_MPS

    return model
