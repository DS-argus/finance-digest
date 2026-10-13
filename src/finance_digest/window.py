from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

WEEKEND_EXTRA_HOURS = 48


@dataclass(frozen=True)
class CollectionWindow:
    start: datetime
    end: datetime
    timezone: str
    label: str
    chat_starts: dict[str, datetime]


def compute_collection_window(
    *,
    timezone_name: str,
    recent_hours: int,
    monday_weekend: bool,
    chat_keys: Iterable[str] = (),
    cursors: Mapping[str, datetime] | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    now: datetime | None = None,
) -> CollectionWindow:
    """Plan the [start, end) collection window for every chat.

    Each chat starts at `since` when given. Otherwise, unless `until` pins a past
    window, it resumes from its saved cursor. Chats without either fall back to the
    last `recent_hours` before the end, extended over the weekend on Mondays.
    Naive datetimes are interpreted in `timezone_name`.
    """
    tz = ZoneInfo(timezone_name)
    local_now = datetime.now(tz) if now is None else _as_timezone(now, tz)
    end = local_now if until is None else _as_timezone(until, tz)
    if end > local_now:
        raise ValueError(f"Collection end {end.isoformat()} is in the future")

    hours = recent_hours
    if monday_weekend and end.weekday() == 0:
        hours += WEEKEND_EXTRA_HOURS
    fallback = end - timedelta(hours=hours)
    explicit = None if since is None else _as_timezone(since, tz)
    saved = cursors if since is None and until is None and cursors else {}

    chat_starts: dict[str, datetime] = {}
    for key in chat_keys:
        if explicit is not None:
            start = explicit
        elif key in saved:
            start = _as_timezone(saved[key], tz)
        else:
            start = fallback
        if start >= end:
            raise ValueError(
                f"Collection start {start.isoformat()} for chat {key!r} is not "
                f"earlier than the end {end.isoformat()}"
            )
        chat_starts[key] = start

    start = min(
        chat_starts.values(),
        default=explicit if explicit is not None else fallback,
    )
    if start >= end:
        raise ValueError(
            f"Collection start {start.isoformat()} is not earlier than the end "
            f"{end.isoformat()}"
        )
    label = f"{start:%Y%m%d_%H%M}-{end:%Y%m%d_%H%M}"
    return CollectionWindow(
        start=start,
        end=end,
        timezone=timezone_name,
        label=label,
        chat_starts=chat_starts,
    )


def _as_timezone(value: datetime, tz: ZoneInfo) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=tz)
    return value.astimezone(tz)
