# Plan: kit region list (#204) — direwolf-dashboard

Companion to the four HELD enhancements; this one is the basemap-kit client rule
conformance. Mirrors haminfo PR #344 (merge `ad2e8262`, feat `407408e0`).

- Issue: http://git.hemna.com/hemna/direwolf-dashboard/issues/204
- Client rules: http://git.hemna.com/hemna/basemap-kit/src/branch/docs/61-client-spec/docs/CLIENTS.md
- Reference: `~/devel/mine/hamradio/haminfo` @ `ad2e8262`
  - `haminfo-dashboard/src/haminfo_dashboard/basemap-manifest.json` (kit manifest copy)
  - `haminfo-dashboard/src/haminfo_dashboard/basemap.py` (+`kit_region_manifest`, `basemap_maps_manifest`, `REGION_MANIFEST_ROUTE`, `INTERNAL_REGION_IDS`, `CORS_HEADERS`)
  - `haminfo-dashboard/tests/test_basemap.py` (new manifest asserts)

## Measured current state (this repo, master `3e98b2f`)

- `basemap.py:39` `DEFAULT_REGION_ARCHIVE = "basemap/regions/sample.pmtiles"`;
  `region_url()` (`:49`) rejects paths escaping the static mount;
  `resolve_region_archive()` (`:132`) priority: absolute path →
  `<data_dir>/basemap/region.pmtiles` → `<data_dir>/<value>` →
  `<static_dir>/<value>`; `region_archive_info()` (`:159`) → `{name,size}`.
- `routers.py:610` `Route("/basemap/region.pmtiles", get_region_archive)`;
  `:635` style route; `:664` upstream tile proxy with `PROXY_CACHE_MAX_ENTRIES=512`,
  16 MiB in-memory.
- `routers.py:300` `GET /api/config` already reports `d["basemap"]["region"]`.
- No `maps-manifest.json` anywhere in the tree; no region picker.
- CI: `.forgejo/workflows/ci.yml` runs `ruff check . --select=E,F --ignore=E501`,
  `pytest tests/ -q`, and `node --check` on every non-vendor `.js`.

## Design (the smallest conforming change)

1. **Region choice by id.** `basemap.region_archive` may name a manifest region
   *id* (`sample`, `world`, …). Resolution adds one rule ahead of the existing
   path rules: if the value is not an absolute path, has no `.pmtiles` suffix,
   and `<static_dir>/basemap/regions/<id>.pmtiles` exists → serve that file.
   Unknown id → fall back to `sample` **with a `LOG.warning` line**, never a blank map.
   Existing values (paths, absolute paths) keep working unchanged.
2. **Serve the shared list.** Commit a copy of the kit manifest at
   `src/direwolf_dashboard/static/basemap/maps-manifest.json` and serve
   `GET /basemap/maps-manifest.json`, filtered to the ids this host can actually
   serve (today: `sample`, `world`), with `url` rewritten to the same-origin
   `/basemap/regions/<id>.pmtiles` route. Never emit `cloud.hemna.com`.
3. **New archive route.** `GET /basemap/regions/{name}.pmtiles` serves any
   archive in `static/basemap/regions/` with byte-range support
   (starlette `FileResponse`; ranges already proven working for
   `/basemap/region.pmtiles` per tests/test_basemap.py).
4. **Client: read-only list.** Map init fetches the manifest once per day
   (localStorage timestamp), renders id + name + bytes in the existing info/settings
   surface. No download UI, no map behaviour change from the picker.
5. **Cache documentation** stays a docs line pointing at the shared spec; no new cache.

Manifest validation errors are 503 + log line (mirrors haminfo tests): unreadable
file, not-valid-JSON, region field missing, region pointing at an undeclared
group. `schemaVersion`, `groups[].order`, `regions[].maxzoom` int,
`INTERNAL_REGION_IDS` exclusion (`low-fallback`) as in the reference.

## Tasks (TDD, one commit each)

