"""Tests for the kit region-manifest loader + served routes (issue #204).

Port of the haminfo reference asserts (merge ad2e8262, Appendix A): loader
unit tests for the kit manifest shape and error filtering, and client-level
asserts on the served ``/basemap/maps-manifest.json`` body, the
``/basemap/regions/{name}.pmtiles`` archive route (HTTP Range 206, CORS,
404 + traversal) and the 503 + caplog manifest error shapes.
"""

import json

import pytest
from direwolf_dashboard.basemap import (
    BASEMAP_DIR,
    INTERNAL_REGION_IDS,
    REGION_MANIFEST_FILE,
    BasemapSourceError,
    kit_region_manifest,
)
from direwolf_dashboard.lifecycle import ServiceContainer
from direwolf_dashboard.routers import (
    create_basemap_manifest_route,
    create_basemap_region_archive_route,
)
from starlette.applications import Starlette
from starlette.testclient import TestClient

SAMPLE = BASEMAP_DIR / "regions" / "sample.pmtiles"

# ---------------------------------------------------------------------------
# A kit manifest shape (hemna/basemap-kit, docs/CLIENTS.md §2)
# ---------------------------------------------------------------------------

CONTINENT_GROUPS = [
    {"id": "africa", "name": "Africa", "order": 10},
    {"id": "antarctica", "name": "Antarctica", "order": 70},
    {"id": "asia", "name": "Asia", "order": 20},
    {"id": "europe", "name": "Europe", "order": 30},
    {"id": "north-america", "name": "North America", "order": 40},
    {"id": "oceania", "name": "Oceania", "order": 60},
    {"id": "south-america", "name": "South America", "order": 50},
    {"id": "sample", "name": "Sample", "order": 0},
]


def _kit_manifest(regions=("sample", "world")):
    """Two-region manifest in the shape the kit publishes."""
    doc = {
        "schemaVersion": 1,
        "tileSchema": "v4",
        "buildDate": "2026-10-28T00:00:00Z",
        "groups": [{"id": "sample", "name": "Sample", "order": 0},
                   {"id": "world", "name": "World overview", "order": 10}],
        "regions": [
            {"id": rid,
             "name": rid.title(),
             "group": "sample" if rid == "sample" else "world",
             "url": f"pmtiles://https://cloud.hemna.com/basemap-kit/"
                    f"archives/{rid}.pmtiles",
             "bytes": 999999,
             "sha256": "a" * 64,
             "maxzoom": 11,
             "bounds": [-1.0, -1.0, 1.0, 1.0]}
            for rid in regions
        ],
    }
    return doc


def _full_kit_manifest():
    """The whole kit list: 7 continent groups + sample, 33 regions."""
    groups = list(CONTINENT_GROUPS)
    regions = [
        {"id": f"{g['id']}-{i}",
         "name": f"{g['name']} {i}",
         "group": g["id"],
         "url": f"pmtiles://https://cloud.hemna.com/basemap-kit/"
                f"archives/{g['id']}-{i}.pmtiles",
         "bytes": 999999,
         "sha256": "a" * 64,
         "maxzoom": 11,
         "bounds": [-1.0, -1.0, 1.0, 1.0]}
        for rep in range(5)
        for i, g in enumerate(groups)
    ][:33]
    return {"schemaVersion": 1,
            "tileSchema": "v4",
            "buildDate": "2026-10-28T00:00:00Z",
            "groups": groups,
            "regions": regions}


def _write_manifest(tmp_path, doc, name="maps-manifest.json"):
    path = tmp_path / name
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def test_region_manifest_file_constant_points_at_the_basemap_dir():
    assert REGION_MANIFEST_FILE.name == "maps-manifest.json"
    assert REGION_MANIFEST_FILE.parent.name == "basemap"


def test_full_kit_manifest_is_a_complete_kit_list(tmp_path):
    # Port of test_committed_manifest_is_a_complete_kit_list: the kit list
    # (seven continent slugs + sample) must load with 33 regions, every
    # region filed under a declared group, integer maxzooms.
    path = _write_manifest(tmp_path, _full_kit_manifest())
    doc = kit_region_manifest(path)
    declared = {g["id"] for g in doc["groups"]}
    assert {"africa", "antarctica", "asia", "europe", "north-america",
            "oceania", "south-america", "sample"} <= declared
    assert len(doc["regions"]) == 33
    assert {r["group"] for r in doc["regions"]} <= declared
    assert all(r["maxzoom"] == int(r["maxzoom"]) for r in doc["regions"])


def test_loader_accepts_a_kit_manifest(tmp_path):
    path = _write_manifest(tmp_path, _kit_manifest())
    doc = kit_region_manifest(path)
    assert doc["schemaVersion"] == 1
    assert [r["id"] for r in doc["regions"]] == ["sample", "world"]


