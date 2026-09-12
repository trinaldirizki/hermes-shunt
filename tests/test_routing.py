"""Task 2 — routing gate tests.

Semantics ported from shunt's hooks/check-file-size and hooks/check-bash-read
(spotify/portal-ai-plugins, Apache-2.0). Boundary fixtures: exact350.go,
big351.go, small50.txt. Run: python -m pytest tests/test_routing.py -v
(works under plain unittest too).
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(TESTS_DIR)
FIXTURES = os.path.join(TESTS_DIR, "fixtures")

sys.path.insert(0, REPO)  # -> `plugin` package (has __init__.py)

from plugin import config, routing  # noqa: E402

BIG = os.path.join(FIXTURES, "big351.go")        # 351 lines
EXACT = os.path.join(FIXTURES, "exact350.go")    # exactly 350 lines
SMALL = os.path.join(FIXTURES, "small50.txt")    # 50 lines


class TestShouldBlockRead(unittest.TestCase):
    def test_offset_set_allows(self):
        self.assertEqual(routing.should_block_read(BIG, 10, None, 350), (False, ""))

    def test_limit_set_allows(self):
        self.assertEqual(routing.should_block_read(BIG, None, 50, 350), (False, ""))

    def test_nonexistent_allows(self):
        self.assertEqual(routing.should_block_read("/no/such/file.go", None, None, 350), (False, ""))

    def test_empty_path_allows(self):
        self.assertEqual(routing.should_block_read("", None, None, 350), (False, ""))

    def test_directory_allows(self):
        self.assertEqual(routing.should_block_read(FIXTURES, None, None, 350), (False, ""))

    def test_351_blocks(self):
        blocked, msg = routing.should_block_read(BIG, None, None, 350)
        self.assertTrue(blocked)
        self.assertIn("351 lines", msg)
        self.assertIn("350", msg)
        self.assertIn("bulk_read", msg)
        self.assertIn("offset/limit", msg)

    def test_350_exact_allows(self):
        self.assertEqual(routing.should_block_read(EXACT, None, None, 350), (False, ""))

    def test_threshold_respected(self):
        # threshold 50: small50 exactly at threshold allows; big blocks
        self.assertFalse(routing.should_block_read(SMALL, None, None, 50)[0])
        self.assertTrue(routing.should_block_read(BIG, None, None, 50)[0])

    def test_message_format(self):
        _, msg = routing.should_block_read(BIG, None, None, 350)
        self.assertTrue(msg.startswith("File is 351 lines (threshold: 350)."), msg)


class TestExtractBashReadTarget(unittest.TestCase):
    def test_cat(self):
        self.assertEqual(routing.extract_bash_read_target("cat f"), "f")

    def test_head_flag(self):
        self.assertEqual(routing.extract_bash_read_target("head -100 f"), "f")

    def test_tail_flag(self):
        self.assertEqual(routing.extract_bash_read_target("tail -50 f"), "f")

    def test_cat_number_flag(self):
        self.assertEqual(routing.extract_bash_read_target("cat -n f"), "f")

    def test_less_and_more(self):
        self.assertEqual(routing.extract_bash_read_target("less f"), "f")
        self.assertEqual(routing.extract_bash_read_target("more f"), "f")

    def test_quoted_operand(self):
        self.assertEqual(routing.extract_bash_read_target('cat "my file.ts"'), "my file.ts")
        self.assertEqual(routing.extract_bash_read_target("cat 'my file.ts'"), "my file.ts")

    def test_pipe_none(self):
        self.assertIsNone(routing.extract_bash_read_target("cat f | grep x"))

    def test_redirect_none(self):
        self.assertIsNone(routing.extract_bash_read_target("cat f > out"))

    def test_stderr_redirect_none(self):
        self.assertIsNone(routing.extract_bash_read_target("cat f 2>/dev/null"))

    def test_git_none(self):
        self.assertIsNone(routing.extract_bash_read_target("git status"))

    def test_grep_none(self):
        self.assertIsNone(routing.extract_bash_read_target("grep -rn x ."))

    def test_echo_none(self):
        self.assertIsNone(routing.extract_bash_read_target("echo hello"))

    def test_bare_cat_none(self):
        self.assertIsNone(routing.extract_bash_read_target("cat"))

    def test_empty_none(self):
        self.assertIsNone(routing.extract_bash_read_target(""))

    def test_non_file_operand_passes_through_for_isfile_check(self):
        # extraction returns the operand even if it isn't a file — the
        # block decision does the isfile check (upstream behavior)
        self.assertEqual(routing.extract_bash_read_target("cat /no/such/file"), "/no/such/file")


class TestBashReadBlock(unittest.TestCase):
    def test_big_file_cat_blocked(self):
        blocked, msg = routing.bash_read_block(f"cat {BIG}", 350)
        self.assertTrue(blocked)
        self.assertIn("351 lines", msg)
        self.assertIn("bulk_read", msg)

    def test_small_file_cat_allowed(self):
        self.assertEqual(routing.bash_read_block(f"cat {SMALL}", 350), (False, ""))

    def test_pipe_on_big_file_allowed(self):
        # pipes are targeted reads — passthrough even for huge files
        self.assertEqual(routing.bash_read_block(f"cat {BIG} | grep x", 350), (False, ""))

    def test_nonexistent_operand_allowed(self):
        self.assertEqual(routing.bash_read_block("cat /no/such/file", 350), (False, ""))


class TestLineCount(unittest.TestCase):
    def test_no_trailing_newline(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("a\nb\nc\nd\ne")  # 5 lines, last without newline
            path = f.name
        try:
            self.assertEqual(routing.line_count(path), 5)
        finally:
            os.unlink(path)


class TestConfig(unittest.TestCase):
    def test_defaults(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            for k in ("SHUNT_MIN_LINES", "SHUNT_TIMEOUT_SECONDS", "SHUNT_MAX_CORPUS_TOKENS",
                      "SHUNT_WORKER_MODEL", "SHUNT_WORKER_PROVIDER", "SHUNT_DISABLE_HOOK"):
                os.environ.pop(k, None)
            cfg = config.load()
        self.assertEqual(cfg.min_lines, 350)
        self.assertEqual(cfg.timeout_seconds, 180)
        self.assertEqual(cfg.max_corpus_tokens, 100_000)
        self.assertIsNone(cfg.worker_model)
        self.assertIsNone(cfg.worker_provider)
        self.assertFalse(cfg.disable_hook)

    def test_garbage_min_lines_defaults(self):
        with mock.patch.dict(os.environ, {"SHUNT_MIN_LINES": "abc"}):
            self.assertEqual(config.load().min_lines, 350)

    def test_valid_override(self):
        with mock.patch.dict(os.environ, {"SHUNT_MIN_LINES": "10"}):
            self.assertEqual(config.load().min_lines, 10)

    def test_garbage_timeout_and_corpus(self):
        with mock.patch.dict(os.environ, {"SHUNT_TIMEOUT_SECONDS": "x", "SHUNT_MAX_CORPUS_TOKENS": "y"}):
            cfg = config.load()
        self.assertEqual(cfg.timeout_seconds, 180)
        self.assertEqual(cfg.max_corpus_tokens, 100_000)

    def test_disable_hook_flag(self):
        with mock.patch.dict(os.environ, {"SHUNT_DISABLE_HOOK": "1"}):
            self.assertTrue(config.load().disable_hook)
        with mock.patch.dict(os.environ, {"SHUNT_DISABLE_HOOK": ""}):
            self.assertFalse(config.load().disable_hook)

    def test_worker_env(self):
        with mock.patch.dict(os.environ, {"SHUNT_WORKER_MODEL": "deepseek-chat",
                                          "SHUNT_WORKER_PROVIDER": "deepseek"}):
            cfg = config.load()
        self.assertEqual(cfg.worker_model, "deepseek-chat")
        self.assertEqual(cfg.worker_provider, "deepseek")

    def test_corpus_tokens_math(self):
        with mock.patch.dict(os.environ, {}):
            cfg = config.load()
        self.assertEqual(cfg.corpus_tokens("x" * 4000), 1000)
        self.assertEqual(cfg.corpus_tokens(""), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
