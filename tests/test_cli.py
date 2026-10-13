from __future__ import annotations

import argparse
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from finance_digest import cli
from finance_digest.config import ChatConfig
from finance_digest.state import read_cursors, write_cursors

SEOUL = ZoneInfo("Asia/Seoul")
START = datetime(2026, 9, 18, 8, 0, tzinfo=SEOUL)
END = datetime(2026, 9, 21, 8, 0, tzinfo=SEOUL)
LABEL = "20260918_0800-20260921_0800"


def _config(root: Path, *, send_enabled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        chats=[ChatConfig(name="A", identifier="a")],
        enabled_chats=[ChatConfig(name="A", identifier="a")],
        collection=SimpleNamespace(
            output_dir=root / "collections",
            state_file=root / "state.json",
            timezone="Asia/Seoul",
            recent_hours=24,
            monday_weekend=True,
        ),
        summary=SimpleNamespace(output_dir=root / "reports"),
        send=SimpleNamespace(telegram=SimpleNamespace(enabled=send_enabled)),
    )


def _collection(*, error: str | None = None) -> dict:
    chat = {
        "name": "A",
        "key": "a",
        "start": START.isoformat(),
        "message_count": 0 if error else 1,
        "messages": [],
    }
    if error:
        chat["error"] = error
    return {
        "window": {
            "start": START.isoformat(),
            "end": END.isoformat(),
            "timezone": "Asia/Seoul",
            "label": LABEL,
        },
        "total_messages": chat["message_count"],
        "chats": [chat],
    }


def _run_args(**overrides) -> argparse.Namespace:
    values = {"config": "config.yml", "dry_run": False, "since": None, "until": None}
    values.update(overrides)
    return argparse.Namespace(**values)


class RunCommandTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _patches(self, config, collection, *, summarize=None, send=None):
        async def collect_messages(_config, _window):
            return collection

        return [
            mock.patch.object(cli, "load_config", return_value=config),
            mock.patch.object(cli, "collect_messages", side_effect=collect_messages),
            mock.patch.object(
                cli, "summarize_collection", **(summarize or {"return_value": "report"})
            ),
            mock.patch.object(
                cli, "send_telegram_report", **(send or {"return_value": 1})
            ),
            mock.patch.object(cli, "send_failure_alert"),
        ]

    def _start(self, patches):
        mocks = [patcher.start() for patcher in patches]
        for patcher in patches:
            self.addCleanup(patcher.stop)
        return mocks

    def test_successful_run_advances_cursor(self) -> None:
        self._start(self._patches(_config(self.root), _collection()))

        self.assertEqual(cli.cmd_run(_run_args()), 0)

        self.assertEqual(read_cursors(self.root / "state.json"), {"a": END})
        self.assertTrue((self.root / "reports" / f"{LABEL}.md").exists())

    def test_summarizer_failure_keeps_collection_and_cursor_and_alerts(self) -> None:
        write_cursors(self.root / "state.json", {"a": START})
        *_, alert = self._start(
            self._patches(
                _config(self.root),
                _collection(),
                summarize={"side_effect": RuntimeError("summarizer failed")},
            )
        )

        with self.assertRaisesRegex(RuntimeError, "summarizer failed"):
            cli.cmd_run(_run_args())

        self.assertTrue((self.root / "collections" / f"{LABEL}.json").exists())
        self.assertTrue((self.root / "collections" / f"{LABEL}.md").exists())
        self.assertEqual(read_cursors(self.root / "state.json"), {"a": START})
        alert.assert_called_once()
        self.assertIn("summarizer failed", alert.call_args.args[1])

    def test_send_failure_keeps_cursor(self) -> None:
        self._start(
            self._patches(
                _config(self.root),
                _collection(),
                send={"side_effect": RuntimeError("Telegram down")},
            )
        )

        with self.assertRaisesRegex(RuntimeError, "Telegram down"):
            cli.cmd_run(_run_args())

        self.assertEqual(read_cursors(self.root / "state.json"), {})
        self.assertTrue((self.root / "reports" / f"{LABEL}.md").exists())

    def test_alert_failure_does_not_mask_original_error(self) -> None:
        *_, alert = self._start(
            self._patches(
                _config(self.root),
                _collection(),
                summarize={"side_effect": RuntimeError("summarizer failed")},
            )
        )
        alert.side_effect = RuntimeError("bot unreachable")

        with self.assertRaisesRegex(RuntimeError, "summarizer failed"):
            cli.cmd_run(_run_args())

    def test_no_alert_when_sending_disabled(self) -> None:
        *_, alert = self._start(
            self._patches(
                _config(self.root, send_enabled=False),
                _collection(),
                summarize={"side_effect": RuntimeError("summarizer failed")},
            )
        )

        with self.assertRaises(RuntimeError):
            cli.cmd_run(_run_args())

        alert.assert_not_called()

    def test_failed_chat_is_reported_and_keeps_its_start(self) -> None:
        _, _, _, send, _ = self._start(
            self._patches(_config(self.root), _collection(error="ChannelPrivateError: x"))
        )

        cli.cmd_run(_run_args())

        self.assertIn("**수집 실패 채널**", send.call_args.args[1])
        self.assertEqual(read_cursors(self.root / "state.json"), {"a": START})

    def test_backfill_with_until_does_not_reset_cursor(self) -> None:
        later = datetime(2026, 10, 12, 8, 0, tzinfo=SEOUL)
        write_cursors(self.root / "state.json", {"a": later})
        self._start(self._patches(_config(self.root), _collection()))

        cli.cmd_run(_run_args(since=START, until=END))

        self.assertEqual(read_cursors(self.root / "state.json"), {"a": later})

    def test_dry_run_writes_nothing(self) -> None:
        _, _, _, send, _ = self._start(self._patches(_config(self.root), _collection()))

        cli.cmd_run(_run_args(dry_run=True))

        send.assert_not_called()
        self.assertFalse((self.root / "collections").exists())
        self.assertFalse((self.root / "state.json").exists())


