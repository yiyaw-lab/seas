"""Usage/cost telemetry regression tests (argo_cost).

Locks the measurement the cost lever needs: a model call's `usage` is normalized
across the three providers Argo speaks (anthropic input/output + cache fields;
OpenAI/xAI chat-completions prompt/completion; OpenAI Responses cached_tokens
nested under input_tokens_details) and appended as one ledger row; summarize()
totals it; a missing/partial usage shape does NOT crash; and -- the hard contract
-- a forced write failure is swallowed (logged), never propagated into the call
path. Pure -- no network/LLM/real data: LEDGER_PATH is patched to a tmp dir.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import argo_cost
import argo_store


class _Usage:
    """Stand-in for an SDK usage object: attribute access only (the real
    Anthropic/OpenAI usage objects are not dicts)."""

    def __init__(self, **fields):
        self.__dict__.update(fields)


class _Resp:
    def __init__(self, usage):
        self.usage = usage


class CostLedgerTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = tmp / "argo_cost_ledger.json"
        self.enterContext(mock.patch.object(argo_cost, "LEDGER_PATH", self.path))

    def _rows(self):
        return argo_store.load_json(self.path, [])

    def test_anthropic_normalization_with_cache_fields(self):
        # Anthropic carries input/output + both cache_* fields directly.
        resp = _Resp(_Usage(
            input_tokens=100, output_tokens=40,
            cache_creation_input_tokens=200, cache_read_input_tokens=300))
        row = argo_cost.record_usage(resp, "claude-opus-4-8", "anthropic",
                                     "chat/claude-opus-4-8", ts=1_000_000.0)
        self.assertIsNotNone(row)
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["model"], "claude-opus-4-8")
        self.assertEqual(r["provider"], "anthropic")
        self.assertEqual(r["label"], "chat/claude-opus-4-8")
        self.assertEqual(r["ts"], 1_000_000.0)
        self.assertEqual(r["input_tokens"], 100)
        self.assertEqual(r["output_tokens"], 40)
        self.assertEqual(r["cache_creation_tokens"], 200)
        self.assertEqual(r["cache_read_tokens"], 300)

    def test_openai_chat_completions_prompt_completion_names(self):
        # OpenAI/xAI chat-completions use prompt/completion_tokens, no cache fields.
        resp = _Resp(_Usage(prompt_tokens=70, completion_tokens=25))
        argo_cost.record_usage(resp, "gpt-4.1", "openai", "openai/gpt-4.1")
        r = self._rows()[0]
        self.assertEqual(r["input_tokens"], 70)
        self.assertEqual(r["output_tokens"], 25)
        self.assertEqual(r["cache_creation_tokens"], 0)
        self.assertEqual(r["cache_read_tokens"], 0)

    def test_openai_responses_cached_tokens_nested(self):
        # OpenAI Responses API nests cache reads under input_tokens_details.
        resp = _Resp(_Usage(
            input_tokens=500, output_tokens=80,
            input_tokens_details=_Usage(cached_tokens=400)))
        argo_cost.record_usage(resp, "gpt-5", "openai", "responses/gpt-5")
        r = self._rows()[0]
        self.assertEqual(r["input_tokens"], 500)
        self.assertEqual(r["output_tokens"], 80)
        self.assertEqual(r["cache_read_tokens"], 400)

    def test_missing_usage_does_not_crash(self):
        # A response with no usage at all -> a zeroed row, not an exception.
        row = argo_cost.record_usage(_Resp(None), "claude-sonnet-4-6",
                                     "anthropic", "anthropic/claude-sonnet-4-6")
        self.assertIsNotNone(row)
        r = self._rows()[0]
        self.assertEqual(
            (r["input_tokens"], r["output_tokens"],
             r["cache_creation_tokens"], r["cache_read_tokens"]),
            (0, 0, 0, 0))

    def test_summarize_by_model_and_provider(self):
        argo_cost.record_usage(
            _Resp(_Usage(input_tokens=10, output_tokens=1)),
            "claude-opus-4-8", "anthropic", "chat/claude-opus-4-8")
        argo_cost.record_usage(
            _Resp(_Usage(input_tokens=20, output_tokens=2)),
            "claude-opus-4-8", "anthropic", "chat/claude-opus-4-8")
        argo_cost.record_usage(
            _Resp(_Usage(prompt_tokens=5, completion_tokens=3)),
            "gpt-4.1", "openai", "openai/gpt-4.1")

        by_model = argo_cost.summarize(by="model")
        self.assertEqual(by_model["claude-opus-4-8"]["calls"], 2)
        self.assertEqual(by_model["claude-opus-4-8"]["input_tokens"], 30)
        self.assertEqual(by_model["claude-opus-4-8"]["output_tokens"], 3)
        self.assertEqual(by_model["gpt-4.1"]["calls"], 1)

        by_provider = argo_cost.summarize(by="provider")
        self.assertEqual(by_provider["anthropic"]["calls"], 2)
        self.assertEqual(by_provider["openai"]["input_tokens"], 5)

    def test_write_failure_is_swallowed_not_propagated(self):
        # The hard contract: a ledger-write failure must NEVER propagate into the
        # call path. Force save_json to raise; record_usage returns None and the
        # call does not raise.
        resp = _Resp(_Usage(input_tokens=1, output_tokens=1))
        with mock.patch.object(argo_cost.argo_store, "save_json",
                               side_effect=OSError("disk full")):
            with self.assertLogs("argo_cost", level="WARNING") as cm:
                row = argo_cost.record_usage(resp, "claude-opus-4-8",
                                             "anthropic", "chat/claude-opus-4-8")
        self.assertIsNone(row)
        # It logged the swallowed failure (the operator must still see it).
        self.assertTrue(any("cost telemetry record failed" in m for m in cm.output))
        # And nothing was written.
        self.assertEqual(self._rows(), [])


if __name__ == "__main__":
    unittest.main()
