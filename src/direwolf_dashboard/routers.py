"""Router factory functions — create APIRouter instances bound to a ServiceContainer."""

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

from dataclasses import asdict
from fastapi import APIRouter, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from direwolf_dashboard.config import update_config
from direwolf_dashboard.lifecycle import (
    ServiceContainer,
    broadcast_event,
    enrich_with_bearing,
)

LOG = logging.getLogger(__name__)


def create_api_router(container: ServiceContainer) -> APIRouter:
    """Create the REST API router. Route handlers access container.services at request time.

    Routes are defined WITHOUT the /api/ prefix — the caller mounts at the desired prefix.
    Standalone: prefix="/api", Hosted: prefix="/api/direwolf-dashboard".
    """
    router = APIRouter()

    @router.get("/packets")
    async def get_packets(
        since: Optional[float] = Query(None),
        limit: int = Query(100, le=500),
        callsign: Optional[str] = Query(None),
        type: Optional[str] = Query(None),
    ):
        """Query packet history with optional filters."""
        services = container.services
        assert services is not None, "Services not initialized"
        packets = await services.storage.query_packets(since=since, limit=limit, callsign=callsign, packet_type=type)
        # Compute bearing/distance for each packet
        for p in packets:
            await enrich_with_bearing(p, services)
        return packets

    @router.get("/stations")
    async def get_stations():
        """Get all known stations with last position."""
        services = container.services
        assert services is not None, "Services not initialized"
        return await services.storage.get_stations()

    @router.get("/station/{callsign}")
    async def get_station(callsign: str, track_limit: int = Query(100, le=500)):
        """Get station detail and position track."""
        services = container.services
        assert services is not None, "Services not initialized"
        stations = await services.storage.get_stations()
        station = next((s for s in stations if s["callsign"] == callsign), None)
        if not station:
            raise HTTPException(status_code=404, detail="Station not found")

        track = await services.storage.get_station_track(callsign, limit=track_limit)
        return {"station": station, "track": track}

    @router.get("/stations/positions")
    async def get_station_positions():
        """Return lightweight position map for all known stations."""
        services = container.services
        assert services is not None, "Services not initialized"
        rows = await services.storage.get_all_station_positions()
        return {row["callsign"]: {"lat": row["latitude"], "lng": row["longitude"]} for row in rows}

    @router.get("/stations/tracks")
    async def get_station_tracks(hours: int = Query(1, ge=1, le=24)):
        """Return position tracks for all stations within a time window.

        Returns a dict of callsign -> [[lat, lon, timestamp], ...] for map display.
        """
        services = container.services
        assert services is not None, "Services not initialized"
        since = time.time() - (hours * 3600)
        tracks = await services.storage.get_all_station_tracks(since)
        # Convert to lightweight format: {callsign: [[lat, lon, ts], ...]}
        return {cs: [[p["latitude"], p["longitude"], p["timestamp"]] for p in points] for cs, points in tracks.items()}

    @router.get("/stats")
    async def get_stats():
        """Get dashboard statistics."""
        services = container.services
        assert services is not None, "Services not initialized"
        db_stats = await services.storage.get_stats()
        db_stats.update(services.get_stats_dict())
        return db_stats

    @router.get("/config")
    async def get_config():
        """Get current configuration."""
        services = container.services
        assert services is not None, "Services not initialized"
        from direwolf_dashboard import __version__

        d = services.config.to_dict()
        d["version"] = __version__
        return d

    @router.put("/config")
    async def put_config(updates: dict):
        """Update configuration. Returns updated fields and restart status."""
        services = container.services
        assert services is not None, "Services not initialized"
        try:
            # Validate my_position if present
            station_updates = updates.get("station", {})
            has_my_pos = "my_position" in station_updates
            my_pos = station_updates.get("my_position")
            if my_pos is not None:
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

            new_config, updated_fields, restart_required = update_config(services.config, updates, services.config_path)
            services.config = new_config

            # Broadcast my_position changes to all WebSocket clients
            if has_my_pos:
                await broadcast_event(
                    "config_updated",
                    {"my_position": asdict(services.config.station.my_position)},
                    services.ws_clients,
                )

            return {
                "updated": updated_fields,
                "restart_required": restart_required,
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail={"errors": str(e)})

    @router.get("/tiles/{z}/{x}/{y}.png")
    async def get_tile(z: int, x: int, y: int):
        """Serve a map tile (cached or proxied from upstream)."""
        services = container.services
        assert services is not None, "Services not initialized"
        tile_data = await services.tile_proxy.get_tile(z, x, y)
        if tile_data:
            return Response(content=tile_data, media_type="image/png")
        raise HTTPException(status_code=404, detail="Tile not found")

    @router.post("/tiles/preload")
    async def preload_tiles(request: dict):
        """Estimate or start a tile preload.

        Without confirm=true: returns estimate.
        With confirm=true: starts background preload.
        """
        services = container.services
        assert services is not None, "Services not initialized"
        bbox = request.get("bbox", [])
        if len(bbox) != 4:
            raise HTTPException(status_code=400, detail="bbox must be [south, west, north, east]")

        south, west, north, east = bbox
        min_zoom = request.get("min_zoom", 1)
        max_zoom = min(request.get("max_zoom", 14), 16)

        estimate = services.tile_proxy.estimate_preload(south, west, north, east, min_zoom, max_zoom)

        if not request.get("confirm"):
            return estimate

        # Start preload in background
        async def progress_cb(done, total):
            await broadcast_event("preload_progress", {"done": done, "total": total}, services.ws_clients)

        asyncio.create_task(services.tile_proxy.preload(south, west, north, east, min_zoom, max_zoom, progress_cb))
        return {"status": "started", **estimate}

    @router.delete("/tiles/preload")
    async def cancel_preload():
        """Cancel an in-progress tile preload."""
        services = container.services
        assert services is not None, "Services not initialized"
        if services.tile_proxy:
            services.tile_proxy.cancel_preload()
        return {"status": "cancelled"}

    @router.delete("/storage")
    async def wipe_storage():
        """Wipe the packet database and recreate empty tables.

        Safe to call at runtime — no restart required.
        """
        services = container.services
        assert services is not None, "Services not initialized"
        await services.storage.reset()
        await broadcast_event("storage_reset", {}, services.ws_clients)
        return {"status": "ok"}

    # --- Decode Endpoint ---

    class DecodeRequest(BaseModel):
        raw_packet: str

    @router.post("/decode")
    async def decode_packet(req: DecodeRequest):
        """Decode a raw APRS packet and return structured JSON result."""
        import re
        from direwolf_dashboard.decoder import decode_packet as _decode

        services = container.services
        assert services is not None, "Services not initialized"

        result = _decode(req.raw_packet)

        if not result["success"]:
            return result

        # Look up path station positions from DB
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
                    path_stations = (
                        await services.storage.get_stations_by_callsigns(real_calls)
                    )
                except Exception:
                    LOG.exception("Failed to look up path station positions")

        result["path_stations"] = path_stations
        return result

    return router


