"""MapLibre station label + my-station glow parity with Leaflet (issue #133)."""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "src" / "direwolf_dashboard" / "static"
JS = (STATIC / "app.js").read_text(encoding="utf-8")


def _layer(layer_id: str) -> str:
    m = re.search(r"id: '" + re.escape(layer_id) + r"',(.*?)\n        \}\);", JS, re.S)
    assert m, layer_id
    return m.group(1)


def test_label_right_of_icon_bold():
    lbl = _layer("stations-label")
    assert "'text-anchor': 'left'" in lbl
    assert "'text-offset': [1.27, 0]" in lbl
    assert "'text-font': ['NotoSansMedium']" in lbl
    assert "'text-size': 11" in lbl
    assert "themeColor('--label-shadow')" in lbl
    assert "themeColor('--text-primary')" in lbl


def test_bold_font_glyphs_vendored():
    assert (STATIC / "basemap" / "fonts" / "NotoSansMedium" / "0-255.pbf").is_file()


def test_my_station_glow_layers_under_icon():
    for lid in ("stations-my-glow", "stations-my-glow-core"):
        g = _layer(lid)
        assert "type: 'circle'" in g
        assert "['==', ['get', 'is_my'], true]" in g
        assert "themeColor('--my-station-glow')" in g
        assert "'circle-blur'" in g
    assert JS.index("id: 'stations-my-glow'") < JS.index("id: 'stations-symbol-base'")


def test_glow_color_defined_for_both_themes():
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert css.count("--my-station-glow:") >= 2
