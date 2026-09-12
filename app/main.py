"""FastAPI application factory."""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import hue_client
from app.alerts import AlertEngine
from app.config import ConfigError, Settings, load_settings
from app.routers import admin, api
from app.storage import Store

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DESCRIPTION = """
Raise **Amber** or **Red** alerts on selected Philips Hue lights.

Authenticate with `Authorization: Bearer <token>` (or `X-API-Key: <token>`).
Tokens are generated in the [admin page](/admin).

| Level | Colour |
|-------|--------|
| `amber` | #FFBF00 |
| `red`   | #FF0000 |

| Mode | Behaviour |
|------|-----------|
| `glow` | brightness pulses between the configured min and max |
| `dead` | static colour at max brightness, no change until cleared |
"""


def create_app(settings: Settings | None = None, store: Store | None = None,
               client_factory=None, discover_bridges=None, pair_bridge=None) -> FastAPI:
    settings = settings or load_settings()
    store = store or Store(settings.config_path)
    client_factory = client_factory or hue_client.default_client_factory
    engine = AlertEngine(store, client_factory)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.getLogger("app").info("Hue alert service started; data file: %s", store.path)
        yield
        await engine.shutdown()

    app = FastAPI(
        title="Hue Alert Lights",
        version="1.0.0",
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )
    app.state.settings = settings
    app.state.store = store
    app.state.engine = engine
    app.state.client_factory = client_factory
    app.state.discover_bridges = discover_bridges or hue_client.discover_bridges
    app.state.pair_bridge = pair_bridge or hue_client.pair_bridge
    app.state.templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

    app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
    app.include_router(api.router)
    app.include_router(admin.router)

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/admin")

    return app


def app_factory() -> FastAPI:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    try:
        return create_app()
    except ConfigError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        sys.exit(1)
