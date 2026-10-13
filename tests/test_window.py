from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from finance_digest.window import compute_collection_window

SEOUL = ZoneInfo("Asia/Seoul")


def _window(**overrides):
    params = {
        "timezone_name": "Asia/Seoul",
        "recent_hours": 24,
        "monday_weekend": True,
        "chat_keys": ["a", "b"],
        "now": datetime(2026, 6, 9, 8, 0, tzinfo=SEOUL),
    }
    params.update(overrides)
    return compute_collection_window(**params)


class CollectionWindowTest(unittest.TestCase):
    def test_monday_includes_weekend(self) -> None:
        now = datetime(2026, 6, 8, 8, 0, tzinfo=SEOUL)

        window = _window(now=now)

        self.assertEqual(window.start, datetime(2026, 6, 5, 8, 0, tzinfo=SEOUL))
        self.assertEqual(window.end, now)

    def test_monday_extension_adds_weekend_to_recent_hours(self) -> None:
        now = datetime(2026, 6, 8, 8, 0, tzinfo=SEOUL)

        window = _window(recent_hours=12, now=now)

        self.assertEqual(window.start, now - timedelta(hours=60))

    def test_weekday_uses_recent_hours(self) -> None:
        window = _window()

        self.assertEqual(window.start, datetime(2026, 6, 8, 8, 0, tzinfo=SEOUL))
        self.assertEqual(window.chat_starts["a"], window.start)

    def test_cursor_extends_window_after_missed_runs(self) -> None:
        # Friday's run failed, so the cursor still points at Thursday 08:00.
        now = datetime(2026, 9, 21, 8, 0, tzinfo=SEOUL)
        cursor = datetime(2026, 9, 17, 23, 0, tzinfo=ZoneInfo("UTC"))

        window = _window(now=now, cursors={"a": cursor, "b": cursor})

        self.assertEqual(window.start, datetime(2026, 9, 18, 8, 0, tzinfo=SEOUL))
        self.assertEqual(window.label, "20260918_0800-20260921_0800")

    def test_chats_without_cursor_use_recent_hours(self) -> None:
        cursor = datetime(2026, 6, 7, 8, 0, tzinfo=SEOUL)

        window = _window(cursors={"a": cursor})

        self.assertEqual(window.chat_starts["a"], cursor)
        self.assertEqual(window.chat_starts["b"], datetime(2026, 6, 8, 8, 0, tzinfo=SEOUL))
        self.assertEqual(window.start, cursor)

    def test_since_overrides_cursors(self) -> None:
        since = datetime(2026, 6, 1, 9, 0)

        window = _window(since=since, cursors={"a": datetime(2026, 6, 8, 8, 0, tzinfo=SEOUL)})

        expected = datetime(2026, 6, 1, 9, 0, tzinfo=SEOUL)
        self.assertEqual(window.chat_starts, {"a": expected, "b": expected})

    def test_until_ignores_cursors_and_ends_in_the_past(self) -> None:
        until = datetime(2026, 9, 21, 8, 0, tzinfo=SEOUL)

        window = _window(
            now=datetime(2026, 10, 12, 14, 0, tzinfo=SEOUL),
            since=datetime(2026, 9, 18, 8, 0, tzinfo=SEOUL),
            until=until,
            cursors={"a": datetime(2026, 10, 12, 8, 0, tzinfo=SEOUL)},
        )

        self.assertEqual(window.end, until)
        self.assertEqual(window.chat_starts["a"], datetime(2026, 9, 18, 8, 0, tzinfo=SEOUL))

    def test_until_without_since_uses_recent_hours_before_until(self) -> None:
        until = datetime(2026, 9, 21, 8, 0, tzinfo=SEOUL)  # Monday

        window = _window(now=datetime(2026, 10, 12, 14, 0, tzinfo=SEOUL), until=until)

        self.assertEqual(window.start, datetime(2026, 9, 18, 8, 0, tzinfo=SEOUL))

    def test_rejects_future_until(self) -> None:
        with self.assertRaisesRegex(ValueError, "in the future"):
            _window(until=datetime(2026, 6, 10, 8, 0, tzinfo=SEOUL))

    def test_rejects_start_not_before_end(self) -> None:
        with self.assertRaisesRegex(ValueError, "not earlier"):
            _window(since=datetime(2026, 6, 9, 8, 0, tzinfo=SEOUL))

    def test_rejects_cursor_in_the_future(self) -> None:
        with self.assertRaisesRegex(ValueError, "'a'"):
            _window(cursors={"a": datetime(2026, 6, 9, 9, 0, tzinfo=SEOUL)})


if __name__ == "__main__":
    unittest.main()