def test_sample_and_world_survive_low_fallback_never_does(tmp_path):
    # The kit publishes an internal zoom-11 gap filler; it is never a
    # choice an app can download.
    doc = _kit_manifest(regions=("sample", "world", "low-fallback"))
    path = _write_manifest(tmp_path, doc)
    loaded = kit_region_manifest(path)
    assert [r["id"] for r in loaded["regions"]] == ["sample", "world"]
    assert "low-fallback" in INTERNAL_REGION_IDS


def test_loader_raises_when_the_file_is_unreadable(tmp_path):
    with pytest.raises(BasemapSourceError, match="unreadable"):
        kit_region_manifest(tmp_path / "nope.json")


def test_loader_raises_when_the_file_is_not_valid_json(tmp_path):
    path = tmp_path / "maps-manifest.json"
    path.write_text('{"regions": [', encoding="utf-8")
    with pytest.raises(BasemapSourceError, match="not valid JSON"):
        kit_region_manifest(path)


def test_loader_raises_when_a_region_field_is_missing(tmp_path):
    doc = _kit_manifest()
    del doc["regions"][0]["bounds"]
    path = _write_manifest(tmp_path, doc)
    with pytest.raises(BasemapSourceError, match="bounds"):
        kit_region_manifest(path)


def test_loader_raises_when_a_region_points_at_an_undeclared_group(tmp_path):
    doc = _kit_manifest()
    doc["regions"][0]["group"] = "atlantis"
    path = _write_manifest(tmp_path, doc)
    with pytest.raises(BasemapSourceError, match="atlantis"):
        kit_region_manifest(path)


def test_loader_raises_when_a_required_top_level_key_is_missing(tmp_path):
    doc = _kit_manifest()
    del doc["tileSchema"]
    path = _write_manifest(tmp_path, doc)
    with pytest.raises(BasemapSourceError, match="tileSchema"):
        kit_region_manifest(path)


def test_loader_raises_when_maxzoom_is_not_an_int(tmp_path):
    doc = _kit_manifest()
    doc["regions"][0]["maxzoom"] = 11.5
    path = _write_manifest(tmp_path, doc)
    with pytest.raises(BasemapSourceError, match="maxzoom"):
        kit_region_manifest(path)


def test_loader_raises_when_group_order_is_not_an_int(tmp_path):
    doc = _kit_manifest()
    doc["groups"][0]["order"] = "zero"
    path = _write_manifest(tmp_path, doc)
    with pytest.raises(BasemapSourceError, match="order"):
        kit_region_manifest(path)


def test_loader_raises_on_empty_regions_or_groups(tmp_path):
    doc = _kit_manifest()
    doc["groups"] = []
    path = _write_manifest(tmp_path, doc)
    with pytest.raises(BasemapSourceError, match="regions and groups"):
        kit_region_manifest(path)


def test_loader_raises_when_a_group_entry_is_not_an_object(tmp_path):
    # A structurally corrupt manifest must not leak a bare AttributeError:
    # every consumer catches BasemapSourceError.
    doc = _kit_manifest()
    doc["groups"] = ["oops"]
    path = _write_manifest(tmp_path, doc)
    with pytest.raises(BasemapSourceError, match="oops"):
        kit_region_manifest(path)


def test_loader_raises_when_a_region_entry_is_not_an_object(tmp_path):
    doc = _kit_manifest()
    doc["regions"] = ["oops"]
    path = _write_manifest(tmp_path, doc)
    with pytest.raises(BasemapSourceError, match="oops"):
        kit_region_manifest(path)


def test_int_type_violations_do_not_say_missing(tmp_path):
    doc = _kit_manifest()
    doc["groups"][0]["order"] = "zero"
    path = _write_manifest(tmp_path, doc)
    with pytest.raises(BasemapSourceError) as ei:
        kit_region_manifest(path)
    assert "order is not an int" in str(ei.value)
    assert "missing" not in str(ei.value)


# ---------------------------------------------------------------------------
# Served region list + per-region archives (task 2: the two new routes)
# ---------------------------------------------------------------------------


def _basemap_app() -> Starlette:
    """App holding only the two new routes (they read the package tree,
    not live services, so a bare container is enough)."""
    container = ServiceContainer()
    return Starlette(
        routes=[
            create_basemap_manifest_route(container),
            create_basemap_region_archive_route(container),
        ]
    )


