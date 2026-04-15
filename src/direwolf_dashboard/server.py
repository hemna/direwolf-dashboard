"""FastAPI server — REST API, WebSocket, and static file serving."""

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import (
    FastAPI,
    WebSocket,
    WebSocketDisconnect,
    Query,
    Response,
    HTTPException,
)
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from direwolf_dashboard.config import Config, load_config, update_config
from direwolf_dashboard.storage import Storage
from direwolf_dashboard.tile_proxy import TileProxy
from direwolf_dashboard.processor import PacketProcessor
from direwolf_dashboard.agw import AGWReader
from direwolf_dashboard.log_tailer import LogTailer

LOG = logging.getLogger(__name__)


class AppState:
    """Shared application state."""

    def __init__(self):
        self.config: Optional[Config] = None
        self.config_path: Optional[str] = None
        self.storage: Optional[Storage] = None
        self.tile_proxy: Optional[TileProxy] = None
        self.processor: Optional[PacketProcessor] = None
        self.broadcast_queue: Optional[asyncio.Queue] = None
        self.agw_reader: Optional[AGWReader] = None
        self.log_tailer: Optional[LogTailer] = None
        self.ws_clients: set[WebSocket] = set()
        self.start_time: float = 0
        self._background_tasks: list[asyncio.Task] = []


state = AppState()


