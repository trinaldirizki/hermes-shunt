"""hermes-shunt — delegate I/O-heavy work to one-shot worker LLM calls.

Hermes port of Spotify's shunt Claude Code plugin (Apache-2.0).
Routing semantics, allow-rules, and mode instruction texts ported from
spotify/portal-ai-plugins plugins/shunt — see NOTICE.

Task 1 scaffold: registers nothing yet; wiring lands in Task 4.
"""

from . import config


def register(ctx):
    """Plugin entry point. Task 4 wires the hook + tools + skills here."""
    cfg = config.load()
    _ = cfg  # consumed by shunt_gate/handlers in Task 4
