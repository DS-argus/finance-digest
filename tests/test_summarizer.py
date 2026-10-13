from __future__ import annotations

import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from finance_digest.summarizer import (
    NO_MESSAGES_REPORT,
    _resolve_command,
    _run_summarizer,
    append_failed_chats_notice,
    claude_command,
    codex_command,
    error_detail,
    format_collection_markdown,
    summarize_collection,
)


def _summary_config(**claude_overrides) -> SimpleNamespace:
    claude = {"command": "claude", "model": None}
    claude.update(claude_overrides)
    return SimpleNamespace(
        summary=SimpleNamespace(
            prompt="PROMPT",
            timeout_seconds=600,
            claude=SimpleNamespace(**claude),
            codex=SimpleNamespace(command="codex", model="gpt-5.5"),
        )
    )


def _script(directory: Path, body: str) -> str:
    path = directory / "fake-cli"
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


class SummarizerTest(unittest.TestCase):
    def test_empty_collection_does_not_need_summarizer_config(self) -> None:
        report = summarize_collection(
            config=None,  # type: ignore[arg-type]
            collection={"total_messages": 0},
        )

        self.assertEqual(report, NO_MESSAGES_REPORT)

    def test_no_messages_report_uses_telegram_safe_format(self) -> None:
        self.assertNotRegex(NO_MESSAGES_REPORT, r"(?m)^(#|---)")

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


class CommandIsolationTest(unittest.TestCase):
    def test_claude_runs_without_tools_or_mcp(self) -> None:
        cmd = claude_command(_summary_config())

        self.assertEqual(cmd[cmd.index("--tools") + 1], "")
        self.assertIn("--strict-mcp-config", cmd)
        self.assertIn("--no-session-persistence", cmd)
        self.assertNotIn("--model", cmd)

    def test_claude_model_is_passed_when_set(self) -> None:
        cmd = claude_command(_summary_config(model="opus"))

        self.assertEqual(cmd[cmd.index("--model") + 1], "opus")

    def test_codex_runs_in_given_workdir_without_user_config_or_tools(self) -> None:
        workdir = Path("/tmp/empty-workdir")

        cmd = codex_command(_summary_config(), workdir)

        self.assertEqual(cmd[cmd.index("--cd") + 1], str(workdir))
        self.assertEqual(cmd[cmd.index("--sandbox") + 1], "read-only")
        self.assertIn("--ignore-user-config", cmd)
        self.assertIn("features.shell_tool=false", cmd)
        self.assertIn("features.unified_exec=false", cmd)
        self.assertIn('web_search="disabled"', cmd)
        self.assertEqual(cmd[-1], "PROMPT")


class RunSummarizerTest(unittest.TestCase):
    def test_returns_stdout_and_runs_in_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            cmd = _script(workdir, 'pwd; cat\n')

            report = _run_summarizer(
                [cmd], "hello", cwd=workdir, timeout=10, provider="claude"
            )

        self.assertEqual(
            report.splitlines(), [str(workdir.resolve()), "hello"]
        )

    def test_timeout_kills_the_process_group(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            # The child holds stdout open; without a group kill communicate() would hang.
            cmd = _script(workdir, "sleep 30 & sleep 30\n")
            started = time.monotonic()

            with self.assertRaisesRegex(RuntimeError, "claude summarizer timed out"):
                _run_summarizer([cmd], "", cwd=workdir, timeout=0.5, provider="claude")

        self.assertLess(time.monotonic() - started, 10)

    def test_timeout_does_not_wait_for_descendants_outside_the_group(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            # A grandchild that starts its own session survives the group kill and
            # keeps stdout open; reading the pipes after the kill would block on it.
            escape = f"{sys.executable} -c 'import os, time; os.setsid(); time.sleep(8)'"
            cmd = _script(workdir, f"{escape} &\nsleep 30\n")
            started = time.monotonic()

            with self.assertRaisesRegex(RuntimeError, "timed out"):
                _run_summarizer([cmd], "", cwd=workdir, timeout=0.5, provider="claude")

        self.assertLess(time.monotonic() - started, 5)
    def test_failure_uses_stdout_when_stderr_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            cmd = _script(workdir, "echo 'Not logged in'; exit 1\n")

            with self.assertRaisesRegex(RuntimeError, "code 1: Not logged in"):
                _run_summarizer([cmd], "", cwd=workdir, timeout=10, provider="claude")

    def test_empty_report_is_a_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            cmd = _script(workdir, "exit 0\n")

            with self.assertRaisesRegex(RuntimeError, "empty report"):
                _run_summarizer([cmd], "", cwd=workdir, timeout=10, provider="codex")

    def test_unstartable_command_is_a_summarizer_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            not_executable = workdir / "not-executable"
            not_executable.write_text("", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "claude summarizer could not start"):
                _run_summarizer(
                    [str(not_executable)], "", cwd=workdir, timeout=10, provider="claude"
                )


class ErrorDetailTest(unittest.TestCase):
    def test_drops_echoed_collection_before_stdin_end(self) -> None:
        stderr = (
            "OpenAI Codex\n<stdin>\nsecret channel message\n</stdin>\n"
            "hook: SessionStart\nERROR: You've hit your usage limit."
        )

        detail = error_detail(stderr, "")

        self.assertNotIn("secret channel message", detail)
        self.assertTrue(detail.endswith("ERROR: You've hit your usage limit."))

    def test_keeps_only_the_tail(self) -> None:
        stderr = "\n".join(f"line {index}" for index in range(100))

        detail = error_detail(stderr, "")

        self.assertEqual(detail.splitlines(), [f"line {index}" for index in range(90, 100)])
        self.assertLessEqual(len(error_detail("x" * 5000, "")), 1500)


class FailedChatsTest(unittest.TestCase):
    COLLECTION = {
        "window": {"start": "s", "end": "e", "timezone": "Asia/Seoul"},
        "total_messages": 1,
        "chats": [
            {"name": "ok", "message_count": 1, "messages": [{"id": 1, "text": "hi"}]},
            {"name": "broken", "message_count": 0, "messages": [], "error": "RPCError: x\nmore"},
        ],
    }

    def test_markdown_marks_failed_chat(self) -> None:
        markdown = format_collection_markdown(self.COLLECTION)

        self.assertIn("## broken (collection failed)", markdown)
        self.assertNotIn("RPCError", markdown)

    def test_notice_lists_failed_chats(self) -> None:
        report = append_failed_chats_notice("report", self.COLLECTION, retry_scheduled=True)

        self.assertIn("- broken: RPCError: x\n", report)
        self.assertIn("다음 실행", report)

    def test_notice_without_retry_promise(self) -> None:
        report = append_failed_chats_notice("report", self.COLLECTION, retry_scheduled=False)

        self.assertNotIn("다음 실행", report)

    def test_no_notice_when_all_chats_succeeded(self) -> None:
        collection = {"chats": [{"name": "ok"}]}

        self.assertEqual(
            append_failed_chats_notice("report", collection, retry_scheduled=True), "report"
        )


if __name__ == "__main__":
    unittest.main()
