"""Isolate ALL tests from the real stats file — handlers under test go
through delegate -> stats.record_call, and without this the default path
(~/.hermes/hermes-shunt-stats.jsonl) gets polluted with stub-model entries."""

import os
import tempfile

_d = tempfile.mkdtemp(prefix="shunt-test-stats-")
os.environ["SHUNT_STATS_FILE"] = os.path.join(_d, "stats.jsonl")
