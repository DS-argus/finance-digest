from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from telethon import utils


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class TelegramConfig:
    api_id_env: str
    api_hash_env: str
    phone_env: str | None
    session_path: Path


@dataclass(frozen=True)
class ChatConfig:
    name: str
    identifier: str | int
    enabled: bool = True

    @property
    def key(self) -> str:
        """Stable state key: `@name`/`name` and `"-100…"`/`-100…` map to one key."""
        return str(normalize_chat_identifier(self.identifier))


@dataclass(frozen=True)
class CollectionConfig:
    timezone: str
    recent_hours: int
    monday_weekend: bool
    output_dir: Path
    state_file: Path


@dataclass(frozen=True)
class ClaudeConfig:
    command: str
    model: str | None


@dataclass(frozen=True)
class CodexConfig:
    command: str
    model: str | None


@dataclass(frozen=True)
class SummaryConfig:
    output_dir: Path
    provider: str
    prompt_file: Path
    prompt: str
    timeout_seconds: int
    claude: ClaudeConfig
    codex: CodexConfig


@dataclass(frozen=True)
class TelegramSendConfig:
    enabled: bool
    bot_token_env: str
    delivery_chat_id_env: str
    disable_web_page_preview: bool
    max_chars: int


@dataclass(frozen=True)
class SendConfig:
    telegram: TelegramSendConfig


@dataclass(frozen=True)
class AppConfig:
    path: Path
    telegram: TelegramConfig
    chats: list[ChatConfig]
    collection: CollectionConfig
    summary: SummaryConfig
    send: SendConfig

    @property
    def enabled_chats(self) -> list[ChatConfig]:
        return [chat for chat in self.chats if chat.enabled]

    def required_env(self, name: str) -> str:
        value = os.environ.get(name)
        if value is None or value == "":
            raise ConfigError(f"Missing required environment variable: {name}")
        return value

    def telegram_api_id(self) -> int:
        raw = self.required_env(self.telegram.api_id_env)
        try:
            return int(raw)
        except ValueError as exc:
            raise ConfigError(
                f"{self.telegram.api_id_env} must be an integer Telegram API ID"
            ) from exc

    def telegram_api_hash(self) -> str:
        return self.required_env(self.telegram.api_hash_env)

    def telegram_phone(self) -> str | None:
        if not self.telegram.phone_env:
            return None
        phone = os.environ.get(self.telegram.phone_env) or None
        if phone is None:
            return None
        return utils.parse_phone(phone) or phone

    def bot_token(self) -> str:
        return self.required_env(self.send.telegram.bot_token_env)

    def delivery_chat_id(self) -> str:
        return self.required_env(self.send.telegram.delivery_chat_id_env)


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).expanduser().resolve()
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError("Config root must be a YAML mapping")

    env_file = raw.get("env_file")
    if env_file:
        load_env_file(_resolve_path(config_path.parent, str(env_file)))

    telegram_raw = _mapping(raw.get("telegram"), "telegram")
    collection_raw = _mapping(raw.get("collection", {}), "collection")
    summary_raw = _mapping(raw.get("summary", {}), "summary")
    provider = str(summary_raw.get("provider", "claude")).strip().lower()
    if provider not in {"claude", "codex"}:
        raise ConfigError("summary.provider must be either 'claude' or 'codex'")
    claude_raw = _mapping(summary_raw.get("claude", {}), "summary.claude")
    codex_raw = _mapping(summary_raw.get("codex", {}), "summary.codex")
    send_raw = _mapping(raw.get("send", {}), "send")
    send_telegram_raw = _mapping(send_raw.get("telegram", {}), "send.telegram")

    chats_raw = raw.get("chats", [])
    if not isinstance(chats_raw, list):
        raise ConfigError("chats must be a YAML list")

    chats = [_parse_chat(item, index) for index, item in enumerate(chats_raw, start=1)]

    prompt_file = _resolve_path(
        config_path.parent, str(summary_raw.get("prompt_file", "summary_prompt.yml"))
    )

    return AppConfig(
        path=config_path,
        telegram=TelegramConfig(
            api_id_env=str(telegram_raw.get("api_id_env", "TELEGRAM_API_ID")),
            api_hash_env=str(telegram_raw.get("api_hash_env", "TELEGRAM_API_HASH")),
            phone_env=_optional_str(telegram_raw.get("phone_env", "TELEGRAM_PHONE")),
            session_path=_resolve_path(
                config_path.parent, str(telegram_raw.get("session_path", "data/telegram"))
            ),
        ),
        chats=chats,
        collection=CollectionConfig(
            timezone=str(collection_raw.get("timezone", "Asia/Seoul")),
            recent_hours=int(collection_raw.get("recent_hours", 24)),
            monday_weekend=bool(collection_raw.get("monday_weekend", True)),
            output_dir=_resolve_path(
                config_path.parent, str(collection_raw.get("output_dir", "data/collections"))
            ),
            state_file=_resolve_path(
                config_path.parent, str(collection_raw.get("state_file", "data/state.json"))
            ),
        ),
        summary=SummaryConfig(
            output_dir=_resolve_path(
                config_path.parent, str(summary_raw.get("output_dir", "data/reports"))
            ),
            provider=provider,
            prompt_file=prompt_file,
            prompt=_load_prompt_file(prompt_file),
            timeout_seconds=_positive_int(
                summary_raw.get("timeout_seconds", 600), "summary.timeout_seconds"
            ),
            claude=ClaudeConfig(
                command=str(claude_raw.get("command", "claude")),
                model=_optional_str(claude_raw.get("model")),
            ),
            codex=CodexConfig(
                command=str(codex_raw.get("command", "codex")),
                model=_optional_str(codex_raw.get("model")),
            ),
        ),
        send=SendConfig(
            telegram=TelegramSendConfig(
                enabled=bool(send_telegram_raw.get("enabled", False)),
                bot_token_env=str(
                    send_telegram_raw.get("bot_token_env", "TELEGRAM_BOT_TOKEN")
                ),
                delivery_chat_id_env=str(
                    send_telegram_raw.get(
                        "delivery_chat_id_env", "TELEGRAM_DELIVERY_CHAT_ID"
                    )
                ),
                disable_web_page_preview=bool(
                    send_telegram_raw.get("disable_web_page_preview", True)
                ),
                max_chars=int(send_telegram_raw.get("max_chars", 3900)),
            )
        ),
    )


