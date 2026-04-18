"""Service lifecycle management — startup, shutdown, and shared state container."""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from fastapi import WebSocket

from direwolf_dashboard.agw import AGWReader
from direwolf_dashboard.config import Config
from direwolf_dashboard.log_tailer import LogTailer
from direwolf_dashboard.processor import PacketProcessor
from direwolf_dashboard.storage import Storage
from direwolf_dashboard.tile_proxy import TileProxy

LOG = logging.getLogger(__name__)


@dataclass
class DirewolfServices:
    """All runtime services for the Direwolf Dashboard."""

    config: Config
    config_path: str | None
    storage: Storage
    tile_proxy: TileProxy
    processor: PacketProcessor
    broadcast_queue: asyncio.Queue
    agw_reader: AGWReader
    log_tailer: LogTailer
    ws_clients: set[WebSocket] = field(default_factory=set)
    start_time: float = 0.0
    background_tasks: list[asyncio.Task] = field(default_factory=list)

    def get_stats_dict(self) -> dict:
        """Build the stats response dict (sync portion — caller must merge DB stats)."""
        return {
            "uptime_seconds": int(time.time() - self.start_time),
            "agw_connected": self.agw_reader.connected if self.agw_reader else False,
            "log_tailer_active": self.log_tailer.active if self.log_tailer else False,
            "tile_cache": self.tile_proxy.get_cache_stats() if self.tile_proxy else {},
        }


class ServiceContainer:
    """Mutable holder passed to router factories at creation time, populated during lifespan."""

    def __init__(self) -> None:
        self.services: DirewolfServices | None = None


async def startup_services(config: Config, config_path: str | None = None) -> DirewolfServices:
    """Initialize all services and start background tasks. Returns a populated DirewolfServices."""
    # Init storage
    storage = Storage(config.storage.db_path)
    await storage.init()
    LOG.info(f"Database initialized: {config.storage.db_path}")

    # Init tile proxy
    tile_proxy = TileProxy(
        cache_dir=config.tiles.cache_dir,
        tile_url_template=config.tiles.tile_url,
        max_cache_mb=config.tiles.max_cache_mb,
    )
    await tile_proxy.init()

    # Init broadcast queue and processor
    broadcast_queue: asyncio.Queue = asyncio.Queue(maxsize=500)
    processor = PacketProcessor(broadcast_queue=broadcast_queue)

    # Create AGW reader
    agw_reader = AGWReader(
        host=config.direwolf.agw_host,
        port=config.direwolf.agw_port,
        packet_callback=processor.on_agw_packet,
    )

    # Create log tailer
    log_tailer = LogTailer(
        log_path=config.direwolf.log_file,
        line_callback=processor.on_log_lines,
    )

    services = DirewolfServices(
        config=config,
        config_path=config_path,
        storage=storage,
        tile_proxy=tile_proxy,
        processor=processor,
        broadcast_queue=broadcast_queue,
        agw_reader=agw_reader,
        log_tailer=log_tailer,
        start_time=time.time(),
    )

    # Start background tasks
    background_tasks = services.background_tasks
    background_tasks.append(asyncio.create_task(agw_reader.run()))
    background_tasks.append(asyncio.create_task(log_tailer.run()))
    background_tasks.append(asyncio.create_task(_broadcast_consumer(services)))
    background_tasks.append(asyncio.create_task(_housekeeping_loop(services, config.storage.retention_days)))
    background_tasks.append(asyncio.create_task(_stats_broadcaster(services)))

    LOG.info("All background tasks started.")
    return services


async def shutdown_services(services: DirewolfServices) -> None:
    """Gracefully stop all services and cancel background tasks."""
    LOG.info("Shutting down...")
    if services.agw_reader:
        await services.agw_reader.stop()
    if services.log_tailer:
        await services.log_tailer.stop()
    for task in services.background_tasks:
        task.cancel()
    if services.tile_proxy:
        await services.tile_proxy.close()
    if services.storage:
        await services.storage.close()


# ---------------------------------------------------------------------------
# Helper functions (previously module-level in server.py, now take services)
# ---------------------------------------------------------------------------


async def resolve_my_position(services: DirewolfServices) -> Optional[tuple[float, float]]:
    """Resolve current 'my position' coordinates from config."""
    if not services.config:
        return None
    mp = services.config.station.my_position
    if mp.type == "pin" and mp.latitude is not None and mp.longitude is not None:
        return (mp.latitude, mp.longitude)
    if mp.type == "station" and mp.callsign and services.storage:
        stn = await services.storage.get_station(mp.callsign)
        if stn and stn.get("latitude") and stn.get("longitude"):
            return (stn["latitude"], stn["longitude"])
    return None


