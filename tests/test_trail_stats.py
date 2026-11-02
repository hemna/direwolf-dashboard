"""Executable tests for the trail-statistics maths (issue #207, basemap-kit#65).

The shared spec says every map client must reproduce the worked example
exactly, so these tests run the real stats functions from ``app.js`` under
node with the same fixture as the reference client's trail-stats.test.js
(haminfo 565ee8e5), plus the rules the spec calls out: de-duplication,
same-timestamp corrections, bad-fix rejection (>1000 km/h), the gap rule
at ``max(3 x median interval, 300 s)``, fewer-than-two points, and the
metric/imperial display formatting.
"""

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

import direwolf_dashboard

APP_JS = Path(direwolf_dashboard.__file__).parent / "static" / "app.js"

# The spec's six points near Appomattox, Virginia (10:00 to 10:09 local),
# copied from the reference test's WORKED_EXAMPLE fixture. Rows are the
# trail-endpoint shape: [longitude, latitude, timestamp, speed, course].
WORKED_EXAMPLE = [
    [-78.8200, 37.3500, "2026-10-28T10:00:00", 21.0, 50],
    [-78.8170, 37.3520, "2026-10-28T10:01:00", 22.0, 51],
    [-78.8139, 37.3541, "2026-10-28T10:02:00", None, None],
    [-78.8108, 37.3562, "2026-10-28T10:03:00", 21.0, 52],
    [-78.8020, 37.3620, "2026-10-28T10:08:00", 12.0, 50],
    [-78.7990, 37.3640, "2026-10-28T10:09:00", 21.0, 50],
]

REPEATS = [
    [-78.8200, 37.3500, "2026-10-28T10:00:00", 21.0, 50],
    # Same packet heard by a second receiver: one point.
    [-78.8200, 37.3500, "2026-10-28T10:00:00", 21.0, 50],
    # Same timestamp, different position: the later hear is the correction.
    [-78.8170, 37.3520, "2026-10-28T10:01:00", 22.0, 51],
    [-78.8172, 37.3522, "2026-10-28T10:01:00", 22.0, 51],
]

BAD_FIX = [
    [-78.8200, 37.3500, "2026-10-28T10:00:00", None, None],
    [-78.8100, 37.3600, "2026-10-28T10:00:05", None, None],  # ~1020 km/h
    [-78.8099, 37.3601, "2026-10-28T10:01:05", None, None],
]

GAP = [
    [-78.8200, 37.3500, "2026-10-28T10:00:00", None, None],
    [-78.8180, 37.3520, "2026-10-28T10:01:00", None, None],
    [-78.8160, 37.3540, "2026-10-28T10:02:00", None, None],
    [-78.8140, 37.3560, "2026-10-28T10:03:00", None, None],
    [-78.8120, 37.3580, "2026-10-28T10:33:00", None, None],
]

SINGLE = [[-78.82, 37.35, "2026-10-28T10:00:00", None, None]]


def _node(harness: str) -> str:
    result = subprocess.run(["node", "-e", harness], capture_output=True, text=True)
    assert result.returncode == 0, f"node failed:\n{result.stderr}\n{result.stdout}"
    return result.stdout


def _extract(js: str, name: str) -> str:
    """Extract a function including its ``async`` keyword, if present."""
    marker = f"function {name}("
    start = js.index(marker)
    if js[:start].rstrip().endswith("async"):
        start = js.rindex("async", 0, start)
    body_start = js.index("{", start)
    depth = 0
    for i in range(body_start, len(js)):
        if js[i] == "{":
            depth += 1
        elif js[i] == "}":
            depth -= 1
            if depth == 0:
                return js[start : i + 1]
    raise AssertionError(f"unbalanced braces in {name}")


def _const(js: str, name: str) -> str:
    """Extract a top-level constant declaration by name."""
    match = re.search(rf"^([ \t]*(?:const|var|let) {name} = [^;]+;)", js, re.M)
    if not match:
        raise AssertionError(f"missing constant {name}")
    return match.group(1)


EXTRACT_CONSTS = [
    "EARTH_RADIUS_KM",
    "SAME_POSITION_DEG",
    "GAP_MIN_SECONDS",
    "GAP_MEDIAN_FACTOR",
    "BAD_FIX_KMH",
    "KM_PER_MILE",
]

EXTRACT_FUNCS = [
    "toRad",
    "haversineKm",
    "bearingDeg",
    "numberOf",
    "timeMs",
    "normalise",
    "median",
    "mean",
    "compute",
    "isMetric",
    "formatDistance",
    "formatSpeed",
    "formatElapsed",
    "formatInterval",
    "formatHeading",
    "formatClock",
]


