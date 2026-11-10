"""Tool panel layout (issue #218): the tool layer floats over the map region
only, below the two top toolbar bands (#topnav, #toolrow), nearly full map
height, with a visible scrollbar; opening it never changes the map height or
the toolbar layout.

Hemna rework (2026-11-09/10): at cfab0c7 the panel painted over the two top
bars; at adb8ca69 it was an in-flow page-column child that gave the open panel
only a short stub of height (content behind a nearly invisible thin scrollbar)
and squeezed the map. The correct layout (the haminfo reference on issue
#218): `#toolpanel` is a child of `#map-container` (which starts below the two
bands), absolutely positioned over the map only.
"""
from pathlib import Path

import direwolf_dashboard

STATIC = Path(direwolf_dashboard.__file__).parent / "static"


def _css() -> str:
    return (STATIC / "style.css").read_text(encoding="utf-8")


def _html() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


def _block(css: str, selector: str) -> str:
    start = css.index(selector + " {")
    return css[start:css.index("}", start) + 1]


def test_toolpanel_lives_inside_map_container():
    """The tool layer must be a child of #map-container, which starts below the
    two top bars — so the panel can only ever overlay the map region and can
    never sit over #topnav or #toolrow."""
    html = _html()
    topnav = html.index('id="topnav"')
    toolrow = html.index('id="toolrow"')
    mapc = html.index('id="map-container"')
    panel = html.index('id="toolpanel"')
    logc = html.index('id="log-container"')
    assert topnav < toolrow < mapc < panel < logc, (
        "the tool panel must be nested inside #map-container, below the two "
        "top toolbar bands and above the packet log"
    )


def test_toolpanel_absolute_over_the_map_only():
    """The panel is position:absolute inside the map, anchored 8px from the top
    and left and 8px from the bottom (nearly full map height). Because it is
    out of flow, opening it never changes the map's height or the toolbar
    layout; its height comes from the map box, not the viewport."""
    css = _css()
    block = _block(css, "#toolpanel")
    assert "position: absolute" in block, "panel must be positioned inside the map container"
    assert "top: 8px" in block and "left: 8px" in block
    assert "bottom: 8px" in block, "panel must stretch nearly the full map height"
    assert "vh" not in block, "panel height must come from the map box, not the viewport"
    if "max-height" in block:
        assert "calc(100% - 16px)" in block or "bottom: 8px" in block


def test_toolpanel_never_over_the_top_bands():
    """The two top bars are z-index 110; the panel must stay below them. The
    map container isolates the panel with its own z-index: 0 stacking context,
    and the panel's own z-index is a positive value below 110 — above the map
    content and the left overlay cards, never over the bands."""
    import re

    css = _css()
    map_block = _block(css, "#map-container")
    assert "position: relative" in map_block, "map container must anchor the absolute panel"
    assert "z-index: 0" in map_block, (
        "map container must create a stacking context that confines the panel "
        "below the two top bands"
    )
    def band_z(sel):
        return int(re.search(r"z-index:\s*(\d+)", _block(css, sel)).group(1))
    assert band_z("#topnav") == 110 and band_z("#toolrow") == 110, (
        "both top bands must stay at z-index 110"
    )
    block = _block(css, "#toolpanel")
    m = re.search(r"z-index:\s*(\d+)", block)
    assert m, "panel needs an explicit z-index"
    assert 0 < int(m.group(1)) < 110, (
        "panel z-index must sit above the map content but below the two bands (110)"
    )


def test_all_sections_reachable_with_visible_scrollbar():
    """Every panel section exists; the body scrolls internally with an obvious
    scrollbar when the content overflows (a thin scrollbar was the defect at
    adb8ca69)."""
    html, css = _html(), _css()
    sections = html.count('class="toolpanel-section"')
    assert sections == 4, "PACKETS/OVERLAYS/STATIONS/DISPLAY sections must all exist"
    body = _block(css, ".toolpanel-body")
    assert "overflow-y: auto" in body, "panel body must scroll so every section stays reachable"
    assert "flex: 1" in body and "min-height: 0" in body, (
        "panel body must fill the fixed-height panel and be shrinkable to it"
    )
    assert "scrollbar-width: thin" not in body, "a thin scrollbar is effectively invisible"
    assert (
        ".toolpanel-body::-webkit-scrollbar" in css and "width: 12px" in css
    ), "an explicit Chromium scrollbar with a real gutter is required"


