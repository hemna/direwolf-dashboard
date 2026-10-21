"""Tests for the real basemap region (issue #132).

Covers: bbox-from-radius math, pmtiles command construction, the ``basemap
fetch`` CLI (subprocess/which mocked — never downloads a planet), the
/basemap/region.pmtiles route (data-dir archive vs vendored sample fallback),
HTTP Range (206/416), and the active-region field in the config payload.
"""

import unittest.mock as mock
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

import direwolf_dashboard
from direwolf_dashboard.basemap import (
    DEFAULT_REGION_ARCHIVE,
    bbox_around,
    build_extract_command,
    default_planet_url,
    parse_bbox,
    region_archive_info,
    resolve_region_archive,
    sha256_file,
)
from direwolf_dashboard.cli import main as cli_main
from direwolf_dashboard.config import Config
from direwolf_dashboard.lifecycle import ServiceContainer
from direwolf_dashboard.routers import create_api_routes, create_region_archive_route

STATIC = Path(direwolf_dashboard.__file__).parent / "static"
SAMPLE = STATIC / "basemap" / "regions" / "sample.pmtiles"


# ---------------------------------------------------------------------------
# Pure functions: bbox math, command construction, planet URL
# ---------------------------------------------------------------------------


def test_bbox_around_centers_on_point():
    min_lon, min_lat, max_lon, max_lat = bbox_around(43.0, -89.0, 75.0)
    assert abs((min_lat + max_lat) / 2 - 43.0) < 1e-9
    assert abs((min_lon + max_lon) / 2 - -89.0) < 1e-9
    # 75 km radius -> ~150 km of latitude span
    assert max_lat - min_lat == pytest.approx(150.0 / 111.0)
    # Longitude span wider than latitude span away from equator
    assert max_lon - min_lon > max_lat - min_lat


def test_bbox_around_radius_zero_is_point():
    assert bbox_around(10.0, 20.0, 0.0) == (20.0, 10.0, 20.0, 10.0)


def test_bbox_around_clamps_to_world():
    min_lon, min_lat, max_lon, max_lat = bbox_around(89.0, -179.0, 75.0)
    assert min_lat >= -90 and max_lat <= 90
    assert min_lon >= -180 and max_lon <= 180


def test_parse_bbox():
    assert parse_bbox(" -89.5 , 42,-88, 44 ") == (-89.5, 42.0, -88.0, 44.0)
    with pytest.raises(ValueError):
        parse_bbox("1,2,3")
    with pytest.raises(ValueError):
        parse_bbox("a,b,c,d")
    with pytest.raises(ValueError):
        parse_bbox("1,2,3,200")  # lon out of range
    with pytest.raises(ValueError):
        parse_bbox("1,2,0,3")  # min_lon > max_lon


def test_build_extract_command_shape():
    bbox = (-89.9, 42.3, -88.1, 43.7)
    cmd = build_extract_command("https://build.protomaps.com/20261019.pmtiles", "/tmp/out.tmp", bbox, 14)
    assert cmd == [
        "pmtiles",
        "extract",
        "https://build.protomaps.com/20261019.pmtiles",
        "/tmp/out.tmp",
        "--bbox=-89.900000,42.300000,-88.100000,43.700000",
        "--maxzoom=14",
    ]


def test_default_planet_url_yesterday_utc():
    now = datetime(2026, 10, 20, 1, 0, tzinfo=UTC)
    assert default_planet_url(now) == "https://build.protomaps.com/20261019.pmtiles"
    # Same UTC day uses the same build regardless of local wall-clock
    late = datetime(2026, 10, 20, 23, 0, tzinfo=UTC)
    assert default_planet_url(late) == default_planet_url(now)


# ---------------------------------------------------------------------------
# CLI: basemap fetch (pmtiles/which mocked — never runs a real planet extract)
# ---------------------------------------------------------------------------

CONFIG_TEMPLATE = """\
data_dir: {data_dir}
station:
  latitude: {lat}
  longitude: {lon}
"""


def _write_config(tmp_path, data_dir, lat, lon):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG_TEMPLATE.format(data_dir=data_dir, lat=lat, lon=lon), encoding="utf-8")
    return cfg


