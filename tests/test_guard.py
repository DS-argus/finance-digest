from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest import mock

from finance_digest.guard import (
    SecretLeakError,
    ensure_no_secrets,
    find_leaked_secrets,
    redact_secrets,
)

API_HASH = "0123456789abcdef0123456789abcdef"
BOT_SECRET = "AAH-very_long_bot_secret_value_123"
BOT_TOKEN = f"123456789:{BOT_SECRET}"

CONFIG = SimpleNamespace(
    telegram=SimpleNamespace(api_hash_env="T_API_HASH"),
    send=SimpleNamespace(telegram=SimpleNamespace(bot_token_env="T_BOT_TOKEN")),
)


class GuardTest(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.dict(
            os.environ,
            {"T_API_HASH": API_HASH, "T_BOT_TOKEN": BOT_TOKEN},
            clear=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_clean_report_passes(self) -> None:
        ensure_no_secrets(CONFIG, "**Daily Finance Digest**\n- 코스피 123456789 거래")

    def test_blocks_api_hash_case_insensitively(self) -> None:
        with self.assertRaisesRegex(SecretLeakError, "T_API_HASH"):
            ensure_no_secrets(CONFIG, f"leak: {API_HASH.upper()}")

    def test_blocks_bot_secret_without_bot_id(self) -> None:
        self.assertEqual(find_leaked_secrets(CONFIG, f"x {BOT_SECRET} y"), ["T_BOT_TOKEN"])

    def test_bot_id_alone_is_not_a_secret(self) -> None:
        self.assertEqual(find_leaked_secrets(CONFIG, "bot 123456789 sent it"), [])

    def test_short_or_missing_values_are_ignored(self) -> None:
        with mock.patch.dict(os.environ, {"T_API_HASH": "short"}, clear=True):
            self.assertEqual(find_leaked_secrets(CONFIG, "short anything"), [])

    def test_redact_masks_every_occurrence(self) -> None:
        text = f"token={BOT_TOKEN} hash={API_HASH}"

        redacted = redact_secrets(CONFIG, text)

        self.assertNotIn(BOT_SECRET, redacted)
        self.assertNotIn(API_HASH, redacted)
        self.assertIn("<T_BOT_TOKEN>", redacted)
        self.assertIn("<T_API_HASH>", redacted)


if __name__ == "__main__":
    unittest.main()
