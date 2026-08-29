"""Direwolf Dashboard - Lightweight web-based live display of Direwolf activity."""

__version__ = "1.1.1"

from direwolf_dashboard.lifecycle import DirewolfServices, ServiceContainer, shutdown_services, startup_services
from direwolf_dashboard.routers import create_api_routes, create_index_handler, create_ws_handler

__all__ = [
    "DirewolfServices",
    "ServiceContainer",
    "startup_services",
    "shutdown_services",
    "create_api_routes",
    "create_ws_handler",
    "create_index_handler",
]
