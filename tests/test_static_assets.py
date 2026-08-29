"""Tests verifying vendored static assets are present and valid."""

from pathlib import Path

import direwolf_dashboard


def test_aprs_symbol_sprites_are_vendored():
    """Both APRS symbol sprite PNG files must be vendored in static/symbols/.

    The project constraint is fully offline — no external URLs.  These files
    were previously fetched from raw.githubusercontent.com on every page load,
    which breaks on DigiPi and other air-gapped Raspberry Pi deployments.
    """
    static_dir = Path(direwolf_dashboard.__file__).parent / "static" / "symbols"
    assert static_dir.is_dir(), f"static/symbols/ directory missing: {static_dir}"

    for name in ("aprs-symbols-24-0.png", "aprs-symbols-24-1.png"):
        f = static_dir / name
        assert f.exists(), f"Missing vendored APRS symbol sprite: {name}"
        assert f.stat().st_size > 0, f"Vendored sprite file is empty: {name}"
        # Basic PNG magic bytes check
        magic = f.read_bytes()[:4]
        assert magic == b"\x89PNG", f"File {name} does not look like a valid PNG"


def test_aprs_sprite_constants_use_local_paths():
    """Verify app.js sprite constants reference local /static/ paths, not CDN URLs."""
    app_js = (
        Path(direwolf_dashboard.__file__).parent / "static" / "app.js"
    )
    assert app_js.exists(), "app.js not found"
    content = app_js.read_text(encoding="utf-8")

    # Must NOT reference any external URL for sprites
    assert "raw.githubusercontent.com" not in content or \
        "aprs-symbols" not in content.split("raw.githubusercontent.com")[1][:200], \
        "app.js still references APRS symbol sprites from raw.githubusercontent.com"

    # Must reference the vendored local paths
    assert "/static/symbols/aprs-symbols-24-0.png" in content, \
        "PRIMARY_SPRITE must point to /static/symbols/aprs-symbols-24-0.png"
    assert "/static/symbols/aprs-symbols-24-1.png" in content, \
        "SECONDARY_SPRITE must point to /static/symbols/aprs-symbols-24-1.png"
