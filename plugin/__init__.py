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
from pathlib import Path

from . import config, delegate, modes, routing, stats

_SKILLS_DIR = Path(__file__).parent / "skills"


def _error(msg: str) -> str:
    return json.dumps({"error": msg})


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
            s = stats.summarize()
            if not s.get("calls"):
                return "No delegations recorded yet."
            per = ", ".join(f"{k}:{v}" for k, v in sorted(s["per_mode"].items()))
            return (
                f"calls: {s['calls']} ({per})\n"
                f"worker tokens spent: {s['worker_tokens_spent']:,}\n"
                f"orchestrator tokens avoided: "
                f"{s['orchestrator_tokens_avoided']:,}"
            )
        return "Usage: /shunt stats"

    ctx.register_command(
        name="shunt",
        handler=_shunt_cmd,
        description="hermes-shunt delegation stats",
        args_hint="[stats]",
    )
