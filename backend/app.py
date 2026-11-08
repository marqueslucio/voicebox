"""FastAPI application and local dictation lifecycle."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__, database
from .routes import register_routers
from .services import retention, transcribe
from .utils.progress import get_progress_manager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    get_progress_manager()._set_main_loop(asyncio.get_running_loop())
    await asyncio.to_thread(retention.run_sweep)
    stop = asyncio.Event()
    task = asyncio.create_task(retention.periodic_sweep(stop))
    logger.info("Voicebox v%s ready", __version__)
    try:
        yield
    finally:
        stop.set()
        await task
        transcribe.unload_whisper_model()


def _configure_cors(application: FastAPI) -> None:
    """Set up CORS middleware with local-first defaults."""
    default_origins = [
        "http://localhost:5173",  # Vite dev server
        "http://127.0.0.1:5173",
        "http://localhost:17493",
        "http://127.0.0.1:17493",
        "http://localhost:17494",
        "http://127.0.0.1:17494",
        "http://localhost:17495",
        "http://127.0.0.1:17495",
        "tauri://localhost",  # Tauri webview (macOS)
        "https://tauri.localhost",  # Tauri webview (Windows/Linux)
        "http://tauri.localhost",  # Tauri webview (Windows, some builds)
    ]
    env_origins = os.environ.get("VOICEBOX_CORS_ORIGINS", "")
    all_origins = default_origins + [o.strip() for o in env_origins.split(",") if o.strip()]

    application.add_middleware(
        CORSMiddleware,
        allow_origins=all_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def create_app() -> FastAPI:
    application = FastAPI(title="Voicebox dictation API", version=__version__, lifespan=lifespan)
    _configure_cors(application)
    register_routers(application)
    return application


app = create_app()
