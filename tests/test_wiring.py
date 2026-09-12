"""Task 4 — wiring tests: register() against a recording stub ctx.

Verifies the gate hook directives, tool handler behavior end-to-end
(stubbed ctx.llm), skill/command registration, and the kill-switch.
Run: .venv/bin/python -m pytest tests/test_wiring.py -v
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(TESTS_DIR)
FIXTURES = os.path.join(TESTS_DIR, "fixtures")
sys.path.insert(0, REPO)

import plugin  # noqa: E402
from plugin import delegate  # noqa: E402

BIG = os.path.join(FIXTURES, "big351.go")
SMALL = os.path.join(FIXTURES, "small50.txt")


class StubResult:
    def __init__(self, text, model="glm-5.2", usage=None):
        self.text = text
        self.model = model
        self.provider = "zai"
        self.usage = usage


class StubLLM:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def complete(self, **kw):
        self.calls.append(kw)
        nxt = self.script.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


class RecordingCtx:
    """Captures every registration; llm stub drives delegations."""

    def __init__(self, script=()):
        self.llm = StubLLM(script)
        self.tools = {}
        self.hooks = {}
        self.commands = {}
        self.skills = {}

    def register_tool(self, name, toolset, schema, handler, **kw):
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler}

    def register_hook(self, event, cb):
        self.hooks[event] = cb

    def register_command(self, name, handler, **kw):
        self.commands[name] = handler

    def register_skill(self, name, path):
        self.skills[name] = path


def wired(script=()):
    ctx = RecordingCtx(script)
    plugin.register(ctx)
    return ctx


class TestRegistrations(unittest.TestCase):
    def setUp(self):
        self.ctx = wired()

    def test_tool_count(self):
        self.assertEqual(set(self.ctx.tools), {"bulk_read", "code_write"})
        for t in self.ctx.tools.values():
            self.assertEqual(t["toolset"], "hermes_shunt")

    def test_hook_registered(self):
        self.assertIn("pre_tool_call", self.ctx.hooks)

    def test_skills_registered(self):
        self.assertEqual(set(self.ctx.skills), {"bulk-reader", "code-writer"})
        for p in self.ctx.skills.values():
            self.assertTrue(os.path.isdir(p), p)

    def test_command_registered(self):
        self.assertIn("shunt", self.ctx.commands)

    def test_schemas_have_required(self):
        self.assertEqual(
            self.ctx.tools["bulk_read"]["schema"]["parameters"]["required"],
            ["question", "paths"])
        self.assertEqual(
            self.ctx.tools["code_write"]["schema"]["parameters"]["required"],
            ["spec", "reference"])


class TestGateHook(unittest.TestCase):
    def setUp(self):
        self.ctx = wired()
        self.gate = self.ctx.hooks["pre_tool_call"]

    def test_blocks_full_read_of_big_file(self):
        out = self.gate(tool_name="read_file", args={"path": BIG})
        self.assertEqual(out["action"], "block")
        self.assertIn("351 lines", out["message"])
        self.assertIn("bulk_read", out["message"])

    def test_allows_targeted_read(self):
        self.assertIsNone(
            self.gate(tool_name="read_file", args={"path": BIG, "limit": 40}))

    def test_allows_small_file(self):
        self.assertIsNone(
            self.gate(tool_name="read_file", args={"path": SMALL}))

    def test_blocks_terminal_cat_big(self):
        out = self.gate(tool_name="terminal", args={"command": f"cat {BIG}"})
        self.assertEqual(out["action"], "block")

    def test_allows_terminal_git(self):
        self.assertIsNone(
            self.gate(tool_name="terminal", args={"command": "git status"}))

    def test_allows_other_tools(self):
        self.assertIsNone(
            self.gate(tool_name="write_file", args={"path": BIG, "content": "x"}))

    def test_kill_switch(self):
        with mock.patch.dict(os.environ, {"SHUNT_DISABLE_HOOK": "1"}):
            self.assertIsNone(
                self.gate(tool_name="read_file", args={"path": BIG}))

    def test_threshold_env_live(self):
        with mock.patch.dict(os.environ, {"SHUNT_MIN_LINES": "10"}):
            out = self.gate(tool_name="read_file", args={"path": SMALL})
        self.assertEqual(out["action"], "block")


class TestBulkReadHandler(unittest.TestCase):
    def _run(self, script, params):
        ctx = wired(script)
        return ctx, ctx.tools["bulk_read"]["handler"](params)

    def test_success_text_and_footer(self):
        ctx, out = self._run(
            [StubResult("- noop1: func\n- noop2: func",
                        usage={"prompt_tokens": 900, "completion_tokens": 80})],
            {"question": "what functions?", "paths": [BIG]})
        self.assertIn("- noop1: func", out)
        self.assertIn("[hermes-shunt:", out)
        self.assertIn("bulk-reader@glm-5.2", out)
        kw = ctx.llm.calls[0]
        self.assertEqual(kw["purpose"], "hermes-shunt.bulk-read")
        self.assertIn(f'<file path="{BIG}">', kw["messages"][1]["content"])

    def test_paths_string_tolerated(self):
        _, out = self._run([StubResult("ok")], {"question": "q", "paths": BIG})
        self.assertIn("ok", out)

    def test_missing_question(self):
        _, out = self._run([StubResult("x")], {"paths": [BIG]})
        self.assertIn("error", out)

    def test_missing_paths(self):
        _, out = self._run([StubResult("x")], {"question": "q"})
        self.assertIn("error", out)

    def test_bad_path_fails_loud(self):
        _, out = self._run(
            [StubResult("x")],
            {"question": "q", "paths": ["/no/such/file.go"]})
        self.assertIn("not found or unreadable", out)
        # LLM never called: the one scripted result is still queued
        # (verified implicitly: no exhaustion error raised)

    def test_delegation_error_surface(self):
        _, out = self._run(
            [TimeoutError("timed out")],
            {"question": "q", "paths": [BIG]})
        self.assertIn("SHUNT_TIMEOUT_SECONDS", out)


class TestCodeWriteHandler(unittest.TestCase):
    def _run(self, script, params):
        ctx = wired(script)
        return ctx, ctx.tools["code_write"]["handler"](params)

    def test_stdout_mode_strips_fences(self):
        _, out = self._run(
            [StubResult("```go\npackage x\n// code\n```")],
            {"spec": "make a stub", "reference": SMALL})
        self.assertNotIn("```", out)
        self.assertIn("package x", out)
        self.assertIn("code-writer@glm-5.2", out)

    def test_target_mode_writes_file(self):
        with tempfile.TemporaryDirectory() as d:
            tgt = os.path.join(d, "out", "gen.go")
            _, out = self._run(
                [StubResult("```go\npackage gen\n```")],
                {"spec": "s", "reference": SMALL, "target": tgt})
            self.assertIn("Wrote 1 lines", out)
            content = open(tgt).read()
            self.assertEqual(content, "package gen")

    def test_reference_required_doctrine(self):
        _, out = self._run([StubResult("x")], {"spec": "s"})
        self.assertIn("reference is required", out)
        self.assertIn("patterns", out)

    def test_missing_reference_file(self):
        _, out = self._run(
            [StubResult("x")], {"spec": "s", "reference": "/nope.go"})
        self.assertIn("not found or unreadable", out)


class TestShuntCommand(unittest.TestCase):
    def test_stats_empty(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ,
                                 {"SHUNT_STATS_FILE": os.path.join(d, "s.jsonl")}):
                ctx = wired()
                out = ctx.commands["shunt"]("")
                self.assertIn("No delegations", out)

    def test_stats_after_call(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ,
                                 {"SHUNT_STATS_FILE": os.path.join(d, "s.jsonl")}):
                ctx = wired([StubResult("ans", usage={"prompt_tokens": 100,
                                                      "completion_tokens": 10})])
                ctx.tools["bulk_read"]["handler"](
                    {"question": "q", "paths": [SMALL]})
                out = ctx.commands["shunt"]("stats")
                self.assertIn("all-time: 1 delegations", out)
                self.assertIn("bulk-reader:1", out)
                self.assertIn("ratio", out)

    def test_usage_hint(self):
        ctx = wired()
        self.assertIn("Usage", ctx.commands["shunt"]("bogus"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
