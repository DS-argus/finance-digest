from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from finance_digest.config import AppConfig

NO_MESSAGES_REPORT = """# Telegram Finance Digest

수집된 메시지가 없습니다.

---
투자 조언이 아닙니다. 원문과 출처를 확인하세요.
"""


def summarize_collection(config: AppConfig, collection: dict[str, Any]) -> str:
    if int(collection.get("total_messages") or 0) == 0:
        return NO_MESSAGES_REPORT

    if config.summary.provider == "claude":
        return _summarize_with_claude(config, collection)
    if config.summary.provider == "codex":
        return _summarize_with_codex(config, collection)
    raise RuntimeError(f"Unsupported summary provider: {config.summary.provider}")


def _summarize_with_claude(config: AppConfig, collection: dict[str, Any]) -> str:
    claude = config.summary.claude
    cmd = [
        claude.command,
        "-p",
        "--output-format",
        "text",
        "--input-format",
        "text",
    ]
    if claude.no_session_persistence:
        cmd.append("--no-session-persistence")
    if claude.tools is not None:
        cmd.extend(["--tools", claude.tools])
    if claude.model:
        cmd.extend(["--model", claude.model])

    input_text = _combined_prompt_and_collection(config.summary.prompt, collection)
    return _run_summarizer(cmd, input_text, config, provider="claude")


def _summarize_with_codex(config: AppConfig, collection: dict[str, Any]) -> str:
    codex = config.summary.codex
    cmd = [codex.command, "exec", "--color", "never"]
    if codex.skip_git_repo_check:
        cmd.append("--skip-git-repo-check")
    if codex.ephemeral:
        cmd.append("--ephemeral")
    if codex.sandbox:
        cmd.extend(["--sandbox", codex.sandbox])
    if codex.model:
        cmd.extend(["--model", codex.model])
    cmd.append(config.summary.prompt)

    return _run_summarizer(
        cmd,
        format_collection_markdown(collection),
        config,
        provider="codex",
    )


def _run_summarizer(
    cmd: list[str],
    input_text: str,
    config: AppConfig,
    *,
    provider: str,
) -> str:
    proc = subprocess.run(
        cmd,
        input=input_text,
        text=True,
        capture_output=True,
        cwd=config.base_dir,
        check=False,
    )
    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        raise RuntimeError(f"{provider} summarizer failed with code {proc.returncode}: {stderr}")
    return proc.stdout.strip()


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


def read_collection(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _combined_prompt_and_collection(prompt: str, collection: dict[str, Any]) -> str:
    return (
        prompt.strip()
        + "\n\n---\n\n"
        + "아래가 요약할 텔레그램 수집본이다.\n\n"
        + format_collection_markdown(collection)
    )
