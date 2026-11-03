"""Router factory functions — create Starlette route lists bound to a ServiceContainer."""

import asyncio
import csv
import io
import logging
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.routing import Route
from starlette.websockets import WebSocket, WebSocketDisconnect

from direwolf_dashboard import __version__
from direwolf_dashboard.basemap import (
    CORS_HEADERS,
    INTERNAL_REGION_IDS,
    PROXY_RANGE_MAX_BYTES,
    REGION_ARCHIVE_ROUTE,
    REGION_MANIFEST_ROUTE,
    REGIONS_DIR,
    UPSTREAM_PROXY_URL,
    BasemapSourceError,
    UpstreamRangeError,
    proxy_fetch_range,
    region_archive_info,
    region_manifest,
    render_style,
    resolve_region_archive,
    resolve_upstream_cached,
    upstream_sources,
)
from direwolf_dashboard.config import update_config
from direwolf_dashboard.decoder import decode_packet as _decode
from direwolf_dashboard.lifecycle import (
    ServiceContainer,
    broadcast_event,
    enrich_with_bearing,
)
from direwolf_dashboard.station_model import build_station_model, legend_data

LOG = logging.getLogger(__name__)

#: Package static tree (same parent dir as server.py). Used to resolve the
#: vendored region-archive fallback for /basemap/region.pmtiles.
_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def _get_services(container: ServiceContainer):
    """Get services from container, raising HTTP 503 if not initialized."""
    services = container.services
    if services is None:
        raise HTTPException(status_code=503, detail="Services not initialized")
    return services


def _safe_filename(s: str) -> str:
    """Sanitize a string for use in a Content-Disposition filename."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s)


async def _require_api_token(request: Request, services) -> None:
    """Enforce the configured API token on state-changing endpoints.

    When ``server.api_token`` is empty (default) token auth is disabled and
    this is a no-op — matching the historical LAN-only behavior.  When set,
    the request must carry a matching ``X-API-Token`` header. (issue #73)
    """
    token = services.config.server.api_token
    if not token:
        return
    supplied = request.headers.get("X-API-Token") or ""
    if supplied != token:
        raise HTTPException(status_code=401, detail="Invalid or missing API token")


def _generate_gpx(callsign: str, track: list[dict]) -> str:
    """Generate a GPX 1.1 XML string from a station's position track."""
    from xml.sax.saxutils import escape as _xml_escape

    points = list(reversed(track))

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="Direwolf Dashboard"',
        '     xmlns="http://www.topografix.com/GPX/1/1">',
        '  <metadata>',
        # Callsign is user/radio-controlled — XML-escape it (issue #74)
        f'    <name>{_xml_escape(callsign)} Track</name>',
        f'    <time>{datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}</time>',
        '  </metadata>',
        '  <trk>',
        f'    <name>{_xml_escape(callsign)}</name>',
        '    <trkseg>',
    ]

    for pt in points:
        lat = pt["latitude"]
        lon = pt["longitude"]
        ts = pt.get("timestamp")
        time_str = ""
        if ts:
            dt = datetime.fromtimestamp(ts, tz=UTC)
            time_str = f"\n        <time>{dt.strftime('%Y-%m-%dT%H:%M:%SZ')}</time>"
        lines.append(f'      <trkpt lat="{lat}" lon="{lon}">{time_str}')
        lines.append('      </trkpt>')

    lines.extend([
        '    </trkseg>',
        '  </trk>',
        '</gpx>',
    ])

    return "\n".join(lines)


def _qint(request: Request, name: str, default: int, min_val: Optional[int] = None, max_val: Optional[int] = None) -> int:
    """Parse an integer query parameter with optional clamping."""
    try:
        val = int(request.query_params.get(name, default))
    except (ValueError, TypeError):
        val = default
    if min_val is not None:
        val = max(val, min_val)
    if max_val is not None:
        val = min(val, max_val)
    return val


