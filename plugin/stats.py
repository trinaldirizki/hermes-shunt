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
        return {"calls": 0, "worker_tokens_spent": 0,
                "orchestrator_tokens_avoided": 0, "per_mode": {}}
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


def summarize_window(hours: int = 168) -> dict:
    """Aggregate stats within the last `hours` hours (default 7 days)."""
    import datetime as _dt
    cutoff = _dt.datetime.now() - _dt.timedelta(hours=hours)
    path = stats_path()
    if not os.path.isfile(path):
        return {"calls": 0, "worker_tokens_spent": 0,
                "orchestrator_tokens_avoided": 0, "per_mode": {}}
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
            try:
                ts = _dt.datetime.strptime(e.get("ts", ""), "%Y-%m-%dT%H:%M:%S")
            except ValueError:
                continue
            if ts < cutoff:
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


def format_report() -> str:
    """Human-readable all-time + 7-day report for /shunt stats."""
    all_t = summarize()
    week = summarize_window(168)
    if not all_t["calls"]:
        return "No delegations recorded yet."
    ratio_all = (all_t["orchestrator_tokens_avoided"] / all_t["worker_tokens_spent"]
                 if all_t["worker_tokens_spent"] else 0)
    ratio_wk = (week["orchestrator_tokens_avoided"] / week["worker_tokens_spent"]
                if week["worker_tokens_spent"] else 0)
    per_all = ", ".join(f"{k}:{v}" for k, v in sorted(all_t["per_mode"].items()))
    per_wk = ", ".join(f"{k}:{v}" for k, v in sorted(week["per_mode"].items()))
    lines = [
        f"all-time: {all_t['calls']} delegations ({per_all})",
        f"  worker spent: {all_t['worker_tokens_spent']:,} | "
        f"orchestrator avoided: {all_t['orchestrator_tokens_avoided']:,} "
        f"(ratio {ratio_all:.2f})",
        f"7-day:  {week['calls']} delegations ({per_wk or 'none'})",
        f"  worker spent: {week['worker_tokens_spent']:,} | "
        f"orchestrator avoided: {week['orchestrator_tokens_avoided']:,} "
        f"(ratio {ratio_wk:.2f})",
    ]
    return "\n".join(lines)
