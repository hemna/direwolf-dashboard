"""Starlette server — thin wrapper using lifecycle and router modules."""

import logging
import os
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles

from direwolf_dashboard.config import Config
from direwolf_dashboard.lifecycle import (
    ServiceContainer,
    startup_services,
    shutdown_services,
)
from direwolf_dashboard.routers import (
    create_api_routes,
    create_ws_handler,
    create_index_handler,
)

LOG = logging.getLogger(__name__)


async def _json_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Return JSON instead of Starlette's default HTML error pages."""
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


def create_app(config: Config, config_path: str) -> Starlette:
    """Create and configure the Starlette application."""
    container = ServiceContainer()

    @asynccontextmanager
    async def lifespan(app: Starlette):
        container.services = await startup_services(config, config_path)
        yield
        await shutdown_services(container.services)

    static_dir = os.path.join(os.path.dirname(__file__), "static")

    routes = [
        Mount("/api", routes=create_api_routes(container)),
        WebSocketRoute("/ws", create_ws_handler(container)),
    ]
    if os.path.exists(static_dir):
        routes.append(Mount("/static", StaticFiles(directory=static_dir)))
    routes.append(Route("/", create_index_handler(static_dir)))

    app = Starlette(
        lifespan=lifespan,
        routes=routes,
        exception_handlers={HTTPException: _json_error_handler},
    )

    return app
