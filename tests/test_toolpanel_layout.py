"""Tool panel must never cover the top toolbar bands (issue #218, 2026-11-09)."""
from pathlib import Path
import direwolf_dashboard


def test_toolpanel_must_not_cover_toolbar_bands():
    """The tool panel (issue #218) must sit BELOW the two top toolbar bands.

    Hemna rework (2026-11-09): with the panel open it must NEVER cover
    #topnav or #toolrow.  The panel is a normal-flow <body> flex child, so it
    must not use position absolute/top that would overlay the bands.
    """
    css = (
        Path(direwolf_dashboard.__file__).parent / "static" / "style.css"
    ).read_text(encoding="utf-8")
    start = css.index("#toolpanel {")
    block = css[start:css.index("}", start) + 1]
    assert "#toolpanel" in block
    # Regression guard: the panel must not be pulled out of flow over the bars.
    assert "position: absolute" not in block, \
        "toolpanel must be a normal-flow flex child, not an overlay"
    assert "z-index" not in block, \
        "toolpanel must not stack above the toolbar bands"
    # It must be a flex-column child of <body> (flex-direction: column).
    assert "align-self: flex-start" in block, \
        "toolpanel must align itself against the left of the flex column"
    body_start = css.index("\nbody {\n")
    body_block = css[body_start:css.index("}", body_start) + 1]
    assert "flex-direction: column" in body_block, \
        "body must lay children out as a column so the panel falls below the bands"
