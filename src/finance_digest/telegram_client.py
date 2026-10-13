from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from telethon import TelegramClient
from telethon.errors import RPCError
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
        resolver = _ChatResolver(client)
        for chat in config.enabled_chats:
            start = window.chat_starts[chat.key]
            try:
                entity = await resolver.resolve(chat)
                chat_result = await _collect_chat(client, entity, chat, start, end_utc)
            except (RPCError, ValueError) as exc:
                # One unreadable chat must not abort the others; its cursor stays put.
                chat_result = _chat_result(chat, start, chat.name, [])
                chat_result["error"] = f"{type(exc).__name__}: {exc}"
            collection["total_messages"] += chat_result["message_count"]
            collection["chats"].append(chat_result)

    return collection


async def _collect_chat(
    client: TelegramClient,
    entity: Any,
    chat: ChatConfig,
    start: datetime,
    end_utc: datetime,
) -> dict[str, Any]:
    start_utc = start.astimezone(UTC)
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
    return _chat_result(chat, start, title, messages)


def _chat_result(
    chat: ChatConfig,
    start: datetime,
    title: str,
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "name": chat.name,
        "title": title,
        "identifier": str(chat.identifier),
        "key": chat.key,
        "start": start.isoformat(),
        "message_count": len(messages),
        "messages": messages,
    }


class _ChatResolver:
    """Resolve configured chats, scanning the dialog list at most once per run."""

    def __init__(self, client: TelegramClient) -> None:
        self._client = client
        self._dialogs: dict[int, Any] | None = None

    async def resolve(self, chat: ChatConfig) -> Any:
        identifier = normalize_chat_identifier(chat.identifier)
        if not isinstance(identifier, int):
            return await self._client.get_entity(identifier)

        if self._dialogs is None:
            self._dialogs = {}
            async for dialog in self._client.iter_dialogs():
                entity = dialog.entity
                self._dialogs.setdefault(get_peer_id(entity), entity)
                entity_id = getattr(entity, "id", None)
                if entity_id is not None:
                    self._dialogs.setdefault(entity_id, entity)

        entity = self._dialogs.get(identifier)
        if entity is not None:
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
    session_path = config.telegram.session_path
    session_path.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(
        str(session_path),
        config.telegram_api_id(),
        config.telegram_api_hash(),
    )
    # Telethon appends ".session" unless present; the file grants full account access.
    session_file = (
        session_path
        if session_path.name.endswith(".session")
        else session_path.with_name(session_path.name + ".session")
    )
    if session_file.exists():
        session_file.chmod(0o600)
    return client
