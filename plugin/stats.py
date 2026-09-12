"""Append-only JSONL stats + in-memory counters for /shunt stats."""

from __future__ import annotations

import json
import os
import time

STATS_FILE = os.path.expanduser("~/.hermes/plugins/hermes-shunt/stats.jsonl")


def record_call(mode: str, model: str, prompt_tokens: int, completion_tokens: int, corpus_tokens: int) -> None:
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "mode": mode,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "corpus_tokens": corpus_tokens,  # orchestrator tokens avoided
    }
    os.makedirs(os.path.dirname(STATS_FILE), exist_ok=True)
    with open(STATS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def summarize() -> dict:
    """Aggregate stats.jsonl into a summary dict; empty if no file."""
    if not os.path.isfile(STATS_FILE):
        return {"calls": 0}
    calls = 0
    spent = avoided = 0
    per_mode: dict[str, int] = {}
    with open(STATS_FILE, encoding="utf-8") as f:
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
            per_mode[e.get("mode", "?")] = per_mode.get(e.get("mode", "?"), 0) + 1
    return {"calls": calls, "worker_tokens_spent": spent, "orchestrator_tokens_avoided": avoided, "per_mode": per_mode}
