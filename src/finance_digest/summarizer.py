from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from finance_digest.config import AppConfig

NO_MESSAGES_REPORT = """**Daily Finance Digest**

수집된 메시지가 없습니다.

투자 조언이 아닙니다. 원문과 출처를 확인하세요.
"""

# Codex features that give the model tools to run commands, read files, browse,
# or call external apps. They are switched off with `-c features.<name>=false`
# because, unlike `--disable`, an unknown key there only warns: a Codex release
# that drops one of these names must not break the fallback summarizer.
CODEX_DISABLED_FEATURES = (
    "shell_tool",
    "unified_exec",
    "apps",
    "plugins",
    "hooks",
    "browser_use",
    "computer_use",
    "image_generation",
    "view_image",
    "memories",
    "skill_search",
    "goals",
    "tool_suggest",
)

_ERROR_TAIL_LINES = 10
_ERROR_TAIL_CHARS = 1500
# Codex echoes the prompt and the piped collection on stderr before its own output.
_CODEX_STDIN_END = "</stdin>"


def summarize_collection(config: AppConfig, collection: dict[str, Any]) -> str:
    if int(collection.get("total_messages") or 0) == 0:
        return NO_MESSAGES_REPORT

    if config.summary.provider == "claude":
        try:
            return _summarize_with_claude(config, collection)
        except RuntimeError as claude_error:
            try:
                return _summarize_with_codex(config, collection)
            except RuntimeError as codex_error:
                raise RuntimeError(
                    "claude summarizer failed and codex fallback also failed: "
                    f"claude: {claude_error}; codex: {codex_error}"
                ) from codex_error
    if config.summary.provider == "codex":
        return _summarize_with_codex(config, collection)
    raise RuntimeError(f"Unsupported summary provider: {config.summary.provider}")


def claude_command(config: AppConfig) -> list[str]:
    claude = config.summary.claude
    cmd = [
        claude.command,
        "-p",
        "--output-format",
        "text",
        "--input-format",
        "text",
        "--no-session-persistence",
        # No built-in tools and no MCP servers: the model can only read stdin.
        "--tools",
        "",
        "--strict-mcp-config",
    ]
    if claude.model:
        cmd.extend(["--model", claude.model])
    return cmd


def codex_command(config: AppConfig, workdir: Path) -> list[str]:
    codex = config.summary.codex
    cmd = [
        codex.command,
        "exec",
        "--color",
        "never",
        "--skip-git-repo-check",
        "--ephemeral",
        "--sandbox",
        "read-only",
        # Drops user MCP servers, hooks and rules; auth still comes from CODEX_HOME.
        "--ignore-user-config",
        "--ignore-rules",
        "--cd",
        str(workdir),
        "-c",
        'web_search="disabled"',
    ]
    for feature in CODEX_DISABLED_FEATURES:
        cmd.extend(["-c", f"features.{feature}=false"])
    if codex.model:
        cmd.extend(["--model", codex.model])
    cmd.append(config.summary.prompt)
    return cmd


def _summarize_with_claude(config: AppConfig, collection: dict[str, Any]) -> str:
    input_text = _combined_prompt_and_collection(config.summary.prompt, collection)
    with tempfile.TemporaryDirectory(prefix="finance-digest-claude-") as workdir:
        return _run_summarizer(
            claude_command(config),
            input_text,
            cwd=Path(workdir),
            timeout=config.summary.timeout_seconds,
            provider="claude",
        )


def _summarize_with_codex(config: AppConfig, collection: dict[str, Any]) -> str:
    with tempfile.TemporaryDirectory(prefix="finance-digest-codex-") as workdir:
        return _run_summarizer(
            codex_command(config, Path(workdir)),
            format_collection_markdown(collection),
            cwd=Path(workdir),
            timeout=config.summary.timeout_seconds,
            provider="codex",
        )


