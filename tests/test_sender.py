from __future__ import annotations

import io
import os
import unittest
import urllib.error
import urllib.parse
from types import SimpleNamespace
from unittest import mock

from finance_digest.guard import SecretLeakError
from finance_digest.sender import (
    _send_message,
    markdown_to_telegram_html,
    send_failure_alert,
    send_telegram_report,
    split_for_telegram,
)

URLOPEN = "finance_digest.sender.urllib.request.urlopen"
API_HASH = "0123456789abcdef0123456789abcdef"
CONFIG = SimpleNamespace(
    telegram=SimpleNamespace(api_hash_env="T_API_HASH"),
    send=SimpleNamespace(
        telegram=SimpleNamespace(
            bot_token_env="T_BOT_TOKEN",
            delivery_chat_id_env="T_CHAT",
            disable_web_page_preview=True,
            max_chars=3900,
        )
    ),
    bot_token=lambda: os.environ["T_BOT_TOKEN"],
    delivery_chat_id=lambda: os.environ["T_CHAT"],
)


def _ok_response() -> mock.MagicMock:
    response = mock.MagicMock()
    response.read.return_value = b'{"ok": true}'
    response.__enter__.return_value = response
    return response


def _http_error(code: int, body: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://api.telegram.org", code, "error", {}, io.BytesIO(body.encode("utf-8"))
    )


def _payload(urlopen: mock.MagicMock, call: int = -1) -> dict[str, str]:
    request = urlopen.call_args_list[call].args[0]
    return dict(urllib.parse.parse_qsl(request.data.decode("utf-8")))


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
    def setUp(self) -> None:
        patcher = mock.patch("finance_digest.sender.time.sleep")
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    def _send(self, parse_mode: str | None = "HTML") -> None:
        _send_message(
            token="t",
            chat_id="c",
            text="<b>hi</b>",
            disable_web_page_preview=True,
            parse_mode=parse_mode,
        )

    def test_payload_includes_html_parse_mode(self) -> None:
        with mock.patch(URLOPEN, return_value=_ok_response()) as urlopen:
            self._send()

        self.assertEqual(_payload(urlopen)["parse_mode"], "HTML")
        self.assertEqual(_payload(urlopen)["text"], "<b>hi</b>")

    def test_plain_text_omits_parse_mode(self) -> None:
        with mock.patch(URLOPEN, return_value=_ok_response()) as urlopen:
            self._send(parse_mode=None)

        self.assertNotIn("parse_mode", _payload(urlopen))

    def test_retries_server_errors_then_succeeds(self) -> None:
        responses = [_http_error(502, '{"ok":false}'), _ok_response()]
        with mock.patch(URLOPEN, side_effect=responses) as urlopen:
            self._send()

        self.assertEqual(urlopen.call_count, 2)
        self.sleep.assert_called_once_with(2.0)

    def test_honors_retry_after_on_rate_limit(self) -> None:
        body = '{"ok":false,"error_code":429,"parameters":{"retry_after":7}}'
        with mock.patch(URLOPEN, side_effect=[_http_error(429, body), _ok_response()]):
            self._send()

        self.sleep.assert_called_once_with(7.0)

    def test_retries_connection_errors_and_gives_up(self) -> None:
        error = urllib.error.URLError(ConnectionResetError(54, "reset"))
        with mock.patch(URLOPEN, side_effect=[error, error, error]) as urlopen:
            with self.assertRaisesRegex(RuntimeError, "after 3 attempts"):
                self._send()

        self.assertEqual(urlopen.call_count, 3)

    def test_does_not_retry_client_errors(self) -> None:
        error = _http_error(400, '{"ok":false,"description":"Bad Request"}')
        with mock.patch(URLOPEN, side_effect=[error]) as urlopen:
            with self.assertRaisesRegex(RuntimeError, "400"):
                self._send()

        self.assertEqual(urlopen.call_count, 1)
        self.sleep.assert_not_called()


class SendReportTest(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.dict(
            os.environ,
            {"T_API_HASH": API_HASH, "T_BOT_TOKEN": "123:" + "s" * 30, "T_CHAT": "42"},
            clear=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_blocks_report_containing_secret_before_sending(self) -> None:
        with mock.patch(URLOPEN) as urlopen:
            with self.assertRaises(SecretLeakError):
                send_telegram_report(CONFIG, f"요약\n{API_HASH}")

        urlopen.assert_not_called()

    def test_sends_clean_report_as_html(self) -> None:
        with mock.patch(URLOPEN, return_value=_ok_response()) as urlopen:
            sent = send_telegram_report(CONFIG, "**요약**")

        self.assertEqual(sent, 1)
        self.assertEqual(_payload(urlopen)["text"], "<b>요약</b>")
        self.assertEqual(_payload(urlopen)["chat_id"], "42")

    def test_failure_alert_is_plain_text_with_secrets_masked(self) -> None:
        with mock.patch(URLOPEN, return_value=_ok_response()) as urlopen:
            send_failure_alert(CONFIG, f"run failed <x> {API_HASH}")

        payload = _payload(urlopen)
        self.assertNotIn("parse_mode", payload)
        self.assertEqual(payload["text"], "run failed <x> <T_API_HASH>")


if __name__ == "__main__":
    unittest.main()
