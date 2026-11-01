"""Node harness: follow-a-station state machine in app.js (issue #206).

The follow state machine lives in app.js as one section between
`// --- Follow a station (issue #206) ---` and the next `// ---` banner.
This test slices that section plus the existing app.js helpers it reuses
(GENERIC_PATH_ALIASES, parsePath, haversineKm) into a node script, drives
a scripted timeline and asserts the behaviour prescribed by the reference
implementation (haminfo map-shared/follow-station.js, issue basemap-kit #64).
Behaviour only — returned objects, log(), state(), chip text — never structure.

Run: cd direwolf-dashboard && uv run pytest tests/test_map_follow_station.py -v
"""

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

import direwolf_dashboard

APP_JS = Path(direwolf_dashboard.__file__).parent / "static" / "app.js"

BANNER = "// --- Follow a station (issue #206) ---"

NODE_SCRIPT = r"""
var fs = require('fs');
var app = fs.readFileSync(process.argv[2], 'utf8');
if (app.indexOf('// --- Follow a station (issue #206) ---') < 0) {
    console.log(JSON.stringify({ error: 'follow section missing from app.js (#206 not implemented)' }));
    process.exit(3);
}
function braceFn(src, mark) {
    var i = src.indexOf(mark);
    if (i < 0) throw new Error('missing ' + mark);
    if (mark.indexOf('function') === 0) {
        var open = src.indexOf('{', i), depth = 0;
        for (var j = open; j < src.length; j++) {
            if (src[j] === '{') depth++;
            else if (src[j] === '}') { depth--; if (depth === 0) return src.slice(i, j + 1); }
        }
    }
    var close = src.indexOf(']);', i); // multi-line Set literal
    return src.slice(i, close + 3);
}
var b = app.indexOf('// --- Follow a station (issue #206) ---');
var e = app.indexOf('\n    // --- ', b + 41);
var follow = app.slice(b, e);
var helpers = braceFn(app, 'const GENERIC_PATH_ALIASES = new Set([')
    + '\n' + braceFn(app, 'function parsePath(')
    + '\n' + braceFn(app, 'function haversineKm(');

function nowMs() { return clock; }
var clock = 0;
var document = { addEventListener: function () {} };
var setInterval = function () {};
var setTimeout = function () {};

var src = helpers + '\n' + follow +
    '\nglobalThis.__F = { createFollower: followShared.create, normalizeCallsign: followShared.normalizeCallsign };';
// Date.now must resolve to the clock stub when the eval body calls it.
(new Function('return (function(){ return Date.now; })()'))(); // sanity, no side effect
try { new Function(src)(); } catch (err) {
    console.log(JSON.stringify({ error: 'follow section threw: ' + err.message }));
    process.exit(4);
}
var T0 = 1700000000000;
var view = { center: { lat: 47.6, lng: 15.5 }, viewportKmWidth: 100, zoom: 3 };
var viewNC = { viewportKmWidth: 100 };
var NEAR = { lat: 47.85, lng: 15.5 }, INSIDE = { lat: 47.65, lng: 15.5 }, NEAR2 = { lat: 47.5, lng: 15.5 };
var FARPT = { lat: 46.9, lng: 15.5 }, FAR2 = { lat: 47.85, lng: 15.5 };
var PK = { packet_id: 8, received_at: T0 + 6000, direction: 'from', from_call: 'W2ABC', addressee: 'K1ABC',
    packet_type: 'msg', comment: '', latitude: 48.45, longitude: 15.5, path: ['K1ABC*', 'FOO*'] };
var DUP = { packet_id: 8, received_at: T0 + 6500, direction: 'to', from_call: 'W2ABC', addressee: 'K1ABC',
    packet_type: 'msg', comment: '', latitude: 48.45, longitude: 15.5, path: ['OLD*', 'NEW*'] };
var NOID = { packet_id: 0, received_at: T0 + 7000, direction: 'from', from_call: 'W2ABC', addressee: 'K1ABC',
    packet_type: 'msg', comment: '', latitude: 47.0, longitude: 15.5, path: [] };

var f = globalThis.__F.createFollower({});
var R = {};
R.follow = f.follow('k1abc');
R.p1 = f.setPosition(NEAR, T0 + 100, viewNC);
R.dead = f.setPosition(INSIDE, T0 + 1300, view);
R.left = f.setPosition(NEAR2, T0 + 1400, view);
R.pend = f.setPosition(NEAR2, T0 + 1800, view);
R.flush = f.flushPending(T0 + 2700, view);
R.far1 = f.setPosition(FARPT, T0 + 3200, view);
R.pk = f.onPacket(PK, T0 + 6000, view);
R.fd1 = f.farDue(T0 + 5000);
R.fd2 = f.farDue(T0 + 9000);
R.dup = f.onPacket(DUP, T0 + 6500, view);
R.noid = f.onPacket(NOID, T0 + 7000, view);
R.susp = f.suspend();
R.chipP = f.chipText(T0 + 7500);
R.res = f.resume(T0 + 8000);
R.mode = f.setMode('bogus');
R.red = f.setReduceMotion(true);
R.far2 = f.setPosition(FAR2, T0 + 12000, view);
R.sil = f.onSilence(60);
R.chipS = f.chipText(T0 + 12500);
R.stop = f.unfollow();
R.log = f.log().map(function (r) { return [r.callsign, r.hop_count, r.via, r.id]; });
R.st = f.state();
R.norm = [globalThis.__F.normalizeCallsign('W1ABC-99'),
    globalThis.__F.normalizeCallsign('W1ABC >'),
    globalThis.__F.normalizeCallsign(' k1abc-1')];

var FAILS = [];
function chk(name, ok) { if (!ok) FAILS.push(name); }
chk('follow', R.follow.type === 'following' && R.follow.callsign === 'K1ABC');
chk('first-position', R.p1.type === 'move' && R.p1.reason === 'first-position');
chk('deadzone', R.dead.type === 'none' && R.dead.reason === 'dead-zone');
chk('left-dead-zone', R.left.type === 'move' && R.left.reason === 'left-dead-zone');
chk('pending', R.pend.type === 'pending' && R.pend.retryInMs === 800);
chk('flush', R.flush.type === 'move' && R.flush.reason === 'deferred');
chk('far-jump', R.far1.type === 'far' && R.far1.holdMs === 2500 && R.far1.motion === 'ease');
chk('far-packet', R.pk.type === 'packet' && R.pk.far && R.pk.far.returnMs === 700 && R.pk.far.holdMs === 2500);
chk('far-due-early', R.fd1 === null);
chk('far-due-late', R.fd2.type === 'far-return' && R.fd2.motion === 'ease');
chk('merge', R.dup.type === 'duplicate' && R.dup.row.path_count === 2 && String(R.dup.row.path).indexOf(' / ') >= 0);
chk('noid', R.noid.type === 'packet' && R.noid.row.id === 'W2ABC>K1ABC:');
chk('suspend', R.susp.type === 'suspended');
chk('chip-paused', /paused/.test(R.chipP.label));
chk('resume', R.res.type === 'resumed' && R.res.move && R.res.move.zoom === null);
chk('mode-dim', R.mode === 'dim');
chk('reduce-true', R.red === true);
chk('far-jump-jump', R.far2.type === 'far' && R.far2.motion === 'jump');
chk('silence', R.sil.type === 'silent' && R.sil.silent === true);
chk('chip-silent', /silent 1m/.test(R.chipS.text) && /nothing heard for 1 minute/.test(R.chipS.label));
chk('unfollow', R.stop.type === 'stopped' && R.stop.callsign === 'K1ABC');
chk('log-rows', R.log.length === 2 && R.log[0][3] === 'W2ABC>K1ABC:' && R.log[0][1] === 0 &&
    R.log[1][2] === 'K1ABC>FOO' && R.log[1][1] === 2);
chk('state-cleared', R.st.callsign === null && R.st.following === false && R.st.position === null);
chk('normalize', R.norm[0] === null && R.norm[1] === null && R.norm[2] === 'K1ABC-1');
console.log(JSON.stringify({ fail: FAILS, results: R }));
"""


