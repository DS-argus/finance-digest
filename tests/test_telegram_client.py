from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from telethon.errors import ChannelPrivateError

from finance_digest import telegram_client
from finance_digest.config import ChatConfig
from finance_digest.window import CollectionWindow

SEOUL = ZoneInfo("Asia/Seoul")
END = datetime(2026, 9, 21, 8, 0, tzinfo=SEOUL)
START = datetime(2026, 9, 18, 8, 0, tzinfo=SEOUL)


def _message(message_id: int, date: datetime, text: str) -> SimpleNamespace:
    async def get_sender():
        return SimpleNamespace(title=None, first_name="Analyst", last_name=None)

    return SimpleNamespace(
        id=message_id,
        date=date.astimezone(UTC),
        raw_text=text,
        sender_id=7,
        get_sender=get_sender,
    )


class FakeClient:
    def __init__(self, dialogs, messages, failing=()):
        self.dialogs = dialogs
        self.messages = messages
        self.failing = set(failing)
        self.dialog_scans = 0
        self.offsets = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def start(self, phone=None):
        return self

    async def get_entity(self, identifier):
        if identifier in self.failing:
            raise ChannelPrivateError(request=None)
        return SimpleNamespace(id=identifier, title=f"@{identifier}", username=identifier)

    async def iter_dialogs(self):
        self.dialog_scans += 1
        for dialog in self.dialogs:
            yield dialog

    async def iter_messages(self, entity, offset_date):
        self.offsets.append(offset_date)
        for message in self.messages.get(entity.id, []):
            if message.date < offset_date:
                yield message


def _config(*chats: ChatConfig) -> SimpleNamespace:
    return SimpleNamespace(enabled_chats=list(chats), telegram_phone=lambda: None)


def _window(starts: dict[str, datetime]) -> CollectionWindow:
    return CollectionWindow(
        start=min(starts.values()),
        end=END,
        timezone="Asia/Seoul",
        label="20260918_0800-20260921_0800",
        chat_starts=starts,
    )


class CollectMessagesTest(unittest.TestCase):
    def _collect(self, client: FakeClient, config, window):
        with mock.patch.object(telegram_client, "_build_client", return_value=client):
            return asyncio.run(telegram_client.collect_messages(config, window))

    def test_failed_chat_does_not_abort_others(self) -> None:
        good = ChatConfig(name="Good", identifier="good_channel")
        bad = ChatConfig(name="Private", identifier="@private_channel")
        client = FakeClient(
            dialogs=[],
            messages={"good_channel": [_message(1, END.replace(hour=7), "CPI 발표")]},
            failing={"private_channel"},
        )

        collection = self._collect(
            client, _config(bad, good), _window({bad.key: START, good.key: START})
        )

        failed, ok = collection["chats"]
        self.assertIn("ChannelPrivateError", failed["error"])
        self.assertEqual(failed["message_count"], 0)
        self.assertEqual(failed["key"], "private_channel")
        self.assertNotIn("error", ok)
        self.assertEqual(ok["message_count"], 1)
        self.assertEqual(collection["total_messages"], 1)

    def test_each_chat_uses_its_own_start(self) -> None:
        early = datetime(2026, 9, 17, 8, 0, tzinfo=SEOUL)
        chat_a = ChatConfig(name="A", identifier="a")
        chat_b = ChatConfig(name="B", identifier="b")
        thursday_noon = datetime(2026, 9, 17, 12, 0, tzinfo=SEOUL)
        saturday = datetime(2026, 9, 19, 12, 0, tzinfo=SEOUL)
        messages = [_message(2, saturday, "new"), _message(1, thursday_noon, "old")]
        client = FakeClient(dialogs=[], messages={"a": messages, "b": messages})

        collection = self._collect(
            client, _config(chat_a, chat_b), _window({"a": early, "b": START})
        )

        a, b = collection["chats"]
        self.assertEqual([m["text"] for m in a["messages"]], ["old", "new"])
        self.assertEqual([m["text"] for m in b["messages"]], ["new"])
        self.assertEqual(a["start"], early.isoformat())
        self.assertEqual(collection["window"]["start"], early.isoformat())

    def test_numeric_ids_scan_dialogs_once(self) -> None:
        entity_a = SimpleNamespace(id=1001, title="A")
        entity_b = SimpleNamespace(id=1002, title="B")
        dialogs = [SimpleNamespace(entity=entity_a), SimpleNamespace(entity=entity_b)]
        client = FakeClient(dialogs=dialogs, messages={})
        chats = [
            ChatConfig(name="A", identifier="-1001"),
            ChatConfig(name="B", identifier=-1002),
            ChatConfig(name="Missing", identifier=-1999),
        ]

        with mock.patch.object(
            telegram_client,
            "get_peer_id",
            side_effect=lambda entity: -entity.id,
        ):
            collection = self._collect(
                client, _config(*chats), _window({chat.key: START for chat in chats})
            )

        self.assertEqual(client.dialog_scans, 1)
        self.assertEqual([chat.get("title") for chat in collection["chats"]], ["A", "B", "Missing"])
        self.assertIn("Could not resolve", collection["chats"][2]["error"])


class BuildClientTest(unittest.TestCase):
    def test_existing_session_file_is_made_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "telegram"
            session_file = Path(tmp) / "telegram.session"
            session_file.write_bytes(b"")
            session_file.chmod(0o644)
            config = SimpleNamespace(
                telegram=SimpleNamespace(session_path=session),
                telegram_api_id=lambda: 1,
                telegram_api_hash=lambda: "hash",
            )

            with mock.patch.object(telegram_client, "TelegramClient") as client_cls:
                telegram_client._build_client(config)

            client_cls.assert_called_once_with(str(session), 1, "hash")
            self.assertEqual(os.stat(session_file).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
