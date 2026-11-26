"""World z0-4 fallback under the region archive (issue #144)."""

import hashlib
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "src" / "direwolf_dashboard" / "static"
JS = (STATIC / "app.js").read_text(encoding="utf-8")
WORLD = STATIC / "basemap" / "regions" / "world.pmtiles"


def test_world_source_added_to_style():
    assert "addWorldFallback(style);" in JS
    assert "const WORLD_TILES_URL = '/static/basemap/regions/world.pmtiles';" in JS
    assert "style.sources.world = {" in JS


def test_world_copies_only_fill_and_line_layers_under_region():
    fn = JS[JS.index("function addWorldFallback") :]
    fn = fn[: fn.index("\n    }\n")]
    assert "(l.type === 'fill' || l.type === 'line')" in fn
    assert "id: 'world-' + l.id, source: 'world'" in fn
    assert "style.layers.splice(bg + 1, 0, ...worldLayers);" in fn


def test_vendored_world_archive_is_z0_4_and_pinned():
    data = WORLD.read_bytes()
    assert data[:7] == b"PMTiles"
    assert data[100] == 0 and data[101] == 4  # header min/max zoom
    assert hashlib.sha256(data).hexdigest() == (
        "c6428a4856ec8e8802b9667dfa38b17a506c497fae20a1169baed83835e708aa"
    )
    assert len(data) < 8 * 1024 * 1024
