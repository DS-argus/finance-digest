from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from finance_digest.config import AppConfig


def send_telegram_report(config: AppConfig, text: str) -> int:
    token = config.bot_token()
    chat_id = config.delivery_chat_id()
    chunks = split_for_telegram(text, config.send.telegram.max_chars)
    for index, chunk in enumerate(chunks, start=1):
        prefix = f"[{index}/{len(chunks)}]\n" if len(chunks) > 1 else ""
        _send_message(
            token=token,
            chat_id=chat_id,
            text=prefix + chunk,
            disable_web_page_preview=config.send.telegram.disable_web_page_preview,
        )
    return len(chunks)


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
    return [text[index : index + max_chars] for index in range(0, len(text), max_chars)]


def _send_message(
    *,
    token: str,
    chat_id: str,
    text: str,
    disable_web_page_preview: bool,
) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true"
            if disable_web_page_preview
            else "false",
        }
    ).encode("utf-8")
    request = urllib.request.Request(url, data=payload, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram sendMessage failed: {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Telegram sendMessage failed: {exc}") from exc

    result = json.loads(body)
    if not result.get("ok"):
        raise RuntimeError(f"Telegram sendMessage failed: {body}")