def test_toggle_buttons_are_distinguishable():
    """The packet-log toggle and the tools toggle are two separate toolbar
    buttons with different icons, so a user can tell them apart."""
    html = _html()
    log_btn = '<button id="btn-toggle-log"'
    tools_btn = '<button id="btn-tools"'
    assert log_btn in html and tools_btn in html
    log_icon = html[html.index(log_btn):html.index("</button>", html.index(log_btn))]
    tools_icon = html[html.index(tools_btn):html.index("</button>", html.index(tools_btn))]
    assert log_icon != tools_icon, "packet-log and tools toggles must show different icons"


def _js_function(js: str, name: str) -> str:
    """Extract one function body, balanced by braces."""
    start = js.index(f"function {name}(")
    depth = 0
    for i in range(js.index("{", start), len(js)):
        if js[i] == "{":
            depth += 1
        elif js[i] == "}":
            depth -= 1
            if depth == 0:
                return js[start:i + 1]
    raise AssertionError(f"unbalanced braces in {name}")


def test_waiting_toast_never_intersects_the_top_bands():
    """Issue #218 round 5: the waiting toast is position:fixed; its top is
    derived at runtime from the real bottom of #topnav + #toolrow. A fixed
    50px top (written for the old single #toolbar) painted it over the tool
    row. Rect check: the toast must start at or below both bands' bottoms."""
    import json
    import subprocess

    js = (STATIC / "app.js").read_text(encoding="utf-8")
    func = _js_function(js, "positionWaitingToast")
    for topnav_bottom, toolrow_bottom in ((36, 70), (44, 82)):
        harness = f"""
const bands = {{topnav: {{top: 0, bottom: {topnav_bottom}}},
                toolrow: {{top: {topnav_bottom}, bottom: {toolrow_bottom}}}}};
global.document = {{ getElementById(id) {{
  return (id === 'topnav' || id === 'toolrow')
    ? {{ getBoundingClientRect: () => ({{...bands[id]}}) }} : null;
}} }};
{func}
const toast = {{ style: {{}},
  getBoundingClientRect() {{
    const top = parseFloat(this.style.top);
    return {{top, bottom: top + 32}};
  }} }};
positionWaitingToast(toast);
const r = toast.getBoundingClientRect();
console.log(JSON.stringify({{toast: r, topnav: bands.topnav, toolrow: bands.toolrow}}));
"""
        out = subprocess.run(["node", "-e", harness], capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        data = json.loads(out.stdout)
        assert data["toast"]["top"] >= data["topnav"]["bottom"], (
            f"toast overlaps #topnav: {data}"
        )
        assert data["toast"]["top"] >= data["toolrow"]["bottom"], (
            f"toast overlaps #toolrow: {data}"
        )

    css_block = _block(_css(), ".toast")
    assert "top: 50px" not in css_block, (
        "the fixed 50px top painted the toast over the tool row"
    )


def test_top_right_column_css_caps_height_and_insets_attribution():
    """CSS contract only: this reads style.css rules, it does NOT measure a
    rendered box. Issue #218 round 7: the cards themselves are compact enough
    to fit a 308px map at 1100x560 (measured in Chromium and recorded on the
    pull request); this test guards the rules that keep the column from
    escaping the map at any height and keep the MapLibre attribution strip off
    the card column. A name promising rendered geometry was what let a clipped
    card pass a green suite in round 6, so the name now says what it checks."""
    css = _css()
    block = _block(css, ".maplibregl-ctrl-top-right")
    assert "max-height: 100%" in block, (
        "the top-right control column must be capped to the map height"
    )
    assert "overflow-y: auto" in block, (
        "an overflowing column must scroll so every card stays reachable"
    )
    vendor = (STATIC / "vendor" / "maplibre-gl.css").read_text(encoding="utf-8")
    assert ".maplibregl-ctrl-top-right{pointer-events:none;position:absolute" in vendor, (
        "vendor CSS positions the column absolutely at the map's top-right, so "
        "100% resolves to the map box"
    )
    attrib = _block(css, ".maplibregl-ctrl-bottom-right")
    assert "right: 210px" in attrib, (
        "the attribution strip must be inset left of the top-right card column"
    )