def test_maps_manifest_serves_the_kit_list_verbatim():
    # The committed kit list (33 regions) served through this app must
    # carry its groups untouched and only the regions this host can
    # actually serve — every field verbatim, only ``url`` rewritten to
    # the same-origin archive route (never the kit's production host).
    published = kit_region_manifest()
    with TestClient(_basemap_app(), raise_server_exceptions=False) as client:
        r = client.get("/basemap/maps-manifest.json")
        body = r.json()
        # every listed region is downloadable from this host (CLIENTS.md §2)
        for reg in body["regions"]:
            part = client.get(reg["url"], headers={"Range": "bytes=0-126"})
            assert part.status_code == 206, reg["url"]
            assert len(part.content) == 127
            assert part.headers["accept-ranges"] == "bytes"
    assert r.status_code == 200
    assert body["groups"] == published["groups"]
    assert [reg["id"] for reg in body["regions"]] == ["sample", "world"]
    published_by_id = {reg["id"]: reg for reg in published["regions"]}
    for served in body["regions"]:
        kit_region = published_by_id[served["id"]]
        assert {k: v for k, v in served.items() if k != "url"} == {
            k: v for k, v in kit_region.items() if k != "url"
        }
        assert served["url"] == f"/basemap/regions/{served['id']}.pmtiles"
    assert "cloud.hemna.com" not in r.text
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers["access-control-allow-origin"] == "*"


def test_region_archive_route_range_206():
    total = SAMPLE.stat().st_size
    with TestClient(_basemap_app(), raise_server_exceptions=False) as client:
        r = client.get("/basemap/regions/sample.pmtiles",
                       headers={"Range": "bytes=0-1023"})
    assert r.status_code == 206
    assert len(r.content) == 1024
    assert r.headers["content-range"] == f"bytes 0-1023/{total}"
    assert r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-type"] == "application/vnd.pmtiles"
    assert r.headers["access-control-allow-origin"] == "*"


def test_region_archive_route_404_and_traversal():
    with TestClient(_basemap_app(), raise_server_exceptions=False) as client:
        assert client.get("/basemap/regions/missing.pmtiles").status_code == 404
        # kit-internal gap filler is never a client choice
        assert client.get("/basemap/regions/low-fallback.pmtiles").status_code == 404
        # encoded upward paths must not escape REGIONS_DIR
        assert (
            client.get("/basemap/regions/..%2f..%2fetc%2fpasswd.pmtiles").status_code
            == 404
        )


def test_maps_manifest_errors_when_the_list_is_missing(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(
        "direwolf_dashboard.basemap.REGION_MANIFEST_FILE", tmp_path / "nope.json"
    )
    with TestClient(_basemap_app(), raise_server_exceptions=False) as client:
        with caplog.at_level("ERROR", logger="direwolf_dashboard.routers"):
            r = client.get("/basemap/maps-manifest.json")
    assert r.status_code == 503
    assert "unreadable" in r.json()["detail"]
    assert "nope.json" in caplog.text


def test_maps_manifest_errors_when_the_list_is_malformed(monkeypatch, tmp_path, caplog):
    path = tmp_path / "maps-manifest.json"
    path.write_text('{"regions": [', encoding="utf-8")
    monkeypatch.setattr(
        "direwolf_dashboard.basemap.REGION_MANIFEST_FILE", path
    )
    with TestClient(_basemap_app(), raise_server_exceptions=False) as client:
        with caplog.at_level("ERROR", logger="direwolf_dashboard.routers"):
            r = client.get("/basemap/maps-manifest.json")
    assert r.status_code == 503
    assert "not valid JSON" in r.json()["detail"]
    assert "not valid JSON" in caplog.text


def test_maps_manifest_errors_when_a_region_field_is_missing(monkeypatch, tmp_path, caplog):
    doc = _kit_manifest()
    del doc["regions"][0]["bounds"]
    path = _write_manifest(tmp_path, doc)
    monkeypatch.setattr(
        "direwolf_dashboard.basemap.REGION_MANIFEST_FILE", path
    )
    with TestClient(_basemap_app(), raise_server_exceptions=False) as client:
        with caplog.at_level("ERROR", logger="direwolf_dashboard.routers"):
            r = client.get("/basemap/maps-manifest.json")
    assert r.status_code == 503
    assert "bounds" in r.json()["detail"]
    assert "bounds" in caplog.text


def test_maps_manifest_errors_when_a_region_points_at_an_undeclared_group(
    monkeypatch, tmp_path, caplog
):
    doc = _kit_manifest()
    doc["regions"][0]["group"] = "atlantis"
    path = _write_manifest(tmp_path, doc)
    monkeypatch.setattr(
        "direwolf_dashboard.basemap.REGION_MANIFEST_FILE", path
    )
    with TestClient(_basemap_app(), raise_server_exceptions=False) as client:
        with caplog.at_level("ERROR", logger="direwolf_dashboard.routers"):
            r = client.get("/basemap/maps-manifest.json")
    assert r.status_code == 503
    assert "atlantis" in r.json()["detail"]
    assert "atlantis" in caplog.text
