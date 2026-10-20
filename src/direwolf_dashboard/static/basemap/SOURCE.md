# Basemap surface provenance (issue #124)

Everything under `static/basemap/` is vendored from the shared map kit,
`hemna/basemap-kit`, so it can be re-derived at any time and this copy stays a
stable pin. The vendored JS libraries under `static/vendor/` follow the same
pattern. direwolf-dashboard is **fully LAN-offline** (DigiPi has no internet),
so unlike the aprslive leg, the style URLs are rewritten to local `/static`
paths at pin time — the map needs zero internet.

## Kit surface (styles + sprites + glyphs)

- **Kit repo:** `hemna/basemap-kit` (git.hemna.com)
- **Pinned commit (kit):** `f1227386d27a3e3f9bdb92d4e2ef6438cd16cb0c` — master after
  kit PRs #2/#4/#5 (styles + build pipeline + APRS sprite) merged.
- **Copied from the kit publish-dir layout:** `sprites/`, `fonts/`, `assets.sha256`,
  and the two style files (which are then URL-rewritten, below).
- **Byte identity:** every file listed in `assets.sha256` (13 sprites incl. the
  `aprs` set + the APRS copyright notice, 768 glyph `.pbf`, `fonts/OFL.txt`)
  hashes to the kit's published value — verified by `shasum -a 256 -c`.
- **Upstream provenance** (from the kit `SOURCE.md`, itself vendored from
  `protomaps/basemaps-assets` @ `028c18f713baecad011301ff7a69acc39bcc2ae7` and
  `hessu/aprs-symbols` @ `f2286a9cd43eb6ba4501250b4c39fff111e3796c`).

### URL rewrites (at pin time — LAN-offline)

The two style files are *not* byte-identical to the kit: the zero-internet
requirement means sprite/glyph/tile URLs are rewritten from the kit's
`cloud.hemna.com` base to this app's own static mount. Exact rewrites applied
(measured on the rewritten files — 0 `cloud.hemna.com` / `__MAP_TILES_URL__`
remaining):

| Kit field | Kit value | Rewritten to |
|---|---|---|
| `glyphs` | `https://cloud.hemna.com/basemap-kit/fonts/{fontstack}/{range}.pbf` | `/static/basemap/fonts/{fontstack}/{range}.pbf` |
| `sprite[].url` (default) | `https://cloud.hemna.com/basemap-kit/sprites/light` (dark) | `/static/basemap/sprites/light` (dark) |
| `sprite[].url` (aprs) | `https://cloud.hemna.com/basemap-kit/sprites/aprs` | `/static/basemap/sprites/aprs` |
| `sources.protomaps.url` | `__MAP_TILES_URL__` | `/static/basemap/regions/sample.pmtiles` |

The tile URL is baked to the **default** region archive from the config seam
(`basemap.region_archive` → `region_url()`, `direwolf_dashboard/basemap.py`);
an operator replaces the file on disk (Pi copies its real region file here) or,
in the map-shell slice, can serve the style with the configured URL. The only
remaining `https://` text is the <a> attribution label (`Protomaps` /
`OpenStreetMap`) — a display link, not a runtime fetch; the map renders
offline.

## Vendored JS libraries (`static/vendor/`)

- **maplibre-gl** `5.24.0` (classic UMD bundle + CSS, inline worker — chosen
  over 6.x which is ESM-only, because this project has no JS build step).
  - `https://unpkg.com/maplibre-gl@5.24.0/dist/maplibre-gl.js`
    sha256 `45a9b07a9189ce56054c620a947ccf41e291e58c95e9b61533b740aaa65ee5cb`
  - `https://unpkg.com/maplibre-gl@5.24.0/dist/maplibre-gl.css`
    sha256 `ab1e70d59ec40465bae7e7030da2f3ccf28133fd502e62bd598eefbadfd7a732`
- **pmtiles** `4.5.0` (UMD).
  - `https://unpkg.com/pmtiles@4.5.0/dist/pmtiles.js`
    sha256 `caf981bc46f6327ee7e65d5dc964d89d38a69f60edca2bd4c5c890c21b554c6c`
- Licensed under BSD-3-Clause; the bundles carry their licenses in-file.

## Sample region archive (`static/basemap/regions/sample.pmtiles`)

- **Source:** kit `scripts/build_regions.py` sample extraction — Florence IT
  bbox (from kit `regions.json`), built 2026-10-19.
- **Bytes:** 6,596,326 B · sha256 `1efc31f57ce799eb0831ab82c3753160d4939f8888f9e601d3a1c261f4f1b450`
- This is the dev/test region file the server serves (with HTTP Range support,
  see `tests/test_basemap.py`); a real region archive replaces it.
