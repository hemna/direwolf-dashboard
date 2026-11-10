"""Resting geometry of the map's top-right corner (issue #218 round 8).

Round 7 shipped a stylesheet-string test that stayed green while a card hung
45 px below the map, so this guard renders the real ``style.css`` in headless
Chromium and measures the boxes the browser actually lays out. It is skipped
when no Chromium binary is available; continuous integration installs one.

The fixture reuses style.css verbatim and reproduces the two cards' markup
from ``app.js`` (same class names, same rows) inside MapLibre's
``.maplibregl-ctrl-top-right`` column. ``updateCornerMode()`` toggles
``corner-compact`` from the measured map height; the fixture applies exactly
that class so the guard fails if a card leaves the map box.
"""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

import direwolf_dashboard

STATIC = Path(direwolf_dashboard.__file__).parent / "static"

CHROMIUM = (
    shutil.which("chromium")
    or shutil.which("chromium-browser")
    or shutil.which("google-chrome")
    or shutil.which("chrome")
)

pytestmark = pytest.mark.skipif(
    CHROMIUM is None, reason="resting-geometry guard needs a headless Chromium"
)

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
        '<div class="filter-overlay-header">Filters '
        '<span class="filter-collapse-icon">&#9660;</span></div>'
        f'<div class="filter-overlay-body">{rows}</div>'
        "</div>"
    )


def _myloc_card() -> str:
    return (
        '<div class="my-location-overlay">'
        '<div class="my-location-header">'
        '<span class="my-location-title">My Location</span>'
        '<span class="my-location-actions">'
        '<span class="my-location-btn my-location-center" title="Center map on my location">'
        "&#8982;</span>"
        '<span class="my-location-btn my-location-clear hidden" title="Clear my location">'
        "&times;</span>"
        '<span class="my-location-collapse-icon">&#9660;</span>'
        "</span></div>"
        '<div class="my-location-body">'
        '<div class="my-location-type">Not set</div>'
        '<div class="my-location-coords"></div>'
        "</div></div>"
    )


def _measure(map_height: int, compact: bool, opened: str | None = None) -> dict:
    """Lay the real stylesheet out in Chromium and return the box rects."""
    classes = "corner-compact" if compact else ""
    if opened:
        classes = (classes + f" corner-{opened}-open").strip()
    filter_cls = "filter-overlay corner-open" if opened == "filter" else "filter-overlay"
    myloc_cls = ("my-location-overlay corner-open" if opened == "myloc"
                 else "my-location-overlay")
    filter_html = _filter_card().replace(
        'class="filter-overlay"', f'class="{filter_cls}"', 1)
    myloc_html = _myloc_card().replace(
        'class="my-location-overlay"', f'class="{myloc_cls}"', 1)

    page = f"""<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="file://{STATIC / 'vendor' / 'maplibre-gl.css'}">
<link rel="stylesheet" href="file://{STATIC / 'style.css'}">
<style>body {{ margin: 0; }}</style></head><body>
<div id="map-container" class="{classes}"
     style="position:relative;height:{map_height}px;overflow:hidden;width:1100px">
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
  .map((el) => ({{
    name: el.className.split(' ')[0], rect: r(el), body: body(el),
    headScrollW: el.querySelector('.filter-overlay-header, .my-location-header').scrollWidth,
    headClientW: el.querySelector('.filter-overlay-header, .my-location-header').clientWidth,
  }}));
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
    data = _measure(255, compact=True)
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


def test_opened_filter_stays_inside_and_hides_my_location():
    data = _measure(255, compact=True, opened="filter")
    map_box = data["map"]
    flt = _card(data, "filter-overlay")
    myloc = _card(data, "my-location-overlay")

    assert flt["body"] == "block", flt
    assert flt["rect"]["bottom"] <= map_box["bottom"] - 4, (
        f"open Filters leaves the map box: {flt['rect']} vs {map_box}")
    assert myloc["body"] is not None
    assert myloc["rect"]["height"] == 0, "opening Filters must hide My Location"


def test_opened_my_location_stays_inside_and_hides_filter():
    data = _measure(255, compact=True, opened="myloc")
    map_box = data["map"]
    flt = _card(data, "filter-overlay")
    myloc = _card(data, "my-location-overlay")

    assert myloc["body"] == "block", myloc
    assert myloc["rect"]["bottom"] <= map_box["bottom"] - 4, (
        f"open My Location leaves the map box: {myloc['rect']} vs {map_box}")
    assert flt["rect"]["height"] == 0, "opening My Location must hide Filters"


def test_round7_layout_is_untouched_on_a_308px_map():
    """1100x560 keeps round 7 exactly: both cards open, bodies visible, both
    inside the map with clearance, at the measured 219px and 66px heights."""
    data = _measure(308, compact=False)
    map_box = data["map"]
    flt = _card(data, "filter-overlay")
    myloc = _card(data, "my-location-overlay")

    assert abs(flt["rect"]["height"] - 219) <= 2, flt
    assert abs(myloc["rect"]["height"] - 66) <= 2, myloc
    assert flt["body"] == "block" and myloc["body"] == "block"
    assert myloc["rect"]["bottom"] <= map_box["bottom"] - 10, (
        f"round-7 clearance lost: {myloc['rect']} vs {map_box}")


def test_corner_rules_that_cannot_be_seen_in_the_fixture():
    """Two rules the fixture cannot show: the attribution strip must stay
    inset left of the column, and the sub-1024x480 fallback must be stated."""
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert ".maplibregl-ctrl-bottom-right {\n    right: 210px;\n}" in css
    assert "shortcut: below 1024x480 an open card scrolls inside itself" in css
