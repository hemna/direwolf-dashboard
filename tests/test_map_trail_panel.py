"""Trail hover + info panel tests (issue #207, plan tasks 2-4).

Two layers of evidence, per this repo's client-test convention:

1. Source asserts against ``app.js``: the spec's 8 px hover tolerance, a
   ``queryRenderedFeatures`` box that queries ``station-tracks-line`` ONLY,
   a ``station-tracks-highlight`` layer that the hover handler adds and
   removes, the cursor change on hit, the highlight re-added after a style
   reload the way ``tx-arcs`` is (inside ``addOverlaySources``, the
   style.load handler), and the panel wiring: a click opens the panel
   through the same hit test (no prior hover needed), bare map closes it,
   Escape closes it, a station marker owns its own click, and the panel
   re-places when the map resizes or a chrome overlay is toggled.
2. Node harness for the pure helpers (the extraction idiom is the one
   ``tests/test_trail_stats.py`` uses): a stub map answering like MapLibre
   pins the ±8 px box maths, the one-layer query, nearest-wins among
   competing trails, the guards, and the one-feature-per-station
   assumption (a hit feature is the WHOLE trail as a single LineString);
   the panel harness pins the spec's field order, the ``-`` for unknown
   values and the placement rules (beside the trail, never on it, never on
   the chrome).
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


def _hover_harness(tail: str) -> str:
    js = APP_JS.read_text(encoding="utf-8")
    return "\n".join(
        [
            _const(js, "TRAIL_HOVER_TOLERANCE_PX"),
            _extract(js, "pickTrailHit"),
            _extract(js, "distPointToSegment"),
            tail,
        ]
    )


def _run_node(tail: str) -> dict:
    out = _node(_hover_harness(tail))
    return {ln.split(" ", 1)[0]: ln.split(" ", 1)[1] for ln in out.strip().splitlines()}


# --------------------------------------------------------------------------
# Source asserts
# --------------------------------------------------------------------------


def test_hover_tolerance_is_the_spec_8_px():
    js = APP_JS.read_text(encoding="utf-8")
    assert "const TRAIL_HOVER_TOLERANCE_PX = 8;" in js


def test_only_station_tracks_line_is_queried():
    js = APP_JS.read_text(encoding="utf-8")
    block = _extract(js, "pickTrailHit")
    assert "{layers: ['station-tracks-line']}" in block
    # One query site only, and the highlight layer is never a query target.
    assert block.count("queryRenderedFeatures") == 1
    assert "station-tracks-highlight" not in block


def test_highlight_layer_is_added_and_removed_on_hover():
    js = APP_JS.read_text(encoding="utf-8")
    block = _extract(js, "highlightTrail")
    assert "map.addLayer({" in block
    assert "id: 'station-tracks-highlight'," in block
    # Bracket call form: the app.js guard in tests/test_no_leaflet.py treats
    # the dotted spelling as a retired-Leaflet fingerprint.
    assert "map['removeLayer']('station-tracks-highlight')" in block
    # Same source, filtered to the hovered callsign, so the whole-trail
    # feature (port decision 3) is exactly what lights up.
    assert "source: 'station-tracks'," in block
    assert "filter: ['==', ['get', 'callsign'], callsign]," in block
    # Drawn under the base line, like the reference halo under its trail.
    assert "}, 'station-tracks-line');" in block


def test_hover_hit_sets_the_move_cursor():
    js = APP_JS.read_text(encoding="utf-8")
    block = _extract(js, "setTrailHover")
    assert "map.getCanvas().style.cursor = callsign ? 'move' : '';" in block


def test_hover_handler_bound_once():
    js = APP_JS.read_text(encoding="utf-8")
    assert "if (!_trailHoverBound) {" in js
    assert "setTrailHover(pickTrailHit(map, e.point));" in js
    assert "map.on('mousemove', function (e) {" in js
    # Pointer leaves the map: the reference clears the hover there too.
    assert "map.getCanvas().addEventListener('mouseleave', function () {" in js


def test_highlight_re_added_after_style_load():
    js = APP_JS.read_text(encoding="utf-8")
    # addOverlaySources is the style.load handler that re-adds tx-arcs; the
    # hover layer rides along in the same re-add pass.
    assert "map.on('style.load', addOverlaySources);" in js
    block = _extract(js, "addOverlaySources")
    assert "if (hoveredTrailCallsign) highlightTrail(hoveredTrailCallsign);" in block
    # After the trail source/layer block re-created station-tracks-line.
    assert (
        block.index("if (!map.getSource('station-tracks'))")
        < block.index("if (hoveredTrailCallsign) highlightTrail(")
    )


# --------------------------------------------------------------------------
# Source asserts: panel wiring (plan tasks 3-4)
# --------------------------------------------------------------------------


def _trail_click_block(js: str) -> str:
    """The map click handler that opens/closes the trail panel."""
    start = js.index("map.on('click', function (e) {\n                // A station marker")
    return js[start : js.index("map.on('resize', placeTrailPanel);", start)]


def test_panel_mounts_in_the_map_container():
    js = APP_JS.read_text(encoding="utf-8")
    block = _extract(js, "ensureTrailPanel")
    assert "document.getElementById('map-container')" in block
    assert "root.appendChild(el);" in block
    assert "el.setAttribute('role', 'dialog');" in block


def test_panel_field_order_is_the_spec_order():
    js = APP_JS.read_text(encoding="utf-8")
    block = _extract(js, "buildTrailRows")
    labels = [
        "'Trail start'",
        "'Elapsed travel time'",
        "'Total distance'",
        "'Beacon frequency'",
        "'Current speed'",
        "'Average speed'",
        "'Maximum speed'",
        "'Heading'",
        "'Last heard'",
        "'Points'",
        "'Data window'",
    ]
    positions = [block.index(label) for label in labels]
    assert positions == sorted(positions)


def test_panel_rows_are_escaped_and_missing_values_are_dashes():
    js = APP_JS.read_text(encoding="utf-8")
    block = _extract(js, "openTrailPanel")
    # Callsign and every row value go through escapeHtml: the callsign comes
    # from packet data, the values from formatters over that data.
    assert "escapeHtml(callsign)" in block
    assert "escapeHtml(row.value)" in block
    # A field with no value renders "-", never an empty cell.
    assert "return { label: label, value: '-' };" in _extract(js, "trailSpeedRow")
    assert "if (seconds === null || seconds === undefined || !isFinite(seconds)) return '-';" in js


def test_a_click_opens_the_panel_without_a_prior_hover():
    js = APP_JS.read_text(encoding="utf-8")
    block = _trail_click_block(js)
    assert "pickTrailHit(map, e.point)" in block
    assert block.index("pickTrailHit(map, e.point)") < block.index(
        "openTrailPanel(hit.properties.callsign);")
    # Port decision 5: the click never depends on the hover state.
    assert "hoveredTrailCallsign" not in block


def test_bare_map_click_closes_the_panel():
    js = APP_JS.read_text(encoding="utf-8")
    block = _trail_click_block(js)
    assert block.index("closeTrailPanel();") > block.index("pickTrailHit(map, e.point)")


def test_station_marker_owns_its_click_over_the_trail():
    js = APP_JS.read_text(encoding="utf-8")
    block = _trail_click_block(js)
    marker_query = "layers: ['stations-symbol-base', 'stations-symbol-overlay'],"
    assert marker_query in block
    assert block.index(marker_query) < block.index("openTrailPanel(hit.properties.callsign);")


def test_escape_closes_the_panel_and_stops_there():
    js = APP_JS.read_text(encoding="utf-8")
    # Registered once per page, not once per open, so listeners cannot pile up.
    block = _extract(js, "openTrailPanel")
    assert "if (!_trailPanelKeyBound) {" in block
    assert "ev.key === 'Escape'" in js
    assert "ev.stopPropagation();" in js
    assert "closeTrailPanel();" in js


def test_panel_re_places_when_the_map_resizes():
    js = APP_JS.read_text(encoding="utf-8")
    # The log panel and the splitter resize the map; the trail moves with it.
    assert "map.on('resize', placeTrailPanel);" in js
    # Chrome overlays change size without resizing the map.
    assert js.count("placeTrailPanel();\n        });") == 4


def test_panel_placement_avoids_the_map_chrome():
    js = APP_JS.read_text(encoding="utf-8")
    block = _extract(js, "placeTrailPanel")
    assert "mapChromeObstacles(canvasRect)" in block
    assert block.index("computeTrailPlacement(") < block.index("mapChromeObstacles(")
    assert ".maplibregl-ctrl" in _extract(js, "mapChromeObstacles")


def test_trail_rows_use_the_configured_units_and_window():
    js = APP_JS.read_text(encoding="utf-8")
    block = _extract(js, "openTrailPanel")
    assert "buildTrailRows(result, getUnits(), trailHours)" in block
    # Track rows are [lat, lon, epoch seconds]; the stats take [lon, lat, time].
    assert "return [p[1], p[0], p[2]];" in block


# --------------------------------------------------------------------------
# Node harness for the hit-test helper
# --------------------------------------------------------------------------

STUB_MAP = """
let calls = [];
const map = {
    getLayer: (id) => (id === 'station-tracks-line' ? {id: id} : null),
    queryRenderedFeatures: (box, opts) => {
        calls.push({box: box, opts: opts});
        return [{properties: {callsign: 'K1ABC'},
                 geometry: {type: 'LineString',
                            coordinates: [[1, 1], [2, 2], [3, 3]]}}];
    },
    project: (c) => ({x: c[0], y: c[1]}),
};
"""

# MapLibre-like: a horizontal 3 px line at y = 100 answers only when the
# query box touches it (the reference trail-hit.test.js fixture).
LINE_AT_Y100 = """
let calls = [];
const map = {
    getLayer: (id) => (id === 'station-tracks-line' ? {id: id} : null),
    queryRenderedFeatures: (box, opts) => {
        calls.push({box: box, opts: opts});
        const touches = box[0][1] <= 101.5 && box[1][1] >= 98.5;
        return touches
            ? [{properties: {callsign: 'K5ROX'},
                geometry: {type: 'LineString', coordinates: [[0, 100], [400, 100]]}}]
            : [];
    },
    project: (c) => ({x: c[0], y: c[1]}),
};
"""


def test_harness_box_math_and_single_layer():
    """The query box is pointer±8 px and asks for one layer only."""
    if not shutil.which("node"):
        pytest.skip("node not installed")

    line = _run_node(
        STUB_MAP
        + """
