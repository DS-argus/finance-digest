from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from finance_digest.config import ChatConfig, ConfigError, load_config


def _write_config(root: Path, body: str) -> Path:
    (root / "prompt.yml").write_text("prompt: test\n", encoding="utf-8")
    path = root / "config.yml"
    path.write_text(body, encoding="utf-8")
    return path


class ConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        # The developer shell may export real TELEGRAM_* values; keep tests hermetic.
        patcher = mock.patch.dict(os.environ, {}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_normalizes_misplaced_phone_plus_like_telethon_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("TELEGRAM_PHONE=82+1012345678\n", encoding="utf-8")
            path = _write_config(
                root,
                """
env_file: .env
telegram:
  phone_env: TELEGRAM_PHONE
summary:
  prompt_file: prompt.yml
""",
            )

            cfg = load_config(path)
            self.assertEqual(cfg.telegram_phone(), "821012345678")

    def test_accepts_international_phone_format(self) -> None:
        os.environ["TELEGRAM_PHONE_TEST"] = "+821012345678"
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_config(
                Path(tmp),
                """
telegram:
  phone_env: TELEGRAM_PHONE_TEST
summary:
  prompt_file: prompt.yml
""",
            )

            cfg = load_config(path)
            self.assertEqual(cfg.telegram_phone(), "821012345678")

    def test_state_file_and_timeout_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            path = _write_config(root, "summary:\n  prompt_file: prompt.yml\n")

            cfg = load_config(path)

            self.assertEqual(cfg.collection.state_file, root / "data" / "state.json")
            self.assertEqual(cfg.summary.timeout_seconds, 600)

    def test_rejects_non_positive_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_config(
                Path(tmp),
                "summary:\n  prompt_file: prompt.yml\n  timeout_seconds: 0\n",
            )

            with self.assertRaisesRegex(ConfigError, "timeout_seconds"):
                load_config(path)

    def test_chat_key_is_stable_across_identifier_spellings(self) -> None:
        self.assertEqual(
            ChatConfig(name="a", identifier="-1001234567890").key,
            ChatConfig(name="a", identifier=-1001234567890).key,
        )
        self.assertEqual(
            ChatConfig(name="a", identifier="@market_news").key,
            ChatConfig(name="a", identifier="market_news").key,
        )


if __name__ == "__main__":
    unittest.main()