def _qfloat(request: Request, name: str, default=None) -> Optional[float]:
    """Parse an optional float query parameter."""
    raw = request.query_params.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (ValueError, TypeError):
        return default


def create_api_routes(container: ServiceContainer) -> list:
    """Return a list of Starlette Route objects for the REST API."""

    async def get_packets(request: Request):
        services = _get_services(container)
        since = _qfloat(request, "since")
        limit = _qint(request, "limit", 100, min_val=1, max_val=500)
        callsign = request.query_params.get("callsign") or None
        ptype = request.query_params.get("type") or None
        packets = await services.storage.query_packets(
            since=since, limit=limit, callsign=callsign, packet_type=ptype
        )
        for p in packets:
            await enrich_with_bearing(p, services)
        return JSONResponse(packets)

    # CSV columns to include in the export, in order
    _CSV_FIELDS = [
        "timestamp", "datetime", "type", "tx", "from_call", "to_call",
        "path", "latitude", "longitude", "symbol", "symbol_table",
        "human_info", "comment", "msg_no", "audio_level", "raw_packet",
    ]

    async def export_packets_csv(request: Request):
        """Stream all matching packets as a CSV file download.

        Query params (all optional):
          since    — Unix timestamp lower bound
          callsign — filter by from_call
          type     — filter by packet type
          limit    — max rows (default 10000, min 1, max 50000)
        """
        services = _get_services(container)
        since = _qfloat(request, "since")
        limit = _qint(request, "limit", 10_000, min_val=1, max_val=50_000)
        callsign = request.query_params.get("callsign") or None
        ptype = request.query_params.get("type") or None

        packets = await services.storage.query_packets(
            since=since, limit=limit, callsign=callsign, packet_type=ptype
        )

        buf = io.StringIO()
        writer = csv.DictWriter(
            buf,
            fieldnames=_CSV_FIELDS,
            extrasaction="ignore",
            lineterminator="\r\n",
        )
        writer.writeheader()
        for p in packets:
            row = dict(p)
            # Convert Unix timestamp to human-readable ISO datetime
            if row.get("timestamp"):
                row["datetime"] = datetime.fromtimestamp(
                    row["timestamp"], tz=UTC
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
            else:
                row["datetime"] = ""
            # Serialize path list to semicolon-separated string
            if isinstance(row.get("path"), list):
                row["path"] = ";".join(row["path"])
            # Booleans → 1/0 for spreadsheet compatibility
            if isinstance(row.get("tx"), bool):
                row["tx"] = 1 if row["tx"] else 0
            writer.writerow(row)

        csv_bytes = buf.getvalue().encode("utf-8")
        filename = "direwolf-packets.csv"
        return Response(
            content=csv_bytes,
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": str(len(csv_bytes)),
            },
        )

    async def get_messages(request: Request):
        services = _get_services(container)
        since = _qfloat(request, "since")
        limit = _qint(request, "limit", 200, min_val=1, max_val=1000)
        callsign = request.query_params.get("callsign") or None
        messages = await services.storage.query_packets(
            since=since, limit=limit, callsign=callsign, packet_type="MessagePacket"
        )
        return JSONResponse(messages)

    async def get_stations(request: Request):
        services = _get_services(container)
        stations = await services.storage.get_stations()
        latest_weather = await services.storage.get_latest_weather_by_callsign()
        if latest_weather:
            for station in stations:
                cs = station.get("callsign")
                if cs in latest_weather:
                    station["last_weather"] = latest_weather[cs]
                    station["wx_model"] = build_station_model(latest_weather[cs])
        return JSONResponse(stations)

    async def get_station_positions(request: Request):
        services = _get_services(container)
        rows = await services.storage.get_all_station_positions()
        return JSONResponse({
            row["callsign"]: {"lat": row["latitude"], "lng": row["longitude"]}
            for row in rows
        })

    async def get_station_tracks(request: Request):
        services = _get_services(container)
        hours = _qint(request, "hours", 1, min_val=1, max_val=24)
        since = time.time() - (hours * 3600)
        tracks = await services.storage.get_all_station_tracks(since)
        return JSONResponse({
            cs: [[p["latitude"], p["longitude"], p["timestamp"]] for p in points]
            for cs, points in tracks.items()
        })

    async def get_station(request: Request):
        services = _get_services(container)
        callsign = request.path_params["callsign"]
        station = await services.storage.get_station(callsign)
        if not station:
            raise HTTPException(status_code=404, detail="Station not found")
        track_limit = _qint(request, "track_limit", 100, max_val=500)
        track = await services.storage.get_station_track(callsign, limit=track_limit)
        return JSONResponse({"station": station, "track": track})

    async def get_station_gpx(request: Request):
        services = _get_services(container)
        callsign = request.path_params["callsign"]
        hours = _qint(request, "hours", 24, min_val=1, max_val=168)
        since = time.time() - (hours * 3600)
        track = await services.storage.get_station_track(callsign, limit=5000, since=since)
        if not track:
            raise HTTPException(status_code=404, detail="No track data for station")
        gpx_xml = _generate_gpx(callsign, track)
        filename = f"{_safe_filename(callsign)}_{hours}h.gpx"
        return Response(
            content=gpx_xml,
            media_type="application/gpx+xml",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    async def get_stats(request: Request):
        services = _get_services(container)
        db_stats = await services.storage.get_stats()
        db_stats.update(services.get_stats_dict())
        return JSONResponse(db_stats)

    async def config_handler(request: Request):
        services = _get_services(container)
        if request.method == "GET":
            d = services.config.to_dict()
            d["version"] = __version__
            mp = await services.storage.get_my_position()
            d["station"]["my_position"] = mp or {
                "type": None, "callsign": None, "latitude": None, "longitude": None
            }
            d["basemap"]["region"] = region_archive_info(
                resolve_region_archive(
                    d["basemap"]["region_archive"], d["data_dir"], _STATIC_DIR
                )
            )
            d["basemap"]["auto"] = services.basemap_status.to_dict()
            # The public config is served to unauthenticated readers; never
            # expose the write token on it (issue #162).
            d.get("server", {}).pop("api_token", None)
            return JSONResponse(d)

        # PUT
        await _require_api_token(request, services)
        try:
            updates = await request.json()
        except Exception as e:
            raise HTTPException(status_code=400, detail="Invalid JSON body") from e

        try:
            station_updates = updates.get("station", {}).copy()
            has_my_pos = "my_position" in station_updates
            my_pos = station_updates.pop("my_position", None)

            if not station_updates:
                updates.pop("station", None)

            position_to_save = None
            if has_my_pos and my_pos is not None:
                mp_type = my_pos.get("type")
                if mp_type == "station":
                    if not my_pos.get("callsign"):
                        raise HTTPException(
                            status_code=400,
                            detail="my_position type 'station' requires a non-empty callsign",
                        )
                elif mp_type == "pin":
                    lat = my_pos.get("latitude")
                    lon = my_pos.get("longitude")
                    if lat is None or lon is None:
                        raise HTTPException(
                            status_code=400,
                            detail="my_position type 'pin' requires latitude and longitude",
                        )
                    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
                        raise HTTPException(
                            status_code=400,
                            detail="latitude must be [-90, 90], longitude must be [-180, 180]",
                        )
                elif mp_type is not None:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid my_position type: {mp_type}",
                    )
                position_to_save = my_pos if mp_type else None

            updated_fields = []
            restart_required = False
            if updates:
                new_config, updated_fields, restart_required = update_config(
                    services.config, updates, services.config_path
                )
                services.config = new_config

            if has_my_pos:
                await services.storage.set_my_position(position_to_save)
                # Invalidate the cached my_position so the next packet
                # re-reads the updated value from the database.
                services._my_position_dirty = True
                mp_data = await services.storage.get_my_position()
                await broadcast_event(
                    "config_updated",
                    {"my_position": mp_data or {"type": None}},
                    services.ws_clients,
                )

            return JSONResponse({
                "updated": updated_fields + (["station.my_position"] if has_my_pos else []),
                "restart_required": restart_required,
            })
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail={"errors": str(e)}) from e

    async def wipe_storage(request: Request):
        services = _get_services(container)
        await _require_api_token(request, services)
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not body.get("confirm"):
            raise HTTPException(
                status_code=400,
                detail='Must include {"confirm": true} in request body',
            )
        await services.storage.reset()
        await broadcast_event("storage_reset", {}, services.ws_clients)
        return JSONResponse({"status": "ok"})

    async def decode_packet(request: Request):
        services = _get_services(container)
        try:
            body = await request.json()
        except Exception as e:
            raise HTTPException(status_code=400, detail="Invalid JSON body") from e
        raw_packet = body.get("raw_packet", "")
        if not raw_packet:
            raise HTTPException(status_code=400, detail="raw_packet is required")

        result = _decode(raw_packet)

        if not result["success"]:
            return JSONResponse(result)

        path_stations = {}
        path_list = result["sections"].get("station", {}).get("path", [])
        if path_list:
            generic_pattern = re.compile(
                r"^(WIDE\d?|RELAY|TRACE\d?|qA.|TCPIP|TCPXX|RFONLY|NOGATE)",
                re.IGNORECASE,
            )
            real_calls = []
            for call in path_list:
                clean = call.rstrip("*").strip().upper()
                if clean and not generic_pattern.match(clean):
                    real_calls.append(clean)

            if real_calls:
                try:
                    path_stations = await services.storage.get_stations_by_callsigns(real_calls)
                except Exception:
                    LOG.exception("Failed to look up path station positions")

        result["path_stations"] = path_stations
        return JSONResponse(result)

    async def get_weather_stations(request: Request):
        """Return a slim list of stations that report weather.

        Only {callsign, last_weather} for stations with weather data — the
        weather panel no longer needs the full /stations payload. (issue #94)
        """
        services = _get_services(container)
        latest_weather = await services.storage.get_latest_weather_by_callsign()
        stations = await services.storage.get_stations(limit=2000)
        result = []
        for s in stations:
            cs = s.get("callsign")
            if s.get("symbol") == "_" or cs in latest_weather:
                entry = {"callsign": cs}
                if cs in latest_weather:
                    entry["last_weather"] = latest_weather[cs]
                    entry["wx_model"] = build_station_model(latest_weather[cs])
                result.append(entry)
        return JSONResponse(result)
    async def get_wx_legend(request: Request):
        """Colour scales, legend text lines and a sample for the WX
        station-model legend (issue #156 fixes 5 & 6). Server-computed so the
        frontend keeps no colour/unit math and the legend matches markers."""
        return JSONResponse(legend_data())
    async def get_weather(request: Request):
        services = _get_services(container)
        callsign = request.path_params["callsign"]
        hours = _qint(request, "hours", 24, min_val=1, max_val=168)
        limit = _qint(request, "limit", 500, max_val=2000)
        since = time.time() - (hours * 3600)
        reports = await services.storage.get_weather_reports(
            callsign=callsign, since=since, limit=limit
        )
        return JSONResponse({"callsign": callsign, "reports": reports})

    async def get_changelog(request: Request):
        changelog_path = Path(__file__).resolve().parent.parent.parent / "CHANGELOG.md"
        if not changelog_path.exists():
            changelog_path = Path("CHANGELOG.md")
        if changelog_path.exists():
            return Response(
                content=changelog_path.read_text(encoding="utf-8"),
                media_type="text/markdown",
            )
        raise HTTPException(status_code=404, detail="Changelog not found")

    async def health(request: Request):
        """Return service health status.

        Responds with HTTP 200 and ``{"status": "ok"}`` when all required
        services are running, or HTTP 503 with ``{"status": "degraded"}`` and
        a list of ``issues`` when something is not ready.  This endpoint is
        intentionally lightweight — it does *not* perform a DB query so it
        is safe to call frequently from monitoring tools and load-balancers.
        """
        services = container.services
        if services is None:
            return JSONResponse(
                {"status": "degraded", "issues": ["services not initialized"]},
                status_code=503,
            )

        issues = []
        if services.agw_reader and not services.agw_reader.connected:
            issues.append("agw not connected")
        if services.log_tailer and not services.log_tailer.active:
            issues.append("log tailer not active")

        if issues:
            return JSONResponse(
                {
                    "status": "degraded",
                    "issues": issues,
                    "uptime_seconds": int(time.time() - services.start_time),
                },
                status_code=503,
            )

        return JSONResponse(
            {
                "status": "ok",
                "uptime_seconds": int(time.time() - services.start_time),
            }
        )

    # Route ordering: specific paths before parameterised ones
    return [
        Route("/packets/export", export_packets_csv),
        Route("/packets", get_packets),
        Route("/messages", get_messages),
        Route("/stations", get_stations),
        Route("/stations/positions", get_station_positions),
        Route("/stations/tracks", get_station_tracks),
        Route("/station/{callsign}/gpx", get_station_gpx),
        Route("/station/{callsign}", get_station),
        Route("/stats", get_stats),
        Route("/config", config_handler, methods=["GET", "PUT"]),
        Route("/storage", wipe_storage, methods=["DELETE"]),
        Route("/decode", decode_packet, methods=["POST"]),
        Route("/weather/stations", get_weather_stations),
        Route("/weather/stations/legend", get_wx_legend),
        Route("/weather/{callsign}", get_weather),
        Route("/changelog", get_changelog),
        Route("/health", health),
    ]


