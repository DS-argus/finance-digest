from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from finance_digest.config import AppConfig, load_config
from finance_digest.sender import send_failure_alert, send_telegram_report
from finance_digest.state import advanced_cursors, read_cursors, write_cursors
from finance_digest.summarizer import (
    append_failed_chats_notice,
    format_collection_markdown,
    read_collection,
    summarize_collection,
)
from finance_digest.telegram_client import collect_messages, get_me, list_dialogs, login
from finance_digest.window import CollectionWindow, compute_collection_window


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "handler"):
        parser.print_help()
        return 2

    try:
        return args.handler(args)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def cmd_login(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    user = asyncio.run(login(config))
    print(f"Logged in as {user}")
    return 0


def cmd_chats(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    rows = asyncio.run(list_dialogs(config, limit=args.limit))
    for row in rows:
        username = f"@{row['username']}" if row.get("username") else ""
        kind = "channel" if row.get("is_channel") else "group" if row.get("is_group") else "chat"
        print(f"{row['peer_id']}\t{kind}\t{username}\t{row['name']}")
    return 0


def cmd_me(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    me = asyncio.run(get_me(config))
    username = f"@{me['username']}" if me.get("username") else ""
    print(f"id: {me['id']}")
    print(f"name: {me.get('name') or ''}")
    print(f"username: {username}")
    print()
    print(f"Use this in .env: TELEGRAM_DELIVERY_CHAT_ID={me['id']}")
    return 0


def cmd_collect(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    window = _window(config, args)
    collection = asyncio.run(collect_messages(config, window))
    _print_collection_summary(collection)

    if args.dry_run:
        return 0

    json_path, markdown_path = _write_collection(config, collection)
    print(f"Wrote collection JSON: {json_path}")
    print(f"Wrote collection Markdown: {markdown_path}")
    return 0


def cmd_summarize(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    collection_path = (
        _input_path(args.input)
        if args.input
        else _latest(config.collection.output_dir, "*.json")
    )
    collection = read_collection(collection_path)
    report = _summarize(config, collection, retry_scheduled=False)

    if args.dry_run:
        print(report)
        return 0

    report_path = _write_report(config, collection, report)
    print(f"Wrote report: {report_path}")
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    report_path = (
        _input_path(args.input) if args.input else _latest(config.summary.output_dir, "*.md")
    )
    sent = send_telegram_report(config, report_path.read_text(encoding="utf-8"))
    print(f"Sent {report_path} in {sent} message(s)")

    collection_path = config.collection.output_dir / f"{report_path.stem}.json"
    if collection_path.exists():
        _advance_cursors(config, read_collection(collection_path), reset=False)
    else:
        print(f"No matching collection {collection_path}; collection state unchanged")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    try:
        return _run(config, args)
    except Exception as exc:
        if config.send.telegram.enabled and not args.dry_run:
            _alert_failure(config, exc)
        raise


def _run(config: AppConfig, args: argparse.Namespace) -> int:
    # A run that collects up to now owns the cursors; a backfill window must not
    # move them past messages that were never reported.
    reset = args.until is None
    window = _window(config, args)
    collection = asyncio.run(collect_messages(config, window))
    _print_collection_summary(collection)

    if args.dry_run:
        print()
        print(_summarize(config, collection, retry_scheduled=reset))
        return 0

    json_path, markdown_path = _write_collection(config, collection)
    print(f"Wrote collection JSON: {json_path}")
    print(f"Wrote collection Markdown: {markdown_path}")

    report = _summarize(config, collection, retry_scheduled=reset)
    report_path = _write_report(config, collection, report)
    print(f"Wrote report: {report_path}")

    if config.send.telegram.enabled:
        sent = send_telegram_report(config, report)
        print(f"Sent Telegram report in {sent} message(s)")
    else:
        print("Telegram send disabled in config")

    # Only a delivered report moves the cursors, so any failure above makes the
    # next run collect the same range again.
    _advance_cursors(config, collection, reset=reset)
    return 0


def _alert_failure(config: AppConfig, exc: Exception) -> None:
    message = (
        "finance-digest run 실패\n\n"
        f"{type(exc).__name__}: {exc}\n\n"
        "수집 커서를 갱신하지 않았으므로 다음 정기 실행이 같은 구간부터 다시 수집합니다."
    )
    try:
        send_failure_alert(config, message)
    except Exception as alert_exc:
        print(f"error: failure alert could not be sent: {alert_exc}", file=sys.stderr)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="finance-digest")
    subparsers = parser.add_subparsers(dest="command")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default="config.yml", help="Path to YAML config")
    window = argparse.ArgumentParser(add_help=False)
    window.add_argument(
        "--since",
        type=_iso_datetime,
        help="Collect every chat from this ISO time instead of its saved cursor",
    )
    window.add_argument(
        "--until",
        type=_iso_datetime,
        help="Collect up to this past ISO time; the collection state is not reset",
    )

    login_parser = subparsers.add_parser("login", parents=[common])
    login_parser.set_defaults(handler=cmd_login)

    chats_parser = subparsers.add_parser("chats", parents=[common])
    chats_parser.add_argument("--limit", type=int, default=100)
    chats_parser.set_defaults(handler=cmd_chats)

    me_parser = subparsers.add_parser("me", parents=[common])
    me_parser.set_defaults(handler=cmd_me)

    collect_parser = subparsers.add_parser("collect", parents=[common, window])
    collect_parser.add_argument("--dry-run", action="store_true")
    collect_parser.set_defaults(handler=cmd_collect)

    summarize_parser = subparsers.add_parser("summarize", parents=[common])
    summarize_parser.add_argument("--input", help="Collection JSON path")
    summarize_parser.add_argument("--dry-run", action="store_true")
    summarize_parser.set_defaults(handler=cmd_summarize)

    send_parser = subparsers.add_parser("send", parents=[common])
    send_parser.add_argument("--input", help="Report Markdown path (default: latest)")
    send_parser.set_defaults(handler=cmd_send)

    run_parser = subparsers.add_parser("run", parents=[common, window])
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.set_defaults(handler=cmd_run)
    return parser


def _iso_datetime(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid ISO datetime {value!r} (e.g. 2026-09-18T08:00)"
        ) from exc


def _window(config: AppConfig, args: argparse.Namespace) -> CollectionWindow:
    return compute_collection_window(
        timezone_name=config.collection.timezone,
        recent_hours=config.collection.recent_hours,
        monday_weekend=config.collection.monday_weekend,
        chat_keys=[chat.key for chat in config.enabled_chats],
        cursors=read_cursors(config.collection.state_file),
        since=args.since,
        until=args.until,
    )


def _summarize(
    config: AppConfig,
    collection: dict[str, Any],
    *,
    retry_scheduled: bool,
) -> str:
    report = summarize_collection(config, collection)
    return append_failed_chats_notice(report, collection, retry_scheduled=retry_scheduled)


def _advance_cursors(
    config: AppConfig,
    collection: dict[str, Any],
    *,
    reset: bool,
) -> None:
    path = config.collection.state_file
    cursors = read_cursors(path)
    updated = advanced_cursors(cursors, collection, reset=reset)
    if updated == cursors:
        print("Collection state unchanged")
        return
    write_cursors(path, updated)
    print(f"Updated collection state: {path}")


def _write_collection(config: AppConfig, collection: dict[str, Any]) -> tuple[Path, Path]:
    output_dir = config.collection.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    label = collection["window"]["label"]
    json_path = output_dir / f"{label}.json"
    markdown_path = output_dir / f"{label}.md"
    json_path.write_text(
        json.dumps(collection, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path.write_text(format_collection_markdown(collection), encoding="utf-8")
    return json_path, markdown_path


def _write_report(config: AppConfig, collection: dict[str, Any], report: str) -> Path:
    output_dir = config.summary.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    label = collection["window"]["label"]
    report_path = output_dir / f"{label}.md"
    report_path.write_text(report.strip() + "\n", encoding="utf-8")
    return report_path


def _input_path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _latest(directory: Path, pattern: str) -> Path:
    # Most recently written, not label order: a --since window's label starts early.
    files = sorted(directory.glob(pattern), key=lambda path: path.stat().st_mtime)
    if not files:
        raise RuntimeError(f"No {pattern} files found in {directory}")
    return files[-1]


def _print_collection_summary(collection: dict[str, Any]) -> None:
    window = collection["window"]
    print(f"Window: {window['start']} - {window['end']} ({window['timezone']})")
    print(f"Total messages: {collection['total_messages']}")
    for chat in collection["chats"]:
        since = ""
        if chat.get("start") and chat["start"] != window["start"]:
            since = f" (since {chat['start']})"
        if chat.get("error"):
            print(f"- {chat['name']}: FAILED{since}: {chat['error']}")
        else:
            print(f"- {chat['name']}: {chat['message_count']}{since}")


if __name__ == "__main__":
    raise SystemExit(main())