def test_cli_fetch_success(tmp_path):
    data_dir = tmp_path / "data"
    out = tmp_path / "region.pmtiles"
    cfg = _write_config(tmp_path, data_dir, 43.0, -89.0)

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        tmp = Path(cmd[3])
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(b"PMTILES-FAKE")
        return mock.MagicMock(returncode=0)

    with (
        mock.patch("direwolf_dashboard.basemap_auto.probe_source", return_value={}),
        mock.patch("direwolf_dashboard.basemap_auto.shutil.which", return_value="/usr/bin/pmtiles"),
        mock.patch("direwolf_dashboard.basemap_auto.subprocess.run", side_effect=fake_run),
    ):
        result = CliRunner().invoke(
            cli_main, ["-c", str(cfg), "basemap", "fetch", "--output", str(out)]
        )

    assert result.exit_code == 0, result.output
    assert out.exists()
    assert out.read_bytes() == b"PMTILES-FAKE"
    # tmp file must be moved into place atomically
    assert not Path(str(out) + ".tmp.pmtiles").exists()

    cmd = captured["cmd"]
    assert cmd[0:2] == ["pmtiles", "extract"]
    planet, tmp_out = cmd[2], cmd[3]
    # first configured source: the basemap-kit planet on cloud.hemna.com
    assert planet == "https://cloud.hemna.com/basemap-kit/archives/planet.pmtiles"
    assert tmp_out == str(out) + ".tmp.pmtiles"
    assert cmd[4].startswith("--bbox=")
    assert cmd[5] == "--maxzoom=13"  # basemap.maxzoom default
    # bbox matches the default radius box around the station position
    bbox_arg = cmd[4]
    got = tuple(float(v) for v in bbox_arg[len("--bbox="):].split(","))
    assert got == pytest.approx(bbox_around(43.0, -89.0, 40.0))  # basemap.radius_km default

    # Output reports size + sha256
    assert f"sha256: {sha256_file(str(out))}" in result.output
    assert "size" in result.output


def test_cli_fetch_explicit_bbox_and_flags(tmp_path):
    out = tmp_path / "region.pmtiles"
    cfg = _write_config(tmp_path, tmp_path / "data", 0.0, 0.0)  # position irrelevant w/ --bbox

    def fake_run(cmd, **kwargs):
        Path(cmd[3]).parent.mkdir(parents=True, exist_ok=True)
        Path(cmd[3]).write_bytes(b"x")
        return mock.MagicMock(returncode=0)

    with (
        mock.patch("direwolf_dashboard.basemap_auto.probe_source", return_value={}),
        mock.patch("direwolf_dashboard.basemap_auto.shutil.which", return_value="/usr/bin/pmtiles"),
        mock.patch("direwolf_dashboard.basemap_auto.subprocess.run", side_effect=fake_run),
    ):
        result = CliRunner().invoke(
            cli_main,
            [
                "-c", str(cfg), "basemap", "fetch",
                "--bbox", "-90,40,-88,42",
                "--maxzoom", "12",
                "--planet", "https://example.com/planet.pmtiles",
                "--output", str(out),
            ],
        )
    assert result.exit_code == 0, result.output
    cmd = result.output  # planet/bbox visible in the printed command / banner
    assert "https://example.com/planet.pmtiles" in cmd
    assert "bbox=-90.000000,40.000000,-88.000000,42.000000" in cmd
    assert "maxzoom=12" in cmd


def test_cli_fetch_without_pmtiles_cli_uses_python_extractor(tmp_path):
    """No go-pmtiles on the Pi: the built-in extractor does the job (worldwide maps)."""
    from tests.pmtiles_fixture import make_planet

    planet = make_planet(tmp_path / "planet.pmtiles", maxzoom=6)
    data_dir = tmp_path / "data"
    cfg = _write_config(tmp_path, data_dir, 43.0, -89.0)
    with (
        mock.patch("direwolf_dashboard.basemap_auto.shutil.which", return_value=None),
    ):
        result = CliRunner().invoke(
            cli_main, ["-c", str(cfg), "basemap", "fetch", "--planet", str(planet)]
        )
    assert result.exit_code == 0, result.output
    assert "via python" in result.output
    region = data_dir / "basemap" / "region.pmtiles"
    assert region.read_bytes()[:7] == b"PMTiles"
    assert (data_dir / "basemap" / "region.json").exists()


def test_cli_fetch_no_position_no_bbox(tmp_path):
    cfg = _write_config(tmp_path, tmp_path / "data", 0.0, 0.0)
    result = CliRunner().invoke(cli_main, ["-c", str(cfg), "basemap", "fetch"])
    assert result.exit_code == 1
    assert "station.latitude" in result.output or "0,0" in result.output
    assert "--bbox" in result.output