def create_ws_router(container: ServiceContainer, path: str = "/ws") -> APIRouter:
    """Create the WebSocket router. Accepts a custom path for hosted mounting."""
    router = APIRouter()

    @router.websocket(path)
    async def websocket_endpoint(ws: WebSocket):
        services = container.services
        assert services is not None, "Services not initialized"

        await ws.accept()
        services.ws_clients.add(ws)
        LOG.info(f"WebSocket client connected ({len(services.ws_clients)} total)")

        # Use an event to keep the handler alive; broadcast_event sends
        # directly via ws.send_text() from the _broadcast_consumer task.
        disconnect_event = asyncio.Event()
        ws._disconnect_event = disconnect_event

        try:
            # Send initial burst of recent packets
            recent = await services.storage.query_packets(limit=50)
            for p in reversed(recent):  # Oldest first for initial load
                await enrich_with_bearing(p, services)
                await ws.send_json({"event": "packet", "data": p})

            # Send current status
            await ws.send_json(
                {
                    "event": "status",
                    "data": {
                        "agw_connected": services.agw_reader.connected if services.agw_reader else False,
                        "log_tailer_active": services.log_tailer.active if services.log_tailer else False,
                    },
                }
            )

            # Keep-alive: short receive timeout forces the ASGI handler
            # to cycle frequently, which flushes any buffered outbound
            # WebSocket frames that were queued by broadcast_event.
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

    return router


def create_index_router(static_dir: str | Path) -> APIRouter:
    """Create the index route that serves the SPA HTML. Only used by standalone app."""
    router = APIRouter()
    static_dir = str(static_dir)

    @router.get("/")
    async def index():
        """Serve the main page."""
        index_path = os.path.join(static_dir, "index.html")
        if os.path.exists(index_path):
            with open(index_path) as f:
                return HTMLResponse(content=f.read())
        return HTMLResponse(content="<h1>Direwolf Dashboard</h1><p>Static files not found.</p>")

    return router
