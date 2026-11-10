"""Served-page geometry for the map's corners (issue #218 rounds 11 and 12).

Round 9's guard rendered only ``style.css`` in a stitched fixture, so it never
saw two things the browser really does:

* it supplied ``--corner-open-max`` itself, so deleting the app.js call that
  sets it left the suite green (NB-2),
* it asserted a 12px scrollbar gutter on a page that had none (NB-1).

These tests start the real app with the real ``index.html``, ``app.js`` and
``style.css``, open the Filters card the way a person does, and measure the
boxes the browser laid out. Chromium is driven over the DevTools protocol
(``websockets`` is already a runtime dependency), so continuous integration
needs only the Chromium binary it already installs.

Round 11 stopped trusting the UA scrollbar: two reviewers read different
pixels for the same head (172/172 with no bar, 172/184 with a 12px one), so
the page hides the platform bar and paints its own track and thumb. The tests
below assert that no platform gutter is reserved at all, that the drawn
indicator exists in a gutter clear of the rows, and that it moves on scroll.
Round 13 swept eleven viewports and named its pairs by hand, so both reviewers
still found seven overlaps at that head. Round 14 sweeps the whole reviewed
matrix (11 widths x 8 heights plus 560x720 and 560x800) in both themes with
the reviewers' 170px-wide legend, hits every control centre, and reads the
waiting message from the page's own layout.
"""
import json
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
from contextlib import contextmanager

import httpx
import pytest
import uvicorn
from websockets.sync.client import connect

CHROMIUM = (
    shutil.which("chromium")
    or shutil.which("chromium-browser")
    or shutil.which("google-chrome")
    or shutil.which("chrome")
)

pytestmark = pytest.mark.skipif(
    CHROMIUM is None, reason="served-page guard needs a headless Chromium"
)

READY = ("!!document.querySelector('.filter-overlay')"
         " && !!document.getElementById('map-container')")

MEASURE = r"""
(() => {
  const r = (el) => { const b = el.getBoundingClientRect();
    return {top: +b.top.toFixed(1), bottom: +b.bottom.toFixed(1),
            left: +b.left.toFixed(1), right: +b.right.toFixed(1),
            width: +b.width.toFixed(1), height: +b.height.toFixed(1)}; };
  const mc = document.getElementById('map-container');
  const card = document.querySelector('.filter-overlay');
  const body = document.querySelector('.filter-overlay-body');
  const strip = document.querySelector('.maplibregl-ctrl-bottom-right');
  const track = document.querySelector('.corner-scroll-track');
  const thumb = document.querySelector('.corner-scroll-thumb');
  const rows = [...document.querySelectorAll(
    '.filter-overlay-body .filter-overlay-row')];
  const cs = body ? getComputedStyle(body) : null;
  const shown = (el) => !!el && getComputedStyle(el).display !== 'none';
  return {
    inner: [innerWidth, innerHeight],
    map: r(mc),
    mapClientH: mc.clientHeight,
    cap: mc.style.getPropertyValue('--corner-open-max').trim(),
    compact: mc.classList.contains('corner-compact'),
    card: card ? r(card) : null,
    mylocHeight: document.querySelector('.my-location-overlay')
      ? r(document.querySelector('.my-location-overlay')).height : null,
    body: body ? {...r(body), clientH: body.clientHeight,
                  scrollH: body.scrollHeight,
                  clientW: body.clientWidth, offsetW: body.offsetWidth,
                  overflowY: cs.overflowY, scrollTop: body.scrollTop,
                  padRight: cs.paddingRight} : null,
    scrollable: card ? card.classList.contains('corner-scrollable') : null,
    track: track && shown(track)
      ? {...r(track), bg: getComputedStyle(track).backgroundColor} : null,
    thumb: thumb && shown(thumb)
      ? {...r(thumb), bg: getComputedStyle(thumb).backgroundColor} : null,
    cardBg: card ? getComputedStyle(card).backgroundColor : null,
    rowRight: rows.length ? Math.max(...rows.map((el) => r(el).right)) : null,
    strip: strip ? r(strip) : null,
    lastRowBottom: rows.length ? r(rows[rows.length - 1]).bottom : null,
  };
})()
"""


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    """The real app on a loopback port, with a throwaway data directory."""
    from direwolf_dashboard.config import load_config
    from direwolf_dashboard.server import create_app

    data_dir = tmp_path_factory.mktemp("served-data")
    cfg_path = tmp_path_factory.mktemp("served-cfg") / "dash.yaml"
    port = _free_port()
    cfg_path.write_text(
        f"data_dir: {data_dir}\nserver:\n  host: 127.0.0.1\n  port: {port}\n",
        encoding="utf-8")
    app = create_app(load_config(str(cfg_path)), str(cfg_path))
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}/"
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=1).status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.2)
    else:
        server.should_exit = True
        raise AssertionError("the app did not answer on loopback")
    yield url
    server.should_exit = True
    thread.join(timeout=15)