def test_follow_station_behaviour() -> None:
    """Follow-a-station behaviour matches the reference (issue #206)."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node executable not found")
    tmp = Path(tempfile.mkdtemp(prefix="follow-harness-"))
    script = tmp / "harness.js"
    script.write_text(NODE_SCRIPT, encoding="utf-8")
    proc = subprocess.run(
        [node, str(script), str(APP_JS)],
        capture_output=True,
        text=True,
    )
    out = (proc.stdout or "").strip()
    try:
        data = json.loads(out)
    except Exception:
        raise AssertionError("harness produced no JSON: " + (out or proc.stderr)[:300])
    if "error" in data:
        msg = data["error"]
        if "not implemented" in msg:
            raise AssertionError(
                "follow section missing from app.js (#206 not implemented)"
            )
        raise AssertionError(f"harness error: {msg[:300]}")
    assert data["fail"] == [], "failed reference checks: " + ", ".join(data["fail"])


# --- Task 2: UI wiring source-asserts (issue #206) ---
# The page wiring (chip, controls rail entries, log-filter hook, Escape
# unfollow, dw-follow-callsign persistence) lives in app.js; these asserts
# pin that each piece mounts at its direwolf seam, not how it renders.

JS_SRC = APP_JS.read_text(encoding="utf-8")


def _fn(name: str) -> str:
    """Slice one top-level IIFE-indented function out of app.js."""
    m = re.search(r"function " + name + r"\(.*?\n    \}\n", JS_SRC, re.S)
    assert m, name
    return m.group(0)


def test_follow_controls_exist_in_existing_filter_rail():
    """A follow control row exists inside the existing controls rail."""
    body = _fn("initFilterOverlay")
    assert '<div class="filter-overlay-row">' in body  # the rail itself is kept
    assert 'for="filter-follow-callsign"' in body
    assert 'id="filter-follow-callsign"' in body
    assert 'for="filter-follow-mode"' in body
    assert 'id="filter-follow-mode"' in body


def test_follow_chip_element_renders():
    """The chip element is created and re-rendered from the follow state."""
    init = _fn("initFollowStation")
    assert "mlCreate('follow-chip')" in init
    assert 'id="follow-chip-text"' in init
    assert 'id="follow-stop-btn"' in init
    assert 'aria-live="polite"' in init
    render = _fn("renderFollowChip")
    assert "followPage.chipText(" in render
    assert "textContent" in render
    assert "setAttribute('aria-label'" in render
    assert "'is-silent'" in render


def test_per_station_log_filter_hooks_log_row_renderer():
    """The per-station filter runs in the existing log-row filter hook."""
    hook = _fn("applyFilterToRow")
    assert "followLogCallsign()" in hook
    assert "hidden-by-filter" in hook
    renderer = _fn("addLogRow")
    assert "applyFilterToRow(row);" in renderer  # new rows keep flowing through


def test_escape_unfollows():
    """Escape unfollows while following (and only then)."""
    body = _fn("initFollowStation")
    assert "e.key === 'Escape'" in body
    assert "stopFollowing()" in body


def test_follow_callsign_persisted_like_other_dw_keys():
    """dw-follow-callsign round-trips like the other dw-* keys."""
    assert "'dw-follow-callsign'" in JS_SRC
    assert "localStorage.setItem('dw-follow-callsign'" in _fn("startFollowing")
    assert "localStorage.removeItem('dw-follow-callsign')" in _fn("stopFollowing")
    init = _fn("initFollowStation")
    assert "localStorage.getItem('dw-follow-callsign')" in init
    assert "startFollowing(" in init  # restore-follow-on-load
