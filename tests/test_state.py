from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from finance_digest.state import advanced_cursors, read_cursors, write_cursors

SEOUL = ZoneInfo("Asia/Seoul")
T0 = datetime(2026, 9, 17, 8, 0, tzinfo=SEOUL)
T1 = datetime(2026, 9, 18, 8, 0, tzinfo=SEOUL)
T2 = datetime(2026, 9, 21, 8, 0, tzinfo=SEOUL)


def _collection(end: datetime, *chats: dict) -> dict:
    return {"window": {"end": end.isoformat()}, "chats": list(chats)}


def _chat(key: str, start: datetime, error: str | None = None) -> dict:
    chat = {"key": key, "start": start.isoformat(), "message_count": 0}
    if error:
        chat["error"] = error
    return chat


class CursorFileTest(unittest.TestCase):
    def test_missing_file_has_no_cursors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(read_cursors(Path(tmp) / "state.json"), {})

    def test_round_trip_and_no_temp_file_left(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data" / "state.json"

            write_cursors(path, {"-1001": T1, "news": T0})

            self.assertEqual(read_cursors(path), {"-1001": T1, "news": T0})
            self.assertEqual([p.name for p in path.parent.iterdir()], ["state.json"])

    def test_rejects_corrupt_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("{not json", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "Invalid collection state"):
                read_cursors(path)

    def test_rejects_naive_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(
                json.dumps({"collected_until": {"a": "2026-09-18T08:00:00"}}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "no UTC offset"):
                read_cursors(path)


class AdvancedCursorsTest(unittest.TestCase):
    def test_run_moves_collected_chats_to_window_end(self) -> None:
        updated = advanced_cursors(
            {"a": T0},
            _collection(T2, _chat("a", T0), _chat("b", T1)),
            reset=True,
        )

        self.assertEqual(updated, {"a": T2, "b": T2})

    def test_run_keeps_failed_chat_cursor(self) -> None:
        updated = advanced_cursors(
            {"a": T0},
            _collection(T2, _chat("a", T0, error="RPCError: boom")),
            reset=True,
        )

        self.assertEqual(updated, {"a": T0})

    def test_run_pins_new_failed_chat_to_its_start(self) -> None:
        updated = advanced_cursors(
            {},
            _collection(T2, _chat("b", T1, error="ValueError: unknown")),
            reset=True,
        )

        self.assertEqual(updated, {"b": T1})

    def test_resend_advances_only_chats_whose_gap_it_covers(self) -> None:
        updated = advanced_cursors(
            {"a": T1, "b": T0, "c": T2},
            _collection(T2, _chat("a", T1), _chat("b", T1), _chat("c", T1)),
            reset=False,
        )

        # a: report covers [T1, T2) from its cursor -> advance.
        # b: report starts after b's cursor, so [T0, T1) was never reported -> keep.
        # c: already past this report -> keep.
        self.assertEqual(updated, {"a": T2, "b": T0, "c": T2})

    def test_resend_never_creates_cursors(self) -> None:
        updated = advanced_cursors({}, _collection(T2, _chat("a", T1)), reset=False)

        self.assertEqual(updated, {})

    def test_skips_collections_without_per_chat_ranges(self) -> None:
        legacy = {"window": {"end": T2.isoformat()}, "chats": [{"name": "old"}]}

        self.assertEqual(advanced_cursors({"a": T1}, legacy, reset=True), {"a": T1})


if __name__ == "__main__":
    unittest.main()