def create_ws_handler(container: ServiceContainer):
    """Return a WebSocket handler function."""

    async def websocket_endpoint(ws: WebSocket):
        services = container.services
        if services is None:
            await ws.close(code=1013, reason="Services not initialized")
            return

        await ws.accept()
        services.ws_clients.add(ws)
        LOG.info(f"WebSocket client connected ({len(services.ws_clients)} total)")

        try:
            recent = await services.storage.query_packets(limit=50)
            for p in reversed(recent):
                await enrich_with_bearing(p, services)
                await ws.send_json({"event": "packet", "data": p})

            await ws.send_json({
                "event": "status",
                "data": {
                    "agw_connected": services.agw_reader.connected if services.agw_reader else False,
                    "log_tailer_active": services.log_tailer.active if services.log_tailer else False,
                },
            })

            # Block until the client disconnects (or sends a message, which
            # this push-only dashboard ignores). Previously this loop polled
            # receive_text with a 0.1s timeout, waking the event loop ~10x/sec
            # per client for nothing. (issue #84)
            while True:
                try:
                    await ws.receive_text()
                except WebSocketDisconnect:
                    break
        except WebSocketDisconnect:
            pass
        except Exception as e:
            LOG.debug(f"WebSocket error: {e}")
        finally:
            services.ws_clients.discard(ws)
            LOG.info(f"WebSocket client disconnected ({len(services.ws_clients)} total)")

    return websocket_endpoint