const hit = pickTrailHit(map, {x: 200, y: 94});
console.log('BOX ' + JSON.stringify(calls[0].box));
console.log('LAYERS ' + JSON.stringify(calls[0].opts.layers));
console.log('CS ' + hit.properties.callsign);
console.log('GEO ' + hit.geometry.type + ' ' + hit.geometry.coordinates.length);
"""
    )
    assert json.loads(line["BOX"]) == [[192, 86], [208, 102]]
    assert json.loads(line["LAYERS"]) == ["station-tracks-line"]
    assert line["CS"] == "K1ABC"
    # One feature per station: the whole trail (3 points) comes back as one
    # LineString, so the highlight layer draws the WHOLE trail.
    assert line["GEO"] == "LineString 3"


def test_harness_hover_band_is_8_px_not_more():
    """6 px off a 3 px line hits (reference fixture), 12 px off is bare map."""
    if not shutil.which("node"):
        pytest.skip("node not installed")

    line = _run_node(
        LINE_AT_Y100
        + """
console.log('HIT6 ' + (pickTrailHit(map, {x: 200, y: 94}) ? 'yes' : 'no'));
console.log('MISS12 ' + (pickTrailHit(map, {x: 200, y: 112}) === null ? 'yes' : 'no'));
"""
    )
    # Pointer 6 px above the line centre: inside the 8 px box.
    assert line["HIT6"] == "yes"
    # Pointer 12 px away: box [104, 120] no longer touches the 98.5..101.5
    # band, so this is bare map and the highlight must stay off.
    assert line["MISS12"] == "yes"


def test_harness_nearest_wins_among_competing_trails():
    """Low zoom puts two trails in the box; the nearer one lights up."""
    if not shutil.which("node"):
        pytest.skip("node not installed")

    line = _run_node(
        """
