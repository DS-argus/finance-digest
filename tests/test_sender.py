from __future__ import annotations

import unittest

from finance_digest.sender import split_for_telegram


class TelegramSplitTest(unittest.TestCase):
    def test_split_preserves_short_text(self) -> None:
        self.assertEqual(split_for_telegram("hello", 10), ["hello"])

    def test_split_by_paragraph(self) -> None:
        chunks = split_for_telegram("aaa\n\nbbb\n\nccc", 8)
        self.assertEqual(chunks, ["aaa\n\nbbb", "ccc"])

    def test_hard_split_long_paragraph(self) -> None:
        chunks = split_for_telegram("abcdef", 2)
        self.assertEqual(chunks, ["ab", "cd", "ef"])


if __name__ == "__main__":
    unittest.main()
