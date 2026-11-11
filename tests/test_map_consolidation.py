"""Served-page guards for the #219 map consolidation.

Hemna's direction (PR #219 comment 33563): the five floating map cards and the
TOOLS drawer are gone. One closed-by-default "Map" drawer holds the cards; the
overflow menu holds the former TOOLS items; the map itself carries only three
overlay groups (zoom, one locate button, attribution). These tests drive the
real served page over the DevTools protocol and check the acceptance criteria
by measurement, not by eye.
"""
import json
import os
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
# macOS dev machines keep Chrome in /Applications, off PATH.
if CHROMIUM is None:
    _mac = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if os.path.exists(_mac):
        CHROMIUM = _mac

pytestmark = pytest.mark.skipif(
    CHROMIUM is None, reason="served-page guard needs a headless Chromium"
)

# Viewports and themes the acceptance criteria name.
WIDTHS = [320, 360, 560, 768, 1024, 1280, 1600]
HEIGHTS = [400, 568, 800]
THEMES = ["dark", "light"]

# Every function that must stay reachable (acceptance criterion 4). Each entry
# is (how-to-open, modal/panel id that proves it opened).
REACHABLE = [
    ("station-list", "station-list-modal"),
    ("weather-panel", "weather-panel-modal"),
    ("messages-panel", "messages-panel-modal"),
    ("decode", "decode-modal"),
    ("keyboard-help", "keyboard-help-modal"),
    ("settings", "settings-modal"),
]


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def served(tmp_path_factory):
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

    def call(self, method, **params):
        self.next_id += 1
        self.ws.send(json.dumps(
            {"id": self.next_id, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.next_id:
                if "error" in msg:
                    raise AssertionError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def eval(self, expression):
        result = self.call("Runtime.evaluate", expression=expression,
                           returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in result:
            raise AssertionError(result["exceptionDetails"])
        return result["result"].get("value")


@contextmanager
def _browser():
    profile = tempfile.mkdtemp(prefix="cdp-profile-")
    port = _free_port()
    # SwiftShader gives headless Chrome a WebGL2 context so MapLibre renders;
    # without it the app shows the "needs WebGL2" message and no overlays exist.
    proc = subprocess.Popen(
        [CHROMIUM, "--headless=new", "--no-sandbox",
         "--use-gl=angle", "--use-angle=swiftshader",
         "--enable-unsafe-swiftshader", "--disable-dev-shm-usage",
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
            except Exception:
                pass
            time.sleep(0.1)
        assert ws_url, "Chromium never exposed a debugging target"
        with connect(ws_url, max_size=32 * 1024 * 1024) as ws:
            cdp = _CDP(ws)
            cdp.call("Page.enable")
            cdp.call("Runtime.enable")
            yield cdp
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def _load(cdp, url, width, height, theme):
    cdp.call("Emulation.setDeviceMetricsOverride", width=width, height=height,
             deviceScaleFactor=1, mobile=False)
    # Set the theme through localStorage (not a URL param, which would put the
    # page in embedded mode and hide the theme toggle). Visit the origin once
    # to get a storage context, clear it, set the theme, then load fresh.
    cdp.call("Page.navigate", url=url)
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            if cdp.eval("document.readyState === 'complete' || document.readyState === 'interactive'"):
                break
        except Exception:
            pass
        time.sleep(0.1)
    cdp.eval(f"localStorage.clear(); localStorage.setItem('dw-theme', '{theme}');")
    cdp.call("Page.navigate", url=url)
    deadline = time.time() + 25
    while time.time() < deadline:
        try:
            if cdp.eval("!!document.querySelector('.filter-overlay')"
                        " && !!document.querySelector('#map canvas')"):
                break
        except Exception:
            pass
        time.sleep(0.2)
    time.sleep(0.8)
    # The cold-start "Waiting for Stations" modal is a centered modal, not a map
    # overlay; close it so it does not confuse coverage/overlap measurement.
    cdp.eval("var m=document.getElementById('waiting-modal');"
             " if(m)m.classList.add('hidden');")


# geometry probe shared by several tests: measures the overlay groups left on
# the map and returns coverage + the largest pairwise overlap among them.
_PROBE = r"""
(() => {
  const mc = document.getElementById('map-container').getBoundingClientRect();
  const area = mc.width * mc.height;
  const sel = '#map-container .maplibregl-ctrl, #map-container .fab-center,'
            + ' #map-container #map-drawer:not(.hidden)';
  const els = [...document.querySelectorAll(sel)].filter(e => {
    const cs = getComputedStyle(e);
    if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    const b = e.getBoundingClientRect();
    return b.width > 1 && b.height > 1;
  });
  const boxes = els.map(e => e.getBoundingClientRect());
  const clamp = (b) => Math.max(0, Math.min(b.right, mc.right) - Math.max(b.left, mc.left))
                     * Math.max(0, Math.min(b.bottom, mc.bottom) - Math.max(b.top, mc.top));
  let cover = 0; boxes.forEach(b => cover += clamp(b));
  let maxOv = 0;
  for (let i = 0; i < boxes.length; i++)
    for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i], b = boxes[j];
      const ov = Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left))
               * Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
      if (ov > maxOv) maxOv = ov;
    }
  // elementFromPoint at the centre of every visible overlay returns that overlay
  const reaches = els.every(e => {
    const b = e.getBoundingClientRect();
    const hit = document.elementFromPoint((b.left + b.right) / 2, (b.top + b.bottom) / 2);
    return e === hit || e.contains(hit);
  });
  return {groups: els.length, coverPct: +(100 * cover / area).toFixed(2),
          maxOverlap: +maxOv.toFixed(1), reaches};
})()
"""


def test_closed_drawer_is_the_default(served):
    """Acceptance criterion 2: at rest the map carries exactly three overlay
    groups (zoom, locate, attribution) and the drawer is closed."""
    with _browser() as cdp:
        _load(cdp, served, 1280, 800, "dark")
        assert cdp.eval("document.getElementById('map-drawer').classList.contains('hidden')"), \
            "the Map drawer must be closed on a fresh load"
        r = cdp.eval(_PROBE)
        assert r["groups"] == 3, f"expected 3 overlay groups at rest, got {r['groups']}"


@pytest.mark.parametrize("theme", THEMES)
def test_coverage_and_overlaps_at_rest(served, theme):
    """Acceptance criteria 1 and 3 (rest): coverage <=5% at 1280x800 and
    <=10% at 360x800, zero overlaps, every control reachable."""
    with _browser() as cdp:
        _load(cdp, served, 1280, 800, theme)
        wide = cdp.eval(_PROBE)
        assert wide["coverPct"] <= 5.0, f"1280x800 {theme}: {wide['coverPct']}% > 5%"
        assert wide["maxOverlap"] == 0, f"1280x800 {theme}: overlap {wide['maxOverlap']} px2"
        assert wide["reaches"], f"1280x800 {theme}: a control centre is covered"

        _load(cdp, served, 360, 800, theme)
        narrow = cdp.eval(_PROBE)
        assert narrow["coverPct"] <= 10.0, f"360x800 {theme}: {narrow['coverPct']}% > 10%"
        assert narrow["maxOverlap"] == 0, f"360x800 {theme}: overlap {narrow['maxOverlap']} px2"
        assert narrow["reaches"], f"360x800 {theme}: a control centre is covered"


@pytest.mark.parametrize("theme", THEMES)
def test_no_overlaps_in_any_state_at_any_reviewed_size(served, theme):
    """Acceptance criterion 3: zero overlaps and every overlay reachable in
    rest, drawer-open and overflow-open, across the reviewed size matrix."""
    with _browser() as cdp:
        for w in WIDTHS:
            for h in HEIGHTS:
                _load(cdp, served, w, h, theme)
                # rest
                r = cdp.eval(_PROBE)
                assert r["maxOverlap"] == 0, f"{w}x{h} {theme} rest: {r['maxOverlap']} px2"
                assert r["reaches"], f"{w}x{h} {theme} rest: a control centre is covered"
                # drawer open
                cdp.eval("window.__dwSetDrawer(true)")
                time.sleep(0.35)
                d = cdp.eval(_PROBE)
                assert d["reaches"], f"{w}x{h} {theme} drawer: a control centre is covered"
                cdp.eval("window.__dwSetDrawer(false)")
                time.sleep(0.2)
                # overflow open
                cdp.eval("document.getElementById('btn-overflow').click()")
                time.sleep(0.2)
                assert not cdp.eval(
                    "document.getElementById('overflow-menu').classList.contains('hidden')"), \
                    f"{w}x{h} {theme}: overflow menu did not open"
                cdp.eval("document.getElementById('btn-overflow').click()")


def test_only_one_surface_open_at_a_time(served):
    """Opening the drawer closes the overflow menu and vice versa."""
    with _browser() as cdp:
        _load(cdp, served, 1280, 800, "dark")
        cdp.eval("document.getElementById('btn-overflow').click()")
        time.sleep(0.2)
        cdp.eval("window.__dwSetDrawer(true)")
        time.sleep(0.2)
        assert cdp.eval(
            "document.getElementById('overflow-menu').classList.contains('hidden')"), \
            "opening the drawer must close the overflow menu"
        cdp.eval("document.getElementById('btn-overflow').click()")
        time.sleep(0.2)
        assert cdp.eval(
            "document.getElementById('map-drawer').classList.contains('hidden')"), \
            "opening the overflow menu must close the drawer"


@pytest.mark.parametrize("name,modal_id", REACHABLE)
def test_every_function_is_reachable(served, name, modal_id):
    """Acceptance criterion 4: every former TOOLS/overlay action is reachable
    through the overflow menu (<=2 taps: open overflow, click the item)."""
    with _browser() as cdp:
        _load(cdp, served, 1280, 800, "dark")
        cdp.eval("document.getElementById('btn-overflow').click()")
        time.sleep(0.2)
        opened = cdp.eval(
            f"(() => {{"
            f"  const it = [...document.querySelectorAll('#overflow-menu [data-action]')]"
            f"    .find(e => e.dataset.action === 'btn-{name}');"
            f"  if (!it) return 'no item';"
            f"  it.click();"
            f"  return document.getElementById('{modal_id}').classList.contains('hidden')"
            f"    ? 'still hidden' : 'open';"
            f"}})()")
        assert opened == "open", f"'{name}' via overflow: {opened}"


def test_drawer_holds_the_five_sections(served):
    """The five former floating cards are collapsible drawer sections."""
    with _browser() as cdp:
        _load(cdp, served, 1280, 800, "dark")
        secs = cdp.eval(
            "[...document.querySelectorAll('.drawer-section-header span:first-child')]"
            ".map(e => e.textContent)")
        assert secs == ["Legend", "Stats", "Filters", "My Location", "GPX Overlay"], secs


def test_drawer_resizes_the_map_on_wide_windows(served):
    """>=1024px the open drawer shrinks the map (so tiles repaint) rather than
    covering it."""
    with _browser() as cdp:
        _load(cdp, served, 1280, 800, "dark")
        before = cdp.eval("document.getElementById('map').getBoundingClientRect().width")
        cdp.eval("window.__dwSetDrawer(true)")
        time.sleep(0.5)
        after = cdp.eval("document.getElementById('map').getBoundingClientRect().width")
        canvas = cdp.eval("(document.querySelector('#map canvas')||{}).width")
        assert after < before, f"map did not shrink: {before} -> {after}"
        assert abs(canvas - after) <= 2, f"canvas {canvas} did not follow map width {after}"


def test_no_overlay_positions_another_overlay(served):
    """Acceptance criterion 6: no JS reads one overlay's box to place another.
    The whole collision cluster is gone from app.js."""
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "src" / "direwolf_dashboard"
          / "static" / "app.js").read_text(encoding="utf-8")
    for banned in ("placeLegend", "setLegendCap", "stepLegendAside", "legendLift",
                   "updateLeftColumnFit", "updateFabStack", "overlayObstacles",
                   "legendCoversControl", "capUnderCards", "setCornerBodyCap",
                   "toggleCornerCard", "initCornerMode", "buildZoomReadout"):
        assert banned not in js, f"collision-era function still present: {banned}"


def test_status_chip_replaces_the_waiting_row(served):
    """The waiting message is a header chip, not a full-width map row."""
    with _browser() as cdp:
        _load(cdp, served, 1280, 800, "dark")
        assert cdp.eval("!!document.getElementById('status-chip')"), "status chip missing"
        assert cdp.eval("!document.getElementById('waiting-toast')"), \
            "the old waiting-toast row must be gone"