async def enrich_with_bearing(packet: dict, services: DirewolfServices) -> None:
    """Add bearing/distance to packet using my_position as reference."""
    from direwolf_dashboard.processor import (
        calculate_initial_compass_bearing,
        degrees_to_cardinal,
    )
    from haversine import haversine, Unit

    if not packet.get("latitude") or not packet.get("longitude"):
        return

    my_coords = await resolve_my_position(services)
    if not my_coords:
        return

    try:
        pkt_coords = (packet["latitude"], packet["longitude"])
        bearing_deg = calculate_initial_compass_bearing(my_coords, pkt_coords)
        packet["bearing"] = degrees_to_cardinal(bearing_deg, full_string=True)
        packet["distance_miles"] = round(haversine(my_coords, pkt_coords, unit=Unit.MILES), 2)
    except Exception:
        pass


async def broadcast_event(event: str, data: dict, ws_clients: set[WebSocket]) -> None:
    """Send an event to all connected WebSocket clients."""
    if not ws_clients:
        return

    message = json.dumps({"event": event, "data": data}, default=str)
    disconnected = set()

    for ws in ws_clients:
        try:
            await ws.send_text(message)
        except Exception:
            disconnected.add(ws)

    ws_clients -= disconnected


# ---------------------------------------------------------------------------
# Background tasks (previously module-level in server.py, now take services)
# ---------------------------------------------------------------------------


async def _broadcast_consumer(services: DirewolfServices) -> None:
    """Read from broadcast queue and send to all WebSocket clients."""
    while True:
        try:
            packet = await services.broadcast_queue.get()

            # Store in database
            if services.storage:
                row_id = await services.storage.insert_packet(packet)
                packet["id"] = row_id

                # Update stations table if position data present
                if packet.get("latitude") and packet.get("longitude"):
                    await services.storage.upsert_station(
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
                    stn = await services.storage.get_station(packet["from_call"])
                    if stn and stn.get("latitude") and stn.get("longitude"):
                        packet["latitude"] = stn["latitude"]
                        packet["longitude"] = stn["longitude"]
                        packet["symbol"] = packet.get("symbol") or stn.get("symbol")
                        packet["symbol_table"] = packet.get("symbol_table") or stn.get("symbol_table")
                        packet["position_from_db"] = True
                    # Update last_seen / packet_count even without position
                    await services.storage.upsert_station(
                        callsign=packet["from_call"],
                        last_seen=packet["timestamp"],
                    )

                # Enrich with bearing/distance from my_position
                await enrich_with_bearing(packet, services)

                # Append bearing/distance to compact_log if present
                if packet.get("bearing") and packet.get("compact_log"):
                    dist = packet.get("distance_miles", 0)
                    bearing_html = (
                        f' : <span style="color:#FFA900">{packet["bearing"]}</span>'
                        f'<span style="color:#FF5733">@{dist:.2f}miles</span>'
                    )
                    packet["compact_log"] += bearing_html

            # Broadcast to WebSocket clients
            await broadcast_event("packet", packet, services.ws_clients)

        except asyncio.CancelledError:
            break
        except Exception as e:
            LOG.error(f"Broadcast consumer error: {e}")


async def _housekeeping_loop(services: DirewolfServices, retention_days: int) -> None:
    """Periodic housekeeping — runs on startup and every hour."""
    while True:
        try:
            if services.storage:
                deleted = await services.storage.housekeep(retention_days)
                if deleted:
                    LOG.info(f"Housekeeping deleted {deleted} old packets")
        except asyncio.CancelledError:
            break
        except Exception as e:
            LOG.error(f"Housekeeping error: {e}")

        await asyncio.sleep(3600)  # Every hour


async def _stats_broadcaster(services: DirewolfServices) -> None:
    """Periodically broadcast stats to WebSocket clients."""
    while True:
        try:
            await asyncio.sleep(10)
            if services.storage and services.ws_clients:
                db_stats = await services.storage.get_stats()
                db_stats.update(services.get_stats_dict())
                await broadcast_event("stats", db_stats, services.ws_clients)
        except asyncio.CancelledError:
            break
        except Exception as e:
            LOG.error(f"Stats broadcaster error: {e}")
