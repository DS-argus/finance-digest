from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class CollectionWindow:
    start: datetime
    end: datetime
    timezone: str
    label: str


def compute_collection_window(
    *,
    timezone_name: str,
    recent_hours: int,
    monday_weekend: bool,
    now: datetime | None = None,
) -> CollectionWindow:
    tz = ZoneInfo(timezone_name)
    local_now = datetime.now(tz) if now is None else _as_timezone(now, tz)

    hours = recent_hours
    if monday_weekend and local_now.weekday() == 0:
        hours = 72

    start = local_now - timedelta(hours=hours)
    label = f"{start:%Y%m%d_%H%M}-{local_now:%Y%m%d_%H%M}"
    return CollectionWindow(
        start=start,
        end=local_now,
        timezone=timezone_name,
        label=label,
    )


def _as_timezone(value: datetime, tz: ZoneInfo) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=tz)
    return value.astimezone(tz)
