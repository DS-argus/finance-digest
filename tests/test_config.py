from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from finance_digest.config import load_config


class ConfigTest(unittest.TestCase):
    def test_normalizes_misplaced_phone_plus_like_telethon_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "prompt.yml").write_text("prompt: test\n", encoding="utf-8")
            (root / ".env").write_text("TELEGRAM_PHONE=82+1012345678\n", encoding="utf-8")
            (root / "config.yml").write_text(
                """
env_file: .env
telegram:
  phone_env: TELEGRAM_PHONE
summary:
  prompt_file: prompt.yml
""",
                encoding="utf-8",
            )

            cfg = load_config(root / "config.yml")
            self.assertEqual(cfg.telegram_phone(), "821012345678")

    def test_accepts_international_phone_format(self) -> None:
        previous = os.environ.get("TELEGRAM_PHONE_TEST")
        os.environ["TELEGRAM_PHONE_TEST"] = "+821012345678"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "prompt.yml").write_text("prompt: test\n", encoding="utf-8")
                (root / "config.yml").write_text(
                    """
telegram:
  phone_env: TELEGRAM_PHONE_TEST
summary:
  prompt_file: prompt.yml
""",
                    encoding="utf-8",
                )

                cfg = load_config(root / "config.yml")
                self.assertEqual(cfg.telegram_phone(), "821012345678")
        finally:
            if previous is None:
                os.environ.pop("TELEGRAM_PHONE_TEST", None)
            else:
                os.environ["TELEGRAM_PHONE_TEST"] = previous


if __name__ == "__main__":
    unittest.main()
