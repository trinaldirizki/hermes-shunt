"""Append-only JSONL stats + in-memory counters for /shunt stats.

Stats live OUTSIDE the plugin package: the package dir is a symlink into
the git repo, and stats must never pollute the working tree. Default:
~/.hermes/hermes-shunt-stats.jsonl (host-owned data location).
Override for tests via SHUNT_STATS_FILE.
"""

from __future__ import annotations

import json
import os
import time

_DEFAULT_PATH = os.path.join(
    os.path.expanduser("~/.hermes"), "hermes-shunt-stats.jsonl"
)


def stats_path() -> str:
    return os.environ.get("SHUNT_STATS_FILE") or _DEFAULT_PATH


def record_call(mode: str, model: str, prompt_tokens: int,
                completion_tokens: int, corpus_tokens: int) -> None:
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "mode": mode,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "corpus_tokens": corpus_tokens,  # orchestrator tokens avoided
    }
    path = stats_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def summarize() -> dict:
    """Aggregate stats.jsonl into a summary dict; empty if no file."""
    path = stats_path()
    if not os.path.isfile(path):
        return {"calls": 0}
    calls = 0
    spent = avoided = 0
    per_mode: dict[str, int] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            calls += 1
            spent += e.get("prompt_tokens", 0) + e.get("completion_tokens", 0)
            avoided += e.get("corpus_tokens", 0)
            m = e.get("mode", "?")
            per_mode[m] = per_mode.get(m, 0) + 1
    return {
        "calls": calls,
        "worker_tokens_spent": spent,
        "orchestrator_tokens_avoided": avoided,
        "per_mode": per_mode,
    }
