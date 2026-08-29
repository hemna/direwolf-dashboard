"""Tests verifying vendored static assets and security properties."""

import re
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


def test_no_callsign_onclick_in_popup_html():
    """Verify app.js has no inline onclick attribute with template-expression interpolation.

    Inline onclick attributes that embed dynamic values (like callsigns) are
    an XSS vector.  All popup buttons must use addEventListener instead.
    Previously, station popup buttons embedded callsign values as:
        onclick="window._setMyPositionStation('${callsign}')"
    which allowed a crafted APRS callsign containing a single-quote to
    execute arbitrary JavaScript when the popup was opened.
    """
    app_js = (
        Path(direwolf_dashboard.__file__).parent / "static" / "app.js"
    )
    assert app_js.exists(), "app.js not found"
    content = app_js.read_text(encoding="utf-8")

    # Detect the old dangerous pattern: onclick="...${...}..." (template expressions in attributes)
    dangerous_pattern = re.compile(
        r"""onclick=["'][^"']*\$\{[^}]+\}[^"']*["']""",
        re.DOTALL,
    )
    matches = dangerous_pattern.findall(content)
    assert not matches, (
        f"Found {len(matches)} dangerous onclick attribute(s) that embed template "
        f"expressions — use addEventListener instead:\n" + "\n".join(matches)
    )


def test_popup_buttons_use_addeventlistener():
    """Verify popup action buttons are wired via addEventListener not onclick strings."""
    app_js = (
        Path(direwolf_dashboard.__file__).parent / "static" / "app.js"
    )
    content = app_js.read_text(encoding="utf-8")

    # The fix patterns that should be present
    assert "popup-btn-set" in content, "popup-btn-set class still present"
    assert "popup-btn-remove" in content, "popup-btn-remove class still present"
    assert "popup-btn-gpx" in content, "popup-btn-gpx class still present"

    # All three button wiring patterns must use addEventListener
    assert "addEventListener('click'" in content or 'addEventListener("click"' in content, \
        "popup buttons must use addEventListener"

    # The old window._setMyPositionStation('${callsign}') pattern must be gone
    assert "window._setMyPositionStation('" not in content, \
        "old onclick='...station' pattern must be removed"
    assert "window._viewWeather('" not in content, \
        "old onclick='...weather' pattern must be removed"
    assert "window._downloadGpx('" not in content, \
        "old onclick='...gpx' pattern must be removed"
    assert "window._dropPinFromPopup" not in content, \
        "dead _dropPinFromPopup global must be removed"


def test_render_compact_log_function_defined():
    """Verify renderCompactLog function is defined in app.js."""
    app_js = (
        Path(direwolf_dashboard.__file__).parent / "static" / "app.js"
    )
    content = app_js.read_text(encoding="utf-8")
    assert "function renderCompactLog(" in content, \
        "renderCompactLog() must be defined in app.js"


def test_addlogrow_uses_render_compact_log():
    """Verify addLogRow calls renderCompactLog instead of reading packet.compact_log."""
    app_js = (
        Path(direwolf_dashboard.__file__).parent / "static" / "app.js"
    )
    content = app_js.read_text(encoding="utf-8")
    # renderCompactLog must be called inside addLogRow
    assert "renderCompactLog(packet)" in content, \
        "addLogRow must call renderCompactLog(packet)"
    # The old pattern of trusting packet.compact_log must be gone
    assert "packet.compact_log" not in content, \
        "app.js must not reference packet.compact_log directly"


def test_station_list_modal_in_html():
    """Verify station-list-modal HTML is present in index.html."""
    html = (
        Path(direwolf_dashboard.__file__).parent / "static" / "index.html"
    )
    content = html.read_text(encoding="utf-8")
    assert 'id="station-list-modal"' in content, "station-list-modal must be in index.html"
    assert 'id="btn-export-csv"' in content, "btn-export-csv must be in index.html"
    assert 'id="station-search"' in content, "station-search must be in index.html"
    assert 'id="station-list-body"' in content, "station-list-body must be in index.html"


