"""FastAPI server — thin wrapper using lifecycle and router modules."""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from direwolf_dashboard.config import Config
from direwolf_dashboard.lifecycle import (
    ServiceContainer,
    startup_services,
    shutdown_services,
)
from direwolf_dashboard.routers import (
    create_api_router,
    create_ws_router,
    create_index_router,
)

LOG = logging.getLogger(__name__)


def create_app(config: Config, config_path: str) -> FastAPI:
    """Create and configure the FastAPI application."""
    container = ServiceContainer()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        container.services = await startup_services(config, config_path)
        yield
        await shutdown_services(container.services)

    app = FastAPI(title="Direwolf Dashboard", lifespan=lifespan)

    # Mount API routes at /api prefix (routes are defined without prefix in routers.py)
    app.include_router(create_api_router(container), prefix="/api")
    app.include_router(create_ws_router(container, path="/ws"))

    # Static files and index route
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.exists(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    app.include_router(create_index_router(static_dir))

    return app
