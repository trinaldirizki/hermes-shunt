"""R2: per-session cumulative read tracking for the cumulative-read gate.

Tracks how many lines of each file a session has already pulled via targeted
(offset/limit) reads. When the cumulative total for a file exceeds the
threshold, further targeted reads of that file are blocked with a
bulk_read-redirect message — closing the pagination bypass for whole-file
comprehension while surgical excerpts stay cheap.

In-process only (state dies with the host). Session key = session_id when
provided, else "" (best effort).
"""

from __future__ import annotations

import threading
from typing import Dict

_lock = threading.Lock()
_state: Dict[str, Dict[str, int]] = {}


def _key(session_id) -> str:
    return session_id or ""


def accumulate(session_id, path: str, lines: int) -> None:
    """Add `lines` read lines for `path` under this session."""
    if lines <= 0:
        return
    with _lock:
        sess = _state.setdefault(_key(session_id), {})
        sess[path] = sess.get(path, 0) + lines


def cumulative(session_id, path: str) -> int:
    """Lines already read for `path` in this session."""
    with _lock:
        return _state.get(_key(session_id), {}).get(path, 0)


def state() -> Dict[str, Dict[str, int]]:
    """Read-only view (tests)."""
    with _lock:
        return {k: dict(v) for k, v in _state.items()}


def reset() -> None:
    """Clear all state (tests)."""
    with _lock:
        _state.clear()