def create_region_archive_route(container: ServiceContainer, static_dir: str | None = None):
    """Return the Route that serves the active region archive (issue #132).

    Serves ``/basemap/region.pmtiles`` with HTTP Range support (starlette
    FileResponse). The archive resolves from ``basemap.region_archive``: an
    absolute path, a data-dir-relative file that exists, or the vendored
    static fallback (the Florence sample by default).
    """
    static_dir = static_dir or _STATIC_DIR

    async def get_region_archive(request: Request):
        services = _get_services(container)
        path = resolve_region_archive(
            services.config.basemap.region_archive, services.config.data_dir, static_dir
        )
        if not os.path.isfile(path):
            raise HTTPException(status_code=404, detail="Region archive not found")
        return FileResponse(path, media_type="application/octet-stream")

    return Route("/basemap/region.pmtiles", get_region_archive, name="region_archive")


def create_index_handler(static_dir: str):
    """Return a handler that serves the SPA index.html.

    The file is read once at startup (explicit UTF-8) instead of on every
    request — index.html is a bundled static asset. (issue #87)
    """
    index_path = os.path.join(str(static_dir), "index.html")
    content = None
    if os.path.exists(index_path):
        with open(index_path, encoding="utf-8") as f:
            content = f.read()

    async def index(request: Request):
        if content is None:
            return HTMLResponse(
                content="<h1>Direwolf Dashboard</h1><p>Static files not found.</p>"
            )
        return HTMLResponse(content=content)

    return index