def _run_summarizer(
    cmd: list[str],
    input_text: str,
    *,
    cwd: Path,
    timeout: float,
    provider: str,
) -> str:
    cmd = [_resolve_command(cmd[0], provider), *cmd[1:]]
    # A new session makes the CLI the leader of its own process group, so a
    # timeout can kill helper processes that would otherwise keep the pipes open.
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
        )
    except OSError as exc:
        raise RuntimeError(f"{provider} summarizer could not start: {exc}") from exc
    try:
        stdout, stderr = proc.communicate(input_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        _abandon(proc)
        raise RuntimeError(f"{provider} summarizer timed out after {timeout}s") from None
    except BaseException:
        _abandon(proc)
        raise

    if proc.returncode != 0:
        raise RuntimeError(
            f"{provider} summarizer failed with code {proc.returncode}: "
            f"{error_detail(stderr, stdout)}"
        )
    report = stdout.strip()
    if not report:
        raise RuntimeError(f"{provider} summarizer returned an empty report")
    return report


def error_detail(stderr: str, stdout: str) -> str:
    """Return the tail of a failed CLI's output without the echoed collection."""
    text = stderr.rsplit(_CODEX_STDIN_END, 1)[-1].strip() or stdout.strip()
    tail = "\n".join(text.splitlines()[-_ERROR_TAIL_LINES:])
    return tail[-_ERROR_TAIL_CHARS:]


def _kill_process_group(proc: subprocess.Popen[str]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _abandon(proc: subprocess.Popen[str]) -> None:
    """Kill the CLI's process group and reap it without reading its pipes.

    communicate() would block again if a descendant that left the group still
    holds stdout/stderr open, so the pipes are closed unread instead.
    """
    _kill_process_group(proc)
    proc.wait()
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        if stream is not None:
            stream.close()


def _resolve_command(command: str, provider: str) -> str:
    if os.path.sep in command:
        return command

    search_path = os.pathsep.join(
        [
            os.environ.get("PATH", ""),
            "/opt/homebrew/bin",
            "/usr/local/bin",
            str(Path.home() / ".local/bin"),
            str(Path.home() / ".npm-global/bin"),
        ]
    )
    resolved = shutil.which(command, path=search_path)
    if resolved:
        return resolved

    raise RuntimeError(
        f"Could not find summarizer command '{command}'. "
        f"Set summary.{provider}.command to an absolute path or install it on PATH."
    )


def format_collection_markdown(collection: dict[str, Any]) -> str:
    window = collection.get("window", {})
    lines = [
        "# Telegram Raw Collection",
        "",
        f"- Window: {window.get('start')} - {window.get('end')}",
        f"- Timezone: {window.get('timezone')}",
        f"- Total messages: {collection.get('total_messages', 0)}",
        "",
    ]

    for chat in collection.get("chats", []):
        if chat.get("error"):
            lines.extend([f"## {chat.get('name')} (collection failed)", ""])
            continue
        lines.extend(
            [
                f"## {chat.get('name')} ({chat.get('message_count', 0)} messages)",
                "",
            ]
        )
        for message in chat.get("messages", []):
            sender = message.get("sender_name") or message.get("sender_id") or "unknown"
            text = str(message.get("text") or "").strip()
            links = message.get("links") or []
            lines.extend(
                [
                    f"### {message.get('date')} | {sender} | msg:{message.get('id')}",
                    text,
                ]
            )
            if links:
                lines.append("Links: " + ", ".join(links))
            lines.append("")
    return "\n".join(lines).strip() + "\n"


def append_failed_chats_notice(
    report: str,
    collection: dict[str, Any],
    *,
    retry_scheduled: bool,
) -> str:
    failed = [chat for chat in collection.get("chats", []) if chat.get("error")]
    if not failed:
        return report
    lines = ["**수집 실패 채널**"]
    lines.extend(f"- {chat.get('name')}: {_first_line(chat['error'])}" for chat in failed)
    if retry_scheduled:
        lines.append("다음 실행에서 이 채널들의 누락 구간을 다시 수집합니다.")
    return report.rstrip() + "\n\n" + "\n".join(lines) + "\n"


def read_collection(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _first_line(text: str, limit: int = 160) -> str:
    line = str(text).strip().splitlines()[0] if str(text).strip() else ""
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _combined_prompt_and_collection(prompt: str, collection: dict[str, Any]) -> str:
    return (
        prompt.strip()
        + "\n\n---\n\n"
        + "아래가 요약할 텔레그램 수집본이다.\n\n"
        + format_collection_markdown(collection)
    )
