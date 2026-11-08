"""Add capture settings columns to older databases without changing stored data."""

import json
import logging

from sqlalchemy import inspect, text

from ..utils.capture_chords import default_push_to_talk_chord, default_toggle_to_talk_chord

logger = logging.getLogger(__name__)


def run_migrations(engine) -> None:
    inspector = inspect(engine)
    _migrate_capture_settings(engine, inspector, set(inspector.get_table_names()))


def _get_columns(inspector, table: str) -> set[str]:
    return {col["name"] for col in inspector.get_columns(table)}


def _add_column(engine, table: str, column_sql: str, label: str) -> None:
    with engine.connect() as conn:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column_sql}"))
        conn.commit()
    logger.info("Added %s column to %s", label, table)


def _migrate_capture_settings(engine, inspector, tables: set[str]) -> None:
    if "capture_settings" not in tables:
        return
    columns = _get_columns(inspector, "capture_settings")
    push_default = json.dumps(default_push_to_talk_chord())
    toggle_default = json.dumps(default_toggle_to_talk_chord())
    if "allow_auto_paste" not in columns:
        _add_column(
            engine,
            "capture_settings",
            "allow_auto_paste BOOLEAN NOT NULL DEFAULT 1",
            "allow_auto_paste",
        )
    if "default_playback_voice_id" not in columns:
        _add_column(
            engine,
            "capture_settings",
            "default_playback_voice_id VARCHAR",
            "default_playback_voice_id",
        )
    if "chord_push_to_talk_keys" not in columns:
        _add_column(
            engine,
            "capture_settings",
            f"chord_push_to_talk_keys TEXT NOT NULL DEFAULT '{push_default}'",
            "chord_push_to_talk_keys",
        )
    if "chord_toggle_to_talk_keys" not in columns:
        _add_column(
            engine,
            "capture_settings",
            f"chord_toggle_to_talk_keys TEXT NOT NULL DEFAULT '{toggle_default}'",
            "chord_toggle_to_talk_keys",
        )
    if "hotkey_enabled" not in columns:
        _add_column(
            engine,
            "capture_settings",
            "hotkey_enabled BOOLEAN NOT NULL DEFAULT 0",
            "hotkey_enabled",
        )