def load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = _strip_env_value(value.strip())
        if key and key not in os.environ:
            os.environ[key] = value


def normalize_chat_identifier(identifier: str | int) -> str | int:
    if isinstance(identifier, int):
        return identifier
    stripped = identifier.strip()
    if stripped.startswith("@"):
        return stripped[1:]
    if stripped.lstrip("-").isdigit():
        return int(stripped)
    return stripped


def _parse_chat(item: Any, index: int) -> ChatConfig:
    if not isinstance(item, dict):
        raise ConfigError(f"chats[{index}] must be a mapping")
    if "identifier" not in item:
        raise ConfigError(f"chats[{index}].identifier is required")
    identifier = item["identifier"]
    if not isinstance(identifier, int | str):
        raise ConfigError(f"chats[{index}].identifier must be a string or integer")
    name = str(item.get("name") or identifier)
    return ChatConfig(
        name=name,
        identifier=identifier,
        enabled=bool(item.get("enabled", True)),
    )


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a YAML mapping")
    return value


def _resolve_path(base_dir: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _positive_int(value: Any, name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be a positive integer") from exc
    if number <= 0:
        raise ConfigError(f"{name} must be a positive integer")
    return number


def _required_text(value: Any, name: str) -> str:
    text = _optional_str(value)
    if text is None:
        raise ConfigError(f"{name} is required")
    return text


def _load_prompt_file(path: Path) -> str:
    if not path.exists():
        raise ConfigError(f"Summary prompt file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"Summary prompt file must be a YAML mapping: {path}")
    return _required_text(raw.get("prompt"), f"{path}: prompt")


def _strip_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
