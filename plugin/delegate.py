"""Delegation core — one ctx.llm.complete() per delegation.

The corpus goes to the worker model and never enters the orchestrator
conversation. Retry discipline (empty replies, z.ai quirk): up to 3 attempts
with exponential backoff. Errors fail loud with actionable guidance.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

from .config import Config
from .modes import Mode

MAX_ATTEMPTS = 3
_BACKOFF_S = (0.5, 1.0)  # sleeps between attempts 1→2 and 2→3


class DelegationError(RuntimeError):
    """A delegation call failed after retries, with remediation guidance."""


class CorpusTooLargeError(RuntimeError):
    """Assembled corpus exceeds the worker-model context budget."""


@dataclass(frozen=True)
class DelegateOutcome:
    text: str
    model: str
    provider: str
    prompt_tokens: int
    completion_tokens: int
    corpus_tokens: int  # orchestrator tokens avoided (chars/4 of the corpus)


def assemble_bulk_message(question: str, paths: list[str]) -> str:
    """Corpus assembly, upstream format: <file> blocks + trailing Question."""
    parts = []
    for p in paths:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        # upstream-exact: cat emits the file verbatim (trailing \n included),
        # then </file> follows directly — no inserted blank line
        parts.append(f'<file path="{p}">\n{content}</file>\n')
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


def check_corpus_limit(message: str, cfg: Config) -> int:
    """Token-estimate guard (chars/4): refuse corpora that can't fit the
    worker model's context window. Returns the estimate."""
    est = len(message) // 4
    if est > cfg.max_corpus_tokens:
        raise CorpusTooLargeError(
            f"corpus ≈{est} tokens exceeds the worker limit "
            f"({cfg.max_corpus_tokens}). split the batch into smaller calls "
            "or raise SHUNT_MAX_CORPUS_TOKENS if the worker has headroom."
        )
    return est


def _usage_pair(usage) -> tuple[int, int]:
    """Extract (input, output) token counts from a usage object.

    Real Hermes shape: PluginLlmUsage with input_tokens/output_tokens
    (agent/plugin_llm.py _extract_usage). Dicts accepted for tests and
    OpenAI-shaped payloads (prompt_tokens/completion_tokens).
    """
    if usage is None:
        return 0, 0
    if isinstance(usage, dict):
        inp = usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0)
        out = usage.get("completion_tokens", 0) or usage.get("output_tokens", 0)
        return int(inp), int(out)
    inp = (getattr(usage, "input_tokens", None)
           or getattr(usage, "prompt_tokens", None) or 0)
    out = (getattr(usage, "output_tokens", None)
           or getattr(usage, "completion_tokens", None) or 0)
    return int(inp), int(out)


def delegate(ctx, cfg: Config, mode: Mode, message: str, purpose: str,
             sleep=time.sleep) -> DelegateOutcome:
    """One logical delegation: up to MAX_ATTEMPTS ctx.llm.complete() calls.

    - Empty replies retry (z.ai quirk); other exceptions fail immediately.
    - TimeoutError gets timeout-specific guidance.
    - Records usage to stats after success.
    """
    check_corpus_limit(message, cfg)

    last_empty = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
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
        except TimeoutError as e:
            raise DelegationError(
                f"delegation exceeded {cfg.timeout_seconds}s — raise "
                "SHUNT_TIMEOUT_SECONDS or split the work into smaller calls."
            ) from e
        except Exception as e:  # non-retryable: fail loud, no retry loop
            raise DelegationError(f"delegation failed: {e}") from e

        text = (result.text or "").strip()
        if text:
            prompt_tok, completion_tok = _usage_pair(getattr(result, "usage", None))
            outcome = DelegateOutcome(
                text=text,
                model=getattr(result, "model", "?") or "?",
                provider=getattr(result, "provider", "?") or "?",
                prompt_tokens=prompt_tok,
                completion_tokens=completion_tok,
                corpus_tokens=len(message) // 4,
            )
            from . import stats
            stats.record_call(mode.name, outcome.model,
                              outcome.prompt_tokens, outcome.completion_tokens,
                              outcome.corpus_tokens)
            return outcome

        last_empty = attempt
        if attempt < MAX_ATTEMPTS:
            sleep(_BACKOFF_S[attempt - 1])

    raise DelegationError(
        f"delegation returned empty replies on all {MAX_ATTEMPTS} attempts "
        f"(last at attempt {last_empty}). The worker model produced no text — "
        "retry the call, or switch/route the worker model."
    )


def strip_fences(text: str) -> str:
    """Remove markdown code fences — upstream sed semantic: drop every line
    that is exactly a fence (```lang ... ```)."""
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith("```")]
    return "\n".join(lines).strip()


def footer(outcome: DelegateOutcome, mode: Mode) -> str:
    """Usage footer appended to tool results (upstream convention)."""
    return (f"[hermes-shunt: ~{outcome.corpus_tokens} corpus tokens | "
            f"delegated to {mode.name}@{outcome.model}]")
