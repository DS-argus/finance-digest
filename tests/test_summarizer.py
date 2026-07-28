from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from finance_digest.summarizer import (
    NO_MESSAGES_REPORT,
    _resolve_command,
    _run_summarizer,
    summarize_collection,
)


class SummarizerTest(unittest.TestCase):
    def test_empty_collection_does_not_need_codex_config(self) -> None:
        report = summarize_collection(
            config=None,  # type: ignore[arg-type]
            collection={"total_messages": 0},
        )

        self.assertEqual(report, NO_MESSAGES_REPORT)

    def test_claude_provider_returns_claude_result_without_codex(self) -> None:
        config = mock.Mock()
        config.summary.provider = "claude"

        with (
            mock.patch(
                "finance_digest.summarizer._summarize_with_claude",
                return_value="claude report",
            ) as claude,
            mock.patch("finance_digest.summarizer._summarize_with_codex") as codex,
        ):
            report = summarize_collection(config, {"total_messages": 1})

        self.assertEqual(report, "claude report")
        claude.assert_called_once()
        codex.assert_not_called()

    def test_claude_provider_falls_back_to_codex_on_claude_failure(self) -> None:
        config = mock.Mock()
        config.summary.provider = "claude"

        with (
            mock.patch(
                "finance_digest.summarizer._summarize_with_claude",
                side_effect=RuntimeError("claude failed"),
            ) as claude,
            mock.patch(
                "finance_digest.summarizer._summarize_with_codex",
                return_value="codex report",
            ) as codex,
        ):
            report = summarize_collection(config, {"total_messages": 1})

        self.assertEqual(report, "codex report")
        claude.assert_called_once()
        codex.assert_called_once()

    def test_claude_provider_reports_both_failures_when_fallback_fails(self) -> None:
        config = mock.Mock()
        config.summary.provider = "claude"

        with (
            mock.patch(
                "finance_digest.summarizer._summarize_with_claude",
                side_effect=RuntimeError("claude failed"),
            ),
            mock.patch(
                "finance_digest.summarizer._summarize_with_codex",
                side_effect=RuntimeError("codex failed"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "claude failed.*codex failed"):
                summarize_collection(config, {"total_messages": 1})

    def test_resolve_command_uses_common_paths_when_path_is_minimal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            executable_dir = home / ".local" / "bin"
            executable_dir.mkdir(parents=True)
            executable = executable_dir / "finance-digest-test-summarizer"
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(0o755)

            with (
                mock.patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}, clear=False),
                mock.patch.object(Path, "home", return_value=home),
            ):
                self.assertEqual(
                    _resolve_command("finance-digest-test-summarizer", "claude"),
                    str(executable),
                )

    def test_missing_command_error_names_provider_config_key(self) -> None:
        with (
            mock.patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}, clear=False),
            mock.patch.object(Path, "home", return_value=Path("/definitely/missing")),
        ):
            with self.assertRaisesRegex(RuntimeError, "summary\\.claude\\.command"):
                _resolve_command("definitely-missing-command", "claude")

    def test_summarizer_failure_uses_stdout_when_stderr_is_empty(self) -> None:
        config = mock.Mock()
        config.base_dir = Path.cwd()
        completed = mock.Mock(returncode=1, stderr="", stdout="Not logged in")

        with (
            mock.patch(
                "finance_digest.summarizer._resolve_command",
                return_value="/bin/fake",
            ),
            mock.patch("finance_digest.summarizer.subprocess.run", return_value=completed),
        ):
            with self.assertRaisesRegex(RuntimeError, "Not logged in"):
                _run_summarizer(["fake"], "input", config, provider="claude")


if __name__ == "__main__":
    unittest.main()