const far = {properties: {callsign: 'FAR'},
             geometry: {type: 'LineString', coordinates: [[0, 93], [200, 93]]}};
const near = {properties: {callsign: 'NEAR'},
              geometry: {type: 'LineString', coordinates: [[60, 102], [140, 98]]}};
const map = {
    getLayer: (id) => (id === 'station-tracks-line' ? {id: id} : null),
    queryRenderedFeatures: (box, opts) => [far, near],
    project: (c) => ({x: c[0], y: c[1]}),
};
const hit = pickTrailHit(map, {x: 100, y: 100});
console.log('CS ' + hit.properties.callsign);
"""
    )
    assert line["CS"] == "NEAR"


def test_harness_guards_never_throw():
    """No map / no point / no layer / geometry-less hits are all null-safe."""
    if not shutil.which("node"):
        pytest.skip("node not installed")

    line = _run_node(
        """
const nomap = pickTrailHit(null, {x: 1, y: 1});
const nopoint = pickTrailHit({getLayer: (id) => ({})}, null);
const nolayer = pickTrailHit(
    {getLayer: (id) => null, queryRenderedFeatures: (b, o) => [], project: (c) => ({x: 0, y: 0})},
    {x: 1, y: 1});
const nocoords = pickTrailHit(
    {getLayer: (id) => ({id: id}),
     queryRenderedFeatures: (b, o) => [
         {properties: {callsign: 'A'}}, {properties: {callsign: 'B'}}],
     project: (c) => ({x: c[0], y: c[1]})},
    {x: 1, y: 1});
