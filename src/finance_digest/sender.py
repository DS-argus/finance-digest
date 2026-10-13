from __future__ import annotations

import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from finance_digest.config import AppConfig
from finance_digest.guard import ensure_no_secrets, redact_secrets

_MD_CODE_RE = re.compile(r"`([^`\n]+)`")
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_MD_LINK_RE = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_MD_HEADING_RE = re.compile(r"^#{1,6}\s+(.*)$")
_MD_HR_RE = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")

_SEND_ATTEMPTS = 3
_RETRY_BASE_DELAY_SECONDS = 2.0
_MAX_RETRY_AFTER_SECONDS = 60.0


class _RetryableSendError(Exception):
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def send_telegram_report(config: AppConfig, text: str) -> int:
    # Last line of defense: nothing the summarizer produced may carry a credential out.
    ensure_no_secrets(config, text)
    token = config.bot_token()
    chat_id = config.delivery_chat_id()
    chunks = split_for_telegram(
        markdown_to_telegram_html(text), config.send.telegram.max_chars
    )
    for index, chunk in enumerate(chunks, start=1):
        prefix = f"[{index}/{len(chunks)}]\n" if len(chunks) > 1 else ""
        _send_message(
            token=token,
            chat_id=chat_id,
            text=prefix + chunk,
            disable_web_page_preview=config.send.telegram.disable_web_page_preview,
            parse_mode="HTML",
        )
    return len(chunks)


def send_failure_alert(config: AppConfig, text: str) -> None:
    """Send a short plain-text alert; credentials are masked instead of blocking it."""
    _send_message(
        token=config.bot_token(),
        chat_id=config.delivery_chat_id(),
        text=redact_secrets(config, text)[: config.send.telegram.max_chars],
        disable_web_page_preview=True,
        parse_mode=None,
    )


def markdown_to_telegram_html(text: str) -> str:
    """Convert the digest's Markdown subset to Telegram-safe HTML.

    Conversion is line-based, so no tag ever spans a line break and chunk
    splitting can never produce unbalanced tags.
    """
    lines: list[str] = []
    for line in text.split("\n"):
        if _MD_HR_RE.match(line):
            continue
        escaped = html.escape(line, quote=False)
        heading = _MD_HEADING_RE.match(escaped)
        if heading:
            lines.append(f"<b>{_convert_inline(heading.group(1)).strip()}</b>")
            continue
        if escaped.startswith("&gt; "):
            lines.append(f"<blockquote>{_convert_inline(escaped[5:])}</blockquote>")
            continue
        lines.append(_convert_inline(escaped))
    return "\n".join(lines)


def _convert_inline(text: str) -> str:
    text = _MD_CODE_RE.sub(r"<code>\1</code>", text)
    text = _MD_BOLD_RE.sub(r"<b>\1</b>", text)
    text = _MD_LINK_RE.sub(r'<a href="\2">\1</a>', text)
    return text


def split_for_telegram(text: str, max_chars: int = 3900) -> list[str]:
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")

    paragraphs = text.strip().split("\n\n")
    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_hard_split(paragraph, max_chars))
            continue
        candidate = paragraph if not current else current + "\n\n" + paragraph
        if len(candidate) <= max_chars:
            current = candidate
        else:
            chunks.append(current)
            current = paragraph

    if current:
        chunks.append(current)
    return chunks or [""]


def _hard_split(text: str, max_chars: int) -> list[str]:
    """Split an oversized paragraph, preferring line boundaries over raw chars."""
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        if len(line) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(
                line[index : index + max_chars]
                for index in range(0, len(line), max_chars)
            )
            continue
        candidate = line if not current else current + "\n" + line
        if len(candidate) <= max_chars:
            current = candidate
        else:
            chunks.append(current)
            current = line
    if current:
        chunks.append(current)
    return chunks or [""]


def _send_message(
    *,
    token: str,
    chat_id: str,
    text: str,
    disable_web_page_preview: bool,
    parse_mode: str | None,
) -> None:
    fields = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true" if disable_web_page_preview else "false",
    }
    if parse_mode:
        fields["parse_mode"] = parse_mode
    payload = urllib.parse.urlencode(fields).encode("utf-8")
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    for attempt in range(1, _SEND_ATTEMPTS + 1):
        try:
            _post(url, payload)
            return
        except _RetryableSendError as exc:
            if attempt == _SEND_ATTEMPTS:
                raise RuntimeError(
                    f"Telegram sendMessage failed after {attempt} attempts: {exc}"
                ) from exc
            time.sleep(exc.retry_after or _RETRY_BASE_DELAY_SECONDS * attempt)


def _post(url: str, payload: bytes) -> None:
    request = urllib.request.Request(url, data=payload, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        message = f"{exc.code} {detail}"
        if exc.code == 429 or exc.code >= 500:
            raise _RetryableSendError(message, _retry_after(detail)) from exc
        raise RuntimeError(f"Telegram sendMessage failed: {message}") from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise _RetryableSendError(str(exc)) from exc

    result = json.loads(body)
    if not result.get("ok"):
        raise RuntimeError(f"Telegram sendMessage failed: {body}")


def _retry_after(body: str) -> float | None:
    try:
        value = json.loads(body)["parameters"]["retry_after"]
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(value, int | float) or value <= 0:
        return None
    return min(float(value), _MAX_RETRY_AFTER_SECONDS)
