from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.errors import register_provider_error_handlers
from app.api.http import router as assist_router
from app.api.miniapp import router as miniapp_router
from app.api.telegram import router as telegram_router
from app.container import build_container
from app.settings import AppSettings


MINIAPP_MOUNT_PATH = "/miniapp"


def mount_miniapp_static(app: FastAPI, static_dir: str | Path) -> bool:
    """Serve the built Mini App frontend at /miniapp when the build directory exists.

    Must be called after API routers are included so API routes keep precedence.
    """
    directory = Path(static_dir)
    if not directory.is_dir():
        return False
    app.mount(MINIAPP_MOUNT_PATH, StaticFiles(directory=directory, html=True), name="miniapp")
    return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = AppSettings()
    container = build_container(settings)
    app.state.settings = settings
    app.state.container = container
    try:
        yield
    finally:
        await container.aclose()


app = FastAPI(title="Свои Правила API", version="0.6.0", lifespan=lifespan)
register_provider_error_handlers(app)
app.include_router(assist_router)
app.include_router(miniapp_router)
app.include_router(telegram_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": "0.6.0"}


def resolve_miniapp_static_dir() -> str:
    # Read only this value at import time; full AppSettings validation stays in lifespan.
    default = AppSettings.model_fields["miniapp_static_dir"].default
    return os.environ.get("MINIAPP_STATIC_DIR", default)


mount_miniapp_static(app, resolve_miniapp_static_dir())
