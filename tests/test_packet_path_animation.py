"""Executable tests for the app.js packet-path animator (issue #205).

Node-harness pattern of ``test_packet_path_parsing.py``: the animator section
and the overlay glue are extracted from ``app.js`` and run under node with a
stubbed maplibregl map + DOM, driven by the server packet fields wired in b0a37b5
(``packet_id/source/hops/rx_igate/tx_igate/tx_hops/no_rf_leg``) and the Task 3
``stationPositionCache`` entries with ``.at``.

Asserted: the concurrency cap is enforced in ``offer()`` (over-cap requests
are dropped and ``dropped`` counts them, the queue never exceeds the cap),
dedupe inside 30 s animates once, hops with no recent cached position are
skipped without inventing one, aprsis legs render through the dashed
internet line style, exactly one ``setData`` per rendered frame, and the
packet-paths layers re-add below ``stations-symbol-base`` on style reload.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

import direwolf_dashboard

APP_JS = Path(direwolf_dashboard.__file__).parent / "static" / "app.js"

# Stub maplibregl/DOM prelude. The animator itself is map-independent; the
# overlay glue needs project/unproject bounds, addSource/addLayer capture and
# a deferred requestAnimationFrame (recorded, not invoked, so render() is
# driven explicitly by the driver below).
HARNESS_PRELUDE = r"""
const document = { hidden: false };
global.window = globalThis;
// Task 5's state line reads the setting at load; no saved choice + no matchMedia
// stub (guard falls to null) keeps the animator enabled as before.
global.localStorage = { getItem: (key) => null, setItem: (key, value) => {} };
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

