"""Setting + reduced-motion + tab-idle tests for packet paths (#205 Task 5).

Node-harness pattern of ``test_packet_path_animation.py``: the setting glue and
animator are extracted from ``app.js`` and run under node with stubbed maplibre
map, DOM, localStorage and ``window.matchMedia``. Asserted: OS
``prefers-reduced-motion: reduce`` starts the feature disabled with nothing
scheduled; a saved ``dw-packet-paths`` ('1'/'0') choice overrides the OS
default either way; ``setPacketPathsEnabled(true)`` still turns it on despite
reduce-motion; while ``document.hidden`` is true the rAF loop stops and the
queue drains through the existing hidden-clear path so offers are refused and
the queue stays bounded (the day-open-tab criterion).
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

import direwolf_dashboard

APP_JS = Path(direwolf_dashboard.__file__).parent / "static" / "app.js"

HARNESS_PRELUDE_HEAD = r"""
const document = { hidden: false };
const LS_HANDLERS = {};
document.addEventListener = (evt, fn) => { LS_HANDLERS[evt] = fn; };
"""

HARNESS_PRELUDE_TAIL = r"""
global.window = globalThis;
global.window.matchMedia = (query) => ({ matches: OS_REDUCE });
global.window.localStorage = { getItem: (k) => SAVED.get(k),
    setItem: (k, v) => { SAVED.set(k, v); } };
global.localStorage = global.window.localStorage;
let rafQueue = [];
global.requestAnimationFrame = (fn) => { rafQueue.push(fn); return rafQueue.length; };
global.cancelAnimationFrame = () => {};
let setDataCalls = [];
let addedLayers = [];
let sources = {};
const mapStub = {
    getSource: (id) => sources[id],
    addSource: (id, spec) => { sources[id] = { setData: (d) => { setDataCalls.push(d); } }; return true; },
    getLayer: (id) => id === 'stations-symbol-base' || addedLayers.some((pair) => pair[0].id === id) ? id : null,
    addLayer: (layer, before) => { addedLayers.push([layer, before]); return true; },
    project: (c) => ({ x: c[0] + 180, y: 90 - c[1] }),
    unproject: (p) => ({ lng: p.x - 180, lat: 90 - p.y }),
    getCanvas: () => ({ clientWidth: 800 }),
    getContainer: () => ({ getBoundingClientRect: () => ({ top: 0 }) }),
    getBounds: () => ({ contains: (c) => c[0] >= -180 && c[0] <= 180 && c[1] >= -90 && c[1] <= 90 }),
    on: (evt, fn) => {},
};
const map = mapStub;
let mainMapIsMaplibre = true;
"""

# animator glue extraction pattern from test_packet_path_animation.py plus the
# Task 5 setting pieces (state line, resolver, clear/set, visibilitychange).
EXTRACTIONS = [
    (r"const SOURCE_MAX_AGE = .*?const COLORS = \{[^}]*\};", "re"),
    (r"const stationPositionCache = [^;]*;", "re"),
    (r"const POSITION_CACHE_MAX = \d+;", "re"),
    (r"let packetPathsData = [^;]*;", "re"),
    (r"let packetPathsRaf = null;", "re"),
    (r"let packetPathChrome = 0;", "re"),
    (r"let packetPathWidth = 0;", "re"),
    (r"let packetPathsEnabled = [^;]*;", "re"),
    (r"const packetPaths = createPacketPathAnimator\([^;]*;", "re"),
    ("haversineKm", "fn"),
    ("updatePositionCache", "fn"),
    ("createPacketPathAnimator", "fn"),
    ("packetPathInternetPosition", "fn"),
    ("packetPathInternetCurve", "fn"),
    ("packetPathInitialEnabled", "fn"),
    ("refreshPacketPathSource", "fn"),
    ("clearPacketPaths", "fn"),
    ("setPacketPathsEnabled", "fn"),
    ("schedulePacketPaths", "fn"),
    ("renderPacketPaths", "fn"),
    ("offerPacketPath", "fn"),
    (r"document\.addEventListener\('visibilitychange', function \(\) \{.*?\n *\}\);", "re"),
]


def _node(harness: str) -> str:
    result = subprocess.run(["node", "-e", harness], capture_output=True, text=True)
    assert result.returncode == 0, f"node failed:\n{result.stderr}\n{result.stdout}"
    return result.stdout


def _extract(js: str, name: str) -> str:
    """Extract a function including its braces (balanced scan)."""
    marker = f"function {name}("
    start = js.index(marker)
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


def _harness(saved, reduced: bool, drivers: list[str]) -> str:
    js = APP_JS.read_text(encoding="utf-8")
    scenario = (
        f"const OS_REDUCE = {'true' if reduced else 'false'};\n"
        f"const SAVED = new Map({saved!r});\n"
    )
    parts = [HARNESS_PRELUDE_HEAD, scenario, HARNESS_PRELUDE_TAIL]
    for pattern, kind in EXTRACTIONS:
        if kind == "fn":
            parts.append(_extract(js, pattern))
        else:
            m = re.search(pattern, js, re.DOTALL)
            assert m, f"missing app.js section matching {pattern}"
            parts.append(m.group(0))
    return "\n".join(parts + drivers)


def _lines(out: str) -> dict:
    return {ln.split(" ", 1)[0]: ln.split(" ", 1)[1] for ln in out.strip().splitlines()}


SETUP = r"""
updatePositionCache('WB4BOR', 42.5, -71.2);
updatePositionCache('APPOMX', 42.6, -71.3);
updatePositionCache('KM6LYW', 44.0, -69.0);
updatePositionCache('K4STN1', 43.0, -70.0);
updatePositionCache('K4STN2', 43.1, -70.1);
updatePositionCache('K4STN3', 43.2, -70.2);
const ts = Date.now() / 1000;
function makePacket(id, src, hops, rx, txIgate, txHops) {
    return { packet_id: id, source: src, hops: hops, rx_igate: rx, tx_igate: txIgate,
        tx_hops: txHops, no_rf_leg: false, from_call: src, latitude: 42.5, longitude: -71.2,
        timestamp: ts, raw_packet: 'WB4BOR>APRS,qAR,APPOMX:test{01' };
}
"""


def test_settings_panel_source_asserts():
    """Toggle control, persistence key, reduced-motion gate and visibilitychange
    guard are wired into app.js."""
    js = APP_JS.read_text(encoding="utf-8")
    assert 'id="filter-packet-paths"' in js, "settings panel toggle missing"
    assert ">Packet paths</label>" in js, "toggle label missing"
    assert "localStorage.getItem('dw-packet-paths'" in js, "setting not read"
    assert "localStorage.setItem('dw-packet-paths'" in js, "setting not persisted"
    assert "matchMedia('(prefers-reduced-motion: reduce)')" in js, "reduced-motion gate missing"
    assert "addEventListener('visibilitychange'" in js, "tab-hidden guard missing"
    assert "function clearPacketPaths(" in js, "shared clear path missing"
    assert "if (!packetPathsEnabled || document.hidden) return false;" in js, (
        "offer gate missing"
    )
    assert "packetPathsEnabled && !document.hidden" in js, "schedule gate missing"


def test_reduced_motion_starts_disabled():
    """OS reduce-motion + no saved choice: initial disabled, nothing scheduled
    and no animator state touched."""
    if not shutil.which("node"):
        pytest.skip("node not installed")
    out = _node(_harness([], True, [SETUP, r"""
