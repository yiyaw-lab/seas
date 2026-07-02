"""Refusal-must-still-record-cost tests -- Bugbot finding on PR #84: _check_refusal
raises BEFORE argo_cost.record_usage() ran on all three Anthropic response-unpack
sites (_call_anthropic, describe_image, chat_with_mcp), so an HTTP-200 refusal
advanced the daily CALL cap (argo_guard.DailyBudget) but never recorded token
spend -- cost_today_usd() undercounts exactly when a premium model refuses
(refusals can bill partial output). Fix: record_usage() now runs BEFORE
_check_refusal() at each site, using the response's usage block (present on a
refusal) -- ModelRefusal still raises after the row lands.

Pure: LEDGER_PATH patched to a tmp file, _guarded bypassed, no network/key.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import argo_cost
import argo_observe as observe


_ENV_KEY = {"ANTHROPIC_API_KEY": "test-key"}


class _Usage:
    def __init__(self, **fields):
        self.__dict__.update(fields)


class _RefusedResponseWithUsage:
    """A refusal that still carries a usage block -- the actual shape Anthropic
    returns on a refusal (HTTP 200, stop_reason == 'refusal', partial billing)."""
    stop_reason = "refusal"
    content = []
    usage = _Usage(input_tokens=100, output_tokens=17)


class RefusalRecordsCostTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.ledger_path = tmp / "argo_cost_ledger.json"
        self.enterContext(mock.patch.object(argo_cost, "LEDGER_PATH", self.ledger_path))
        # _call_anthropic/describe_image/chat_with_mcp all read the key BEFORE
        # _guarded() is reached (client construction), so it must be present even
        # though _guarded itself is stubbed out below.
        self.enterContext(mock.patch.dict("os.environ", _ENV_KEY))

    def test_call_anthropic_records_usage_before_refusal_raises(self):
        with mock.patch.object(observe, "_guarded",
                               lambda p, do_call, label: _RefusedResponseWithUsage()):
            with self.assertRaises(observe.ModelRefusal):
                observe._call_anthropic("job", "claude-fable-5")
        rows = argo_cost._load()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["model"], "claude-fable-5")
        self.assertEqual(rows[0]["input_tokens"], 100)
        self.assertEqual(rows[0]["output_tokens"], 17)

    def test_describe_image_records_usage_before_refusal_raises(self):
        with mock.patch.object(observe, "_guarded",
                               lambda p, do_call, label: _RefusedResponseWithUsage()):
            with self.assertRaises(observe.ModelRefusal):
                observe.describe_image(b"x", "image/png", "describe this")
        rows = argo_cost._load()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["input_tokens"], 100)

    def test_chat_with_mcp_records_usage_before_refusal_raises(self):
        with mock.patch.object(observe, "_guarded",
                               lambda p, do_call, label: _RefusedResponseWithUsage()):
            with self.assertRaises(observe.ModelRefusal):
                observe.chat_with_mcp("sys", [{"role": "user", "content": "hi"}],
                                      "claude-fable-5")
        rows = argo_cost._load()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["output_tokens"], 17)

    def test_refusal_still_raises_after_recording(self):
        # The whole point: recording usage first must not swallow the refusal.
        with mock.patch.object(observe, "_guarded",
                               lambda p, do_call, label: _RefusedResponseWithUsage()):
            with self.assertRaises(observe.ModelRefusal) as ctx:
                observe._call_anthropic("job", "claude-fable-5")
        self.assertIn("refused", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