### T1 — Manifest loader (`basemap.py`)
- **RED:** new `tests/test_basemap_manifest.py`, port of the reference asserts in
  `ad2e8262:…/tests/test_basemap.py`:
  `test_committed_manifest_is_a_complete_kit_list` (kit copy has
  `africa/antarctica/asia/europe/north-america/…` groups, 33 regions, every
  `region.group` declared, `maxzoom` ints); loader unit tests for the error
  shapes (unreadable → raises, malformed JSON → raises, missing field → raises,
  undeclared group → raises) and for filtering (`['sample','world']` survive on
  this tree, `low-fallback` never does).
- **GREEN:** add `kit_region_manifest(path=None) -> dict` +
  `REGION_MANIFEST_FILE = BASEMAP_DIR / "maps-manifest.json"`.
- Commit: `feat(basemap): read the kit region manifest (#204)`

### T2 — Manifest + archive routes (`routers.py`)
- **RED:** extend `tests/test_basemap_manifest.py` with client-level asserts on the
  served body (`groups` verbatim, region ids exactly `['sample','world']`, every
  non-`url` field verbatim, `url == /basemap/regions/<id>.pmtiles`,
  `cloud.hemna.com` absent, `Cache-Control: no-cache`,
  `Access-Control-Allow-Origin: *`), and `GET /basemap/regions/sample.pmtiles`
  returns 206 for `Range: bytes=0-1023` with `Content-Range` correct.
  503 + detail + caplog text for each manifest error shape.
- **GREEN:** `create_basemap_manifest_route(container)` +
  `create_basemap_region_archive_route(container)`; register both in `server.py`
  route assembly next to the existing basemap routes.
- Commit: `feat(routers): serve the kit region list and per-region archives (#204)`

### T3 — Region-by-id resolution (`basemap.py`)
- **RED:** `tests/test_basemap_region.py` additions: `resolve_region_archive("world", …)`
  → the vendored `world.pmtiles` path; `resolve_region_archive("atlantis", …)`
  → the sample path **and** a warning logged; `resolve_region_archive("basemap/regions/…path", …)`
  and absolute-path behaviour unchanged (regression guard).
- **GREEN:** id rule + `LOG.warning("unknown basemap region id %r; serving sample", …)`
  in the resolution path (needs the module logger; check whether `basemap.py`
  already has one — if not, one `LOG = logging.getLogger(__name__)` line).
- Commit: `feat(basemap): accept a kit region id in basemap.region_archive (#204)`

### T4 — Client region list (`app.js` + `index.html`)
- **RED:** `tests/test_maplibre_region_list.py` in the existing source-assert
  style (`test_maplibre_main_map.py`): `app.js` fetches `/basemap/maps-manifest.json`,
  caches per day under a `dw-region-list-*` localStorage key, renders id/name/bytes,
  no download button, and `index.html` unchanged in script order.
- **GREEN:** one section in `app.js` next to the basemap setup; reuse
  `region_archive_info` data already in `GET /api/config` for the active size.
- Commit: `feat(map): list kit regions from the manifest (#204)`

## Out of scope

No download UI, no new cache, no upstream change, no deploy, no `cloud.hemna.com`
URL ever emitted. The tile proxy cache numbers stay as they are (512 / 16 MiB).

## Verification gate before PR

`uvx ruff check . --select=E,F --ignore=E501` · `uv run pytest tests/ -q` ·
`node --check` every touched `.js`.

## Appendix A — haminfo reference kit (verbatim, inlined so any machine can port)