def create_basemap_style_route(container: ServiceContainer):
    """Return the Route serving the split basemap style (issue #154).

    ``/basemap/style/<theme>.json`` returns the vendored kit style split
    server-side into a local z0-15 source (the region archive, so the
    station's z12-15 tiles render locally - issue #160) and a z12+ upstream
    source (band :data:`UPSTREAM_MINZOOM`) pointed at the same-origin proxy
    (:data:`UPSTREAM_PROXY_URL`). The browser never talks to
    an external tile host directly. ``X-Basemap-Source`` records which
    upstream archive the z12+ source resolves to (observable proof).
    """
    async def get_basemap_style(request: Request):
        theme = request.path_params["theme"]
        services = _get_services(container)
        try:
            # Probing is blocking HTTP; keep it off the event loop (issue #180).
            _, source_label, upstream_max = await asyncio.to_thread(
                resolve_upstream_cached, upstream_sources(services.config)
            )
            body = render_style(theme, upstream_max_zoom=upstream_max)
        except (ValueError, FileNotFoundError):
            raise HTTPException(status_code=404, detail=f"unknown basemap style: {theme!r}") from None
        resp = Response(body, media_type="application/json")
        resp.headers["X-Basemap-Source"] = source_label
        return resp

    return Route("/basemap/style/{theme}.json", get_basemap_style, name="basemap_style")


