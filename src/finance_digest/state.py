from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

_CURSORS_KEY = "collected_until"


def read_cursors(path: Path) -> dict[str, datetime]:
    """Return, per chat key, the end of the last collected range that was reported."""
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        cursors = {
            str(key): datetime.fromisoformat(value)
            for key, value in raw[_CURSORS_KEY].items()
        }
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise RuntimeError(f"Invalid collection state file {path}: {exc}") from exc
    for key, value in cursors.items():
        if value.tzinfo is None:
            raise RuntimeError(
                f"Invalid collection state file {path}: cursor for {key!r} has no UTC offset"
            )
    return cursors


def write_cursors(path: Path, cursors: dict[str, datetime]) -> None:
    for key, value in cursors.items():
        if value.tzinfo is None:
            raise ValueError(f"Cursor for {key!r} must be timezone-aware")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {_CURSORS_KEY: {key: cursors[key].isoformat() for key in sorted(cursors)}}
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp_path, path)


def advanced_cursors(
    cursors: dict[str, datetime],
    collection: dict[str, Any],
    *,
    reset: bool,
) -> dict[str, datetime]:
    """Return the cursors after `collection` has been reported.

    reset=True is for a `run` that collected up to now: every collected chat moves
    to the window end, and a failed chat keeps its cursor (or gets its start) so the
    next run retries the missed range. reset=False is for a backfill window or a
    re-sent report: a chat only advances when its collected range covers its
    existing cursor, so an unrelated or older report never skips messages.
    """
    end = datetime.fromisoformat(collection["window"]["end"])
    updated = dict(cursors)
    for chat in collection.get("chats", []):
        if "key" not in chat or "start" not in chat:
            # Written before per-chat cursors existed; it cannot prove coverage.
            continue
        key = str(chat["key"])
        start = datetime.fromisoformat(chat["start"])
        if chat.get("error"):
            if reset:
                updated.setdefault(key, start)
            continue
        current = updated.get(key)
        if reset or (current is not None and start <= current < end):
            updated[key] = end
    return updated