def test_keyboard_help_modal_in_html():
    """Verify keyboard-help-modal HTML is present in index.html."""
    html = (
        Path(direwolf_dashboard.__file__).parent / "static" / "index.html"
    )
    content = html.read_text(encoding="utf-8")
    assert 'id="keyboard-help-modal"' in content, "keyboard-help-modal must be in index.html"
    assert '<kbd>' in content, "keyboard-help-modal must use <kbd> elements"


def test_station_list_functions_in_app_js():
    """Verify station list and CSV export functions are defined in app.js."""
    app_js = (
        Path(direwolf_dashboard.__file__).parent / "static" / "app.js"
    )
    content = app_js.read_text(encoding="utf-8")
    assert "function initStationListModal" in content, "initStationListModal must be defined"
    assert "function _renderStationTable" in content, "_renderStationTable must be defined"
    assert "function _exportStationsCsv" in content, "_exportStationsCsv must be defined"
    assert "function formatAge" in content, "formatAge must be defined"
    assert "function initKeyboardHelpModal" in content, "initKeyboardHelpModal must be defined"


def test_csv_export_escaping_in_app_js():
    """Verify CSV export properly escapes fields with commas and double-quotes."""
    app_js = (
        Path(direwolf_dashboard.__file__).parent / "static" / "app.js"
    )
    content = app_js.read_text(encoding="utf-8")
    # RFC 4180: fields with commas/quotes/newlines must be quoted
    assert 'v.replace(/"/g, \'""\')'  in content, \
        "_exportStationsCsv must escape double-quotes per RFC 4180"
    assert "text/csv" in content, "CSV export must set correct MIME type"


def test_log_color_vars_in_css():
    """Verify all log color CSS custom properties are defined in style.css."""
    css = (
        Path(direwolf_dashboard.__file__).parent / "static" / "style.css"
    )
    content = css.read_text(encoding="utf-8")
    required_vars = [
        '--log-rx', '--log-tx', '--log-type', '--log-comment',
        '--log-human-info', '--log-bearing', '--log-distance', '--log-dim',
    ]
    for var in required_vars:
        assert var in content, f"CSS variable {var} not defined in style.css"


def test_log_color_map_in_app_js():
    """Verify LOG_COLOR_MAP and log color functions are defined in app.js."""
    app_js = (
        Path(direwolf_dashboard.__file__).parent / "static" / "app.js"
    )
    content = app_js.read_text(encoding="utf-8")
    assert "LOG_COLOR_MAP" in content, "LOG_COLOR_MAP must be defined in app.js"
    assert "function _applyLogColors" in content, "_applyLogColors must be defined"
    assert "function _saveLogColors" in content, "_saveLogColors must be defined"
    assert "function _resetLogColors" in content, "_resetLogColors must be defined"
    assert "LOG_COLORS_KEY" in content, "LOG_COLORS_KEY must be defined"


def test_log_color_settings_html():
    """Verify log color inputs are present in the Settings modal."""
    html = (
        Path(direwolf_dashboard.__file__).parent / "static" / "index.html"
    )
    content = html.read_text(encoding="utf-8")
    assert 'id="lc-rx"' in content, "lc-rx color input must be in settings"
    assert 'id="lc-tx"' in content, "lc-tx color input must be in settings"
    assert 'id="btn-reset-log-colors"' in content, "btn-reset-log-colors must be in settings"


def test_audio_level_signal_bars_in_app_js():
    """Verify audio level signal bar code is present in app.js."""
    app_js = (
        Path(direwolf_dashboard.__file__).parent / "static" / "app.js"
    )
    content = app_js.read_text(encoding="utf-8")
    assert "log-signal" in content, "log-signal class must be in app.js"
    assert "signal-bar" in content, "signal-bar class must be in app.js"
    assert "audio_level" in content, "audio_level must be referenced in app.js"
    assert "Audio level:" in content, "Audio level tooltip must be in app.js"
