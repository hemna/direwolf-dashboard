"""Worldwide offline maps: auto region download around the station."""
import asyncio
import json
from unittest import mock

import pytest
from pmtiles.reader import Reader
from pmtiles.tile import deserialize_header

from direwolf_dashboard import basemap_auto, basemap_extract
from direwolf_dashboard.basemap import bbox_around
from direwolf_dashboard.config import Config
from tests.pmtiles_fixture import make_planet, tile_bytes


# --- extractor ---------------------------------------------------------------

def test_extract_contains_exactly_bbox_tiles(tmp_path):
    planet = make_planet(tmp_path / "planet.pmtiles", maxzoom=6)
    bbox = (14.0, 49.5, 15.0, 50.5)  # Prague, not the US
    out = tmp_path / "region.pmtiles"
    stats = basemap_extract.extract(basemap_extract.file_get_bytes(str(planet)), str(out), bbox, 6)
    want = basemap_extract.tiles_in_bbox(bbox, 0, 6)
    assert stats["tiles"] == len(want)
    reader = Reader(basemap_extract.file_get_bytes(str(out)))
    assert reader.get(0, 0, 0) == tile_bytes(0, 0, 0)
    # Prague at z6 is x=34, y=21
    assert reader.get(6, 34, 21) == tile_bytes(6, 34, 21)
    assert reader.get(6, 10, 10) is None  # outside the bbox
    h = deserialize_header(out.read_bytes()[:127])
    assert (h["min_zoom"], h["max_zoom"]) == (0, 6)
    assert h["min_lon_e7"] == 140000000 and h["max_lat_e7"] == 505000000


def test_extract_caps_maxzoom_at_planet(tmp_path):
    planet = make_planet(tmp_path / "p.pmtiles", maxzoom=4)
    stats = basemap_extract.extract(
        basemap_extract.file_get_bytes(str(planet)), str(tmp_path / "r.pmtiles"), (0, 0, 1, 1), 13)
    assert stats["maxzoom"] == 4


def test_extract_coalesces_reads(tmp_path):
    planet = make_planet(tmp_path / "p.pmtiles", maxzoom=6)
    calls = []
    inner = basemap_extract.file_get_bytes(str(planet))

    def counting(o, n):
        calls.append((o, n))
        return inner(o, n)

    stats = basemap_extract.extract(counting, str(tmp_path / "r.pmtiles"), (14, 49.5, 15, 50.5), 6)
    assert len(calls) < stats["tiles"], "tile reads must be coalesced, not one per tile"


def test_extract_refuses_huge_regions(tmp_path):
    planet = make_planet(tmp_path / "p.pmtiles", maxzoom=2)
    with mock.patch.object(basemap_extract, "estimate_tile_count", return_value=10**9):
        with pytest.raises(basemap_extract.ExtractError, match="too large"):
            basemap_extract.extract(basemap_extract.file_get_bytes(str(planet)),
                                    str(tmp_path / "r.pmtiles"), (0, 0, 1, 1), 2)


def test_default_radius_zoom_are_pi_friendly():
    bm = Config().basemap
    assert bm.auto_fetch is True
    assert bm.radius_km == 40.0 and bm.maxzoom == 13
    assert basemap_extract.estimate_tile_count(bbox_around(50.08, 14.42, bm.radius_km), bm.maxzoom) < 5000


# --- needs_fetch ---------------------------------------------------------------

def test_needs_fetch_when_missing(tmp_path):
    need, why = basemap_auto.needs_fetch(1, 2, 40, 13, str(tmp_path / "none"), None)
    assert need and "no region" in why


def test_hand_copied_region_without_meta_is_kept(tmp_path):
    r = tmp_path / "region.pmtiles"
    r.write_bytes(b"x")
    assert basemap_auto.needs_fetch(1, 2, 40, 13, str(r), None)[0] is False


def test_refetch_when_station_moves_or_radius_grows(tmp_path):
    r = tmp_path / "region.pmtiles"
    r.write_bytes(b"x")
    meta = {"lat": 50.08, "lon": 14.42, "radius_km": 40, "maxzoom": 13}
    assert basemap_auto.needs_fetch(50.09, 14.43, 40, 13, str(r), meta)[0] is False
    assert basemap_auto.needs_fetch(-33.87, 151.21, 40, 13, str(r), meta)[0] is True  # Sydney
    assert basemap_auto.needs_fetch(50.08, 14.42, 80, 13, str(r), meta)[0] is True
    assert basemap_auto.needs_fetch(50.08, 14.42, 40, 14, str(r), meta)[0] is True