class SendCommandTest(unittest.TestCase):
    def test_resend_closes_the_gap_it_covers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _config(root)
            config.collection.output_dir.mkdir()
            config.summary.output_dir.mkdir()
            (config.summary.output_dir / f"{LABEL}.md").write_text("report", encoding="utf-8")
            cli._write_collection(config, _collection())
            write_cursors(config.collection.state_file, {"a": START})

            with (
                mock.patch.object(cli, "load_config", return_value=config),
                mock.patch.object(cli, "send_telegram_report", return_value=1) as send,
            ):
                cli.cmd_send(argparse.Namespace(config="config.yml", input=None))

            send.assert_called_once_with(config, "report")
            self.assertEqual(read_cursors(config.collection.state_file), {"a": END})


class LatestFileTest(unittest.TestCase):
    def test_picks_most_recently_written_not_label_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            daily = root / "20261012_0800-20261013_0800.md"
            backfill = root / "20260901_0000-20261013_1200.md"
            daily.write_text("daily", encoding="utf-8")
            backfill.write_text("backfill", encoding="utf-8")
            os.utime(daily, (1_000, 1_000))
            os.utime(backfill, (2_000, 2_000))

            self.assertEqual(cli._latest(root, "*.md"), backfill)

    def test_missing_files_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "No \\*.md files"):
                cli._latest(Path(tmp), "*.md")


class ParserTest(unittest.TestCase):
    def test_run_accepts_since_and_until(self) -> None:
        args = cli._build_parser().parse_args(
            ["run", "--since", "2026-09-18T08:00", "--until", "2026-09-21T08:00+09:00"]
        )

        self.assertEqual(args.since, datetime(2026, 9, 18, 8, 0))
        self.assertEqual(args.until, END)

    def test_rejects_invalid_since(self) -> None:
        with mock.patch("sys.stderr"), self.assertRaises(SystemExit):
            cli._build_parser().parse_args(["run", "--since", "friday"])


if __name__ == "__main__":
    unittest.main()
