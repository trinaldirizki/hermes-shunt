"""Task 3 — delegation core tests, ctx.llm stubbed.

Covers: corpus assembly, fail-loud path validation, token-estimate guard,
empty-reply retry x3 (z.ai discipline), retry exhaustion, timeout guidance,
fence-stripping, usage footer, stats roundtrip. Run:
  .venv/bin/python -m pytest tests/test_delegate.py -v
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(TESTS_DIR)
sys.path.insert(0, REPO)

from plugin import config, delegate, modes, stats  # noqa: E402


# ---------------------------------------------------------------- stubs

class StubResult:
    """Mimics PluginLlmCompleteResult(text, provider, model, usage, audit)."""

    def __init__(self, text, model="stub-model", provider="stub", usage=None):
        self.text = text
        self.model = model
        self.provider = provider
        # usage passes through verbatim (None stays None — tests rely on it)
        self.usage = usage


class StubLLM:
    """complete() pops the next scripted result; records every call."""

    def __init__(self, script):
        self.script = list(script)  # StubResult | Exception instances
        self.calls = []

    def complete(self, **kw):
        self.calls.append(kw)
        nxt = self.script.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


class StubCtx:
    def __init__(self, llm):
        self.llm = llm


def make_cfg(**over):
    base = dict(min_lines=350, timeout_seconds=180, max_corpus_tokens=100_000,
                worker_model=None, worker_provider=None, disable_hook=False)
    base.update(over)
    return config.Config(**base)


def no_sleep():
    return lambda _s: None


# ------------------------------------------------------------ assembly

class TestAssembly(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.f1 = os.path.join(self.d, "a.go")
        self.f2 = os.path.join(self.d, "b.go")
        open(self.f1, "w").write("package a\n")
        open(self.f2, "w").write("package b\n")

    def test_bulk_format(self):
        msg = delegate.assemble_bulk_message("what?", [self.f1, self.f2])
        self.assertIn('<file path="%s">\npackage a\n</file>' % self.f1, msg)
        self.assertIn('<file path="%s">\npackage b\n</file>' % self.f2, msg)
        self.assertTrue(msg.rstrip().endswith("Question: what?"))
        self.assertLess(msg.index(self.f1), msg.index(self.f2))  # order kept

    def test_write_format(self):
        msg = delegate.assemble_write_message("make tests", self.f1)
        self.assertTrue(msg.startswith("Spec: make tests"))
        self.assertIn("\nReference:\n", msg)
        self.assertIn("package a", msg)

    def test_validate_paths_ok(self):
        delegate.validate_paths([self.f1, self.f2])  # must not raise

    def test_validate_paths_fail_loud(self):
        with self.assertRaises(FileNotFoundError):
            delegate.validate_paths([self.f1, os.path.join(self.d, "nope")])


# -------------------------------------------------------- corpus guard

class TestCorpusGuard(unittest.TestCase):
    def test_under_limit_returns_estimate(self):
        cfg = make_cfg(max_corpus_tokens=1000)
        est = delegate.check_corpus_limit("x" * 2000, cfg)  # ~500 tokens
        self.assertEqual(est, 500)

    def test_over_limit_raises_with_guidance(self):
        cfg = make_cfg(max_corpus_tokens=100)
        with self.assertRaises(delegate.CorpusTooLargeError) as cm:
            delegate.check_corpus_limit("x" * 1000, cfg)  # ~250 tokens
        self.assertIn("split the batch", str(cm.exception))
        self.assertIn("SHUNT_MAX_CORPUS_TOKENS", str(cm.exception))
        self.assertIn("250", str(cm.exception))

    def test_delegate_refuses_oversized_corpus(self):
        cfg = make_cfg(max_corpus_tokens=10)
        llm = StubLLM([StubResult("hi")])
        with self.assertRaises(delegate.CorpusTooLargeError):
            delegate.delegate(StubCtx(llm), cfg, modes.BULK_READER_MODE,
                              "y" * 1000, "t")
        self.assertEqual(llm.calls, [])  # guard fires BEFORE any LLM call


# ----------------------------------------------------- happy path

class TestDelegateHappyPath(unittest.TestCase):
    def _run(self, usage=None):
        llm = StubLLM([StubResult("the answer", model="glm-5.2", usage=usage)])
        cfg = make_cfg()
        out = delegate.delegate(StubCtx(llm), cfg, modes.BULK_READER_MODE,
                                "question corpus", "hermes-shunt.bulk-read")
        return out, llm

    def test_text_returned(self):
        out, _ = self._run()
        self.assertEqual(out.text, "the answer")

    def test_call_shape(self):
        out, llm = self._run()
        kw = llm.calls[0]
        self.assertEqual(kw["messages"][0]["role"], "system")
        self.assertEqual(kw["messages"][0]["content"], modes.BULK_READER_MODE.system)
        self.assertEqual(kw["messages"][1]["content"], "question corpus")
        self.assertEqual(kw["temperature"], 0.2)
        self.assertEqual(kw["timeout"], 180)
        self.assertEqual(kw["purpose"], "hermes-shunt.bulk-read")
        self.assertIsNone(kw["model"])      # zero-config: no routing
        self.assertIsNone(kw["provider"])

    def test_usage_extracted_from_dict(self):
        out, _ = self._run(usage={"prompt_tokens": 500, "completion_tokens": 50})
        self.assertEqual(out.prompt_tokens, 500)
        self.assertEqual(out.completion_tokens, 50)

    def test_usage_none_tolerated(self):
        out, _ = self._run(usage=None)
        self.assertEqual(out.prompt_tokens, 0)
        self.assertEqual(out.completion_tokens, 0)

    def test_worker_model_forwarded(self):
        llm = StubLLM([StubResult("a", model="deepseek-chat")])
        cfg = make_cfg(worker_model="deepseek-chat", worker_provider="deepseek")
        out = delegate.delegate(StubCtx(llm), cfg, modes.BULK_READER_MODE, "m", "p")
        self.assertEqual(llm.calls[0]["model"], "deepseek-chat")
        self.assertEqual(llm.calls[0]["provider"], "deepseek")
        self.assertEqual(out.model, "deepseek-chat")

    def test_corpus_tokens_on_outcome(self):
        out, _ = self._run()
        self.assertEqual(out.corpus_tokens, len("question corpus") // 4)


# ------------------------------------------------------ retry discipline

class TestRetry(unittest.TestCase):
    def test_empty_then_success(self):
        llm = StubLLM([StubResult(""), StubResult("   "), StubResult("ok")])
        sleeps = []
        out = delegate.delegate(StubCtx(llm), make_cfg(), modes.BULK_READER_MODE,
                                "m", "p", sleep=sleeps.append)
        self.assertEqual(out.text, "ok")
        self.assertEqual(len(llm.calls), 3)
        self.assertEqual(sleeps, [0.5, 1.0])  # exponential backoff

    def test_exhaustion(self):
        llm = StubLLM([StubResult(""), StubResult(""), StubResult("")])
        with self.assertRaises(delegate.DelegationError) as cm:
            delegate.delegate(StubCtx(llm), make_cfg(), modes.BULK_READER_MODE,
                              "m", "p", sleep=no_sleep())
        self.assertIn("empty", str(cm.exception).lower())
        self.assertEqual(len(llm.calls), 3)

    def test_timeout_guidance(self):
        llm = StubLLM([TimeoutError("request timed out after 180s")])
        with self.assertRaises(delegate.DelegationError) as cm:
            delegate.delegate(StubCtx(llm), make_cfg(), modes.BULK_READER_MODE,
                              "m", "p", sleep=no_sleep())
        self.assertIn("SHUNT_TIMEOUT_SECONDS", str(cm.exception))
        self.assertIn("split the work", str(cm.exception))

    def test_other_exception_wrapped_not_retried(self):
        llm = StubLLM([ValueError("boom")])
        with self.assertRaises(delegate.DelegationError) as cm:
            delegate.delegate(StubCtx(llm), make_cfg(), modes.BULK_READER_MODE,
                              "m", "p", sleep=no_sleep())
        self.assertIn("boom", str(cm.exception))
        self.assertEqual(len(llm.calls), 1)  # exceptions don't retry


# --------------------------------------------------------- fences/footer

class TestFencesFooter(unittest.TestCase):
    def test_strip_fences_full_wrap(self):
        self.assertEqual(delegate.strip_fences("```go\ncode\n```"), "code")

    def test_strip_fences_interior_lines(self):
        # upstream sed semantic: remove every line that is exactly a fence
        self.assertEqual(delegate.strip_fences("```\na\n```\nb\n```"), "a\nb")

    def test_strip_fences_noop(self):
        self.assertEqual(delegate.strip_fences("plain text"), "plain text")

    def test_footer_format(self):
        out = delegate.DelegateOutcome(
            text="a", model="m", provider="p",
            prompt_tokens=1, completion_tokens=2, corpus_tokens=3)
        f = delegate.footer(out, modes.BULK_READER_MODE)
        self.assertTrue(f.startswith("[hermes-shunt:"))
        self.assertIn("~3 corpus tokens", f)
        self.assertIn("bulk-reader@m", f)


# --------------------------------------------------------------- stats

class TestStats(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.jsonl")
            with mock.patch.dict(os.environ, {"SHUNT_STATS_FILE": p}):
                stats.record_call("bulk-reader", "glm-5.2", 500, 50, 4000)
                stats.record_call("code-writer", "glm-5.2", 300, 30, 2000)
                s = stats.summarize()
            self.assertEqual(s["calls"], 2)
            self.assertEqual(s["worker_tokens_spent"], 500 + 50 + 300 + 30)
            self.assertEqual(s["orchestrator_tokens_avoided"], 6000)
            self.assertEqual(s["per_mode"], {"bulk-reader": 1, "code-writer": 1})

    def test_default_path_outside_repo(self):
        # stats must NEVER land inside the plugin package (symlinked repo)
        default = stats.stats_path()
        self.assertIn(".hermes", default)
        self.assertNotIn("hermes-shunt/plugin", default)

    def test_malformed_lines_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.jsonl")
            with open(p, "w") as f:
                f.write("not json\n")
                f.write('{"ts":"t","mode":"bulk-reader","model":"m",'
                        '"prompt_tokens":1,"completion_tokens":1,"corpus_tokens":2}\n')
            with mock.patch.dict(os.environ, {"SHUNT_STATS_FILE": p}):
                s = stats.summarize()
            self.assertEqual(s["calls"], 1)

    def test_delegate_records_stats(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.jsonl")
            with mock.patch.dict(os.environ, {"SHUNT_STATS_FILE": p}):
                llm = StubLLM([StubResult("ans", usage={"prompt_tokens": 900,
                                                        "completion_tokens": 90})])
                delegate.delegate(StubCtx(llm), make_cfg(), modes.BULK_READER_MODE,
                                  "q" * 400, "p", sleep=no_sleep())
                s = stats.summarize()
            self.assertEqual(s["calls"], 1)
            self.assertEqual(s["orchestrator_tokens_avoided"], 100)  # 400/4
            self.assertEqual(s["worker_tokens_spent"], 990)


if __name__ == "__main__":
    unittest.main(verbosity=2)
