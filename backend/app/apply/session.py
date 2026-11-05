from __future__ import annotations

import json
from pathlib import Path

from app.apply.paths import DATA_DIR

SESSION_DIR = DATA_DIR / "sessions"


def session_path(source: str) -> Path:
    return SESSION_DIR / f"{source}.json"


def has_session(source: str) -> bool:
    return session_path(source).exists()


def load_state(source: str) -> dict | None:
    path = session_path(source)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def save_state(context, source: str) -> Path:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    path = session_path(source)
    context.storage_state(path=str(path))
    return path