# The extracted glue reads the module-level map under its own name; alias it.
EXTRACTIONS = [
    (r"const SOURCE_MAX_AGE = .*?const COLORS = \{[^}]*\};", "const"),
    (r"const stationPositionCache = [^;]*;", "const"),
    (r"const POSITION_CACHE_MAX = \d+;", "const"),
    (r"let packetPathsData = [^;]*;", "let"),
    (r"let packetPathsRaf = null;", "let"),
    (r"let packetPathChrome = 0;", "let"),
    (r"let packetPathWidth = 0;", "let"),
    (r"let packetPathsEnabled = [^;]*;", "let"),
    (r"const packetPaths = createPacketPathAnimator\([^;]*;", "const"),
    ("haversineKm", "fn"),
    ("updatePositionCache", "fn"),
    ("createPacketPathAnimator", "fn"),
    ("packetPathInitialEnabled", "fn"),
    ("packetPathInternetPosition", "fn"),
    ("packetPathInternetCurve", "fn"),
    ("measurePacketPathChrome", "fn"),
    ("addPacketPathLayers", "fn"),
    ("refreshPacketPathSource", "fn"),
    ("schedulePacketPaths", "fn"),
    ("renderPacketPaths", "fn"),
    ("offerPacketPath", "fn"),
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


def _harness(drivers: list[str]) -> str:
    js = APP_JS.read_text(encoding="utf-8")
    parts = [HARNESS_PRELUDE]
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


# Cache + packet factories mirroring the server dict wired in b0a37b5;
# timestamp is epoch seconds like processor.py, positions recent (now).
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


def test_cap_one_drops_and_counts():
    """With cap 1 a second concurrent request queues, further ones are
    dropped and dropped counts them; queue never exceeds the cap."""
    if not shutil.which("node"):
        pytest.skip("node not installed")
    out = _node(_harness([SETUP, r"""
packetPaths.configure({ maxConcurrent: 1 });
const a = makePacket('ca', 'WB4BOR', ['WB4BOR', 'APPOMX'], 'APPOMX', null, []);
const b = makePacket('cb', 'WB4BOR', ['WB4BOR', 'APPOMX'], 'APPOMX', null, []);
const c = makePacket('cc', 'WB4BOR', ['WB4BOR', 'APPOMX'], 'APPOMX', null, []);
const d = makePacket('cd', 'WB4BOR', ['WB4BOR', 'APPOMX'], 'APPOMX', null, []);
offerPacketPath(a); offerPacketPath(b); offerPacketPath(c); offerPacketPath(d);
console.log('ACTIVE ' + packetPaths.active.size);
console.log('QUEUE ' + packetPaths.queue.length);
console.log('DROPPED ' + packetPaths.dropped);
"""]))
    line = _lines(out)
    assert line["ACTIVE"] == "1", "cap must hold concurrent journeys at 1"
    assert line["QUEUE"] == "1", "queue must be bounded by the cap"
    assert line["DROPPED"] == "2", "dropped must count the over-cap requests"


def test_default_cap_defers_fourth_concurrent():
    """Default cap 3: the 4th concurrent packet does not start a 4th
    journey; the queue holds it (bounded, not silently invented)."""
    if not shutil.which("node"):
        pytest.skip("node not installed")
    out = _node(_harness([SETUP, r"""
const p1 = makePacket('p1', 'WB4BOR', ['WB4BOR', 'APPOMX'], 'APPOMX', null, []);
const p2 = makePacket('p2', 'WB4BOR', ['WB4BOR', 'K4STN1'], 'K4STN1', null, []);
const p3 = makePacket('p3', 'WB4BOR', ['WB4BOR', 'K4STN2'], 'K4STN2', null, []);
const p4 = makePacket('p4', 'WB4BOR', ['WB4BOR', 'K4STN3'], 'K4STN3', null, []);
offerPacketPath(p1); offerPacketPath(p2); offerPacketPath(p3);
const fourth = offerPacketPath(p4);
console.log('FOURTH ' + fourth);
console.log('ACTIVE ' + packetPaths.active.size);
console.log('QUEUE ' + packetPaths.queue.length);
console.log('DROPPED ' + packetPaths.dropped);
"""]))
    line = _lines(out)
    assert line["ACTIVE"] == "3", "cap must hold concurrent journeys at 3"
    assert line["QUEUE"] == "1", "the 4th concurrent packet is queued, not started"
    assert line["DROPPED"] == "0", "queue space exists, nothing dropped yet"


def test_dedupe_window_animates_once():
    """Same packet_id + stop signature inside 30 s animates exactly once."""
    if not shutil.which("node"):
        pytest.skip("node not installed")
    out = _node(_harness([SETUP, r"""
const a = makePacket('p1', 'WB4BOR', ['WB4BOR', 'APPOMX'], 'APPOMX', null, []);
const first = offerPacketPath(a);
const second = offerPacketPath(makePacket('p1', 'WB4BOR', ['WB4BOR', 'APPOMX'], 'APPOMX', null, []));
const fc = packetPaths.frame(Date.now() + 100);
console.log('FIRST ' + first);
console.log('SECOND ' + second);
console.log('JOURNEYS ' + new Set(fc.features.map((f) => f.properties.anim_id)).size);
console.log('SEEN ' + packetPaths.seen.size);
"""]))
    line = _lines(out)
    assert line["FIRST"] == "true" and line["SECOND"] == "false"
    assert line["JOURNEYS"] == "1", "dedupe must not start a second journey"
    assert line["SEEN"] == "1"


def test_missing_positions_are_skipped_not_invented():
    """A hop with no recent cached position produces no stop, no edge,
    and no feature that mentions the unknown callsign."""
    if not shutil.which("node"):
        pytest.skip("node not installed")
    out = _node(_harness([SETUP, r"""
const p = makePacket('p7', 'WB4BOR', ['WB4BOR', 'NOMAPHOP', 'APPOMX'], 'APPOMX', null, []);
console.log('OFFER ' + offerPacketPath(p));
const fc = packetPaths.frame(Date.now() + 100);
const segs = fc.features.filter((f) => f.properties.feature === 'segment');
console.log('SEG ' + segs.map((f) => f.properties.from_call + '>' + f.properties.to_call).join(','));
console.log('NOMAPMENTIONS ' + fc.features.filter((f) => JSON.stringify(f.properties).includes('NOMAPHOP')).length);
console.log('KNOWN ' + packetPaths.knownPosition('NOMAPHOP', p, Date.now()));
"""]))
    line = _lines(out)
    assert line["OFFER"] == "true"
    assert line["SEG"] == "WB4BOR>APPOMX", "unknown middle hop must not appear on any leg"
    assert line["NOMAPMENTIONS"] == "0"
    assert line["KNOWN"] in ("null", "false"), "knownPosition must not invent a position"


def test_aprsis_leg_and_layers():
    """Correlated TX/RX observations share one packet_id; the shared IGate
    reaches the TX IGate through the dashed aprsis internet style. Layers
    mount below stations-symbol-base and re-add after a style reload."""
    if not shutil.which("node"):
        pytest.skip("node not installed")
    out = _node(_harness([SETUP, r"""
offerPacketPath(makePacket('p9', 'WB4BOR', ['WB4BOR'], null, 'KM6LYW', ['KM6LYW']));
offerPacketPath(makePacket('p9', 'WB4BOR', ['WB4BOR', 'APPOMX'], 'APPOMX', null, []));
addPacketPathLayers();
const fc = packetPaths.frame(Date.now() + 2600);
const aprsis = fc.features.filter((f) => f.properties.feature === 'segment' && f.properties.kind === 'aprsis');
console.log('APRSIS ' + aprsis.length);
const dashed = addedLayers.filter((pair) => JSON.stringify(pair[0].filter).includes('aprsis'));
console.log('DASHED ' + (dashed.length > 0 && dashed.every((pair) => pair[0].paint['line-dasharray'] !== undefined)));
console.log('SOLIDRF ' + addedLayers.filter((pair) => JSON.stringify(pair[0].filter).includes('\"rf\"') && pair[0].type === 'line' && pair[0].paint['line-dasharray'] !== undefined).length);
console.log('BEFORES ' + addedLayers.map((pair) => String(pair[1])).join(','));
console.log('IDS ' + addedLayers.map((pair) => pair[0].id).join(','));
console.log('SOURCES ' + JSON.stringify(Object.keys(sources)));
addedLayers = []; sources = {};
addPacketPathLayers();
console.log('READDED ' + addedLayers.length);
"""]))
    line = _lines(out)
    assert int(line["APRSIS"]) >= 2, "rx->internet-node->tx internet legs"
    assert line["DASHED"] == "true", "aprsis line layers must carry line-dasharray"
    assert line["SOLIDRF"] == "0", "rf line layers must stay solid"
    ids = line["IDS"].split(",")
    assert len(set(ids)) == len(ids)
    for want in ("packet-path-line", "packet-path-internet", "packet-path-dot", "packet-path-pulse"):
        assert want in ids, f"missing layer {want}"
    befores = line["BEFORES"].split(",")
    assert all(b == "stations-symbol-base" for b in befores), "layers sit below the station symbols"
    assert line["SOURCES"] == '["packet-paths"]'
    assert line["READDED"] == str(len(ids)), "reload must re-add every layer (tx-arcs pattern)"


def test_one_setdata_per_frame():
    """Each rendered frame writes the packet-paths source exactly once."""
    if not shutil.which("node"):
        pytest.skip("node not installed")
    out = _node(_harness([SETUP, r"""
offerPacketPath(makePacket('p1', 'WB4BOR', ['WB4BOR', 'APPOMX'], 'APPOMX', null, []));
addPacketPathLayers();
setDataCalls.length = 0;
renderPacketPaths();
renderPacketPaths();
console.log('SETDATA ' + setDataCalls.length);
console.log('FEATURES ' + setDataCalls[0].features.length);
"""]))
    line = _lines(out)
    assert line["SETDATA"] == "2", "exactly one setData per render()"
    assert int(line["FEATURES"]) > 0, "frame near start must emit features"


def test_on_packet_hook_and_style_reload_mount():
    """Source asserts: onPacket feeds the animator and style.load re-adds
    the layers the way tx-arcs does."""
    js = APP_JS.read_text(encoding="utf-8")
    assert "offerPacketPath(packet)" in js, "onPacket must feed the animator"
    assert "map.on('style.load', addPacketPathLayers)" in js, "style reload re-add"
