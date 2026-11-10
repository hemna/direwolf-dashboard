"""Resting geometry of the map's top-right corner (issue #218 rounds 8 and 9).

Round 7 shipped a stylesheet-string test that stayed green while a card hung
45 px below the map, so this guard renders the real ``style.css`` in headless
Chromium and measures the boxes the browser actually lays out. It is skipped
when no Chromium binary is available; continuous integration installs one.

The fixture reuses style.css verbatim and reproduces the two cards' markup
from ``app.js`` (buttons, same class names, same rows) inside MapLibre's
``.maplibregl-ctrl-top-right`` column. Round 9 also reads the compact switch
``CORNER_COMPACT_MAX_MAP_HEIGHT`` out of ``app.js`` instead of hard-coding
"compact", so a mutated threshold fails these tests too.
"""
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

import direwolf_dashboard

STATIC = Path(direwolf_dashboard.__file__).parent / "static"
APP_JS = STATIC / "app.js"

CHROMIUM = (
    shutil.which("chromium")
    or shutil.which("chromium-browser")
    or shutil.which("google-chrome")
    or shutil.which("chrome")
)

pytestmark = pytest.mark.skipif(
    CHROMIUM is None, reason="resting-geometry guard needs a headless Chromium"
)

def _compact_threshold() -> int:
    """The switch app.js really uses to enter the collapsed corner."""
    match = re.search(r"var CORNER_COMPACT_MAX_MAP_HEIGHT = (\d+);",
                      APP_JS.read_text(encoding="utf-8"))
    assert match, "app.js no longer declares CORNER_COMPACT_MAX_MAP_HEIGHT"
    return int(match.group(1))


def _compact(map_height: int) -> bool:
    return map_height < _compact_threshold()


FILTER_ROWS = [
    ("Callsign", '<input type="text" id="filter-callsign" placeholder="Filter..." />', ""),
    ("Search", '<input type="search" id="filter-search" placeholder="Search log..." />', ""),
    ("Trails", '<select id="trail-duration"><option>1h</option><option>2h</option>'
               '<option>6h</option><option>24h</option></select>', ""),
    ("Type", '<select id="filter-type"><option>All</option><option>GPS</option>'
             '<option>Message</option></select>', ""),
    ("TX/RX", '<select id="filter-txrx"><option>All</option><option>RX</option>'
              '<option>TX</option></select>', ""),
    ("Station plots", '<input type="checkbox" id="filter-station-model" checked />', ""),
    ("Packet paths", '<input type="checkbox" id="filter-packet-paths" />', ""),
    ("Follow", '<input type="text" id="filter-follow-callsign" placeholder="Follow callsign..." />', ""),
    ("Follow mode", '<select id="filter-follow-mode"><option>Dim</option>'
                    '<option>Hide</option></select>', ""),
]


def _filter_card() -> str:
    rows = "".join(
        '<div class="filter-overlay-row">'
        f'<label class="filter-overlay-label">{label}</label>{control}'
        "</div>"
        for label, control, _ in FILTER_ROWS
    )
    return (
        '<div class="filter-overlay">'
        '<button type="button" class="filter-overlay-header" aria-expanded="false"'
        ' aria-controls="filter-overlay-body">Filters '
        '<span class="filter-collapse-icon">&#9660;</span></button>'
        f'<div class="filter-overlay-body" id="filter-overlay-body">{rows}</div>'
        "</div>"
    )


def _myloc_card() -> str:
    return (
        '<div class="my-location-overlay">'
        '<button type="button" class="my-location-header" aria-expanded="false"'
        ' aria-controls="my-location-body">'
        '<span class="my-location-title">My Location</span>'
        '<span class="my-location-actions">'
        '<span class="my-location-btn my-location-center" title="Center map on my location">'
        "&#8982;</span>"
        '<span class="my-location-btn my-location-clear hidden" title="Clear my location">'
        "&times;</span>"
        '<span class="my-location-collapse-icon">&#9660;</span>'
        "</span></button>"
        '<div class="my-location-body" id="my-location-body">'
        '<div class="my-location-type">Not set</div>'
        '<div class="my-location-coords"></div>'
        "</div></div>"
    )