def _stats_tail(rows: list, extra: str, options: str = "") -> str:
    """Harness that computes a scenario and prints ``KEY value`` lines."""
    return "\n".join(
        [
            f"const rows = {json.dumps(rows)};",
            f"const r = compute(rows{', ' + options if options else ''});",
            "console.log('POINTS ' + r.points);",
            "console.log('HAS_TRAIL ' + r.hasTrail);",
            "console.log('DIST ' + r.distanceKm);",
            "console.log('ELAPSED ' + r.elapsedSeconds);",
            "console.log('AVG ' + r.averageKph);",
            "console.log('CUR ' + r.currentKph);",
            "console.log('CUR_REP ' + r.currentReported);",
            "console.log('MAX ' + r.maxKph);",
            "console.log('MAX_REP ' + r.maxReported);",
            "console.log('MED ' + r.beacon.medianSeconds);",
            "console.log('MEAN ' + r.beacon.meanSeconds);",
            "console.log('INT ' + r.beacon.intervals);",
            "console.log('HEAD ' + r.headingDeg);",
            "console.log('HEAD_REP ' + r.headingReported);",
            "console.log('BAD ' + r.badFixes);",
            extra,
        ]
    )


def _run(rows: list, extra: str = "", options: str = "") -> dict:
    js = APP_JS.read_text(encoding="utf-8")
    harness = "\n".join(
        [_const(js, name) for name in EXTRACT_CONSTS]
        + [_extract(js, name) for name in EXTRACT_FUNCS]
        + [_stats_tail(rows, extra, options)]
    )
    out = _node(harness)
    return {ln.split(" ", 1)[0]: ln.split(" ", 1)[1] for ln in out.strip().splitlines()}


def _num(line: str) -> float:
    return float(line)


def test_trail_stats_worked_example():
    """The spec's worked example reproduces every number exactly."""
    if not shutil.which("node"):
        pytest.skip("node not installed")

    extra = "\n".join(
        [
            "console.log('FD_M ' + formatDistance(r.distanceKm, 'metric'));",
            "console.log('FE ' + formatElapsed(r.elapsedSeconds));",
            "console.log('FS_AVG ' + formatSpeed(r.averageKph, 'metric'));",
            "console.log('FS_CUR ' + formatSpeed(r.currentKph, 'metric'));",
            "console.log('FS_MAX ' + formatSpeed(r.maxKph, 'metric'));",
            "console.log('FI ' + formatInterval(r.beacon.medianSeconds));",
            "console.log('NORM ' + normalise(rows).length);",
        ]
    )
    line = _run(WORKED_EXAMPLE, extra)

    assert int(line["POINTS"]) == 6
    assert line["HAS_TRAIL"] == "true"
    # Spec display says 2.4 km; the maths is 2.4225 km (reference tolerance).
    assert abs(_num(line["DIST"]) - 2.4225) < 0.0001
    assert int(line["ELAPSED"]) == 540  # 9 min
    assert abs(_num(line["AVG"]) - 16.15) < 0.01
    assert _num(line["CUR"]) == 21.0 and line["CUR_REP"] == "true"
    assert _num(line["MAX"]) == 22.0 and line["MAX_REP"] == "true"
    # Beacon cadence: 60 s median AND mean over the 4 non-gap intervals.
    assert _num(line["MED"]) == 60 and _num(line["MEAN"]) == 60
    assert int(line["INT"]) == 4
    assert _num(line["HEAD"]) == 50 and line["HEAD_REP"] == "true"
    assert int(line["BAD"]) == 0
    assert _num(line["NORM"]) == 6

    assert line["FD_M"] == "2.4 km"
    assert line["FE"] == "9 min"
    assert line["FS_AVG"] == "16 km/h"
    assert line["FS_CUR"] == "21 km/h"
    assert line["FS_MAX"] == "22 km/h"
    assert line["FI"] == "60 s"


def test_repeats_of_one_hear_collapse():
    """Duplicates collapse to one point; a same-time correction wins."""
    if not shutil.which("node"):
        pytest.skip("node not installed")

    extra = "console.log('NORM ' + normalise(rows).length);"
    line = _run(REPEATS, extra)
    assert int(line["POINTS"]) == 2
    assert line["DIST"] != "null" and _num(line["DIST"]) > 0
    assert int(line["NORM"]) == 2


def test_bad_fix_is_counted_and_excluded():
    """A >1000 km/h segment is counted as a bad fix, not in the distance."""
    if not shutil.which("node"):
        pytest.skip("node not installed")

    line = _run(BAD_FIX)
    assert int(line["BAD"]) == 1
    assert _num(line["DIST"]) < 0.05  # only the plausible segment counts
    assert int(line["POINTS"]) == 3


