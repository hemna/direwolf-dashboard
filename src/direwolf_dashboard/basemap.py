"""direwolf_dashboard/basemap.py — MapLibre basemap foundation (issue #124).

The basemap surface is vendored into ``static/basemap/`` (kit pin
``f1227386d2``, URLs rewritten to ``/static`` so the map needs zero internet;
see ``static/basemap/SOURCE.md``). This module is the config seam: it owns the
default region archive path and the local URL the vendored styles use for
tiles, driven by the ``basemap.region_archive`` config setting.
"""

from __future__ import annotations

import mimetypes

# MapLibre glyph (.pbf) and tile-archive (.pmtiles) assets are binary; without
# these registrations starlette StaticFiles serves unknown extensions as
# text/plain. Register them once (this module is imported by config.py, which
# server.py imports, so the override applies to the /static mount too).
mimetypes.add_type("application/octet-stream", ".pmtiles")
mimetypes.add_type("application/octet-stream", ".pbf")

#: Default region archive path, relative to the static mount. Matches the
#: vendored Florence sample and the ``basemap.region_archive`` config default.
DEFAULT_REGION_ARCHIVE = "basemap/regions/sample.pmtiles"

#: The tile URL baked into the vendored styles at pin time (LAN-offline).
DEFAULT_TILE_URL = "/static/" + DEFAULT_REGION_ARCHIVE


def region_url(region_archive: str | None = None) -> str:
    """Return the served URL for a region archive under the static mount.

    ``region_archive`` names a file inside the static tree (the config value
    ``basemap.region_archive``), so it must be a relative path without ``..``
    segments — an absolute or upward path would escape the static mount.
    """
    rel = (region_archive or DEFAULT_REGION_ARCHIVE).strip()
    if not rel or rel.startswith("/") or rel.startswith("..") or "/../" in "/" + rel:
        raise ValueError(f"region_archive must stay inside the static mount: {region_archive!r}")
    return f"/static/{rel}"