def create_basemap_tiles_route(container: ServiceContainer):
    """Return the same-origin z12+ tile proxy route (issue #154).

    ``pmtiles.js`` asks the browser for single byte ranges against this URL;
    the proxy passes each range through to the resolved upstream
    (cloud.hemna.com first, Protomaps daily fallback) **server-side**, caches
    the slice, and returns a same-origin 206. The browser never touches
    ``build.protomaps.com`` (CORS-blocked) and no CSP change is needed.
    """
    async def get_basemap_tiles(request: Request):
        services = _get_services(container)
        rng = request.headers.get("Range", "")
        match = re.fullmatch(r"bytes=(\d+)-(\d+)", rng or "")
        if not match or int(match.group(2)) < int(match.group(1)):
            # pmtiles.js always sends a two-sided byte range; anything else is
            # not addressable and gets the RFC 7233 'no bytes' 416 with total 0.
            return Response(status_code=416, headers={
                "Accept-Ranges": "bytes",
                "Content-Range": "bytes */0",
            })
        start, end = int(match.group(1)), int(match.group(2))
        length = end - start + 1
        if length > PROXY_RANGE_MAX_BYTES:
            # Refuse before the upstream fetch: one anonymous request must not
            # pull a multi-megabyte slice into memory (issue #169).
            LOG.warning(
                "basemap proxy range too large: %d bytes (max %d)",
                length, PROXY_RANGE_MAX_BYTES,
            )
            return Response(status_code=416, headers={
                "Accept-Ranges": "bytes",
                "Content-Range": "bytes */0",
            })
        # Probing is blocking HTTP; keep it off the event loop (issue #180).
        url, label, _upstream_max = await asyncio.to_thread(
            resolve_upstream_cached, upstream_sources(services.config)
        )
        try:
            # offload the blocking httpx fetch to a worker thread so a slow
            # upstream range can't stall local region-archive responses (issue #160)
            data, total = await asyncio.to_thread(proxy_fetch_range, url, start, length)
        except UpstreamRangeError as e:
            LOG.warning("basemap proxy range unsatisfiable: %s", e)
            return Response(status_code=416, headers={
                "Accept-Ranges": "bytes",
                "Content-Range": f"bytes */{e.total}",
            })
        except BasemapSourceError as e:
            LOG.warning("basemap proxy upstream error: %s", e)
            return Response(status_code=502, content="bad gateway")
        return Response(content=data, status_code=206, headers={
            "Content-Range": f"bytes {start}-{end}/{total}",
            "Accept-Ranges": "bytes",
            "Cache-Control": "public, max-age=3600",
            "X-Basemap-Source": label,
        })

    return Route(UPSTREAM_PROXY_URL, get_basemap_tiles, name="basemap_tiles")


