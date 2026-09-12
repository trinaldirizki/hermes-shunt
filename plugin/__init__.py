"""hermes-shunt — delegate I/O-heavy work to one-shot worker LLM calls.

Hermes port of Spotify's shunt Claude Code plugin (Apache-2.0).
Routing semantics, allow-rules, and mode instruction texts ported from
spotify/portal-ai-plugins plugins/shunt — see NOTICE.

Layers (upstream structure):
  1. pre_tool_call gate  — blocks oversized full reads, redirects to bulk_read
  2. bulk_read/code_write tools — one ctx.llm.complete() per delegation
  3. skills              — soft guidance on when to delegate
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import config, delegate, modes, readstate, routing, stats

_SKILLS_DIR = Path(__file__).parent / "skills"

_PRIMER = (
    "hermes-shunt is active: for any file over ~350 lines, do not read or "
    "paginate it — call the bulk_read tool with a question and the path(s); "
    "a worker model reads the files and returns a cited answer without the "
    "file entering your context. Use targeted offset/limit reads only for "
    "exact lines you need to edit."
)


def _error(msg: str) -> str:
    return json.dumps({"error": msg})


def _cumulative_on() -> bool:
    return os.environ.get("SHUNT_CUMULATIVE_GATE", "1") != "0"


def _targeted_est_lines(path, offset, limit) -> int | None:
    """Estimate how many lines a targeted read pulls. None = untracked."""
    try:
        off = int(offset) if offset not in (None, "") else 0
        lim = int(limit) if limit not in (None, "") else None
    except (TypeError, ValueError):
        return None
    if lim == 0:
        return None  # limit=0: nothing read
    total = routing.line_count(path)
    if lim is None:
        return max(0, min(2000, total - off))  # read_file default limit 2000
    return max(0, min(lim, total - off))


def register(ctx):
    # ------------------------------------------------ layer 1: the gate
    #
    # read_file note: Hermes' read_file defaults limit=2000 when omitted, but
    # this hook sees the model's RAW args — absent offset/limit means the
    # model asked for a full read. An explicit limit is a targeted read and
    # passes (upstream parity: check-file-size allows offset/limit reads).
    def shunt_gate(tool_name, args, **kwargs):
        cfg = config.load()  # per call: kill-switch flips live, no restart
        if cfg.disable_hook:
            return None
        if tool_name == "read_file":
            path = args.get("path")
            targeted = (args.get("offset") is not None and args.get("offset") != "") or \
                       (args.get("limit") is not None and args.get("limit") != "")
            if targeted and os.path.isfile(path or ""):
                # R2 cumulative gate: accumulate + check before deciding
                est = _targeted_est_lines(path, args.get("offset"), args.get("limit"))
                prior = readstate.cumulative(kwargs.get("session_id"),
                                             os.path.abspath(path))
                if est is not None:
                    readstate.accumulate(kwargs.get("session_id"),
                                         os.path.abspath(path), est)
                # R2: block only when PRIOR cumulative reads exceed the
                # threshold — any single targeted read passes (upstream
                # semantics, eval cases 7-9); pagination beyond
                # threshold+chunk is what gets stopped.
                if prior > cfg.min_lines and _cumulative_on():
                    lines_total = routing.line_count(path)
                    return {"action": "block", "message": (
                        f"Cumulative targeted reads of this file now exceed "
                        f"{cfg.min_lines} lines (already read ~{prior}, this "
                        f"chunk ~{est or 0}, file is {lines_total:,} lines). "
                        "Do not paginate this file further. Call the "
                        "bulk_read tool with a question and this path — the "
                        "worker reads it and returns a cited answer. Use "
                        "offset/limit only for the exact lines you need to edit."
                    )}
                return None  # first/under-threshold targeted read: allow
            blocked, reason = routing.should_block_read(
                args.get("path"), args.get("offset"), args.get("limit"),
                cfg.min_lines,
            )
            if blocked:
                return {"action": "block", "message": reason}
        elif tool_name == "terminal":
            blocked, reason = routing.bash_read_block(
                args.get("command") or "", cfg.min_lines
            )
            if blocked:
                return {"action": "block", "message": reason}
        return None

    ctx.register_hook("pre_tool_call", shunt_gate)

    # ------------------------------------------------ layer 0: R3 primer
    def shunt_primer(session_id, user_message, is_first_turn, **kwargs):
        if is_first_turn:
            return {"context": _PRIMER}
        return None

    ctx.register_hook("pre_llm_call", shunt_primer)

    # ------------------------------------------------ layer 2: tools
    _bulk_schema = {
        "name": "bulk_read",
        "description": (
            "Delegate bulk file reading to a worker model. Use for files "
            ">350 lines or questions spanning 3+ files. Files go to the "
            "worker, never into your context. Verify exact line numbers or "
            "values before using them in edits."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "Question to answer about the files",
                },
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "File paths to read",
                },
            },
            "required": ["question", "paths"],
        },
    }

    def _bulk_read(params, **kwargs):
        question = (params.get("question") or "").strip()
        paths = params.get("paths") or []
        if isinstance(paths, str):
            paths = [paths]
        if not question:
            return _error("question is required")
        if not paths:
            return _error("paths is required (one or more files)")
        try:
            delegate.validate_paths(paths)
            message = delegate.assemble_bulk_message(question, paths)
            outcome = delegate.delegate(
                ctx, config.load(), modes.BULK_READER_MODE, message,
                purpose="hermes-shunt.bulk-read",
            )
            return (
                f"{outcome.text}\n\n"
                f"{delegate.footer(outcome, modes.BULK_READER_MODE)}"
            )
        except FileNotFoundError as e:
            return _error(str(e))
        except delegate.CorpusTooLargeError as e:
            return _error(str(e))
        except delegate.DelegationError as e:
            return _error(str(e))

    ctx.register_tool(
        name="bulk_read", toolset="hermes_shunt",
        schema=_bulk_schema, handler=_bulk_read,
    )

    _write_schema = {
        "name": "code_write",
        "description": (
            "Delegate boilerplate code generation to a worker model. Use for "
            "tests, config, docstrings, type stubs, or any generation where "
            ">80% is predictable from a reference file."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "spec": {
                    "type": "string",
                    "description": "What to generate",
                },
                "reference": {
                    "type": "string",
                    "description": (
                        "File whose patterns the output must match (required — "
                        "a bare spec generates context-free code that fits "
                        "nothing in the project)"
                    ),
                },
                "target": {
                    "type": "string",
                    "description": "Output path; omit to return code as text",
                },
            },
            "required": ["spec", "reference"],
        },
    }

    def _code_write(params, **kwargs):
        spec = (params.get("spec") or "").strip()
        reference = params.get("reference") or ""
        target = params.get("target") or ""
        if not spec:
            return _error("spec is required")
        if not reference:
            return _error(
                "reference is required — pass a file whose patterns the "
                "output should match. For a follow-up, reference the file "
                "the previous call just generated."
            )
        try:
            delegate.validate_paths([reference])
            message = delegate.assemble_write_message(spec, reference)
            outcome = delegate.delegate(
                ctx, config.load(), modes.CODE_WRITER_MODE, message,
                purpose="hermes-shunt.code-write",
            )
            clean = delegate.strip_fences(outcome.text)
            if target:
                parent = Path(target).parent
                if str(parent):
                    parent.mkdir(parents=True, exist_ok=True)
                Path(target).write_text(clean, encoding="utf-8")
                return (
                    f"Wrote {len(clean.splitlines())} lines to {target}\n\n"
                    f"{delegate.footer(outcome, modes.CODE_WRITER_MODE)}"
                )
            return (
                f"{clean}\n\n"
                f"{delegate.footer(outcome, modes.CODE_WRITER_MODE)}"
            )
        except FileNotFoundError as e:
            return _error(str(e))
        except delegate.CorpusTooLargeError as e:
            return _error(str(e))
        except delegate.DelegationError as e:
            return _error(str(e))
        except OSError as e:
            return _error(f"could not write target: {e}")

    ctx.register_tool(
        name="code_write", toolset="hermes_shunt",
        schema=_write_schema, handler=_code_write,
    )

    # ------------------------------------------------ layer 3: skills
    ctx.register_skill(
        "bulk-reader", _SKILLS_DIR / "bulk-reader"
    )
    ctx.register_skill(
        "code-writer", _SKILLS_DIR / "code-writer"
    )

    # ------------------------------------------------ /shunt command
    def _shunt_cmd(raw):
        arg = (raw or "").strip()
        if arg in ("", "stats"):
            return stats.format_report()
        return "Usage: /shunt stats"

    ctx.register_command(
        name="shunt",
        handler=_shunt_cmd,
        description="hermes-shunt delegation stats",
        args_hint="[stats]",
    )
