"""Task 5 — eval-matrix port: upstream shunt's 17 Read-hook + 17 Bash-hook
cases, verbatim (IDs, names, expected decisions, reasons), driven through
the WIRED pre_tool_call gate (plugin.shunt_gate) exactly as the runtime
dispatches it — validating pure-function ports AND the hook wiring together.

Source: spotify/portal-ai-plugins plugins/shunt/evals/hook-evals.json and
bash-hook-evals.json (Apache-2.0). Run:
  .venv/bin/python -m pytest tests/test_eval_matrix.py -v
"""

import json
import os
import sys
import unittest
from unittest import mock

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(TESTS_DIR)
FIXTURES = os.path.join(TESTS_DIR, "fixtures")
sys.path.insert(0, REPO)

import plugin  # noqa: E402


def _wired_gate():
    """Build the gate the way the runtime does: register() then pull the
    pre_tool_call hook out of a recording ctx."""
    hooks = {}

    class Ctx:
        def register_hook(self, event, cb):
            hooks[event] = cb

        def register_tool(self, **kw):
            pass

        def register_command(self, *a, **kw):
            pass

        def register_skill(self, *a, **kw):
            pass

    plugin.register(Ctx())
    return hooks["pre_tool_call"]


GATE = _wired_gate()

_READ_EVALS = [
    # (id, name, file, lines, offset, limit, env_min_lines, expected)
    (1, "small-file", "small.txt", 100, None, None, None, "allow"),
    (2, "boundary-exact-350", "boundary.txt", 350, None, None, None, "allow"),
    (3, "just-over-threshold", "over.txt", 351, None, None, None, "block"),
    (4, "large-file", "large.txt", 1200, None, None, None, "block"),
    (5, "very-large-file", "huge.txt", 5000, None, None, None, "block"),
    (6, "empty-file", "empty.txt", 0, None, None, None, "allow"),
    (7, "targeted-read-offset", "large.txt", 1200, 100, None, None, "allow"),
    (8, "targeted-read-limit", "large.txt", 1200, None, 50, None, "allow"),
    (9, "targeted-read-both", "large.txt", 1200, 100, 50, None, "allow"),
    (10, "nonexistent-file", None, None, None, None, None, "allow"),
    (11, "empty-filepath", "", None, None, None, None, "allow"),
    (12, "missing-filepath-field", None, None, None, None, None, "allow"),
    (13, "offset-zero", "large.txt", 1200, 0, None, None, "allow"),
    (14, "limit-zero", "large.txt", 1200, None, 0, None, "allow"),
    (15, "env-override-lower", "medium.txt", 250, None, None, "200", "block"),
    (16, "env-override-higher", "over.txt", 351, None, None, "500", "allow"),
    (17, "env-non-numeric-fallback", "over.txt", 351, None, None, "abc", "block"),
]

_BASH_EVALS = [
    # (id, name, command-template, fixture lines, expected)
    (1, "cat-large-file", "cat {F}/large.txt", 800, "block"),
    (2, "cat-small-file", "cat {F}/small.txt", 100, "allow"),
    (3, "cat-with-flag", "cat -n {F}/large.txt", 800, "block"),
    (4, "head-large-file", "head {F}/large.txt", 800, "block"),
    (5, "head-with-count", "head -100 {F}/large.txt", 800, "block"),
    (6, "tail-large-file", "tail {F}/large.txt", 800, "block"),
    (7, "less-large-file", "less {F}/large.txt", 800, "block"),
    (8, "cat-pipe", "cat {F}/large.txt | grep export", 800, "allow"),
    (9, "cat-redirect", "cat {F}/large.txt > /tmp/out.txt", 800, "allow"),
    (10, "non-read-command", "git status", None, "allow"),
    (11, "grep-command", "grep -n 'export' {F}/large.txt", 800, "allow"),
    (12, "cat-quoted-path", 'cat "{F}/large.txt"', 800, "block"),
    (13, "cat-nonexistent", "cat /tmp/shunt-does-not-exist.txt", None, "allow"),
    (14, "empty-command", "", None, "allow"),
    (15, "missing-command-field", None, None, "allow"),
    (16, "more-large-file", "more {F}/large.txt", 800, "block"),
    (17, "head-n-space-count", "head -n 5 {F}/large.txt", None, "allow"),
]


def _decision(directive):
    if directive is None:
        return "allow"
    if isinstance(directive, dict) and directive.get("action") == "block":
        return "block"
    return "allow"


class TestReadHookMatrix(unittest.TestCase):
    def test_matrix(self):
        for (eid, name, file, lines, offset, limit, env_min, expected) in _READ_EVALS:
            with self.subTest(eval_id=eid, name=name):
                # fixture setup (upstream: per-eval generated files)
                if file is not None:
                    path = os.path.join(FIXTURES, file)
                    if lines is not None and not os.path.exists(path):
                        with open(path, "w") as f:
                            f.write("x\n" * lines)
                    args = {"path": path}
                elif file == "":
                    args = {"path": ""}  # empty-filepath
                else:
                    args = {}  # missing-filepath-field / nonexistent handled below
                if eid == 10:  # nonexistent-file
                    args = {"path": "/tmp/shunt-does-not-exist.txt"}
                if offset is not None:
                    args["offset"] = offset
                if limit is not None:
                    args["limit"] = limit

                env = {}
                if env_min is not None:
                    env["SHUNT_MIN_LINES"] = env_min
                with mock.patch.dict(os.environ, env, clear=False):
                    directive = GATE(tool_name="read_file", args=args)
                self.assertEqual(
                    _decision(directive), expected,
                    f"eval {eid} ({name}): got {_decision(directive)}, "
                    f"want {expected} — {directive}",
                )


class TestBashHookMatrix(unittest.TestCase):
    def test_matrix(self):
        for (eid, name, cmd_tpl, lines, expected) in _BASH_EVALS:
            with self.subTest(eval_id=eid, name=name):
                os.makedirs(FIXTURES, exist_ok=True)
                large = os.path.join(FIXTURES, "large.txt")
                small = os.path.join(FIXTURES, "small.txt")
                if not os.path.exists(large):
                    with open(large, "w") as f:
                        f.write("x\n" * 800)
                if not os.path.exists(small):
                    with open(small, "w") as f:
                        f.write("x\n" * 100)
                if cmd_tpl is None:  # missing-command-field
                    args = {}
                else:
                    cmd = cmd_tpl.replace("{F}", FIXTURES)
                    args = {"command": cmd}
                directive = GATE(tool_name="terminal", args=args)
                self.assertEqual(
                    _decision(directive), expected,
                    f"eval {eid} ({name}): got {_decision(directive)}, "
                    f"want {expected} — cmd={args.get('command')}",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
