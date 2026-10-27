"""Executable tests for the browser settings save path (issue #172).

The audited revision replaced the whole ``station`` section with the submitted
values, which dropped ``station.my_position`` after every ordinary settings
save. These tests run the real ``saveSettings`` / ``saveMyPosition`` /
``getMyPosition`` functions from ``app.js`` under node with a stub DOM, so the
merge behaviour is exercised as written.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import direwolf_dashboard

APP_JS = Path(direwolf_dashboard.__file__).parent / "static" / "app.js"

HARNESS_PRELUDE = """
const API_BASE = '/api';
const API_TOKEN_KEY = 'dw_api_token';
const UNITS_KEY = 'dw_units';
let showRouteDistances = false;
let showGpxOverlay = false;
let showStatsOverlay = false;
let showMyLocationOverlay = false;
let showTimestamps = true;
let gpxControlEl = null;
let statsOverlayEl = null;
let myLocationOverlayEl = null;
let waitingForPosition = false;
const stations = {};
const mapFlyTo = () => {};
const toMlZoom = (z) => z;
const updateMyPositionMarker = () => {};
const updateStationPopup = () => {};
const dismissWaiting = () => {};
const _saveLogColors = () => {};
const _apiHeaders = (h) => h;
const els = {};
function _el(id) {
    if (!els[id]) {
        els[id] = {
            value: '', checked: false, className: '', textContent: '',
            classList: { add() {}, remove() {} },
            addEventListener() {},
        };
    }
    return els[id];
}
const document = { getElementById: _el, querySelectorAll: () => [] };
const _storage = {};
const localStorage = {
    getItem: (k) => (k in _storage ? _storage[k] : null),
    setItem: (k, v) => { _storage[k] = String(v); },
    removeItem: (k) => { delete _storage[k]; },
};
let config = {
    station: {
        latitude: 1,
        longitude: 2,
        zoom: 12,
        my_position: { type: 'pin', latitude: 38, longitude: -78 },
    },
    direwolf: {
        agw_host: 'old-host',
        agw_port: 8000,
        log_file: '/tmp/direwolf.log',
        keep_this_field: 'untouched',
    },
    storage: { retention_days: 7, keep_this_too: true },
};
const _responses = [];
const fetch = async () => ({
    ok: true,
    json: async () => ({ updated: ['station', 'direwolf', 'storage'], restart_required: false }),
});
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


def _harness(tail: str) -> str:
    js = APP_JS.read_text(encoding="utf-8")
    return "\n".join([
        HARNESS_PRELUDE,
        _extract(js, "saveSettings"),
        _extract(js, "saveMyPosition"),
        _extract(js, "getMyPosition"),
        "(async () => {",
        tail,
        "})();",
    ])


def test_save_settings_keeps_position_and_untouched_fields():
    """An ordinary save applies submitted fields and keeps everything else."""
    if not shutil.which("node"):
        pytest.skip("node not installed")

    out = _node(_harness("""
        _el('cfg-latitude').value = '38.5';
        _el('cfg-longitude').value = '-77.5';
        _el('cfg-zoom').value = '13';
        _el('cfg-agw-host').value = 'new-host';
        _el('cfg-agw-port').value = '8001';
        _el('cfg-log-file').value = '/tmp/direwolf.log';
        _el('cfg-server-port').value = '8080';
        _el('cfg-api-token').value = '';
        _el('cfg-units').value = 'imperial';
        _el('cfg-retention').value = '9';
        _el('cfg-show-timestamps').checked = true;
        await saveSettings();
        console.log('POS ' + JSON.stringify(getMyPosition()));
        console.log('STATION ' + JSON.stringify(config.station));
        console.log('DIREWOLF ' + JSON.stringify(config.direwolf));
        console.log('STORAGE ' + JSON.stringify(config.storage));
    """))

    line = {ln.split(" ", 1)[0]: ln.split(" ", 1)[1] for ln in out.strip().splitlines()}
    assert json.loads(line["POS"]) == {"lat": 38, "lng": -78}
    station = json.loads(line["STATION"])
    assert station["latitude"] == 38.5 and station["longitude"] == -77.5
    assert station["zoom"] == 13
    assert station["my_position"] == {"type": "pin", "latitude": 38, "longitude": -78}
    direwolf = json.loads(line["DIREWOLF"])
    assert direwolf["agw_host"] == "new-host" and direwolf["agw_port"] == 8001
    assert direwolf["keep_this_field"] == "untouched"
    assert json.loads(line["STORAGE"])["keep_this_too"] is True


def test_clearing_position_explicitly_still_clears_it():
    """Explicit removal must keep clearing the saved position."""
    if not shutil.which("node"):
        pytest.skip("node not installed")

    out = _node(_harness("""
        await saveMyPosition(null);
        console.log('POS ' + JSON.stringify(getMyPosition()));
        console.log('RAW ' + JSON.stringify(config.station.my_position));
    """))

    assert "POS null" in out
    assert "RAW null" in out