def _measure(map_height: int, compact: bool) -> dict:
    """Lay the real stylesheet out in Chromium and return the box rects.

    Round 10: the fixture no longer supplies ``--corner-open-max``. app.js sets
    that from the live map box, so a fixture that invented it hid a broken
    page (NB-2). The opened-card checks now live in tests/test_corner_served.py
    and run against the served page.
    """
    classes = "corner-compact" if compact else ""
    filter_html = _filter_card()
    myloc_html = _myloc_card()

    page = f"""<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="file://{STATIC / 'vendor' / 'maplibre-gl.css'}">
<link rel="stylesheet" href="file://{STATIC / 'style.css'}">
<style>body {{ margin: 0; }}</style></head><body>
<div id="map-container" class="{classes}"
     style="position:relative;height:{map_height}px;overflow:hidden;width:1100px;">
  <div id="map" class="maplibregl-map"
       style="position:absolute;top:0;left:0;right:0;bottom:0;overflow:hidden">
    <div class="maplibregl-ctrl-top-right">
      <div class="maplibregl-ctrl">{filter_html}</div>
      <div class="maplibregl-ctrl">{myloc_html}</div>
    </div>
  </div>
</div>
<pre id="rects"></pre>
<script>
const r = (el) => {{
  const b = el.getBoundingClientRect();
  return {{top: b.top, bottom: b.bottom, left: b.left, right: b.right,
          width: b.width, height: b.height}};
}};
const body = (el) => {{
  const b = el.querySelector('.filter-overlay-body, .my-location-body');
  return b ? getComputedStyle(b).display : null;
}};
const cards = [...document.querySelectorAll('.filter-overlay, .my-location-overlay')]
  .map((el) => {{
    const b = el.querySelector('.filter-overlay-body, .my-location-body');
    return {{
      name: el.className.split(' ')[0], rect: r(el), body: b ? getComputedStyle(b).display : null,
      headScrollW: el.querySelector('.filter-overlay-header, .my-location-header').scrollWidth,
      headClientW: el.querySelector('.filter-overlay-header, .my-location-header').clientWidth,
      bodyScrollH: b ? b.scrollHeight : 0, bodyClientH: b ? b.clientHeight : 0,
      bodyOffsetW: b ? b.offsetWidth : 0, bodyClientW: b ? b.clientWidth : 0,
    }};
  }});
document.getElementById('rects').textContent = JSON.stringify({{
  map: r(document.getElementById('map-container')),
  column: r(document.querySelector('.maplibregl-ctrl-top-right')),
  cards: cards,
}});
</script></body></html>"""

    with tempfile.TemporaryDirectory() as tmp:
        page_path = Path(tmp) / "corner.html"
        page_path.write_text(page, encoding="utf-8")
        out = subprocess.run(
            [CHROMIUM, "--headless=new", "--no-sandbox", "--disable-gpu",
             "--disable-dev-shm-usage", "--virtual-time-budget=3000",
             "--window-size=1100,800", "--dump-dom", page_path.as_uri()],
            capture_output=True, text=True, timeout=120,
        )
    assert out.returncode == 0, out.stderr[-2000:]
    start = out.stdout.index('<pre id="rects">') + len('<pre id="rects">')
    raw = out.stdout[start:out.stdout.index("</pre>", start)]
    assert raw.strip(), "the fixture produced no rects; Chromium printed no layout"
    return json.loads(raw)


def _card(data: dict, name: str) -> dict:
    return next(c for c in data["cards"] if c["name"] == name)


def test_compact_corner_fits_a_255px_map():
    """1100x480: the 255px map is shorter than the 293px card pair, so both
    cards must be single-line headers entirely inside the map (<=44px each,
    column <=96px wide), with no body showing and no clipped header text."""
    data = _measure(255, compact=_compact(255))
    map_box = data["map"]

    assert data["column"]["width"] <= 96, data["column"]
    for name in ("filter-overlay", "my-location-overlay"):
        card = _card(data, name)
        assert card["rect"]["height"] <= 44, card
        assert card["rect"]["top"] >= map_box["top"], card
        assert card["rect"]["bottom"] <= map_box["bottom"] - 4, (
            f"{name} leaves the map box: {card['rect']} vs {map_box}")
        assert card["body"] == "none", f"{name} body must be collapsed at rest"
        assert card["headScrollW"] <= card["headClientW"], (
            f"{name} header text is clipped: {card}")


def test_round7_layout_is_untouched_on_a_308px_map():
    """1100x560 keeps round 7 exactly: both cards open, bodies visible, both
    inside the map with clearance, at the measured 219px and 66px heights."""
    data = _measure(308, compact=_compact(308))
    map_box = data["map"]
    flt = _card(data, "filter-overlay")
    myloc = _card(data, "my-location-overlay")

    assert abs(flt["rect"]["height"] - 219) <= 2, flt
    assert abs(myloc["rect"]["height"] - 66) <= 2, myloc
    assert flt["body"] == "block" and myloc["body"] == "block"
    assert myloc["rect"]["bottom"] <= map_box["bottom"] - 10, (
        f"round-7 clearance lost: {myloc['rect']} vs {map_box}")


def test_compact_engages_at_a_255px_map_and_not_at_308():
    """The switch app.js really uses: 255 px collapses, 308 px does not.

    Round 9 added this because mutating the threshold (300 -> 10) left the
    whole suite green: the geometry guard applied the class by hand and never
    looked at the code that decides to apply it.
    """
    threshold = _compact_threshold()
    assert _compact(255) is True, threshold
    assert _compact(308) is False, threshold
    assert 255 < threshold <= 308, threshold

    short = _measure(255, compact=_compact(255))
    tall = _measure(308, compact=_compact(308))
    for name in ("filter-overlay", "my-location-overlay"):
        assert _card(short, name)["rect"]["height"] <= 44, (
            f"{name} did not collapse at a 255px map: {_card(short, name)}")
    assert abs(_card(tall, "filter-overlay")["rect"]["height"] - 219) <= 2, (
        "the taller map must keep the round-7 cards")


def test_attribution_strip_insets_both_resting_and_open():
    """Round 10 / defect A: the round-7 guard only looked for the resting
    ``right: 210px`` string, so it could not see that an opened card (which
    steps 56px left of the button rail) covers the strip. Assert both insets;
    the measured clearance is checked on the served page."""
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert ".maplibregl-ctrl-bottom-right {\n    right: 210px;\n}" in css
    assert (
        ".corner-compact.corner-filter-open .maplibregl-ctrl-bottom-right,\n"
        ".corner-compact.corner-myloc-open .maplibregl-ctrl-bottom-right {\n"
        "    right: 250px;\n}"
    ) in css, "the opened compact card needs its own, wider strip inset"
    assert "when the map is shorter than the opened card" in css