# --- auto_fetch_once: graceful degradation --------------------------------------

def _cfg(tmp_path, **bm):
    c = Config(data_dir=str(tmp_path))
    for k, v in bm.items():
        setattr(c.basemap, k, v)
    return c


def _pos(value):
    async def p():
        return value
    return p


def test_no_position_falls_back_to_world(tmp_path):
    st = basemap_auto.AutoFetchStatus()
    done = asyncio.run(basemap_auto.auto_fetch_once(_cfg(tmp_path), st, _pos(None)))
    assert done is False and st.state == "no_position" and "world" in st.detail


def test_network_failure_falls_back_to_world(tmp_path):
    st = basemap_auto.AutoFetchStatus()
    cfg = _cfg(tmp_path, planet_url="http://127.0.0.1:9/planet.pmtiles")
    with mock.patch.object(basemap_auto.shutil, "which", return_value=None):
        done = asyncio.run(basemap_auto.auto_fetch_once(cfg, st, _pos((50.08, 14.42))))
    assert done is False and st.state == "failed" and "world" in st.detail
    assert not (tmp_path / "basemap" / "region.pmtiles").exists()


def test_disabled_and_explicit_archive_are_respected(tmp_path):
    st = basemap_auto.AutoFetchStatus()
    assert asyncio.run(basemap_auto.auto_fetch_once(_cfg(tmp_path, auto_fetch=False), st, _pos((1, 2))))
    assert st.state == "disabled"
    assert asyncio.run(basemap_auto.auto_fetch_once(
        _cfg(tmp_path, region_archive="/srv/my.pmtiles"), st, _pos((1, 2))))
    assert st.state == "skipped"


def test_first_run_downloads_region_and_sidecar(tmp_path):
    planet = make_planet(tmp_path / "planet.pmtiles", maxzoom=6)
    data = tmp_path / "data"
    cfg = _cfg(data, planet_url=str(planet), maxzoom=6)
    st = basemap_auto.AutoFetchStatus()
    with mock.patch.object(basemap_auto.shutil, "which", return_value=None):
        assert asyncio.run(basemap_auto.auto_fetch_once(cfg, st, _pos((-33.87, 151.21))))
    assert st.state == "done"
    region = data / "basemap" / "region.pmtiles"
    meta = json.loads((data / "basemap" / "region.json").read_text())
    assert region.read_bytes()[:7] == b"PMTiles"
    assert meta["method"] == "python" and meta["radius_km"] == 40.0
    # Second start: region is current, nothing downloaded again.
    st2 = basemap_auto.AutoFetchStatus()
    assert asyncio.run(basemap_auto.auto_fetch_once(cfg, st2, _pos((-33.87, 151.21))))
    assert st2.state == "current"


def test_cli_used_when_installed(tmp_path):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        open(cmd[3], "wb").write(b"PMTiles-fake")

    with mock.patch.object(basemap_auto.shutil, "which", return_value="/usr/bin/pmtiles"), \
            mock.patch.object(basemap_auto.subprocess, "run", side_effect=fake_run):
        meta = basemap_auto.fetch_region(35.68, 139.69, 40, 13, str(tmp_path), "https://x/p.pmtiles")
    assert meta["method"] == "pmtiles-cli"
    assert seen["cmd"][:3] == ["pmtiles", "extract", "https://x/p.pmtiles"]
    assert "--maxzoom=13" in seen["cmd"]


def test_loop_polls_for_position_then_fetches(tmp_path):
    planet = make_planet(tmp_path / "planet.pmtiles", maxzoom=3)
    cfg = _cfg(tmp_path / "d", planet_url=str(planet), maxzoom=3)
    st = basemap_auto.AutoFetchStatus()
    answers = iter([None, (64.14, -21.94)])  # Reykjavik after the first beacon

    async def pos():
        return next(answers)

    with mock.patch.object(basemap_auto.shutil, "which", return_value=None):
        asyncio.run(basemap_auto.auto_fetch_loop(cfg, st, pos, retry_seconds=0, position_poll_seconds=0))
    assert st.state == "done"


@pytest.mark.real_auto_fetch
def test_startup_schedules_auto_fetch():
    import inspect
    from direwolf_dashboard import lifecycle
    src = inspect.getsource(lifecycle.startup_services)
    assert "auto_fetch_loop(config, services.basemap_status" in src


def test_config_api_reports_auto_status():
    import inspect
    from direwolf_dashboard import routers
    assert 'd["basemap"]["auto"] = services.basemap_status.to_dict()' in inspect.getsource(routers)
