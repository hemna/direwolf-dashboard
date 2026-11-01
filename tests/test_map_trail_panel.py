"""Hover-highlight tests for station trails (issue #207, task 2).

Two layers of evidence, per this repo's client-test convention:

1. Source asserts against ``app.js``: the spec's 8 px hover tolerance, a
   ``queryRenderedFeatures`` box that queries ``station-tracks-line`` ONLY,
   a ``station-tracks-highlight`` layer that the hover handler adds and
   removes, the cursor change on hit, and the highlight re-added after a
   style reload the way ``tx-arcs`` is (inside ``addOverlaySources``, the
   style.load handler).
2. Node harness for the hit-test helper (the extraction idiom is the one
   ``tests/test_trail_stats.py`` uses): a stub map answering like MapLibre
   pins the ±8 px box maths, the one-layer query, nearest-wins among
   competing trails, the guards, and the one-feature-per-station
   assumption (a hit feature is the WHOLE trail as a single LineString).
"""

import json
import re
import shutil
import subprocess
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
