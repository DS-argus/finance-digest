from __future__ import annotations

import unittest
import urllib.parse
from unittest import mock

from finance_digest.sender import (
    _send_message,
    markdown_to_telegram_html,
    split_for_telegram,
)


class TelegramSplitTest(unittest.TestCase):
    def test_split_preserves_short_text(self) -> None:
        self.assertEqual(split_for_telegram("hello", 10), ["hello"])

    def test_split_by_paragraph(self) -> None:
        chunks = split_for_telegram("aaa\n\nbbb\n\nccc", 8)
        self.assertEqual(chunks, ["aaa\n\nbbb", "ccc"])

    def test_hard_split_long_paragraph(self) -> None:
        chunks = split_for_telegram("abcdef", 2)
        self.assertEqual(chunks, ["ab", "cd", "ef"])

    def test_hard_split_prefers_line_boundaries(self) -> None:
        chunks = split_for_telegram("<b>aaa</b>\n<b>bbb</b>", 12)
        self.assertEqual(chunks, ["<b>aaa</b>", "<b>bbb</b>"])


class MarkdownToTelegramHtmlTest(unittest.TestCase):
    def test_heading_becomes_bold(self) -> None:
        self.assertEqual(
            markdown_to_telegram_html("## 시장 & 매크로"),
            "<b>시장 &amp; 매크로</b>",
        )

    def test_bold_and_code(self) -> None:
        self.assertEqual(
            markdown_to_telegram_html("- **삼성전자**: `005930` 상승"),
            "- <b>삼성전자</b>: <code>005930</code> 상승",
        )

    def test_link(self) -> None:
        self.assertEqual(
            markdown_to_telegram_html("[기사](https://example.com/a?x=1&y=2)"),
            '<a href="https://example.com/a?x=1&amp;y=2">기사</a>',
        )

    def test_blockquote(self) -> None:
        self.assertEqual(
            markdown_to_telegram_html("> 2026-07-22 기준"),
            "<blockquote>2026-07-22 기준</blockquote>",
        )

    def test_escapes_angle_brackets(self) -> None:
        self.assertEqual(
            markdown_to_telegram_html("금리 <2% 전망"),
            "금리 &lt;2% 전망",
        )

    def test_plain_lines_unchanged(self) -> None:
        self.assertEqual(
            markdown_to_telegram_html("- [ ] 09:00 — CPI 발표"),
            "- [ ] 09:00 — CPI 발표",
        )

    def test_horizontal_rule_dropped(self) -> None:
        self.assertEqual(
            markdown_to_telegram_html("위\n\n---\n\n아래"),
            "위\n\n\n아래",
        )

    def test_tags_never_span_lines(self) -> None:
        converted = markdown_to_telegram_html("**a\nb**")
        for line in converted.split("\n"):
            self.assertEqual(line.count("<b>"), line.count("</b>"))


class SendMessageTest(unittest.TestCase):
    def test_payload_includes_html_parse_mode(self) -> None:
        response = mock.MagicMock()
        response.read.return_value = b'{"ok": true}'
        response.__enter__.return_value = response
        with mock.patch(
            "finance_digest.sender.urllib.request.urlopen", return_value=response
        ) as urlopen:
            _send_message(
                token="t",
                chat_id="c",
                text="<b>hi</b>",
                disable_web_page_preview=True,
            )
        request = urlopen.call_args.args[0]
        payload = dict(urllib.parse.parse_qsl(request.data.decode("utf-8")))
        self.assertEqual(payload["parse_mode"], "HTML")
        self.assertEqual(payload["text"], "<b>hi</b>")


if __name__ == "__main__":
    unittest.main()
