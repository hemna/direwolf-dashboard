"""Router factory functions — create Starlette route lists bound to a ServiceContainer."""

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from direwolf_dashboard import __version__
from direwolf_dashboard.config import update_config
from direwolf_dashboard.decoder import decode_packet as _decode
from direwolf_dashboard.lifecycle import (
    ServiceContainer,
    broadcast_event,
    enrich_with_bearing,
)

LOG = logging.getLogger(__name__)


def _get_services(container: ServiceContainer):
    """Get services from container, raising HTTP 503 if not initialized."""
    services = container.services
    if services is None:
        raise HTTPException(status_code=503, detail="Services not initialized")
    return services


def _generate_gpx(callsign: str, track: list[dict]) -> str:
    """Generate a GPX 1.1 XML string from a station's position track."""
    from datetime import datetime, timezone

    points = list(reversed(track))

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="Direwolf Dashboard"',
        '     xmlns="http://www.topografix.com/GPX/1/1">',
        '  <metadata>',
        f'    <name>{callsign} Track</name>',
        f'    <time>{datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}</time>',
        '  </metadata>',
        '  <trk>',
        f'    <name>{callsign}</name>',
        '    <trkseg>',
    ]

    for pt in points:
        lat = pt["latitude"]
        lon = pt["longitude"]
        ts = pt.get("timestamp")
        time_str = ""
        if ts:
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            time_str = f"\n        <time>{dt.strftime('%Y-%m-%dT%H:%M:%SZ')}</time>"
        lines.append(f'      <trkpt lat="{lat}" lon="{lon}">{time_str}')
        lines.append('      </trkpt>')

    lines.extend([
        '    </trkseg>',
        '  </trk>',
        '</gpx>',
    ])

    return "\n".join(lines)


def _qint(request: Request, name: str, default: int, min_val: int = None, max_val: int = None) -> int:
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
        limit = _qint(request, "limit", 100, max_val=500)
        callsign = request.query_params.get("callsign") or None
        ptype = request.query_params.get("type") or None
        packets = await services.storage.query_packets(
            since=since, limit=limit, callsign=callsign, packet_type=ptype
        )
        for p in packets:
            await enrich_with_bearing(p, services)
        return JSONResponse(packets)

    async def get_stations(request: Request):
        services = _get_services(container)
        return JSONResponse(await services.storage.get_stations())

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
        filename = f"{callsign.replace(' ', '_')}_{hours}h.gpx"
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
            return JSONResponse(d)

        # PUT
        try:
            updates = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")

        try:
            station_updates = updates.get("station", {}).copy()
            has_my_pos = "my_position" in station_updates
            my_pos = station_updates.pop("my_position", None)

            if not station_updates:
                updates.pop("station", None)

            if has_my_pos:
                if my_pos is None:
                    await services.storage.set_my_position(None)
                else:
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
                    await services.storage.set_my_position(
                        my_pos if mp_type else None
                    )

            updated_fields = []
            restart_required = False
            if updates:
                new_config, updated_fields, restart_required = update_config(
                    services.config, updates, services.config_path
                )
                services.config = new_config

            if has_my_pos:
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
            raise HTTPException(status_code=400, detail={"errors": str(e)})

    async def get_tile(request: Request):
        services = _get_services(container)
        try:
            z = int(request.path_params["z"])
            x = int(request.path_params["x"])
            y = int(request.path_params["y"])
        except (ValueError, KeyError):
            raise HTTPException(status_code=400, detail="Invalid tile coordinates")
        if z < 0 or z > 19:
            raise HTTPException(status_code=400, detail="Zoom level must be 0-19")
        max_coord = (1 << z) - 1
        if x < 0 or x > max_coord or y < 0 or y > max_coord:
            raise HTTPException(status_code=400, detail="Tile coordinates out of range")
        tile_data = await services.tile_proxy.get_tile(z, x, y)
        if tile_data:
            return Response(content=tile_data, media_type="image/png")
        raise HTTPException(status_code=404, detail="Tile not found")

    async def tiles_preload_handler(request: Request):
        services = _get_services(container)
        if request.method == "DELETE":
            if services.tile_proxy:
                services.tile_proxy.cancel_preload()
            return JSONResponse({"status": "cancelled"})

        # POST
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")

        bbox = body.get("bbox", [])
        if len(bbox) != 4:
            raise HTTPException(status_code=400, detail="bbox must be [south, west, north, east]")

        south, west, north, east = bbox
        min_zoom = body.get("min_zoom", 1)
        max_zoom = min(body.get("max_zoom", 14), 16)

        estimate = services.tile_proxy.estimate_preload(south, west, north, east, min_zoom, max_zoom)

        if not body.get("confirm"):
            return JSONResponse(estimate)

        async def progress_cb(done, total):
            await broadcast_event(
                "preload_progress", {"done": done, "total": total}, services.ws_clients
            )

        task = asyncio.create_task(
            services.tile_proxy.preload(south, west, north, east, min_zoom, max_zoom, progress_cb)
        )
        services.background_tasks.append(task)
        return JSONResponse({"status": "started", **estimate})

    async def wipe_storage(request: Request):
        services = _get_services(container)
        await services.storage.reset()
        await broadcast_event("storage_reset", {}, services.ws_clients)
        return JSONResponse({"status": "ok"})

    async def decode_packet(request: Request):
        services = _get_services(container)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")
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
                media_type="text/plain",
            )
        raise HTTPException(status_code=404, detail="Changelog not found")

    # Route ordering: specific paths before parameterised ones
    return [
        Route("/packets", get_packets),
        Route("/stations", get_stations),
        Route("/stations/positions", get_station_positions),
        Route("/stations/tracks", get_station_tracks),
        Route("/station/{callsign}/gpx", get_station_gpx),
        Route("/station/{callsign}", get_station),
        Route("/stats", get_stats),
        Route("/config", config_handler, methods=["GET", "PUT"]),
        Route("/tiles/{z}/{x}/{y}.png", get_tile),
        Route("/tiles/preload", tiles_preload_handler, methods=["POST", "DELETE"]),
        Route("/storage", wipe_storage, methods=["DELETE"]),
        Route("/decode", decode_packet, methods=["POST"]),
        Route("/weather/{callsign}", get_weather),
        Route("/changelog", get_changelog),
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

            while True:
                try:
                    await asyncio.wait_for(ws.receive_text(), timeout=0.1)
                except asyncio.TimeoutError:
                    pass
        except WebSocketDisconnect:
            pass
        except Exception as e:
            LOG.debug(f"WebSocket error: {e}")
        finally:
            services.ws_clients.discard(ws)
            LOG.info(f"WebSocket client disconnected ({len(services.ws_clients)} total)")

    return websocket_endpoint


def create_index_handler(static_dir: str):
    """Return a handler that serves the SPA index.html."""

    async def index(request: Request):
        index_path = os.path.join(str(static_dir), "index.html")
        if os.path.exists(index_path):
            with open(index_path) as f:
                return HTMLResponse(content=f.read())
        return HTMLResponse(content="<h1>Direwolf Dashboard</h1><p>Static files not found.</p>")

    return index
