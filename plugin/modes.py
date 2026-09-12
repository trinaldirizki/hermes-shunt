"""Worker modes — the AiKA mode definitions, ported as local constants.

An AiKA mode is just {instructions, temperature}; shunt ships two. The
instruction texts below are copied verbatim from the shunt README
(spotify/portal-ai-plugins, Apache-2.0) — see NOTICE.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Mode:
    name: str
    system: str
    temperature: float = 0.2


BULK_READER_INSTRUCTIONS = (
    "You are a precise code analyst. Read the provided files and answer the "
    "question concisely. Output structured bullets only. No greetings, no "
    "prose, no preambles, no summaries. Lead every bullet with the exact "
    "name, type, or line number. Use nested bullets for details. Skip "
    "anything the caller did not ask for."
)

CODE_WRITER_INSTRUCTIONS = (
    "You generate code files based on a spec and reference files. Match the "
    "existing patterns, conventions, naming, and style exactly. Output only "
    "the code — no explanations, no markdown fences unless asked. If the "
    "spec is ambiguous, make reasonable choices that match the patterns in "
    "the reference code."
)

BULK_READER_MODE = Mode(name="bulk-reader", system=BULK_READER_INSTRUCTIONS)
CODE_WRITER_MODE = Mode(name="code-writer", system=CODE_WRITER_INSTRUCTIONS)
