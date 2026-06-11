from __future__ import annotations

import unittest

from finance_digest.summarizer import NO_MESSAGES_REPORT, summarize_collection


class SummarizerTest(unittest.TestCase):
    def test_empty_collection_does_not_need_codex_config(self) -> None:
        report = summarize_collection(
            config=None,  # type: ignore[arg-type]
            collection={"total_messages": 0},
        )

        self.assertEqual(report, NO_MESSAGES_REPORT)


if __name__ == "__main__":
    unittest.main()