def create_basemap_manifest_route(container: ServiceContainer):
    """Return the Route serving the kit region list (issues #204, #212).

    ``GET /basemap/maps-manifest.json`` returns the kit's published list
    (hemna/basemap-kit, docs/CLIENTS.md §2) fetched live from
    ``basemap.manifest_url`` and cached under ``data_dir`` for a short TTL;
    the vendored copy is only the offline fallback (see
    :func:`region_manifest`). ``groups`` are served verbatim, regions are
    filtered to the ids this host can actually serve (an archive must exist
    under :data:`REGIONS_DIR`) with every field verbatim and only ``url``
    rewritten to this host's own same-origin archive route — the kit's
    production host URL is never emitted, a client downloads from the host it
    fetched the list from. A list that cannot be read anywhere is a 503 with
    the reason logged, never a partly-filled list (mirrors the haminfo
    reference kit).
    """

    async def get_basemap_manifest(request: Request):
        services = _get_services(container)
        try:
            doc = await asyncio.to_thread(
                region_manifest,
                services.config.data_dir,
                services.config.basemap.manifest_url,
            )
        except BasemapSourceError as e:
            LOG.error("basemap region manifest: %s", e)
            return JSONResponse(
                status_code=503,
                content={
                    "error": "basemap region list unavailable",
                    "detail": str(e),
                },
            )
        servable = [
            {**r, "url": f"/basemap/regions/{r['id']}.pmtiles"}
            for r in doc["regions"]
            if (REGIONS_DIR / f"{r['id']}.pmtiles").is_file()
        ]
        return JSONResponse(
            {**doc, "regions": servable},
            headers={**CORS_HEADERS, "Cache-Control": "no-cache"},
        )

    return Route(REGION_MANIFEST_ROUTE, get_basemap_manifest, name="basemap_manifest")


def create_basemap_region_archive_route(container: ServiceContainer):
    """Return the Route serving one kit region archive (issue #204).

    ``GET /basemap/regions/<id>.pmtiles`` serves a vendored archive with
    HTTP Range support (starlette FileResponse — the same mechanism
    ``/basemap/region.pmtiles`` already proves 206 + Content-Range),
    CORS for cross-origin pmtiles:// downloaders and no-cache (a rolling
    deploy swaps archives without a URL change). The name must be a
    plain region slug — unknown ids, internal ids (:data:`INTERNAL_REGION_IDS`)
    and anything else are 404s, so the name can never escape
    :data:`REGIONS_DIR`.
    """

    async def get_basemap_region(request: Request):
        name = request.path_params["name"]
        if name in INTERNAL_REGION_IDS or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
            raise HTTPException(status_code=404, detail=f"unknown basemap region: {name!r}")
        path = REGIONS_DIR / f"{name}.pmtiles"
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"unknown basemap region: {name!r}")
        return FileResponse(
            path,
            media_type="application/vnd.pmtiles",
            headers={**CORS_HEADERS, "Cache-Control": "no-cache"},
        )

    return Route(REGION_ARCHIVE_ROUTE, get_basemap_region, name="basemap_region_archive")
