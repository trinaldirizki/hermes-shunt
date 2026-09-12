"""Delegation core — one ctx.llm.complete() per delegation.

The corpus goes to the worker model and never enters the orchestrator
conversation. Task 3 adds retry/usage accounting; Task 1 ships the shape.
"""

from __future__ import annotations

import os

from .config import Config
from .modes import Mode


def assemble_bulk_message(question: str, paths: list[str]) -> str:
    """Corpus assembly, upstream format: <file> blocks + trailing Question."""
    parts = []
    for p in paths:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        parts.append(f'<file path="{p}">\n{content}\n</file>\n')
    parts.append(f"Question: {question}\n")
    return "\n".join(parts)


def assemble_write_message(spec: str, reference_path: str) -> str:
    """Spec + reference format from upstream's code-write."""
    with open(reference_path, "r", encoding="utf-8", errors="replace") as f:
        reference = f.read()
    return f"Spec: {spec}\n\nReference:\n{reference}\n"


def validate_paths(paths: list[str]) -> None:
    """Fail loud before any LLM call — a typo'd path must not become a
    confident answer about nothing (upstream doctrine)."""
    for p in paths:
        if not os.path.isfile(p) or not os.access(p, os.R_OK):
            raise FileNotFoundError(f"file not found or unreadable: {p}")


def delegate(ctx, cfg: Config, mode: Mode, message: str, purpose: str) -> str:
    """One ctx.llm.complete() per delegation (wired with retry in Task 3)."""
    result = ctx.llm.complete(
        messages=[
            {"role": "system", "content": mode.system},
            {"role": "user", "content": message},
        ],
        temperature=mode.temperature,
        timeout=cfg.timeout_seconds,
        model=cfg.worker_model,
        provider=cfg.worker_provider,
        purpose=purpose,
    )
    return result.text