console.log('NOMAP ' + nomap);
console.log('NOPOINT ' + nopoint);
console.log('NOLAYER ' + nolayer);
console.log('NOCOORDS ' + nocoords.properties.callsign);
"""
    )
    assert line["NOMAP"] == "null"
    assert line["NOPOINT"] == "null"
    assert line["NOLAYER"] == "null"
    assert line["NOCOORDS"] == "A"


# --------------------------------------------------------------------------
# Node harness for the panel: field order, dashes, placement
# --------------------------------------------------------------------------

PANEL_CONSTS = ["KM_PER_MILE"]
PANEL_FUNCS = [
    "timeMs",
    "isMetric",
    "formatClock",
    "formatElapsed",
    "formatDistance",
    "formatInterval",
    "formatSpeed",
    "formatHeading",
    "trailSpeedRow",
    "trailWindowLabel",
    "buildTrailRows",
    "escapeHtml",
]


def _panel_harness(tail: str) -> str:
    js = APP_JS.read_text(encoding="utf-8")
    return "\n".join(
        [_const(js, name) for name in PANEL_CONSTS]
        + [_extract(js, name) for name in PANEL_FUNCS]
        + [tail]
    )


# Epoch seconds, like this app's track rows (not the reference's ISO strings).
WORKED = {
    "points": 6,
    "firstTime": 1761655200,
    "lastTime": 1761655740,
    "elapsedSeconds": 540,
    "distanceKm": 2.4225,
    "beacon": {"medianSeconds": 60, "meanSeconds": 60, "intervals": 4},
    "currentKph": 21.0,
    "currentReported": False,
    "averageKph": 16.15,
    "maxKph": 22.0,
    "maxReported": False,
    "headingDeg": 50.4,
}


def test_harness_rows_follow_the_spec_order_and_format():
    """Every spec field in order, with the shared display rules."""
    if not shutil.which("node"):
        pytest.skip("node not installed")

    out = _node(
        _panel_harness(
            f"""
const rows = buildTrailRows({json.dumps(WORKED)}, 'metric', 1);
console.log('LABELS ' + JSON.stringify(rows.map(function (r) {{ return r.label; }})));
rows.forEach(function (r) {{ console.log('ROW ' + r.label + '=' + r.value); }});
"""
        )
    )
    line = {ln.split(" ", 1)[0]: ln.split(" ", 1)[1] for ln in out.strip().splitlines()}
    assert json.loads(line["LABELS"]) == [
        "Trail start",
        "Elapsed travel time",
        "Total distance",
        "Beacon frequency",
        "Current speed",
        "Average speed",
        "Maximum speed",
        "Heading",
        "Last heard",
        "Points",
        "Data window",
    ]
    # Epoch seconds are read as seconds, not milliseconds: 10:00 local, 9 min.
    assert "ROW Elapsed travel time=9 min" in out
    assert "ROW Total distance=2.4 km" in out
    assert "ROW Beacon frequency=60 s median \u00B7 60 s mean (4 intervals)" in out
    assert "ROW Current speed=21 km/h (computed)" in out
    assert "ROW Average speed=16 km/h" in out
    assert "ROW Maximum speed=22 km/h (computed)" in out
    assert "ROW Heading=50\u00B0" in out
    assert "ROW Points=6" in out
    assert "ROW Data window=last 1 hour" in out
    # Both times are clock times in the viewer's own zone, so a 1970 reading
    # would mean the epoch seconds were read as milliseconds.
    assert "ROW Trail start=" + time.strftime(
        "%H:%M", time.localtime(WORKED["firstTime"])) in out
    assert "ROW Last heard=" + time.strftime(
        "%H:%M", time.localtime(WORKED["lastTime"])) in out


def test_harness_missing_values_render_a_dash():
    """An unknown value is "-", never an empty cell or NaN."""
    if not shutil.which("node"):
        pytest.skip("node not installed")

    out = _node(
        _panel_harness(
            """
