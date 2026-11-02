"""Executable tests for app.js ``parsePath`` / ``updatePositionCache`` (issue #205).

Follows the node-harness pattern of ``test_app_settings_save.py``: the real
functions plus the constants they reference are extracted from ``app.js`` and
run under node, driven by the shared fixtures in ``tests/fixtures/packet_paths.json``.

The audited ``parsePath`` kept only ``*``-marked hops (``multi_stars`` loses the
unmarked ``K4DG2``) and treated any ``qA*`` construct as an igate (``qax_internet``
invents ``T2TEST`` as an RF hop; ``qai_servers`` invents ``LONGSERVER``). The
conformed behaviour follows ``packet_paths.py::_route()``: used hops are all
calls up to the last ``*`` before the first ``qA`` construct, igate only from
``qAR``/``qAO``/``qAS``, ``isTcpip`` mirrors ``no_rf_leg`` (``qAC``/``qAX``/
``qAU`` or a ``TCPIP`` token on the RF leg), and the full shared alias set.
Client shape stays ``{digipeaters, igate, isTcpip}`` — what every caller reads.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import direwolf_dashboard

APP_JS = Path(direwolf_dashboard.__file__).parent / "static" / "app.js"
FIXTURES = Path(__file__).parent / "fixtures" / "packet_paths.json"

# Only the station lookup needs a stub; the cache, constants and parsePath
# itself come from app.js.
HARNESS_PRELUDE = """
const stations = {
    'K4ST': { data: { latitude: 42.5, longitude: -71.2 } },
};
"""


def _node(harness: str) -> str:
    result = subprocess.run(
        ["node", "-e", harness], capture_output=True, text=True
    )
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


def _extract_const(js: str, pattern: str) -> str:
    m = re.search(pattern, js, re.DOTALL)
    assert m, f"missing const matching {pattern}"
    return m.group(0)


def _extract_if_present(js: str, name: str) -> str:
    return _extract(js, name) if f"function {name}(" in js else ""


JS_DRIVER = r"""
function pathTokens(raw) {
    const colon = raw.indexOf(':');
    if (colon < 0) return [];
    const header = raw.slice(0, colon);
    const gt = header.indexOf('>');
    if (gt < 0) return [];
    const parts = header.slice(gt + 1).split(',');
    return parts.length > 1 ? parts.slice(1) : [];
}
function collapse(arr) {
    return arr.filter((c, i) => i === 0 || c !== arr[i - 1]);
}
for (const f of fixtures) {
    const exp = f.expected;
    const got = parsePath(pathTokens(f.raw));
    const thirdParty = f.raw.indexOf('}') >= 0;
    const checks = [];
    if (thirdParty) {
        if (exp.tx_hops) checks.push(['digi', got.digipeaters, exp.tx_hops.slice(1)]);
    } else {
        const src = f.raw.slice(0, f.raw.indexOf('>'));
        const hops = collapse([src].concat(got.digipeaters, got.igate ? [got.igate] : []));
        checks.push(['hops', hops, exp.hops]);
    }
    if ('rx_igate' in exp) checks.push(['igate', got.igate, exp.rx_igate]);
    if ('no_rf_leg' in exp) checks.push(['tcpip', got.isTcpip, exp.no_rf_leg]);
    const bad = checks
        .map(([n, g, w]) => (JSON.stringify(g) === JSON.stringify(w) ? null : n + ' got ' + JSON.stringify(g) + ' want ' + JSON.stringify(w)))
        .filter((line) => line);
    if (bad.length) console.log('FAIL ' + f.id + ': ' + bad.join('; '));
    else console.log('PASS ' + f.id);
}
"""


def _fixture_harness(tail_js: str = "") -> str:
    js = APP_JS.read_text(encoding="utf-8")
    fixtures = json.dumps(json.loads(FIXTURES.read_text(encoding="utf-8")))
    return "\n".join([
        HARNESS_PRELUDE,
        _extract_const(js, r"const stationPositionCache = [^;]*;"),
        _extract_const(js, r"const POSITION_CACHE_MAX = \d+;"),
        _extract_const(js, r"const GENERIC_PATH_ALIASES = new Set\(\[.*?\]\);"),
        _extract_if_present(js, "isPathAlias"),
        _extract(js, "parsePath"),
        _extract(js, "updatePositionCache"),
        _extract(js, "getStationPosition"),
        f"const fixtures = {fixtures};",
        JS_DRIVER,
        tail_js,
    ])


def test_client_parse_path_matches_shared_fixtures():
    """parsePath on client-visible path tokens agrees with every fixture."""
    if not shutil.which("node"):
        pytest.skip("node not installed")
    out = _node(_fixture_harness())
    failures = [ln for ln in out.splitlines() if ln.startswith("FAIL")]
    assert not failures, "\n".join(failures)


CACHE_TAIL = r"""
updatePositionCache('A', 1, 1);
updatePositionCache('B', 2, 2);
updatePositionCache('C', 3, 3);
updatePositionCache('D', 4, 4);
console.log('SIZE ' + stationPositionCache.size);
console.log('ORDER ' + JSON.stringify([...stationPositionCache.keys()]));
console.log('A ' + JSON.stringify(getStationPosition('A')));
updatePositionCache('B', 20, 20);
updatePositionCache('E', 5, 5);
console.log('ORDER2 ' + JSON.stringify([...stationPositionCache.keys()]));
console.log('C ' + JSON.stringify(getStationPosition('C')));
console.log('B ' + JSON.stringify(getStationPosition('B')));
updatePositionCache('Z', null, null);
console.log('Z ' + JSON.stringify(getStationPosition('Z')));
"""


def test_position_cache_is_map_ordered_lru():
    """The position cache is a Map: insertion-order LRU eviction, writes refresh,
    missing positions dropped, capacity conformed to 512."""
    if not shutil.which("node"):
        pytest.skip("node not installed")
    js = APP_JS.read_text(encoding="utf-8")
    assert "const stationPositionCache = new Map();" in js
    assert "const POSITION_CACHE_MAX = 512;" in js
    cap3 = re.sub(r"\d+", "3", _extract_const(js, r"const POSITION_CACHE_MAX = \d+;"))
    harness = "\n".join([
        HARNESS_PRELUDE,
        _extract_const(js, r"const stationPositionCache = [^;]*;"),
        cap3,
        _extract(js, "updatePositionCache"),
        _extract(js, "getStationPosition"),
        CACHE_TAIL,
    ])
    out = _node(harness)
    line = {ln.split(" ", 1)[0]: ln.split(" ", 1)[1] for ln in out.strip().splitlines()}
    assert line["SIZE"] == "3"
    assert line["ORDER"] == '["B","C","D"]', "insertion-order eviction must drop A"
    assert line["A"] == "null" and line["C"] == "null" and line["Z"] == "null"
    assert line["ORDER2"] == '["D","B","E"]', "rewriting B must refresh its LRU slot"
    assert json.loads(line["B"]) == {"lat": 20, "lng": 20}
