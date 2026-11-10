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


def test_waiting_toast_is_a_row_under_the_top_bands():
    """Issue #218 round 14: round 5 measured the toast's runtime top against the
    two bands, but it stayed position:fixed, and at 344-375px wide and at
    320x400 no clear spot exists: it painted the GPX panel, its Load button and
    the legend. It is a row of the page's own layout now, under #toolrow and
    above #map-container, so it takes its own space and covers nothing."""
    html = _html()
    toolrow = html.index('id="toolrow"')
    toast = html.index('id="waiting-toast"')
    mapc = html.index('id="map-container"')
    assert toolrow < toast < mapc, (
        "the waiting row must sit between #toolrow and #map-container"
    )
    css_block = _block(_css(), ".toast")
    assert "position: fixed" not in css_block, (
        "a floating toast has no clear spot on a 344-375px window"
    )
    assert "position: absolute" not in css_block, (
        "a floating toast has no clear spot on a 344-375px window"
    )
    assert "border-bottom" in css_block, (
        "the row needs a rule between it and the map"
    )
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "positionWaitingToast" not in js, (
        "the row needs no runtime placement code"
    )
