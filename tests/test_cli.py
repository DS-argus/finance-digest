from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from finance_digest import cli


class RunCommandTest(unittest.TestCase):
    def test_run_writes_collection_before_summarizer_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = SimpleNamespace(
                collection=SimpleNamespace(
                    output_dir=root / "collections",
                    timezone="Asia/Seoul",
                    recent_hours=24,
                    monday_weekend=True,
                ),
                summary=SimpleNamespace(output_dir=root / "reports"),
            )
            collection = {
                "window": {
                    "start": "2026-06-11T08:00:00+09:00",
                    "end": "2026-06-12T08:00:00+09:00",
                    "timezone": "Asia/Seoul",
                    "label": "20260611_0800-20260612_0800",
                },
                "total_messages": 1,
                "chats": [{"name": "test", "message_count": 1, "messages": []}],
            }

            async def collect_messages(_config, _window):
                return collection

            with (
                mock.patch.object(cli, "load_config", return_value=config),
                mock.patch.object(cli, "_window", return_value=object()),
                mock.patch.object(cli, "collect_messages", side_effect=collect_messages),
                mock.patch.object(
                    cli,
                    "summarize_collection",
                    side_effect=RuntimeError("summarizer failed"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "summarizer failed"):
                    cli.cmd_run(argparse.Namespace(config="config.yml", dry_run=False))

            self.assertTrue(
                (root / "collections" / "20260611_0800-20260612_0800.json").exists()
            )
            self.assertTrue(
                (root / "collections" / "20260611_0800-20260612_0800.md").exists()
            )


if __name__ == "__main__":
    unittest.main()