class _CDP:
    def __init__(self, ws):
        self.ws = ws
        self.next_id = 0

    def call(self, method: str, **params):
        self.next_id += 1
        self.ws.send(json.dumps(
            {"id": self.next_id, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.next_id:
                if "error" in msg:
                    raise AssertionError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def eval(self, expression: str):
        result = self.call("Runtime.evaluate", expression=expression,
                           returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in result:
            raise AssertionError(result["exceptionDetails"])
        return result["result"].get("value")


@contextmanager
def _page(url: str, width: int, height: int, theme: str = "dark"):
    """A headless Chromium page at an exact viewport, loaded and ready."""
    profile = tempfile.mkdtemp(prefix="cdp-profile-")
    port = _free_port()
    proc = subprocess.Popen(
        [CHROMIUM, "--headless=new", "--no-sandbox", "--disable-gpu",
         # No --hide-scrollbars here: Chromium treats the switch as "present"
         # and paints overlay bars, which is exactly the round-9 blind spot.
        "--disable-dev-shm-usage",
         f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
         "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        ws_url = None
        deadline = time.time() + 20
        while time.time() < deadline and ws_url is None:
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/json/list", timeout=1) as fh:
                    pages = [t for t in json.load(fh) if t["type"] == "page"]
                if pages:
                    ws_url = pages[0]["webSocketDebuggerUrl"]
                    break
            except Exception:
                pass
            time.sleep(0.1)
        assert ws_url, "Chromium never exposed a debugging target"
        with connect(ws_url, max_size=32 * 1024 * 1024) as ws:
            cdp = _CDP(ws)
            cdp.call("Page.enable")
            cdp.call("Runtime.enable")
            cdp.call("Emulation.setDeviceMetricsOverride", width=width,
                     height=height, deviceScaleFactor=1, mobile=False)
            cdp.call("Page.navigate", url=url)
            deadline = time.time() + 40
            ready = False
            while time.time() < deadline:
                try:
                    if cdp.eval(READY):
                        ready = True
                        break
                except Exception:
                    pass
                time.sleep(0.1)
            assert ready, "the served page never built the map cards"
            cdp.eval(
                "(() => { const m = document.getElementById('waiting-modal');"
                " if (m && !m.classList.contains('hidden')) {"
                " const b = document.getElementById('btn-close-waiting');"
                " if (b) b.click(); } })()")
            cdp.eval(
                f"document.documentElement.setAttribute('data-theme', {theme!r})")
            time.sleep(0.4)
            yield cdp
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def _open_filters(cdp: _CDP) -> None:
    cdp.eval("document.querySelector('.filter-overlay-header').click()")
    time.sleep(0.4)


def test_served_page_sets_the_corner_cap_itself(served):
    """NB-2: the cap must come from the running page, not from the fixture."""
    with _page(served, 1100, 432) as cdp:
        data = cdp.eval(MEASURE)
    assert data["compact"] is True, data["mapClientH"]
    cap = data["cap"]
    assert cap.endswith("px"), f"app.js set no --corner-open-max: {cap!r}"
    assert int(cap[:-2]) == data["mapClientH"] - 12, (
        f"cap {cap} does not follow the {data['mapClientH']}px map")


def test_served_attribution_strip_clears_the_open_filter_card(served):
    """Defect A: the opened card steps left of the button rail, so the strip
    needs the wider open-state inset; 8px minimum at every failing size."""
    failures = []
    for width, height in ((1100, 432), (1100, 431), (1100, 420),
                          (1100, 400), (1024, 400)):
        with _page(served, width, height) as cdp:
            _open_filters(cdp)
            data = cdp.eval(MEASURE)
        card, strip = data["card"], data["strip"]
        clearance = card["left"] - strip["right"]
        if clearance < 8:
            failures.append(
                f"{width}x{height}: strip right {strip['right']} vs card left "
                f"{card['left']} = {clearance:.1f}px")
    assert not failures, failures


def test_served_attribution_strip_clears_the_card_in_light_theme(served):
    with _page(served, 1100, 432, theme="light") as cdp:
        _open_filters(cdp)
        data = cdp.eval(MEASURE)
    assert data["card"]["left"] - data["strip"]["right"] >= 8, data


def test_served_filter_panel_fits_short_windows_without_scrolling(served):
    """Round 11 / defect B: at 1100x432 and taller the whole panel is present
    at rest, so nobody meets an indicator there. 9 rows of 19px + 4px of body
    padding = 175px, which the 180px body of a 432px window holds."""
    for width, height in ((1100, 432), (1100, 480), (1280, 800)):
        with _page(served, width, height) as cdp:
            _open_filters(cdp)
            data = cdp.eval(MEASURE)
        body = data["body"]
        where = f"{width}x{height}"
        assert body is not None, where
        assert body["scrollH"] <= body["clientH"], (
            f"{where}: the panel still overflows "
            f"({body['scrollH']} > {body['clientH']})")
        assert data["lastRowBottom"] <= body["bottom"], (
            f"{where}: last row is cut at rest")
        assert data["scrollable"] is False, where
        assert data["track"] is None and data["thumb"] is None, (
            f"{where}: an indicator shows although nothing scrolls")


def test_served_short_window_paints_its_own_scroll_indicator(served):
    """Defect B: the page never relies on the UA scrollbar. The body reserves
    no platform gutter (offsetW == clientW), the page paints a track and a
    thumb into a 16px gutter of its own, the rows stop short of that gutter,
    and the thumb moves when the body scrolls."""
    for width, height in ((1100, 420), (1100, 400), (1024, 400)):
        for theme in ("dark", "light"):
            where = f"{width}x{height} {theme}"
            with _page(served, width, height, theme=theme) as cdp:
                _open_filters(cdp)
                data = cdp.eval(MEASURE)
                assert data["body"]["scrollH"] > data["body"]["clientH"], where
                assert data["body"]["offsetW"] == data["body"]["clientW"], (
                    f"{where}: a platform scrollbar reserved "
                    f"{data['body']['offsetW'] - data['body']['clientW']}px")
                assert data["scrollable"] is True, where
                track, thumb = data["track"], data["thumb"]
                assert track and track["width"] >= 4, (where, track)
                assert thumb and thumb["height"] >= 18, (where, thumb)
                assert data["body"]["right"] - data["rowRight"] >= 12, (
                    f"{where}: no reserved gutter: rows reach "
                    f"{data['rowRight']} of {data['body']['right']}")
                assert data["rowRight"] <= track["left"] - 1, (
                    f"{where}: the indicator would sit on row text")
                assert thumb["bg"] != data["cardBg"], (where, thumb["bg"])
                before = thumb["top"]
                cdp.eval("(() => { const b = document.querySelector("
                         "'.filter-overlay-body');"
                         " b.scrollTop = b.scrollHeight; })()")
                time.sleep(0.3)
                after = cdp.eval(MEASURE)
                assert after["body"]["scrollTop"] > 0, after["body"]
                assert after["thumb"]["top"] > before, (
                    f"{where}: the thumb did not move: "
                    f"{before} -> {after['thumb']['top']}")
                assert after["lastRowBottom"] <= after["body"]["bottom"] + 0.5, (
                    f"{where}: the last row is unreachable")


def test_served_open_card_stays_inside_the_map_and_hides_the_other(served):
    """Replaces the fixture's opened-card tests, which supplied their own cap."""
    with _page(served, 1100, 400) as cdp:
        _open_filters(cdp)
        data = cdp.eval(MEASURE)
    assert data["card"]["bottom"] <= data["map"]["bottom"] - 4, data
    assert data["mylocHeight"] == 0, "opening Filters must hide My Location"


# Round 12: two overlaps a person can see, measured on the served page because
# both placements are computed by app.js. The weather-legend body is filled
# here with a 480px block (the dev data's own row is 480px) so the check does
# not depend on the throwaway data directory having weather stations.
# --- Round 14 -------------------------------------------------------------
# Round 13's guard swept eleven viewports and only asserted pairs it named.
# Both reviewers found seven more defects at that head, so this guard measures
# the real served page at every viewport the issue reviews (11 widths x 8
# heights plus 560x720 and 560x800) in both themes, with a weather-legend body
# at the 170px width both reviewers measured. It fails on any pair a person can
# see, on any control whose centre a click does not reach, on a left column that
# scrolls, or on a waiting message that is not a row of the layout.
ROUND14_VIEWPORTS = [
    (w, h)
    for w in (320, 344, 360, 375, 400, 480, 560, 1024, 1100, 1280, 1600)
    for h in (400, 432, 480, 568, 600, 720, 800, 900)
] + [(560, 720), (560, 800)]

ROUND14_FIXTURE = r"""
(() => {
  // Content only: the weather rows the dev page shows (480px tall) at a width
  // that holds the legend at its 170px max-width — the size at which both
  // reviewers measured 1836.0 / 3120.8 / 2988.0 / 1568.8. Every position is
  // still the page's own decision; nothing here places a box.
  const wx = document.getElementById('legend-wx-body');
  if (wx) wx.innerHTML = '<div style="width:146px;height:480px">weather</div>';
  window.dispatchEvent(new Event('resize'));
  return true;
})()
"""

ROUND14 = r"""
(() => {
  const r = (el) => { if (!el) return null; const b = el.getBoundingClientRect();
    return {top: +b.top.toFixed(1), bottom: +b.bottom.toFixed(1),
            left: +b.left.toFixed(1), right: +b.right.toFixed(1),
            width: +b.width.toFixed(1), height: +b.height.toFixed(1)}; };
  const share = (a, b) => {
    if (!a || !b) return 0;
    const w = Math.min(a.right, b.right) - Math.max(a.left, b.left);
    const h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
    return (w > 0 && h > 0) ? +(w * h).toFixed(1) : 0;
  };
  const vis = (el) => {
    if (!el) return false;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    const b = el.getBoundingClientRect();
    return b.width > 0 && b.height > 0;
  };
  const el = {
    toast: document.getElementById('waiting-toast'),
    filter: document.querySelector('.filter-overlay'),
    myloc: document.querySelector('.my-location-overlay'),
    gpx: document.querySelector('.gpx-control'),
    loadGpx: document.querySelector('.gpx-btn-load'),
    legend: document.querySelector('.map-legend'),
    rail: document.querySelector('.maplibregl-ctrl-top-left'),
    zoom: document.querySelector(
      '.maplibregl-ctrl-top-left .maplibregl-ctrl-group'),
    readout: document.querySelector('.map-zoom-readout'),
    attrib: document.querySelector(
      '.maplibregl-ctrl-bottom-right .maplibregl-ctrl-attrib'),
    stats: document.querySelector('.stats-overlay'),
    strip: document.getElementById('map-resize-handle'),
    fabClear: document.getElementById('btn-clear-data'),
    fabCenter: document.getElementById('btn-center-my-pos'),
    map: document.getElementById('map-container'),
    toolrow: document.getElementById('toolrow'),
    toolsRight: document.querySelector('.toolrow-right'),
    find: document.getElementById('dw-find'),
    btnTools: document.getElementById('btn-tools'),
  };
  const b = {};
  for (const k in el) b[k] = r(el[k]);
  // What a click at a control's own centre reaches.
  const hits = {};
  const hitWhat = {};
  for (const k of ['filter', 'myloc', 'gpx', 'loadGpx', 'legend', 'stats',
                   'zoom', 'fabClear', 'fabCenter', 'find', 'btnTools']) {
    if (!vis(el[k])) { hits[k] = null; continue; }
    const box = b[k];
    const top = document.elementFromPoint(
      Math.round((box.left + box.right) / 2),
      Math.round((box.top + box.bottom) / 2));
    hits[k] = !!top && (el[k] === top || el[k].contains(top)
                        || top.contains(el[k]));
    hitWhat[k] = top ? (top.id || top.className || top.tagName) : null;
  }
  const pairs = {
    toast_filter: share(b.toast, b.filter),
    toast_myloc: share(b.toast, b.myloc),
    toast_gpx: share(b.toast, b.gpx),
    toast_loadGpx: share(b.toast, b.loadGpx),
    toast_legend: share(b.toast, b.legend),
    toast_stats: share(b.toast, b.stats),
    toast_fabClear: share(b.toast, b.fabClear),
    toast_fabCenter: share(b.toast, b.fabCenter),
    toast_strip: share(b.toast, b.strip),
    toast_toolrow: share(b.toast, b.toolrow),
    legend_zoom: share(b.legend, b.zoom),
    legend_readout: share(b.legend, b.readout),
    legend_rail: share(b.legend, b.rail),
    legend_gpx: share(b.legend, b.gpx),
    legend_stats: share(b.legend, b.stats),
    legend_filter: share(b.legend, b.filter),
    legend_myloc: share(b.legend, b.myloc),
    legend_attrib: share(b.legend, b.attrib),
    legend_fabClear: share(b.legend, b.fabClear),
    legend_fabCenter: share(b.legend, b.fabCenter),
    legend_strip: share(b.legend, b.strip),
    gpx_strip: share(b.gpx, b.strip),
    gpx_stats: share(b.gpx, b.stats),
    loadGpx_strip: share(b.loadGpx, b.strip),
    stats_strip: share(b.stats, b.strip),
    stats_myloc: share(b.stats, b.myloc),
    fab_filter: share(b.fabClear, b.filter) + share(b.fabCenter, b.filter),
    fab_myloc: share(b.fabClear, b.myloc) + share(b.fabCenter, b.myloc),
    fab_attrib: share(b.fabClear, b.attrib) + share(b.fabCenter, b.attrib),
    fab_strip: share(b.fabClear, b.strip) + share(b.fabCenter, b.strip),
    fab_legend: share(b.fabClear, b.legend) + share(b.fabCenter, b.legend),
    find_tools: share(b.find, b.toolsRight),
  };
  const toastCS = el.toast ? getComputedStyle(el.toast) : null;
  return {
    viewport: [innerWidth, innerHeight],
    boxes: b,
    hits: hits,
    hitWhat: hitWhat,
    pairs: pairs,
    bandsBottom: Math.max(...['topnav', 'toolrow'].map((id) => {
      const band = document.getElementById(id);
      return band ? r(band).bottom : 0; })),
    toastPosition: toastCS ? toastCS.position : null,
    statsTight: el.map.classList.contains('stats-tight'),
    legendTight: el.legend.classList.contains('legend-tight'),
    legendHidden: el.legend.classList.contains('legend-hidden'),
    fabRow: el.map.classList.contains('fab-row'),
    railScroll: [el.rail.clientHeight, el.rail.scrollHeight],
    shown: {toast: vis(el.toast), gpx: vis(el.gpx), legend: vis(el.legend),
            stats: vis(el.stats), fabClear: vis(el.fabClear),
            find: vis(el.find), btnTools: vis(el.btnTools)},
  };
})()
"""

_CELL = {"n": 0}


@pytest.fixture(scope="module")
def browser():
    """One headless Chromium for every cell of the round-14 matrix.

    Round 13 opened a browser per viewport; the matrix is 90 windows in two
    themes, so one page is reused and only the viewport, the theme and the
    page storage change between cells. Clearing the storage is what a fresh
    browser profile does: the app remembers its map view, and a remembered view
    skips the cold-start waiting message that half of these checks are about.
    """
    profile = tempfile.mkdtemp(prefix="cdp-round14-")
    port = _free_port()
    proc = subprocess.Popen(
        [CHROMIUM, "--headless=new", "--no-sandbox", "--disable-gpu",
         "--disable-dev-shm-usage",
         f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
         "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        ws_url = None
        deadline = time.time() + 20
        while time.time() < deadline and ws_url is None:
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/json/list", timeout=1) as fh:
                    pages = [t for t in json.load(fh) if t["type"] == "page"]
                if pages:
                    ws_url = pages[0]["webSocketDebuggerUrl"]
                    break
            except Exception:
                pass
            time.sleep(0.1)
        assert ws_url, "Chromium never exposed a debugging target"
        with connect(ws_url, max_size=64 * 1024 * 1024) as ws:
            cdp = _CDP(ws)
            cdp.call("Page.enable")
            cdp.call("Runtime.enable")
            yield cdp
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)


def _cell(cdp, url, width, height, theme="dark"):
    """One reviewed cell, retried once if the waiting message never appeared.

    The cold-start modal is shown by the page's own boot sequence, so a slow
    first paint can leave it later than the one fixed settle used here. A cell
    that really never shows it fails twice.
    """
    for _ in range(2):
        data = _cell_once(cdp, url, width, height, theme)
        if data["shown"]["toast"]:
            break
    return data


def _cell_once(cdp, url, width, height, theme="dark"):
    """One reviewed cell: the served page at an exact viewport, measured."""
    # Stop the old page first: it keeps writing dw-map-lat/lng, and a remembered
    # map view skips the cold-start modal — and with it the waiting message
    # every one of these cells is about.
    cdp.call("Page.navigate", url="about:blank")
    cdp.call("Storage.clearDataForOrigin", origin=url.rstrip("/"),
             storageTypes="local_storage")
    cdp.call("Emulation.setDeviceMetricsOverride", width=width, height=height,
             deviceScaleFactor=1, mobile=False)
    _CELL["n"] += 1
    mark = f"?cell={_CELL['n']}"
    cdp.call("Page.navigate", url=url + mark)
    deadline = time.time() + 40
    ready = False
    while time.time() < deadline:
        try:
            if cdp.eval(f"location.search === {mark!r} && "
                        "document.readyState === 'complete' && " + READY):
                ready = True
                break
        except Exception:
            pass
        time.sleep(0.05)
    assert ready, f"{width}x{height}: the page never built the map cards"
    # The modal appears once the map has initialised; a person waits for it and
    # closes it, and that is what shows the waiting message.
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            if cdp.eval(
                    "(() => { const m = document.getElementById("
                    "'waiting-modal'); return !!m && "
                    "!m.classList.contains('hidden'); })()"):
                break
        except Exception:
            pass
        time.sleep(0.05)
    cdp.eval(
        "(() => { const m = document.getElementById('waiting-modal');"
        " if (m && !m.classList.contains('hidden')) {"
        " const b = document.getElementById('btn-close-waiting');"
        " if (b) b.click(); } })()")
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            if cdp.eval(
                    "(() => { const t = document.getElementById("
                    "'waiting-toast'); return !!t && "
                    "!t.classList.contains('hidden'); })()"):
                break
        except Exception:
            pass
        time.sleep(0.05)
    cdp.eval(
        f"document.documentElement.setAttribute('data-theme', {theme!r})")
    cdp.eval(ROUND14_FIXTURE)
    time.sleep(0.35)
    return cdp.eval(ROUND14)


def _corner_failures(data, where):
    """Everything visibly wrong in one cell, as sentences."""
    bad = []
    for pair, value in data["pairs"].items():
        if value:
            bad.append(f"{where}: {pair} = {value} px2")
    for key, ok in data["hits"].items():
        if ok is False:
            bad.append(f"{where}: a click at the {key} centre reaches "
                       f"{data['hitWhat'][key]} instead")
    if data["shown"]["toast"] is not True:
        bad.append(f"{where}: the waiting message is not shown")
    if data["railScroll"][0] != data["railScroll"][1]:
        bad.append(f"{where}: the left column scrolls "
                   f"({data['railScroll'][0]} of {data['railScroll'][1]})")
    if data["boxes"]["gpx"]["bottom"] > data["boxes"]["map"]["bottom"]:
        bad.append(f"{where}: the GPX control ends at "
                   f"{data['boxes']['gpx']['bottom']} against a map bottom of "
                   f"{data['boxes']['map']['bottom']}")
    return bad


@pytest.mark.parametrize("theme", ("dark", "light"))
def test_served_nothing_is_covered_at_any_reviewed_size(served, browser, theme):
    """Round 14: the full matrix, both themes, nothing covered anywhere.

    The seven defects both reviewers found at 12edb83 are all in this sweep: a
    waiting message over the GPX panel and its Load button (802.2 / 330.2 at
    344x800, 715 / 279 at 360x800, 632.2 / 230.4 at 375x800), the same message
    over the legend (3120.8 / 2988.0 / 1568.8 at the same sizes), the legend
    over My Location (1836.0 at 320x568 with Stats collapsed), the GPX panel
    over the 8px resize strip (315.8 at 1024x400 and 1100x400), the two round
    map buttons over the credit pill (141 at 560x400 and 560x480), the Find
    input under the tool buttons (1488) and the TOOLS button off the 320px
    window.
    """
    failures = []
    for width, height in ROUND14_VIEWPORTS:
        data = _cell(browser, served, width, height, theme)
        where = f"{width}x{height} {theme}"
        failures += _corner_failures(data, where)
        # The waiting message is a row of the layout: it spans the page under
        # both bands and ends where the map starts, in every window.
        toast = data["boxes"]["toast"]
        if data["toastPosition"] != "static":
            failures.append(f"{where}: the waiting message floats "
                            f"({data['toastPosition']})")
        if toast["left"] != 0 or toast["right"] != data["viewport"][0]:
            failures.append(f"{where}: the waiting message spans "
                            f"{toast['left']}..{toast['right']} of "
                            f"{data['viewport'][0]}")
        if toast["top"] < data["bandsBottom"] - 0.5:
            failures.append(f"{where}: the waiting message starts at "
                            f"{toast['top']}, above the bands at "
                            f"{data['bandsBottom']}")
        if toast["bottom"] > data["boxes"]["map"]["top"] + 0.5:
            failures.append(f"{where}: the waiting message ends at "
                            f"{toast['bottom']}, below the map top at "
                            f"{data['boxes']['map']['top']}")
        if data["shown"]["legend"]:
            legend = data["boxes"]["legend"]
            if legend["top"] < data["boxes"]["map"]["top"] - 0.5:
                failures.append(f"{where}: the legend starts above the map "
                                f"({legend['top']})")
            if legend["bottom"] > data["boxes"]["map"]["bottom"] + 0.5:
                failures.append(f"{where}: the legend ends below the map "
                                f"({legend['bottom']})")
    assert not failures, "\n".join(failures)


def _open_stats(cdp):
    """Click the Stats header the way a person does, then reach its rows."""
    cdp.eval("document.querySelector('.stats-overlay-header').click()")
    time.sleep(0.25)
    cdp.eval("(() => { const rail = document.querySelector("
             "'.maplibregl-ctrl-top-left');"
             " if (rail) rail.scrollTop = rail.scrollHeight; })()")
    time.sleep(0.2)
    return cdp.eval(r"""
(() => {
  const rail = document.querySelector('.maplibregl-ctrl-top-left');
  const card = document.querySelector('.stats-overlay');
  const rb = rail.getBoundingClientRect();
  const rows = [...document.querySelectorAll('.stats-overlay-row')];
  return {
    open: card.classList.contains('stats-open'),
    card: card.getBoundingClientRect().toJSON(),
    rail: {top: rb.top, bottom: rb.bottom},
    scroll: [rail.clientHeight, rail.scrollHeight],
    rows: rows.map((row) => {
      const b = row.getBoundingClientRect();
      const cx = Math.round((b.left + b.right) / 2);
      const cy = Math.round((b.top + b.bottom) / 2);
      const top = document.elementFromPoint(cx, cy);
      return {text: row.textContent.trim(),
              inside: cy > rb.top && cy < rb.bottom,
              hit: !!top && row.contains(top)};
    }),
  };
})()
""")


def test_served_stats_shows_its_values_in_a_short_window(served, browser):
    """Item 5: at 1100x432 and 1024x400 the Stats box was a 110x32 header with
    four values nobody could reach, and the header click only toggled a class.
    The header now opens the body; the column scrolls and the card stays inside
    its own cap, so no value is painted over the resize strip."""
    for width, height in ((1100, 432), (1100, 480), (1100, 560), (1024, 400),
                          (320, 568)):
        for theme in ("dark", "light"):
            where = f"{width}x{height} {theme}"
            data = _cell(browser, served, width, height, theme)
            assert data["statsTight"] is True, where
            assert data["boxes"]["stats"]["height"] <= 40, (where, data)
            opened = _open_stats(browser)
            assert opened["open"] is True, where
            assert len(opened["rows"]) == 4, (where, opened)
            for row in opened["rows"]:
                assert row["inside"], (where, row, opened)
                assert row["hit"], (where, row, opened)
            assert opened["card"]["bottom"] <= opened["rail"]["bottom"] + 0.5, (
                where, opened)


def test_served_waiting_message_is_a_row_of_the_layout(served, browser):
    """Item 1: a floating box has no clear spot at 344-375px wide or at
    320x400, so it covered the GPX panel, its Load button and the legend in
    both themes. It is a row of the page now, under the tool row."""
    for width, height in ((320, 400), (320, 568), (344, 800), (360, 720),
                          (360, 800), (375, 800), (560, 720), (560, 800)):
        for theme in ("dark", "light"):
            data = _cell(browser, served, width, height, theme)
            where = f"{width}x{height} {theme}"
            assert data["toastPosition"] == "static", (where, data)
            for pair in ("toast_filter", "toast_myloc", "toast_gpx",
                         "toast_loadGpx", "toast_legend", "toast_stats",
                         "toast_strip"):
                assert data["pairs"][pair] == 0, (where, pair, data["pairs"])


def test_served_legend_yields_instead_of_covering_a_control(served, browser):
    """Item 2: on a 320x568 window with the reviewers' 170px-wide legend there
    is no 42px band left that is clear of the corner cards, so the passive key
    is hidden rather than painted over My Location (1836.0 px^2). Where there
    is room, the body is shown and clear of every control."""
    short = _cell(browser, served, 320, 568, "dark")
    assert short["legendHidden"] is True, short["boxes"]["legend"]
    assert short["hits"]["myloc"] is True, short["hitWhat"]
    for width, height in ((344, 800), (560, 800), (1280, 800)):
        for theme in ("dark", "light"):
            data = _cell(browser, served, width, height, theme)
            where = f"{width}x{height} {theme}"
            assert data["shown"]["legend"] is True, (where, data)
            assert data["boxes"]["legend"]["height"] > 42, (where, data)
            for pair in ("legend_zoom", "legend_readout", "legend_rail",
                         "legend_gpx", "legend_stats", "legend_filter",
                         "legend_myloc", "legend_attrib"):
                assert data["pairs"][pair] == 0, (where, pair, data["pairs"])


def test_served_left_column_clears_the_resize_strip(served, browser):
    """Item 3: the GPX panel met the 8px resize strip at 1024x400 and 1100x400
    (315.8 px^2). Every control in the column ends above the strip, with no
    scrollbar to hide the last row."""
    for width, height in ((1024, 400), (1100, 400), (1100, 432), (1100, 480),
                          (1100, 560), (1280, 800)):
        for theme in ("dark", "light"):
            data = _cell(browser, served, width, height, theme)
            where = f"{width}x{height} {theme}"
            assert data["pairs"]["gpx_strip"] == 0, (where, data["pairs"])
            assert data["pairs"]["stats_strip"] == 0, (where, data["pairs"])
            assert data["pairs"]["loadGpx_strip"] == 0, (where, data["pairs"])
            assert data["railScroll"][0] == data["railScroll"][1], (
                f"{where}: the column scrolls ({data['railScroll']})")
            assert data["hits"]["gpx"] is True, (where, data["hitWhat"])
            assert data["hits"]["loadGpx"] is True, (where, data["hitWhat"])


def test_served_map_buttons_clear_the_credit_pill(served, browser):
    """Item 7: in row mode the two round buttons met the map credit pill at
    560x400 and 560x480 (141 px^2 into its rounded corner). They keep their
    size and order and lift clear of it."""
    for width, height in ((560, 400), (560, 480), (560, 720), (1100, 560),
                          (1280, 800)):
        for theme in ("dark", "light"):
            data = _cell(browser, served, width, height, theme)
            where = f"{width}x{height} {theme}"
            assert data["pairs"]["fab_attrib"] == 0, (where, data["pairs"])
            assert data["pairs"]["fab_filter"] == 0, (where, data["pairs"])
            assert data["pairs"]["fab_myloc"] == 0, (where, data["pairs"])
            assert data["pairs"]["fab_strip"] == 0, (where, data["pairs"])
            assert data["hits"]["fabClear"] is True, (where, data["hitWhat"])
            assert data["hits"]["fabCenter"] is True, (where, data["hitWhat"])


def test_served_tool_row_keeps_every_control_reachable(served, browser):
    """Items 4 and 6: below 600px the tool buttons were wider than the window,
    so the TOOLS button sat 8px past the right edge and the overflow painted
    over the Find input (1488 px^2; a click at its centre reached the
    station-list button instead)."""
    for width, height in ((320, 568), (320, 400), (344, 800), (360, 720),
                          (375, 800), (400, 400), (480, 568), (560, 400)):
        for theme in ("dark", "light"):
            data = _cell(browser, served, width, height, theme)
            where = f"{width}x{height} {theme}"
            assert data["pairs"]["find_tools"] == 0, (where, data["pairs"])
            assert data["hits"]["find"] is True, (where, data["hitWhat"])
            assert data["shown"]["btnTools"] is True, (where, data)
            assert data["hits"]["btnTools"] is True, (where, data["hitWhat"])
            assert data["boxes"]["btnTools"]["right"] <= width, (where, data)