def test_cli_fetch_default_output_is_data_dir_region(tmp_path):
    data_dir = tmp_path / "data"
    cfg = _write_config(tmp_path, data_dir, 43.0, -89.0)

    def fake_run(cmd, **kwargs):
        Path(cmd[3]).parent.mkdir(parents=True, exist_ok=True)
        Path(cmd[3]).write_bytes(b"x")
        return mock.MagicMock(returncode=0)

    with (
        mock.patch("direwolf_dashboard.basemap_auto.probe_source", return_value={}),
        mock.patch("direwolf_dashboard.basemap_auto.shutil.which", return_value="/usr/bin/pmtiles"),
        mock.patch("direwolf_dashboard.basemap_auto.subprocess.run", side_effect=fake_run),
    ):
        result = CliRunner().invoke(cli_main, ["-c", str(cfg), "basemap", "fetch"])
    assert result.exit_code == 0, result.output
    assert str(data_dir / "basemap" / "region.pmtiles") in result.output
    assert (data_dir / "basemap" / "region.pmtiles").exists()


# ---------------------------------------------------------------------------
# /basemap/region.pmtiles route
# ---------------------------------------------------------------------------


def _server(config: Config) -> Starlette:
    container = ServiceContainer()
    services = mock.MagicMock()
    services.config = config
    container.services = services
    return Starlette(routes=[create_region_archive_route(container, str(STATIC))])


def test_route_serves_vendored_sample_fallback(tmp_path):
    config = Config()
    config.data_dir = str(tmp_path)  # empty -> no data-dir archive -> vendored sample
    with TestClient(_server(config), raise_server_exceptions=False) as client:
        r = client.get("/basemap/region.pmtiles")
    assert r.status_code == 200
    assert r.headers.get("content-type", "").startswith("application/octet-stream")
    assert r.headers.get("accept-ranges") == "bytes"
    assert r.content == SAMPLE.read_bytes()
    assert len(r.content) == SAMPLE.stat().st_size


def test_route_serves_data_dir_archive_when_present(tmp_path):
    data_dir = tmp_path / "data"
    archive = data_dir / "basemap" / "region.pmtiles"
    archive.parent.mkdir(parents=True)
    payload = b"REAL-REGION-DATA"
    archive.write_bytes(payload)

    config = Config()
    config.data_dir = str(data_dir)
    config.basemap.region_archive = "basemap/region.pmtiles"
    with TestClient(_server(config), raise_server_exceptions=False) as client:
        r = client.get("/basemap/region.pmtiles")
    assert r.status_code == 200
    assert r.content == payload
    assert int(r.headers["content-length"]) == len(payload)


def test_route_serves_absolute_region_archive(tmp_path):
    archive = tmp_path / "elsewhere.pmtiles"
    archive.write_bytes(b"ABS")

    config = Config()
    config.data_dir = str(tmp_path)
    config.basemap.region_archive = str(archive)
    with TestClient(_server(config), raise_server_exceptions=False) as client:
        r = client.get("/basemap/region.pmtiles")
    assert r.status_code == 200
    assert r.content == b"ABS"


def test_route_404_when_configured_archive_missing(tmp_path):
    config = Config()
    config.data_dir = str(tmp_path)
    config.basemap.region_archive = "nope/missing.pmtiles"
    with TestClient(_server(config), raise_server_exceptions=False) as client:
        r = client.get("/basemap/region.pmtiles")
    assert r.status_code == 404


