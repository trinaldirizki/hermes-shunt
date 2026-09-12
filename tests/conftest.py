"""Isolate ALL tests from the real stats file — handlers under test go
through delegate -> stats.record_call, and without this the default path
(~/.hermes/hermes-shunt-stats.jsonl) gets polluted with stub-model entries.

Also resets R2 cumulative-read state before every test: upstream's bash
hooks are stateless (fresh process per call), so per-test isolation mirrors
the ported semantics and prevents cross-test/cross-module accumulation.
"""

import os
import tempfile

_d = tempfile.mkdtemp(prefix="shunt-test-stats-")
os.environ["SHUNT_STATS_FILE"] = os.path.join(_d, "stats.jsonl")


import sys  # noqa: E402

_tests_dir = os.path.dirname(os.path.abspath(__file__))
_repo = os.path.dirname(_tests_dir)
if _repo not in sys.path:
    sys.path.insert(0, _repo)

import pytest  # noqa: E402

from plugin import readstate  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_readstate():
    readstate.reset()
    yield
    readstate.reset()
