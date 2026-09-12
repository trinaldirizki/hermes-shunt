"""Routing gate — port of shunt's hooks/check-file-size and hooks/check-bash-read.

Pure functions; no I/O except os.path.isfile / line counting on real paths.
Semantics ported from spotify/portal-ai-plugins (Apache-2.0):

- targeted reads (offset or limit set) are allowed — editing needs exact content
- nonexistent paths are allowed through — the read tool's own error is the signal
- pipes (cat f | grep x) and redirections (cat f > out) are allowed — targeted /
  no-context-into-model reads
- flag stripping (head -100 f -> f), then an isfile check
- upgrade over upstream: quoted operands (cat "my file.ts" -> my file.ts)
"""

from __future__ import annotations

import os
import shlex

READ_CMDS = ("cat", "head", "tail", "less", "more")


def _count_lines(path: str) -> int:
    try:
        with open(path, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def line_count(path: str) -> int:
    """Public so tests can use the same counting the gate uses."""
    return _count_lines(path)


def should_block_read(path, offset, limit, min_lines: int):
    """Return (blocked, message). Ports hooks/check-file-size."""
    if offset or limit:
        return False, ""
    if not path or not os.path.isfile(path):
        return False, ""
    lines = _count_lines(path)
    if lines <= min_lines:
        return False, ""
    msg = (
        f"File is {lines:,} lines (threshold: {min_lines}). "
        "Call the bulk_read tool with a question and this path instead of "
        "reading it directly. If you need exact content for an edit, re-read "
        "with offset/limit for just the section."
    )
    return True, msg


def _first_operand(args: list[str]):
    for a in args:
        if a.startswith("-"):
            continue
        return a
    return None


def extract_bash_read_target(command: str):
    """Return the file operand of a bare read command, else None.

    Ports hooks/check-bash-read: None for pipes, redirections, non-read
    commands, and commands with no non-flag operand.
    """
    if not command:
        return None
    if "|" in command or ">" in command:
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        # unbalanced quotes — treat as unparseable, allow through
        return None
    if not tokens or tokens[0] not in READ_CMDS:
        return None
    operand = _first_operand(tokens[1:])
    return operand or None


def bash_read_block(command: str, min_lines: int):
    """Convenience: (blocked, message) for a terminal command, if it is a
    bare large-file read. Ports check-bash-read's decision logic."""
    target = extract_bash_read_target(command)
    if target is None:
        return False, ""
    return should_block_read(target, None, None, min_lines)
