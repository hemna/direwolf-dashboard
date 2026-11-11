"""Shareable #zoom/lat/lon map URL (issue #150)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_maplibre_main_map import _extract_js_function

STATIC = Path(__file__).resolve().parent.parent / "src" / "direwolf_dashboard" / "static"
JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")


def _node(body):
    if not shutil.which("node"):
        pytest.skip("node not installed")
    src = "\n".join(
        _extract_js_function(JS, n) for n in ("parseMapHash", "formatMapHash")
    )
    r = subprocess.run(["node", "-e", src + "\n" + body], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_parse_haminfo_format_and_optional_bearing_pitch():
    out = _node(
        "console.log(JSON.stringify(["
        "parseMapHash('#12/37.34/-78.85'),"
        "parseMapHash('12.5/37.3400/-78.8500'),"
        "parseMapHash('#12/37.34/-78.85/30/45'),"
        "]))"
    )
    assert out[0] == {"zoom": 12, "lat": 37.34, "lon": -78.85, "bearing": 0, "pitch": 0}
    assert out[1]["zoom"] == 12.5
    assert out[2]["bearing"] == 30 and out[2]["pitch"] == 45


@pytest.mark.parametrize(
    "h", ["", "#", "#settings", "#12/37.34", "#a/b/c", "#12//-78", "#30/37/-78", "#12/95/0",
          "#12/0/200", "#1/2/3/4/5/6"],
)
def test_parse_rejects_non_view_hashes(h):
    assert _node(f"console.log(JSON.stringify(parseMapHash({json.dumps(h)})))") is None


def test_format_matches_haminfo_and_round_trips():
    out = _node(
        "const h = formatMapHash(12, 37.34, -78.85, 0, 0);"
        "const h2 = formatMapHash(11.23456, 37.123456, -78.987654, 0.2, 0);"
        "const h3 = formatMapHash(12, 37.34, -78.85, 30, 45);"
        "console.log(JSON.stringify([h, h2, h3, parseMapHash(h3)]))"
    )
    assert out[0] == "#12/37.3400/-78.8500"  # haminfo /map: zoom/lat.4f/lng.4f
    assert out[1] == "#11.23/37.1235/-78.9877"
    assert out[2] == "#12/37.3400/-78.8500/30/45"
    assert out[3]["bearing"] == 30 and out[3]["pitch"] == 45


def test_hash_read_before_map_and_written_on_moveend():
    init = _extract_js_function(JS, "initMap")
    assert init.index("urlMapView = parseMapHash(window.location.hash)") < init.index(
        "new maplibregl.Map("
    )
    assert "center: urlMapView ? [urlMapView.lon, urlMapView.lat] : [lon, lat]" in init
    assert "history.replaceState(null, '', h)" in init
    assert "addEventListener('hashchange'" in init


def test_url_view_wins_over_saved_center_and_waiting():
    fn = _extract_js_function(JS, "initMapCenter")
    url_idx = fn.index("if (urlMapView) return;")
    assert url_idx < fn.index("localStorage.getItem('dw-map-lat')")
    assert url_idx < fn.index("showWaitingModal()")
    # saved-center persistence is untouched for the no-hash case
    assert "localStorage.setItem('dw-map-lat', c.lat);" in JS
