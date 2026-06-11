from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from telethon import TelegramClient
from telethon.utils import get_peer_id

from finance_digest.config import AppConfig, ChatConfig, normalize_chat_identifier
from finance_digest.window import CollectionWindow

LINK_RE = re.compile(r"(https?://\S+|t\.me/\S+)", re.IGNORECASE)


async def login(config: AppConfig) -> str:
    client = _build_client(config)
    async with client:
        await client.start(phone=config.telegram_phone())
        me = await client.get_me()
    username = f"@{me.username}" if getattr(me, "username", None) else str(me.id)
    return f"{_display_name(me) or 'Telegram user'} ({username})"


async def get_me(config: AppConfig) -> dict[str, Any]:
    client = _build_client(config)
    async with client:
        await client.start(phone=config.telegram_phone())
        me = await client.get_me()
    return {
        "id": me.id,
        "username": getattr(me, "username", None),
        "name": _display_name(me),
    }


async def list_dialogs(config: AppConfig, limit: int = 100) -> list[dict[str, Any]]:
    client = _build_client(config)
    rows: list[dict[str, Any]] = []
    async with client:
        await client.start(phone=config.telegram_phone())
        async for dialog in client.iter_dialogs(limit=limit):
            entity = dialog.entity
            rows.append(
                {
                    "name": dialog.name,
                    "id": getattr(entity, "id", None),
                    "peer_id": get_peer_id(entity),
                    "username": getattr(entity, "username", None),
                    "is_channel": bool(getattr(entity, "broadcast", False)),
                    "is_group": bool(getattr(entity, "megagroup", False))
                    or bool(getattr(entity, "gigagroup", False)),
                }
            )
    return rows


async def collect_messages(
    config: AppConfig,
    window: CollectionWindow,
) -> dict[str, Any]:
    client = _build_client(config)
    start_utc = window.start.astimezone(UTC)
    end_utc = window.end.astimezone(UTC)

    collection: dict[str, Any] = {
        "created_at": datetime.now(UTC).isoformat(),
        "window": {
            "start": window.start.isoformat(),
            "end": window.end.isoformat(),
            "timezone": window.timezone,
            "label": window.label,
        },
        "chats": [],
        "total_messages": 0,
    }

    async with client:
        await client.start(phone=config.telegram_phone())
        for chat in config.enabled_chats:
            chat_result = await _collect_chat(client, chat, start_utc, end_utc)
            collection["total_messages"] += chat_result["message_count"]
            collection["chats"].append(chat_result)

    return collection


async def _collect_chat(
    client: TelegramClient,
    chat: ChatConfig,
    start_utc: datetime,
    end_utc: datetime,
) -> dict[str, Any]:
    entity = await _resolve_chat_entity(client, chat)
    messages: list[dict[str, Any]] = []
    sender_cache: dict[int, str | None] = {}

    async for message in client.iter_messages(entity, offset_date=end_utc):
        message_date = _ensure_utc(message.date)
        if message_date < start_utc:
            break
        if message_date >= end_utc:
            continue

        text = (message.raw_text or "").strip()
        if not text:
            continue

        sender_id = getattr(message, "sender_id", None)
        sender_name = None
        if sender_id:
            sender_name = sender_cache.get(sender_id)
            if sender_id not in sender_cache:
                sender_name = await _safe_sender_name(message)
                sender_cache[sender_id] = sender_name

        messages.append(
            {
                "id": message.id,
                "date": message_date.isoformat(),
                "sender_id": sender_id,
                "sender_name": sender_name,
                "text": text,
                "links": extract_links(text),
            }
        )

    messages.reverse()
    title = getattr(entity, "title", None) or getattr(entity, "username", None) or chat.name
    return {
        "name": chat.name,
        "title": title,
        "identifier": str(chat.identifier),
        "message_count": len(messages),
        "messages": messages,
    }


async def _resolve_chat_entity(client: TelegramClient, chat: ChatConfig) -> Any:
    identifier = normalize_chat_identifier(chat.identifier)
    if not isinstance(identifier, int):
        return await client.get_entity(identifier)

    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        if get_peer_id(entity) == identifier or getattr(entity, "id", None) == identifier:
            return entity

    raise ValueError(
        "Could not resolve chat identifier "
        f"{chat.identifier!r} for '{chat.name}'. "
        "Run `uv run finance-digest chats --config config.yml --limit 500` "
        "and copy the first column for the target chat. If the chat is missing, "
        "open it once in Telegram and try again."
    )


async def _safe_sender_name(message: Any) -> str | None:
    try:
        sender = await message.get_sender()
    except Exception:
        return None
    return _display_name(sender)


def _display_name(entity: Any) -> str | None:
    if entity is None:
        return None
    title = getattr(entity, "title", None)
    if title:
        return str(title)
    parts = [
        getattr(entity, "first_name", None),
        getattr(entity, "last_name", None),
    ]
    name = " ".join(str(part) for part in parts if part)
    if name:
        return name
    username = getattr(entity, "username", None)
    if username:
        return f"@{username}"
    return None


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def extract_links(text: str) -> list[str]:
    links = []
    for match in LINK_RE.findall(text):
        links.append(match.rstrip(".,);]}>\"'"))
    return links


def _build_client(config: AppConfig) -> TelegramClient:
    config.telegram.session_path.parent.mkdir(parents=True, exist_ok=True)
    return TelegramClient(
        str(config.telegram.session_path),
        config.telegram_api_id(),
        config.telegram_api_hash(),
    )
