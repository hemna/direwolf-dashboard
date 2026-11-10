"""Served-page geometry for the map's top-right corner (issue #218 round 10).

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
  const rows = [...document.querySelectorAll(
    '.filter-overlay-body .filter-overlay-row')];
  const cs = body ? getComputedStyle(body) : null;
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
                  overflowY: cs.overflowY, scrollTop: body.scrollTop} : null,
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


def test_served_filter_body_paints_a_classic_scrollbar(served):
    """NB-1 / defect B: `overflow-y: auto` let Chromium use an overlay bar, so
    the clipped last row had no gutter. The served page must reserve one."""
    for width, height in ((1100, 432), (1100, 400)):
        for theme in ("dark", "light"):
            with _page(served, width, height, theme=theme) as cdp:
                _open_filters(cdp)
                data = cdp.eval(MEASURE)
                body = data["body"]
                assert body["overflowY"] == "scroll", body
                assert body["offsetW"] - body["clientW"] >= 12, (
                    f"{width}x{height} {theme}: no classic gutter: {body}")
                assert body["scrollH"] > body["clientH"], body
                cdp.eval("(() => { const b = document.querySelector("
                         "'.filter-overlay-body'); b.scrollTop = b.scrollHeight; })()")
                time.sleep(0.3)
                after = cdp.eval(MEASURE)
                assert after["body"]["scrollTop"] > 0, after["body"]
                assert after["lastRowBottom"] <= after["body"]["bottom"] + 0.5, (
                    f"{width}x{height} {theme}: last row unreachable")


def test_served_open_card_stays_inside_the_map_and_hides_the_other(served):
    """Replaces the fixture's opened-card tests, which supplied their own cap."""
    with _page(served, 1100, 400) as cdp:
        _open_filters(cdp)
        data = cdp.eval(MEASURE)
    assert data["card"]["bottom"] <= data["map"]["bottom"] - 4, data
    assert data["mylocHeight"] == 0, "opening Filters must hide My Location"