def test_route_range_206(tmp_path):
    total = SAMPLE.stat().st_size
    config = Config()
    config.data_dir = str(tmp_path)
    with TestClient(_server(config), raise_server_exceptions=False) as client:
        r = client.get("/basemap/region.pmtiles", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes 0-99/{total}"
    assert r.headers["accept-ranges"] == "bytes"
    assert len(r.content) == 100
    assert r.content == SAMPLE.read_bytes()[:100]

    mid_start, mid_end = total - 50, total - 1
    with TestClient(_server(config), raise_server_exceptions=False) as client:
        r = client.get(
            "/basemap/region.pmtiles",
            headers={"Range": f"bytes={mid_start}-{mid_end}"},
        )
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes {mid_start}-{mid_end}/{total}"
    assert r.content == SAMPLE.read_bytes()[-50:]


def test_route_range_416_invalid(tmp_path):
    total = SAMPLE.stat().st_size
    config = Config()
    config.data_dir = str(tmp_path)
    with TestClient(_server(config), raise_server_exceptions=False) as client:
        r = client.get(
            "/basemap/region.pmtiles", headers={"Range": "bytes=999999999-"}
        )
    assert r.status_code == 416
    assert r.headers["content-range"] == f"bytes */{total}"


# ---------------------------------------------------------------------------
# Config resolution helpers + active region in /api/config
# ---------------------------------------------------------------------------


def test_resolve_region_archive_priority(tmp_path, monkeypatch):
    static = tmp_path / "static"
    static.mkdir()
    data = tmp_path / "data"
    arc = "basemap/region.pmtiles"
    data_arc = data / arc
    data_arc.parent.mkdir(parents=True)
    data_arc.write_bytes(b"D")
    (static / arc).parent.mkdir(parents=True)
    (static / arc).write_bytes(b"S")

    # absolute path wins
    abs_path = tmp_path / "abs.pmtiles"
    abs_path.write_bytes(b"A")
    assert resolve_region_archive(str(abs_path), str(data), str(static)) == str(abs_path)

    # data-dir-relative file that exists wins over static
    assert resolve_region_archive(arc, str(data), str(static)) == str(data_arc)

    # static-relative fallback (default sample) when not in data_dir
    assert resolve_region_archive(DEFAULT_REGION_ARCHIVE, str(tmp_path / "empty"), str(STATIC)) == str(SAMPLE)


def test_region_archive_info(tmp_path):
    f = tmp_path / "region.pmtiles"
    f.write_bytes(b"12345")
    assert region_archive_info(str(f)) == {"name": "region.pmtiles", "size": 5}
    assert region_archive_info(str(tmp_path / "missing.pmtiles")) is None


def test_config_payload_exposes_active_region(tmp_path):
    """/api/config GET includes the active region name+size (issue #132)."""
    config = Config()
    config.data_dir = str(tmp_path)  # empty data dir -> vendored sample active
    container = ServiceContainer()
    services = mock.MagicMock()
    services.config = config
    services.storage.get_my_position = mock.AsyncMock(return_value=None)
    from direwolf_dashboard.basemap_auto import AutoFetchStatus
    services.basemap_status = AutoFetchStatus()
    services.basemap_status.set("no_position", "no station position yet; using the world map")
    container.services = services
    app = Starlette(routes=[Mount("/api", routes=create_api_routes(container))])
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get("/api/config")
    assert r.status_code == 200
    assert r.json()["basemap"]["auto"]["state"] == "no_position"
    region = r.json()["basemap"]["region"]
    assert region == {"name": "sample.pmtiles", "size": SAMPLE.stat().st_size}

    # With a real data-dir archive configured, that file is reported as active.
    config.basemap.region_archive = "basemap/region.pmtiles"
    data_arc = tmp_path / "basemap" / "region.pmtiles"
    data_arc.parent.mkdir(parents=True)
    data_arc.write_bytes(b"D")
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get("/api/config")
    assert r.status_code == 200
    assert r.json()["basemap"]["region"] == {"name": "region.pmtiles", "size": 1}


def test_fetched_region_preferred_over_sample_when_default(tmp_path):
    """`basemap fetch` output is served without any config change."""
    arc = tmp_path / "basemap" / "region.pmtiles"
    arc.parent.mkdir(parents=True)
    arc.write_bytes(b"FETCHED")
    config = Config()
    config.data_dir = str(tmp_path)
    with TestClient(_server(config), raise_server_exceptions=False) as client:
        r = client.get("/basemap/region.pmtiles")
    assert r.status_code == 200
    assert r.content == b"FETCHED"


def test_explicit_region_archive_beats_fetched_default(tmp_path):
    (tmp_path / "basemap").mkdir()
    (tmp_path / "basemap" / "region.pmtiles").write_bytes(b"FETCHED")
    (tmp_path / "basemap" / "va.pmtiles").write_bytes(b"VIRGINIA")
    config = Config()
    config.data_dir = str(tmp_path)
    config.basemap.region_archive = "basemap/va.pmtiles"
    with TestClient(_server(config), raise_server_exceptions=False) as client:
        r = client.get("/basemap/region.pmtiles")
    assert r.content == b"VIRGINIA"


def test_region_archive_from_yaml_config_is_honored(tmp_path):
    """basemap.region_archive set in config.yaml is what the route serves."""
    from direwolf_dashboard.config import load_config

    data_dir = tmp_path / "data"
    (data_dir / "basemap").mkdir(parents=True)
    (data_dir / "basemap" / "va-appomattox.pmtiles").write_bytes(b"VA-REGION")
    abs_arc = tmp_path / "abs.pmtiles"
    abs_arc.write_bytes(b"ABS-REGION")
    for value, expected in (("basemap/va-appomattox.pmtiles", b"VA-REGION"), (str(abs_arc), b"ABS-REGION")):
        cfg = _write_config(tmp_path, data_dir, 37.34, -78.85)
        cfg.write_text(cfg.read_text(encoding="utf-8") + f"basemap:\n  region_archive: {value}\n", encoding="utf-8")
        config = load_config(str(cfg))
        assert config.basemap.region_archive == value
        with TestClient(_server(config), raise_server_exceptions=False) as client:
            r = client.get("/basemap/region.pmtiles")
        assert r.status_code == 200
        assert r.content == expected
