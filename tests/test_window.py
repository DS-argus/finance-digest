from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from finance_digest.window import compute_collection_window


class CollectionWindowTest(unittest.TestCase):
    def test_monday_includes_weekend(self) -> None:
        now = datetime(2026, 6, 8, 8, 0, tzinfo=ZoneInfo("Asia/Seoul"))

        window = compute_collection_window(
            timezone_name="Asia/Seoul",
            recent_hours=24,
            monday_weekend=True,
            now=now,
        )

        self.assertEqual(window.start, datetime(2026, 6, 5, 8, 0, tzinfo=ZoneInfo("Asia/Seoul")))
        self.assertEqual(window.end, now)

    def test_weekday_uses_recent_hours(self) -> None:
        now = datetime(2026, 6, 9, 8, 0, tzinfo=ZoneInfo("Asia/Seoul"))

        window = compute_collection_window(
            timezone_name="Asia/Seoul",
            recent_hours=24,
            monday_weekend=True,
            now=now,
        )

        self.assertEqual(window.start, datetime(2026, 6, 8, 8, 0, tzinfo=ZoneInfo("Asia/Seoul")))
        self.assertEqual(window.end, now)


if __name__ == "__main__":
    unittest.main()
