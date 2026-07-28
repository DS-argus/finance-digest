from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from finance_digest.config import AppConfig, ConfigError, load_config
from finance_digest.sender import send_telegram_report
from finance_digest.summarizer import (
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
    window = _window(config)
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
    collection_path = Path(args.input).expanduser().resolve() if args.input else _latest_collection(config)
    collection = read_collection(collection_path)
    report = summarize_collection(config, collection)

    if args.dry_run:
        print(report)
        return 0

    report_path = _write_report(config, collection, report)
    print(f"Wrote report: {report_path}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    window = _window(config)
    collection = asyncio.run(collect_messages(config, window))
    _print_collection_summary(collection)

    if args.dry_run:
        report = summarize_collection(config, collection)
        print()
        print(report)
        return 0

    json_path, markdown_path = _write_collection(config, collection)
    print(f"Wrote collection JSON: {json_path}")
    print(f"Wrote collection Markdown: {markdown_path}")

    report = summarize_collection(config, collection)
    report_path = _write_report(config, collection, report)
    print(f"Wrote report: {report_path}")

    if config.send.telegram.enabled:
        sent = send_telegram_report(config, report)
        print(f"Sent Telegram report in {sent} message(s)")
    else:
        print("Telegram send disabled in config")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="finance-digest")
    subparsers = parser.add_subparsers(dest="command")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default="config.yml", help="Path to YAML config")

    login_parser = subparsers.add_parser("login", parents=[common])
    login_parser.set_defaults(handler=cmd_login)

    chats_parser = subparsers.add_parser("chats", parents=[common])
    chats_parser.add_argument("--limit", type=int, default=100)
    chats_parser.set_defaults(handler=cmd_chats)

    me_parser = subparsers.add_parser("me", parents=[common])
    me_parser.set_defaults(handler=cmd_me)

    collect_parser = subparsers.add_parser("collect", parents=[common])
    collect_parser.add_argument("--dry-run", action="store_true")
    collect_parser.set_defaults(handler=cmd_collect)

    summarize_parser = subparsers.add_parser("summarize", parents=[common])
    summarize_parser.add_argument("--input", help="Collection JSON path")
    summarize_parser.add_argument("--dry-run", action="store_true")
    summarize_parser.set_defaults(handler=cmd_summarize)

    run_parser = subparsers.add_parser("run", parents=[common])
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.set_defaults(handler=cmd_run)
    return parser


def _window(config: AppConfig) -> CollectionWindow:
    return compute_collection_window(
        timezone_name=config.collection.timezone,
        recent_hours=config.collection.recent_hours,
        monday_weekend=config.collection.monday_weekend,
    )


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


def _latest_collection(config: AppConfig) -> Path:
    files = sorted(config.collection.output_dir.glob("*.json"))
    if not files:
        raise RuntimeError(
            f"No collection JSON files found in {config.collection.output_dir}"
        )
    return files[-1]


def _print_collection_summary(collection: dict[str, Any]) -> None:
    window = collection["window"]
    print(f"Window: {window['start']} - {window['end']} ({window['timezone']})")
    print(f"Total messages: {collection['total_messages']}")
    for chat in collection["chats"]:
        print(f"- {chat['name']}: {chat['message_count']}")


if __name__ == "__main__":
    raise SystemExit(main())
