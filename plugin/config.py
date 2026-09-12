"""Environment-variable configuration (SHUNT_ prefix kept from upstream)."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_MIN_LINES = 350
DEFAULT_TIMEOUT_SECONDS = 180
DEFAULT_MAX_CORPUS_TOKENS = 100_000

# Worker-model context fit: corpus must leave answer headroom. chars/4 is
# upstream shunt's own token heuristic.
_TOKENS_PER_CHAR = 1 / 4


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    try:
        return int(raw) if raw.strip() else default
    except ValueError:
        return default  # upstream's case-arm guard: garbage falls back


@dataclass(frozen=True)
class Config:
    min_lines: int
    timeout_seconds: int
    max_corpus_tokens: int
    worker_model: str | None
    worker_provider: str | None
    disable_hook: bool

    def corpus_tokens(self, text: str) -> int:
        """Estimated tokens for a corpus string (chars/4, upstream heuristic)."""
        return int(len(text) * _TOKENS_PER_CHAR)


def load() -> Config:
    return Config(
        min_lines=_int_env("SHUNT_MIN_LINES", DEFAULT_MIN_LINES),
        timeout_seconds=_int_env("SHUNT_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS),
        max_corpus_tokens=_int_env("SHUNT_MAX_CORPUS_TOKENS", DEFAULT_MAX_CORPUS_TOKENS),
        worker_model=os.environ.get("SHUNT_WORKER_MODEL") or None,
        worker_provider=os.environ.get("SHUNT_WORKER_PROVIDER") or None,
        disable_hook=os.environ.get("SHUNT_DISABLE_HOOK", "") == "1",
    )