def create_app(config: Config, config_path: str) -> FastAPI:
    """Create and configure the FastAPI application."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Startup and shutdown logic."""
        state.config = config
        state.config_path = config_path
        state.start_time = time.time()

        # Init storage
        state.storage = Storage(config.storage.db_path)
        await state.storage.init()
        LOG.info(f"Database initialized: {config.storage.db_path}")

        # Init tile proxy
        state.tile_proxy = TileProxy(
            cache_dir=config.tiles.cache_dir,
            tile_url_template=config.tiles.tile_url,
            max_cache_mb=config.tiles.max_cache_mb,
        )
        await state.tile_proxy.init()

        # Init broadcast queue and processor
        state.broadcast_queue = asyncio.Queue(maxsize=500)
        state.processor = PacketProcessor(
            station_lat=config.station.latitude,
            station_lon=config.station.longitude,
            broadcast_queue=state.broadcast_queue,
        )

        # Start AGW reader
        state.agw_reader = AGWReader(
            host=config.direwolf.agw_host,
            port=config.direwolf.agw_port,
            packet_callback=state.processor.on_agw_packet,
        )
        agw_task = asyncio.create_task(state.agw_reader.run())
        state._background_tasks.append(agw_task)

        # Start log tailer
        state.log_tailer = LogTailer(
            log_path=config.direwolf.log_file,
            line_callback=state.processor.on_log_lines,
        )
        tailer_task = asyncio.create_task(state.log_tailer.run())
        state._background_tasks.append(tailer_task)

        # Start broadcast consumer
        broadcast_task = asyncio.create_task(_broadcast_consumer())
        state._background_tasks.append(broadcast_task)

        # Start housekeeping
        housekeep_task = asyncio.create_task(
            _housekeeping_loop(config.storage.retention_days)
        )
        state._background_tasks.append(housekeep_task)

        # Start stats broadcaster
        stats_task = asyncio.create_task(_stats_broadcaster())
        state._background_tasks.append(stats_task)

        LOG.info("All background tasks started.")
        yield

        # Shutdown
        LOG.info("Shutting down...")
        if state.agw_reader:
            await state.agw_reader.stop()
        if state.log_tailer:
            await state.log_tailer.stop()
        for task in state._background_tasks:
            task.cancel()
        if state.tile_proxy:
            await state.tile_proxy.close()
        if state.storage:
            await state.storage.close()

    app = FastAPI(title="Direwolf Dashboard", lifespan=lifespan)

    # --- REST API Routes ---

    @app.get("/api/packets")
    async def get_packets(
        since: Optional[float] = Query(None),
        limit: int = Query(100, le=500),
        callsign: Optional[str] = Query(None),
        type: Optional[str] = Query(None),
    ):
        """Query packet history with optional filters."""
        packets = await state.storage.query_packets(
            since=since, limit=limit, callsign=callsign, packet_type=type
        )
        # Compute bearing/distance for each packet
        for p in packets:
            _enrich_with_bearing(p)
        return packets

    @app.get("/api/stations")
    async def get_stations():
        """Get all known stations with last position."""
        return await state.storage.get_stations()

    @app.get("/api/station/{callsign}")
    async def get_station(callsign: str, track_limit: int = Query(100, le=500)):
        """Get station detail and position track."""
        stations = await state.storage.get_stations()
        station = next((s for s in stations if s["callsign"] == callsign), None)
        if not station:
            raise HTTPException(status_code=404, detail="Station not found")

        track = await state.storage.get_station_track(callsign, limit=track_limit)
        return {"station": station, "track": track}

    @app.get("/api/stations/positions")
    async def get_station_positions():
        """Return lightweight position map for all known stations."""
        rows = await state.storage.get_all_station_positions()
        return {
            row["callsign"]: {"lat": row["latitude"], "lng": row["longitude"]}
            for row in rows
        }

    @app.get("/api/stats")
    async def get_stats():
        """Get dashboard statistics."""
        db_stats = await state.storage.get_stats()
        db_stats["uptime_seconds"] = int(time.time() - state.start_time)
        db_stats["agw_connected"] = (
            state.agw_reader.connected if state.agw_reader else False
        )
        db_stats["log_tailer_active"] = (
            state.log_tailer.active if state.log_tailer else False
        )
        db_stats["tile_cache"] = (
            state.tile_proxy.get_cache_stats() if state.tile_proxy else {}
        )
        return db_stats

    @app.get("/api/config")
    async def get_config():
        """Get current configuration."""
        return state.config.to_dict()

    @app.put("/api/config")
    async def put_config(updates: dict):
        """Update configuration. Returns updated fields and restart status."""
        try:
            new_config, updated_fields, restart_required = update_config(
                state.config, updates, state.config_path
            )
            state.config = new_config

            # Apply hot-reload fields
            if state.processor:
                state.processor.station_lat = new_config.station.latitude
                state.processor.station_lon = new_config.station.longitude

            return {
                "updated": updated_fields,
                "restart_required": restart_required,
            }
        except Exception as e:
            raise HTTPException(status_code=400, detail={"errors": str(e)})

    @app.post("/api/import-direwolf-conf")
    async def import_direwolf_conf(body: dict):
        """Parse a Direwolf config file and return extracted station settings."""
        from direwolf_dashboard.config import parse_direwolf_conf

        conf_path = body.get("conf_path", "")
        if not conf_path:
            raise HTTPException(status_code=400, detail="conf_path is required")

        extracted = parse_direwolf_conf(conf_path)
        if not extracted:
            raise HTTPException(
                status_code=404,
                detail=f"Could not read or parse: {conf_path}",
            )
        return extracted

    @app.get("/api/tiles/{z}/{x}/{y}.png")
    async def get_tile(z: int, x: int, y: int):
        """Serve a map tile (cached or proxied from upstream)."""
        tile_data = await state.tile_proxy.get_tile(z, x, y)
        if tile_data:
            return Response(content=tile_data, media_type="image/png")
        raise HTTPException(status_code=404, detail="Tile not found")

    @app.post("/api/tiles/preload")
    async def preload_tiles(request: dict):
        """Estimate or start a tile preload.

        Without confirm=true: returns estimate.
        With confirm=true: starts background preload.
        """
        bbox = request.get("bbox", [])
        if len(bbox) != 4:
            raise HTTPException(
                status_code=400, detail="bbox must be [south, west, north, east]"
            )

        south, west, north, east = bbox
        min_zoom = request.get("min_zoom", 1)
        max_zoom = min(request.get("max_zoom", 14), 16)

        estimate = state.tile_proxy.estimate_preload(
            south, west, north, east, min_zoom, max_zoom
        )

        if not request.get("confirm"):
            return estimate

        # Start preload in background
        async def progress_cb(done, total):
            await _broadcast_event("preload_progress", {"done": done, "total": total})

        asyncio.create_task(
            state.tile_proxy.preload(
                south, west, north, east, min_zoom, max_zoom, progress_cb
            )
        )
        return {"status": "started", **estimate}

    @app.delete("/api/tiles/preload")
    async def cancel_preload():
        """Cancel an in-progress tile preload."""
        if state.tile_proxy:
            state.tile_proxy.cancel_preload()
        return {"status": "cancelled"}

    # --- WebSocket ---

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws.accept()
        state.ws_clients.add(ws)
        LOG.info(f"WebSocket client connected ({len(state.ws_clients)} total)")

        try:
            # Send initial burst of recent packets
            recent = await state.storage.query_packets(limit=50)
            for p in reversed(recent):  # Oldest first for initial load
                _enrich_with_bearing(p)
                await ws.send_json({"event": "packet", "data": p})

            # Send current status
            await ws.send_json(
                {
                    "event": "status",
                    "data": {
                        "agw_connected": state.agw_reader.connected
                        if state.agw_reader
                        else False,
                        "log_tailer_active": state.log_tailer.active
                        if state.log_tailer
                        else False,
                    },
                }
            )

            # Keep connection alive — read messages (for future client-to-server communication)
            while True:
                try:
                    await asyncio.wait_for(ws.receive_text(), timeout=60)
                except asyncio.TimeoutError:
                    # Send ping to check if client is alive
                    await ws.send_json({"event": "ping"})
        except WebSocketDisconnect:
            pass
        except Exception as e:
            LOG.debug(f"WebSocket error: {e}")
        finally:
            state.ws_clients.discard(ws)
            LOG.info(f"WebSocket client disconnected ({len(state.ws_clients)} total)")

    # --- Static files ---
    import os

    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.exists(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    async def index():
        """Serve the main page."""
        index_path = os.path.join(static_dir, "index.html")
        if os.path.exists(index_path):
            with open(index_path) as f:
                return HTMLResponse(content=f.read())
        return HTMLResponse(
            content="<h1>Direwolf Dashboard</h1><p>Static files not found.</p>"
        )

    return app


def _enrich_with_bearing(packet: dict) -> None:
    """Add bearing and distance to a packet dict if position data is available."""
    from direwolf_dashboard.processor import (
        calculate_initial_compass_bearing,
        degrees_to_cardinal,
    )
    from haversine import haversine, Unit

    if (
        state.config
        and state.config.station.latitude
        and state.config.station.longitude
        and packet.get("latitude")
        and packet.get("longitude")
    ):
        try:
            my_coords = (state.config.station.latitude, state.config.station.longitude)
            pkt_coords = (packet["latitude"], packet["longitude"])
            bearing_deg = calculate_initial_compass_bearing(my_coords, pkt_coords)
            packet["bearing"] = degrees_to_cardinal(bearing_deg, full_string=True)
            packet["distance_miles"] = round(
                haversine(my_coords, pkt_coords, unit=Unit.MILES), 2
            )
        except Exception:
            pass


async def _broadcast_consumer() -> None:
    """Read from broadcast queue and send to all WebSocket clients."""
    while True:
        try:
            packet = await state.broadcast_queue.get()

            # Store in database
            if state.storage:
                row_id = await state.storage.insert_packet(packet)
                packet["id"] = row_id

                # Update stations table if position data present
                if packet.get("latitude") and packet.get("longitude"):
                    await state.storage.upsert_station(
                        callsign=packet["from_call"],
                        last_seen=packet["timestamp"],
                        latitude=packet["latitude"],
                        longitude=packet["longitude"],
                        symbol=packet.get("symbol"),
                        symbol_table=packet.get("symbol_table"),
                        comment=packet.get("comment"),
                    )
                else:
                    # Packet has no position — look up last known position
                    # so the frontend can still show the station on the map.
                    stn = await state.storage.get_station(packet["from_call"])
                    if stn and stn.get("latitude") and stn.get("longitude"):
                        packet["latitude"] = stn["latitude"]
                        packet["longitude"] = stn["longitude"]
                        packet["symbol"] = packet.get("symbol") or stn.get("symbol")
                        packet["symbol_table"] = packet.get("symbol_table") or stn.get(
                            "symbol_table"
                        )
                        packet["position_from_db"] = True
                        # Also enrich with bearing/distance
                        _enrich_with_bearing(packet)
                    # Update last_seen / packet_count even without position
                    await state.storage.upsert_station(
                        callsign=packet["from_call"],
                        last_seen=packet["timestamp"],
                    )

            # Broadcast to WebSocket clients
            await _broadcast_event("packet", packet)

        except asyncio.CancelledError:
            break
        except Exception as e:
            LOG.error(f"Broadcast consumer error: {e}")


async def _broadcast_event(event: str, data: dict) -> None:
    """Send an event to all connected WebSocket clients."""
    if not state.ws_clients:
        return

    message = json.dumps({"event": event, "data": data}, default=str)
    disconnected = set()

    for ws in state.ws_clients:
        try:
            await ws.send_text(message)
        except Exception:
            disconnected.add(ws)

    state.ws_clients -= disconnected


async def _housekeeping_loop(retention_days: int) -> None:
    """Periodic housekeeping — runs on startup and every hour."""
    while True:
        try:
            if state.storage:
                deleted = await state.storage.housekeep(retention_days)
                if deleted:
                    LOG.info(f"Housekeeping deleted {deleted} old packets")
        except asyncio.CancelledError:
            break
        except Exception as e:
            LOG.error(f"Housekeeping error: {e}")

        await asyncio.sleep(3600)  # Every hour


async def _stats_broadcaster() -> None:
    """Periodically broadcast stats to WebSocket clients."""
    while True:
        try:
            await asyncio.sleep(10)
            if state.storage and state.ws_clients:
                db_stats = await state.storage.get_stats()
                db_stats["uptime_seconds"] = int(time.time() - state.start_time)
                db_stats["agw_connected"] = (
                    state.agw_reader.connected if state.agw_reader else False
                )
                db_stats["log_tailer_active"] = (
                    state.log_tailer.active if state.log_tailer else False
                )
                await _broadcast_event("stats", db_stats)
        except asyncio.CancelledError:
            break
        except Exception as e:
            LOG.error(f"Stats broadcaster error: {e}")