const p = makePacket('p1', 'WB4BOR', ['WB4BOR', 'APPOMX'], 'APPOMX', null, []);
console.log('ENABLED ' + packetPathsEnabled);
console.log('OFFER ' + offerPacketPath(p));
console.log('RAF ' + rafQueue.length);
console.log('WORK ' + packetPaths.hasWork);
"""]))
    line = _lines(out)
    assert line["ENABLED"] == "false", "reduce-motion must start the feature off"
    assert line["OFFER"] == "false", "offers must be refused while disabled"
    assert line["RAF"] == "0", "no frame may be scheduled while disabled"
    assert line["WORK"] == "false", "animator must not be touched while disabled"


def test_saved_choice_overrides_reduced_motion():
    """dw-packet-paths '1' saved with reduce-motion on: initial enabled (the
    user's explicit choice beats the OS default)."""
    if not shutil.which("node"):
        pytest.skip("node not installed")
    out = _node(_harness([["dw-packet-paths", "1"]], True, [SETUP, r"""
console.log('ENABLED ' + packetPathsEnabled);
const p = makePacket('p1', 'WB4BOR', ['WB4BOR', 'APPOMX'], 'APPOMX', null, []);
console.log('OFFER ' + offerPacketPath(p));
console.log('RAF ' + rafQueue.length);
"""]))
    line = _lines(out)
    assert line["ENABLED"] == "true", "saved 'on' must override reduce-motion"
    assert line["OFFER"] == "true"
    assert line["RAF"] == "1", "the loop schedules exactly once"


def test_saved_off_overrides_default_on():
    """No reduce-motion but saved '0': initial disabled (explicit off persists)."""
    if not shutil.which("node"):
        pytest.skip("node not installed")
    out = _node(_harness([["dw-packet-paths", "0"]], False, [SETUP, r"""
console.log('ENABLED ' + packetPathsEnabled);
const p = makePacket('p1', 'WB4BOR', ['WB4BOR', 'APPOMX'], 'APPOMX', null, []);
console.log('OFFER ' + offerPacketPath(p));
console.log('RAF ' + rafQueue.length);
"""]))
    line = _lines(out)
    assert line["ENABLED"] == "false", "saved 'off' must persist despite no reduce-motion"
    assert line["OFFER"] == "false"
    assert line["RAF"] == "0"


def test_explicit_toggle_turns_on_despite_reduce():
    """Reduce-motion on + no saved choice: setPacketPathsEnabled(true) enables
    and schedules; enabling via the settings control is how this happens."""
    if not shutil.which("node"):
        pytest.skip("node not installed")
    out = _node(_harness([], True, [SETUP, r"""
console.log('BEFORE ' + packetPathsEnabled);
setPacketPathsEnabled(true);
const p = makePacket('p1', 'WB4BOR', ['WB4BOR', 'APPOMX'], 'APPOMX', null, []);
console.log('ENABLED ' + packetPathsEnabled);
console.log('OFFER ' + offerPacketPath(p));
console.log('RAF ' + rafQueue.length);
setPacketPathsEnabled(false);
console.log('AFTER ' + packetPathsEnabled);
console.log('ACTIVE ' + packetPaths.active.size);
console.log('QUEUE ' + packetPaths.queue.length);
"""]))
    line = _lines(out)
    assert line["BEFORE"] == "false"
    assert line["ENABLED"] == "true", "explicit enable must win over reduce-motion"
    assert line["OFFER"] == "true"
    assert line["RAF"] == "1"
    assert line["AFTER"] == "false", "disable must drop queued journeys"
    assert line["ACTIVE"] == "0" and line["QUEUE"] == "0", "clearPacketPaths must drain"


def test_hidden_tab_stops_loop_and_bounds_queue():
    """Day-open-tab criterion: with document.hidden the visibilitychange guard
    drains through the existing hidden-clear path, further offers are refused
    (queue stays bounded) and no new frame is scheduled."""
    if not shutil.which("node"):
        pytest.skip("node not installed")
    out = _node(_harness([], False, [SETUP, r"""
const p1 = makePacket('p1', 'WB4BOR', ['WB4BOR', 'APPOMX'], 'APPOMX', null, []);
const p2 = makePacket('p2', 'WB4BOR', ['WB4BOR', 'K4STN1'], 'K4STN1', null, []);
const p3 = makePacket('p3', 'WB4BOR', ['WB4BOR', 'K4STN2'], 'K4STN2', null, []);
const p4 = makePacket('p4', 'WB4BOR', ['WB4BOR', 'K4STN3'], 'K4STN3', null, []);
const p5 = makePacket('p5', 'WB4BOR', ['WB4BOR', 'K4STN3'], 'K4STN3', null, []);
[p1, p2, p3, p4, p5].forEach((p) => offerPacketPath(p));
console.log('QUEUE_BEFORE ' + packetPaths.queue.length);
renderPacketPaths();
document.hidden = true;
LS_HANDLERS['visibilitychange']();
console.log('ACTIVE ' + packetPaths.active.size);
console.log('QUEUE ' + packetPaths.queue.length);
console.log('SEEN ' + packetPaths.seen.size);
console.log('OFFER_HIDDEN ' + offerPacketPath(makePacket('p6', 'WB4BOR', ['WB4BOR', 'APPOMX'], 'APPOMX', null, [])));
schedulePacketPaths();
console.log('RAF ' + rafQueue.length);
"""]))
    line = _lines(out)
    assert int(line["QUEUE_BEFORE"]) <= 3, "queue must stay bounded by the cap"
    assert line["ACTIVE"] == "0", "hidden guard must drop every active journey"
    assert line["QUEUE"] == "0"
    assert line["SEEN"] == "0", "hidden clear must also reset dedupe records"
    assert line["OFFER_HIDDEN"] == "false", "no work may queue while the tab is hidden"
    assert line["RAF"] == "2", "one scheduled frame per rendered frame, no more while hidden"
