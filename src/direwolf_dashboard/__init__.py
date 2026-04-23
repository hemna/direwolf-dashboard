"""Direwolf Dashboard - Lightweight web-based live display of Direwolf activity."""

__version__ = "1.0.2"

from direwolf_dashboard.lifecycle import DirewolfServices, ServiceContainer, startup_services, shutdown_services
from direwolf_dashboard.routers import create_api_router, create_ws_router, create_index_router

__all__ = [
    "DirewolfServices",
    "ServiceContainer",
    "startup_services",
    "shutdown_services",
    "create_api_router",
    "create_ws_router",
    "create_index_router",
]
