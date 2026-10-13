from __future__ import annotations

import os
import re

from finance_digest.config import AppConfig

# Short values such as the numeric API id or chat ids legitimately appear in
# finance text; only long credential strings are worth matching.
_MIN_SECRET_LENGTH = 16


class SecretLeakError(RuntimeError):
    pass


def secret_values(config: AppConfig) -> list[tuple[str, str]]:
    """Return (env name, value) pairs for credentials that must never leave the host."""
    api_hash_env = config.telegram.api_hash_env
    token_env = config.send.telegram.bot_token_env
    token = os.environ.get(token_env, "").strip()
    pairs = [
        (api_hash_env, os.environ.get(api_hash_env, "").strip()),
        # In "<bot id>:<secret>" only the part after the colon is secret.
        (token_env, token.split(":", 1)[1] if ":" in token else token),
    ]
    return [(name, value) for name, value in pairs if len(value) >= _MIN_SECRET_LENGTH]


def find_leaked_secrets(config: AppConfig, text: str) -> list[str]:
    folded = text.casefold()
    return [name for name, value in secret_values(config) if value.casefold() in folded]


def ensure_no_secrets(config: AppConfig, text: str) -> None:
    leaked = find_leaked_secrets(config, text)
    if leaked:
        raise SecretLeakError(
            "Refusing to send: the text contains the value of " + ", ".join(leaked)
        )


def redact_secrets(config: AppConfig, text: str) -> str:
    for name, value in secret_values(config):
        text = re.sub(re.escape(value), f"<{name}>", text, flags=re.IGNORECASE)
    return text