def test_long_interval_is_a_gap_for_the_beacon_sample():
    """The 30-min hole exceeds max(3 x median, 300 s) and leaves the cadence."""
    if not shutil.which("node"):
        pytest.skip("node not installed")

    line = _run(GAP)
    assert int(line["INT"]) == 3  # the gap is not a beacon interval
    assert _num(line["MED"]) == 60 and _num(line["MEAN"]) == 60
    assert _num(line["DIST"]) > 0.6  # a gap still counts toward distance


def test_three_hundred_second_boundary_is_a_gap():
    """The spec calls its 300 s interval a gap: 4 beacon intervals."""
    if not shutil.which("node"):
        pytest.skip("node not installed")

    line = _run(WORKED_EXAMPLE)
    assert int(line["INT"]) == 4
    assert _num(line["MEAN"]) == 60


def test_single_point_is_not_a_trail():
    """Fewer than two points reports no trail and no stats."""
    if not shutil.which("node"):
        pytest.skip("node not installed")

    extra = (
        "console.log('FD ' + formatDistance(r.distanceKm, 'metric'));\n"
        "console.log('FC ' + formatClock(r.lastTime));"
    )
    line = _run(SINGLE, extra)
    assert line["HAS_TRAIL"] == "false"
    assert int(line["POINTS"]) == 1
    assert line["DIST"] == "null"
    assert line["AVG"] == "null"
    assert int(line["INT"]) == 0
    assert line["HEAD"] == "null"
    assert line["FD"] == "-"
    assert line["FC"] == "10:00"


def test_imperial_converts_never_recomputes():
    """Imperial display divides the metric values; it never recomputes."""
    if not shutil.which("node"):
        pytest.skip("node not installed")

    extra = "\n".join(
        [
            "console.log('FD_I ' + formatDistance(r.distanceKm, 'imperial'));",
            "console.log('FS_AVG_I ' + formatSpeed(r.averageKph, 'imperial'));",
            "console.log('FS_MAX_I ' + formatSpeed(r.maxKph, 'imperial'));",
        ]
    )
    line = _run(WORKED_EXAMPLE, extra)
    assert line["FD_I"] == "1.5 mi"
    assert line["FS_AVG_I"] == "10 mph"
    assert line["FS_MAX_I"] == "14 mph"


def test_truncated_trail_is_reported():
    """Truncation metadata is passed through to the panel."""
    if not shutil.which("node"):
        pytest.skip("node not installed")

    extra = "\n".join(
        [
            "console.log('TRUNC ' + r.truncated);",
            "console.log('RETURNED ' + r.returnedPoints);",
            "console.log('WINDOW ' + r.windowHours);",
        ]
    )
    line = _run(WORKED_EXAMPLE, extra, "{truncated: true, returnedPoints: 500, windowHours: 1}")
    assert line["TRUNC"] == "true"
    assert int(line["RETURNED"]) == 500
    assert int(line["WINDOW"]) == 1


def test_elapsed_minutes_that_round_into_an_hour():
    """2h59m59s is 3 h 0 min, not a crash (the const reassignment bug)."""
    if not shutil.which("node"):
        pytest.skip("node not installed")

    extra = "\n".join(
        [
            "console.log('FE_BAND ' + formatElapsed(10799));",
            "console.log('FE_HOUR ' + formatElapsed(10800));",
            "console.log('FE_TWO ' + formatElapsed(7200));",
            "console.log('FE_DAY ' + formatElapsed(86400));",
        ]
    )
    line = _run(WORKED_EXAMPLE, extra)
    assert line["FE_BAND"] == "3 h 0 min"
    assert line["FE_HOUR"] == "3 h 0 min"
    assert line["FE_TWO"] == "2 h 0 min"
    assert line["FE_DAY"] == "24 h 0 min"


def test_epoch_second_timestamps_are_read_as_seconds():
    """This app's track rows carry epoch seconds; the stats must not read them
    as milliseconds (which would put every trail in 1970)."""
    if not shutil.which("node"):
        pytest.skip("node not installed")

    rows = [
        [-78.8200, 37.3500, 1761655200, None, None],
        [-78.7990, 37.3640, 1761655740, None, None],
    ]
    extra = "\n".join(
        [
            "console.log('FE ' + formatElapsed(r.elapsedSeconds));",
            "console.log('FC_START ' + formatClock(r.firstTime));",
            "console.log('FC_END ' + formatClock(r.lastTime));",
        ]
    )
    line = _run(rows, extra)
    assert int(line["ELAPSED"]) == 540
    assert line["FE"] == "9 min"
    # Local clock times for those two epochs, whatever the test host's zone.
    for key, ts in (("FC_START", 1761655200), ("FC_END", 1761655740)):
        assert line[key] == time.strftime("%H:%M", time.localtime(ts))
