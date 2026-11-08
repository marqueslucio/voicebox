"""Route registration for the local dictation API."""

from fastapi import FastAPI


def register_routers(app: FastAPI) -> None:
    from . import captures, health, models, settings, tasks, transcription

    for module in (health, transcription, captures, models, settings, tasks):
        app.include_router(module.router)
