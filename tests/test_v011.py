"""R2 (cumulative-read gate) + R3 (skill priming) tests.

R2: per-session state; a targeted read is blocked only once PRIOR cumulative
chunks of the same file exceed the threshold (first read always passes —
upstream single-read semantics preserved).
R3: pre_llm_call primer injected on the first turn only.
Run: .venv/bin/python -m pytest tests/test_v011.py -v
"""

import os
import sys
import unittest
from unittest import mock

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(TESTS_DIR)
FIXTURES = os.path.join(TESTS_DIR, "fixtures")
sys.path.insert(0, REPO)

import plugin  # noqa: E402
from plugin import readstate  # noqa: E402

BIG = os.path.join(FIXTURES, "big351.go")       # 351 lines
LARGE = os.path.join(FIXTURES, "large.txt")     # 1200 lines (eval fixture)
CHUNK = os.path.join(FIXTURES, "chunkable.txt")  # generated: 900 lines


def _wired_gate():
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
    return hooks


class TestCumulativeGate(unittest.TestCase):
    def setUp(self):
        readstate.reset()
        if not os.path.exists(CHUNK):
            with open(CHUNK, "w") as f:
                f.write("x\n" * 900)
        self.gate = _wired_gate()["pre_tool_call"]

    def _read(self, offset=None, limit=None, sid="s1", path=None):
        args = {"path": path or CHUNK}
        if offset is not None:
            args["offset"] = offset
        if limit is not None:
            args["limit"] = limit
        return self.gate(tool_name="read_file", args=args, session_id=sid)

    def test_first_targeted_read_passes(self):
        self.assertIsNone(self._read(limit=200))

    def test_third_chunk_blocked_after_threshold(self):
        # 200 + 200 = 400 >= 350 -> third read blocked
        self.assertIsNone(self._read(limit=200))
        self.assertIsNone(self._read(limit=200))
        out = self._read(limit=200)
        self.assertIsNotNone(out)
        self.assertEqual(out["action"], "block")
        self.assertIn("bulk_read", out["message"])
        self.assertIn("already read", out["message"])

    def test_under_threshold_chunks_pass(self):
        self.assertIsNone(self._read(limit=100))
        self.assertIsNone(self._read(limit=100))
        self.assertIsNone(self._read(limit=100))  # 300 cumulative, still under

    def test_state_is_per_session(self):
        self.assertIsNone(self._read(limit=200, sid="s1"))
        self.assertIsNone(self._read(limit=200, sid="s1"))
        # different session: fresh state, passes
        self.assertIsNone(self._read(limit=200, sid="s2"))
        # s1 now blocked
        self.assertEqual(self._read(limit=200, sid="s1")["action"], "block")

    def test_state_is_per_file(self):
        self._read(limit=200)
        self._read(limit=200)
        # other file unaffected
        self.assertIsNone(self._read(limit=200, path=LARGE))

    def test_full_read_blocked_immediately_not_via_state(self):
        out = self.gate(tool_name="read_file", args={"path": BIG}, session_id="s1")
        self.assertEqual(out["action"], "block")
        # and it did not accumulate state for BIG
        read = self.gate(tool_name="read_file",
                         args={"path": BIG, "limit": 50}, session_id="s1")
        self.assertIsNone(read)

    def test_limit_zero_accumulates_nothing(self):
        self.assertIsNone(self._read(limit=0))
        self.assertIsNone(self._read(limit=0))
        self.assertIsNone(self._read(limit=200))  # cumulative still 200

    def test_offset_only_estimate_capped(self):
        # offset=800 on 900-line file: estimate min(2000, 900-800)=100
        self.assertIsNone(self._read(offset=800))
        st = readstate.state().get("s1", {})
        self.assertLessEqual(st.get(os.path.abspath(CHUNK), 0), 150)

    def test_cumulative_disabled_via_env(self):
        with mock.patch.dict(os.environ, {"SHUNT_CUMULATIVE_GATE": "0"}):
            for _ in range(6):
                self.assertIsNone(self._read(limit=200))

    def test_reset_clears(self):
        self._read(limit=200)
        self._read(limit=200)
        readstate.reset()
        self.assertIsNone(self._read(limit=200))


class TestPrimer(unittest.TestCase):
    def setUp(self):
        readstate.reset()
        self.hooks = _wired_gate()

    def test_primer_registered(self):
        self.assertIn("pre_llm_call", self.hooks)

    def test_first_turn_primes(self):
        out = self.hooks["pre_llm_call"](
            session_id="s9", user_message="hi", is_first_turn=True)
        self.assertIsNotNone(out)
        ctx = out["context"] if isinstance(out, dict) else out
        self.assertIn("bulk_read", ctx)
        # R3b (v0.1.2): primer must also steer code_write adoption
        self.assertIn("code_write", ctx)

    def test_later_turns_silent(self):
        self.hooks["pre_llm_call"](session_id="s9", user_message="hi",
                                   is_first_turn=True)
        out = self.hooks["pre_llm_call"](session_id="s9", user_message="again",
                                         is_first_turn=False)
        self.assertIsNone(out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