From `~/devel/mine/hamradio/haminfo` merge `ad2e8262` (PR #344). Flask-based; adapt routes to this app's starlette route factories.

### basemap.py — manifest loader + served manifest + archive route

```python
# ---------------------------------------------------------------------------
# Region list for the mobile apps (issues #342 / #343)
# ---------------------------------------------------------------------------
#: Directory holding the region archives a client may download.
REGIONS_DIR = BASEMAP_DIR / 'regions'
#: Stable URL the apps configure (the dev value is in the PR body).
REGION_MANIFEST_ROUTE = '/basemap/maps-manifest.json'
#: Same-origin archive URL served here (HTTP Range + CORS).
REGION_ARCHIVE_ROUTE = '/basemap/regions/<name>.pmtiles'
#: The kit's published list. hemna/basemap-kit builds and owns
#: ``maps-manifest.json``; this host serves it as published, so ids, names,
#: groups, sizes, digests, maxzoom and bounds cannot drift from what every other
#: client is told (docs/CLIENTS.md §2). The committed copy is the kit's build
#: output and the deploy step replaces it; point ``HAMINFO_BASEMAP_MANIFEST`` at
#: the deployed file to read it from somewhere else.
REGION_MANIFEST_FILE = Path(
    os.environ.get('HAMINFO_BASEMAP_MANIFEST')
    or Path(__file__).resolve().parent / 'basemap-manifest.json'
)
#: Internal zoom-11 gap filler (:data:`LOCAL_LOW_REGION_URL`), not a choice.
INTERNAL_REGION_IDS = frozenset({'low-fallback'})
#: Same values as the kit's nginx CORS block (docker/basemap-kit/cors.conf).
CORS_HEADERS = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
    'Access-Control-Allow-Headers': 'Range, If-Match, If-None-Match, If-Modified-Since',
    'Access-Control-Expose-Headers': (
        'Content-Range, Content-Length, Accept-Ranges, ETag'
    ),
}


def kit_region_manifest(path: Path | None = None) -> dict:
    """The kit's published region list, read exactly as published.

    Raises :class:`BasemapSourceError` with a message a log reader can act on
    when the file is missing, is not the shape clients read, or lists a region
    under a group the list does not define. Serving a differently-named or
    partly-filled list would be worse than an error (docs/CLIENTS.md §2).
    """
    p = Path(path) if path is not None else REGION_MANIFEST_FILE
    try:
        doc = json.loads(p.read_text(encoding='utf-8'))
    except OSError as e:
        raise BasemapSourceError(f'region list unreadable at {p}: {e}') from e
    except ValueError as e:
        raise BasemapSourceError(f'region list is not valid JSON at {p}: {e}') from e
    try:
        regions, groups = doc['regions'], doc['groups']
        if not regions or not groups:
            raise KeyError('a non-empty regions and groups array')
        for key in ('schemaVersion', 'tileSchema', 'buildDate'):
            if key not in doc:
                raise KeyError(key)
        for r in regions:
            for key in ('id', 'name', 'group', 'url', 'bytes', 'sha256',
                        'maxzoom', 'bounds'):
                if key not in r:
                    raise KeyError(f"region {r.get('id')!r}: {key}")
        declared = {g['id'] for g in groups}
        unknown = sorted({r['group'] for r in regions if r['group'] not in declared})
        if unknown:
            raise KeyError(f'a groups entry for {unknown}')
    except (KeyError, TypeError) as e:
        raise BasemapSourceError(f'region list at {p} is missing {e}') from e
    return doc


@basemap_bp.get(REGION_MANIFEST_ROUTE)
def basemap_maps_manifest() -> Response:
    """The kit's published region list, unchanged except the archive URLs.

    Every field comes from the kit manifest. Only ``url`` is rewritten, from the
    production host to this host's own ``/basemap/regions/<id>.pmtiles`` route,
    because a dev client must download from the host it fetched the list from.
    """
    try:
        doc = kit_region_manifest()
    except BasemapSourceError as e:
        LOG.error('basemap region manifest: %s', e)
        return jsonify({
            'error': 'basemap region list unavailable',
            'detail': str(e),
        }), 503
    regions = doc['regions']
    missing = [r['id'] for r in regions
               if not (REGIONS_DIR / f"{r['id']}.pmtiles").is_file()]
    if missing:
        # A listed region with no archive on the host is a publish failure, not
        # a row for the app to filter out (docs/CLIENTS.md §2).
        LOG.error('basemap region manifest: %d of %d listed archives missing '
                  'under %s: %s', len(missing), len(regions), REGIONS_DIR,
                  ', '.join(missing))
        return jsonify({
            'error': 'basemap region archives missing on this host',
            'detail': f'{len(missing)} of {len(regions)} listed archives are not '
                      f'under {REGIONS_DIR}: {", ".join(missing)}',
        }), 503
    base = request.host_url.rstrip('/')
    body = {
        'schemaVersion': doc['schemaVersion'],
        'tileSchema': doc['tileSchema'],
        'buildDate': doc['buildDate'],
        'groups': doc['groups'],
        'regions': [
            {**r, 'url': f"pmtiles://{base}/basemap/regions/{r['id']}.pmtiles"}
            for r in regions
        ],
    }
    resp = jsonify(body)
    resp.headers.update(CORS_HEADERS)
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


@basemap_bp.route(REGION_ARCHIVE_ROUTE, methods=['GET', 'HEAD', 'OPTIONS'])
def basemap_region_archive(name: str) -> Response:
    """One region archive with HTTP Range and CORS, for the app downloaders."""
    if request.method == 'OPTIONS':
        return Response(status=204,
                        headers={**CORS_HEADERS, 'Access-Control-Max-Age': '86400'})
    if name in INTERNAL_REGION_IDS or not re.fullmatch(r'[a-z0-9][a-z0-9-]*', name):
        return jsonify({'error': f'unknown basemap region: {name!r}'}), 404
    path = REGIONS_DIR / f'{name}.pmtiles'
    if not path.is_file():
        return jsonify({'error': f'unknown basemap region: {name!r}'}), 404
    #: Werkzeug's send_file answers a Range request with 206 + Content-Range.
    resp = send_file(path, mimetype='application/vnd.pmtiles', conditional=True)
    resp.headers.update(CORS_HEADERS)
    resp.headers['Cache-Control'] = 'no-cache'
    return resp

```

### test additions (manifest asserts to port)

```diff
diff --git a/haminfo-dashboard/tests/test_basemap.py b/haminfo-dashboard/tests/test_basemap.py
index 0120d151..1e8516e7 100644
--- a/haminfo-dashboard/tests/test_basemap.py
+++ b/haminfo-dashboard/tests/test_basemap.py
@@ -56,6 +56,7 @@ from haminfo_dashboard.basemap import (
     resolve_style_url,
     resolve_upstream_cached,
     tiles_sources,
+    REGION_MANIFEST_ROUTE,
     tiles_url,
 )
 
@@ -1041,3 +1042,208 @@ def test_basemap_tiles_proxy_route_502_on_upstream_failure(client, monkeypatch):
     monkeypatch.setattr(basemap_module, 'proxy_fetch_range', _boom)
     r = client.get(UPSTREAM_PROXY_URL, headers={'Range': 'bytes=0-2'})
     assert r.status_code == 502
+
+# ---------------------------------------------------------------------------
+# Region manifest for the mobile apps (issues #342 / #343)
+# ---------------------------------------------------------------------------
+
+
+def _pmtiles_archive(size=4096):
+    """A minimal, valid PMTiles v3 vector header followed by zero padding."""
+    header = bytearray(127)
+    header[:7] = b'PMTiles'
+    header[7] = 3      # spec version
+    header[99] = 1     # tile type: MVT (vector)
+    header[100] = 0    # min zoom
+    header[101] = 11   # max zoom
+    return bytes(header) + b'\x00' * (size - 127)
+
+
+def _write_archives(directory, names=('sample.pmtiles', 'world.pmtiles'),
+                    size=4096):
+    for name in names:
+        (directory / name).write_bytes(_pmtiles_archive(size))
+    return directory
+
+
+#: A two-region kit manifest: the shape hemna/basemap-kit publishes
+#: (docs/CLIENTS.md §2). Sizes here deliberately differ from the fixture files
+#: on disk, so a test can prove the route publishes the manifest's numbers and
+#: does not measure the files again.
+GROUPS = [
+    {'id': 'sample', 'name': 'Sample', 'order': 0},
+    {'id': 'world', 'name': 'World overview', 'order': 10},
+]
+NAMES = {'sample': 'Sample (Florence, IT)', 'world': 'World'}
+REGION_GROUP = {'sample': 'sample', 'world': 'world'}
+
+
+def _kit_manifest(regions=('sample', 'world'), **overrides):
+    doc = {
+        'schemaVersion': 1,
+        'tileSchema': 'v4',
+        'buildDate': '2026-10-28T00:00:00Z',
+        'groups': [dict(g) for g in GROUPS],
+        'regions': [
+            {'id': rid,
+             'name': NAMES.get(rid, rid),
+             'group': REGION_GROUP.get(rid, 'sample'),
+             'url': (f'pmtiles://https://cloud.hemna.com/basemap-kit/'
+                     f'archives/{rid}.pmtiles'),
+             'bytes': 999999,
+             'sha256': 'a' * 64,
+             'maxzoom': 11,
+             'bounds': [-1.0, -1.0, 1.0, 1.0]}
+            for rid in regions
+        ],
+    }
+    doc.update(overrides)
+    return doc
+
+
+def _use_manifest(monkeypatch, tmp_path, doc):
+    path = tmp_path / 'maps-manifest.json'
+    path.write_text(json.dumps(doc), encoding='utf-8')
+    monkeypatch.setattr(basemap_module, 'REGION_MANIFEST_FILE', path)
+    return path
+
+
+def test_committed_manifest_is_a_complete_kit_list():
+    # The copy shipped with the package must be the kit's list, not a stub: the
+    # seven continent slugs plus 'sample' for the bundled fallback, every region
+    # filed under a declared group.
+    doc = basemap_module.kit_region_manifest()
+    declared = {g['id'] for g in doc['groups']}
+    assert {'africa', 'antarctica', 'asia', 'europe', 'north-america',
+            'oceania', 'south-america', 'sample'} <= declared
+    assert len(doc['regions']) == 33
+    assert {r['group'] for r in doc['regions']} <= declared
+    assert all(r['maxzoom'] == int(r['maxzoom']) for r in doc['regions'])
+
+
+def test_maps_manifest_serves_the_kit_list_verbatim(client, tmp_path, monkeypatch):
+    doc = _kit_manifest()
+    _use_manifest(monkeypatch, tmp_path, doc)
+    _write_archives(tmp_path)
+    monkeypatch.setattr(basemap_module, 'REGIONS_DIR', tmp_path)
+    r = client.get(REGION_MANIFEST_ROUTE)
+    assert r.status_code == 200
+    body = r.get_json()
+    # groups are served unchanged, in the kit's order
+    assert body['groups'] == doc['groups']
+    assert [reg['id'] for reg in body['regions']] == ['sample', 'world']
+    for served, published in zip(body['regions'], doc['regions'], strict=True):
+        assert {k: v for k, v in served.items() if k != 'url'} == \
+               {k: v for k, v in published.items() if k != 'url'}
+        # only the host changes: the dev client must download from the host it
+        # fetched the list from
+        assert served['url'] == (
+            f"pmtiles://http://localhost/basemap/regions/{published['id']}.pmtiles")
+    assert 'cloud.hemna.com' not in r.get_data(as_text=True)
+    assert r.headers['Cache-Control'] == 'no-cache'
+    assert r.headers['Access-Control-Allow-Origin'] == '*'
+    # every region the manifest names is actually downloadable from this host
+    for reg in body['regions']:
+        path = reg['url'].removeprefix('pmtiles://http://localhost')
+        part = client.get(path, headers={'Range': 'bytes=0-126'})
+        assert part.status_code == 206, path
+        assert len(part.data) == 127
+        assert part.headers['Accept-Ranges'] == 'bytes'
+
+
+def test_maps_manifest_urls_follow_request_host(client, tmp_path, monkeypatch):
+    _use_manifest(monkeypatch, tmp_path, _kit_manifest(regions=('world',)))
+    _write_archives(tmp_path, names=('world.pmtiles',))
+    monkeypatch.setattr(basemap_module, 'REGIONS_DIR', tmp_path)
+    r = client.get(REGION_MANIFEST_ROUTE, headers={'Host': '192.168.0.13:5001'})
+    assert r.get_json()['regions'][0]['url'] == (
+        'pmtiles://http://192.168.0.13:5001/basemap/regions/world.pmtiles'
+    )
+
+
+def test_maps_manifest_errors_when_a_listed_archive_is_missing(
+        client, tmp_path, monkeypatch, caplog):
+    # docs/CLIENTS.md §2: a listed region with no archive is a publish failure,
+    # not a row for the app to filter out, so the whole list is refused.
+    _use_manifest(monkeypatch, tmp_path, _kit_manifest())
+    _write_archives(tmp_path, names=('world.pmtiles',))
+    monkeypatch.setattr(basemap_module, 'REGIONS_DIR', tmp_path)
+    with caplog.at_level('ERROR', logger='haminfo_dashboard.basemap'):
+        r = client.get(REGION_MANIFEST_ROUTE)
+    assert r.status_code == 503
+    assert 'sample' in r.get_json()['detail']
+    assert 'sample' in caplog.text
+
+
+def test_maps_manifest_errors_when_the_list_is_missing(
+        client, tmp_path, monkeypatch, caplog):
+    monkeypatch.setattr(basemap_module, 'REGION_MANIFEST_FILE',
+                        tmp_path / 'nope.json')
+    with caplog.at_level('ERROR', logger='haminfo_dashboard.basemap'):
+        r = client.get(REGION_MANIFEST_ROUTE)
+    assert r.status_code == 503
+    assert 'unreadable' in r.get_json()['detail']
+    assert 'nope.json' in caplog.text
+
+
+def test_maps_manifest_errors_when_the_list_is_malformed(
+        client, tmp_path, monkeypatch):
+    path = tmp_path / 'maps-manifest.json'
+    path.write_text('{"regions": [', encoding='utf-8')
+    monkeypatch.setattr(basemap_module, 'REGION_MANIFEST_FILE', path)
+    r = client.get(REGION_MANIFEST_ROUTE)
+    assert r.status_code == 503
+    assert 'not valid JSON' in r.get_json()['detail']
+
+
+def test_maps_manifest_errors_when_a_region_points_at_an_undeclared_group(
+        client, tmp_path, monkeypatch):
+    doc = _kit_manifest()
+    doc['regions'][0]['group'] = 'atlantis'
+    _use_manifest(monkeypatch, tmp_path, doc)
+    _write_archives(tmp_path)
+    monkeypatch.setattr(basemap_module, 'REGIONS_DIR', tmp_path)
+    r = client.get(REGION_MANIFEST_ROUTE)
+    assert r.status_code == 503
+    assert 'atlantis' in r.get_json()['detail']
+
+
+def test_maps_manifest_errors_when_a_region_field_is_missing(
+        client, tmp_path, monkeypatch):
+    doc = _kit_manifest()
+    del doc['regions'][0]['bounds']
+    _use_manifest(monkeypatch, tmp_path, doc)
+    _write_archives(tmp_path)
+    monkeypatch.setattr(basemap_module, 'REGIONS_DIR', tmp_path)
+    r = client.get(REGION_MANIFEST_ROUTE)
+    assert r.status_code == 503
+    assert 'bounds' in r.get_json()['detail']
+
+
+def test_region_archive_route_range_206(client, tmp_path, monkeypatch):
+    _write_archives(tmp_path, names=('world.pmtiles',))
+    monkeypatch.setattr(basemap_module, 'REGIONS_DIR', tmp_path)
+    r = client.get('/basemap/regions/world.pmtiles',
+                   headers={'Range': 'bytes=0-1023'})
+    assert r.status_code == 206
+    assert len(r.data) == 1024
+    assert r.headers['Content-Range'] == 'bytes 0-1023/4096'
+    assert r.headers['Accept-Ranges'] == 'bytes'
+    assert r.headers['Access-Control-Allow-Origin'] == '*'
+    assert r.headers['Content-Type'] == 'application/vnd.pmtiles'
+
+
+def test_region_archive_route_404_and_traversal(client, tmp_path, monkeypatch):
+    _write_archives(tmp_path, names=('world.pmtiles',))
+    monkeypatch.setattr(basemap_module, 'REGIONS_DIR', tmp_path)
+    assert client.get('/basemap/regions/missing.pmtiles').status_code == 404
+    assert client.get('/basemap/regions/low-fallback.pmtiles').status_code == 404
+    assert client.get(
+        '/basemap/regions/..%2f..%2fetc%2fpasswd.pmtiles').status_code == 404
+
+
+def test_region_archive_route_options_preflight(client):
+    r = client.options('/basemap/regions/world.pmtiles')
+    assert r.status_code == 204
+    assert r.headers['Access-Control-Allow-Methods'] == 'GET, HEAD, OPTIONS'
+    assert 'Range' in r.headers['Access-Control-Allow-Headers']

```