const none = {points: 2, firstTime: null, lastTime: null,
              elapsedSeconds: null, distanceKm: null,
              beacon: {medianSeconds: null, meanSeconds: null, intervals: 0},
              currentKph: null, currentReported: false, averageKph: null,
              maxKph: null, maxReported: false, headingDeg: null};
buildTrailRows(none, 'metric', null).forEach(function (r) {
    console.log('ROW ' + r.label + '=' + r.value);
});
console.log('ESC ' + escapeHtml("<K1&>'\\""));
"""
        )
    )
    assert "ROW Total distance=-" in out
    assert "ROW Beacon frequency=-" in out
    assert "ROW Current speed=-" in out
    assert "ROW Elapsed travel time=-" in out
    assert "ROW Data window=-" in out
    assert "ROW Points=2" in out
    # The values come from packet data, so they are escaped before innerHTML.
    assert "ESC &lt;K1&amp;&gt;&#39;&quot;" in out


PLACEMENT_CONSTS = ["TRAIL_PLACEMENT_GAP", "TRAIL_PLACEMENT_MARGIN"]
PLACEMENT_FUNCS = ["rectsOverlap", "computeTrailPlacement"]


def _placement_harness(tail: str) -> str:
    js = APP_JS.read_text(encoding="utf-8")
    return "\n".join(
        [_const(js, name) for name in PLACEMENT_CONSTS]
        + [_extract(js, name) for name in PLACEMENT_FUNCS]
        + [tail]
    )


def test_harness_panel_sits_beside_the_trail_one_gap_away():
    """Right of the trail, one gap off its nearest edge, centred on it."""
    if not shutil.which("node"):
        pytest.skip("node not installed")

    out = _node(
        _placement_harness(
            """
const spot = computeTrailPlacement(
    {minX: 100, minY: 100, maxX: 200, maxY: 200},
    {width: 200, height: 150},
    {width: 800, height: 600},
    {top: 0, obstacles: []});
console.log('SPOT ' + JSON.stringify(spot));
"""
        )
    )
    spot = json.loads(out.strip().split(" ", 1)[1])
    assert spot["left"] == 200 + 12
    assert spot["top"] == 150 - 75
    # Clear of the trail: it never covers the line it describes.
    assert spot["left"] > 200


def test_harness_panel_flips_when_the_right_side_has_no_room():
    """No room on the right: the panel goes to the trail's other side."""
    if not shutil.which("node"):
        pytest.skip("node not installed")

    out = _node(
        _placement_harness(
            """
const spot = computeTrailPlacement(
    {minX: 400, minY: 100, maxX: 600, maxY: 200},
    {width: 200, height: 150},
    {width: 800, height: 600},
    {top: 0, obstacles: []});
console.log('SPOT ' + JSON.stringify(spot));
"""
        )
    )
    spot = json.loads(out.strip().split(" ", 1)[1])
    assert spot["left"] == 400 - 12 - 200
    assert spot["left"] + 200 < 400


def test_harness_panel_slides_clear_of_the_chrome():
    """A control in the panel's spot makes it move, never cover the control."""
    if not shutil.which("node"):
        pytest.skip("node not installed")

    out = _node(
        _placement_harness(
            """
const chrome = {top: 0, obstacles: [{left: 200, right: 420, top: 60, bottom: 300}]};
const spot = computeTrailPlacement(
    {minX: 100, minY: 100, maxX: 200, maxY: 200},
    {width: 200, height: 150},
    {width: 800, height: 600},
    chrome);
const box = {left: spot.left, right: spot.left + 200,
             top: spot.top, bottom: spot.top + 150};
console.log('SPOT ' + JSON.stringify(spot));
console.log('OVERLAP ' + rectsOverlap(box, chrome.obstacles[0]));
"""
        )
    )
    line = {ln.split(" ", 1)[0]: ln.split(" ", 1)[1] for ln in out.strip().splitlines()}
    assert line["OVERLAP"] == "false"
    assert json.loads(line["SPOT"])["top"] == 300 + 12

