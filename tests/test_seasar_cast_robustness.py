"""CAST failure-classification tests.

Pure stdlib, no model calls. These tests lock the first CAST robustness slice:
truncated/malformed CAST output is classified, safely logged, and never persists
an invalid build order.
"""

import json
import os
import tempfile
import unittest

import argo_observe
import argo_rehearse
import seasar_compile as sc


def _brief():
    return {
        "normalized_idea": "Build a tiny CLI.",
        "inferred_stack": "Python",
        "assumptions": ["stdlib only"],
    }


def _critiques():
    return {"critic": "too broad", "user": "needs tests", "ops": "watch storage"}


class CastParserDiagnosticsTest(unittest.TestCase):
    def test_truncated_json_has_diagnostics(self):
        with self.assertRaises(sc.JsonObjectParseError) as cm:
            sc._parse_json_object('{"title": "x", "spec": {"what": "missing close"')

        err = cm.exception
        self.assertEqual(err.reason, "unbalanced_json")
        self.assertGreater(err.brace_depth, 0)
        self.assertTrue(err.likely_truncated)
        self.assertGreater(err.response_chars, 0)
        self.assertIn("missing close", err.tail)


class CastClassificationTest(unittest.TestCase):
    def setUp(self):
        self._orig_call = sc.rehearse._call
        self._orig_runnable = sc.rehearse._runnable
        self._orig_failures = sc.CAST_FAILURES_PATH
        self.tmp = tempfile.TemporaryDirectory()
        sc.CAST_FAILURES_PATH = os.path.join(self.tmp.name, "cast-failures.jsonl")
        sc.rehearse._runnable = lambda model: model

    def tearDown(self):
        sc.rehearse._call = self._orig_call
        sc.rehearse._runnable = self._orig_runnable
        sc.CAST_FAILURES_PATH = self._orig_failures
        self.tmp.cleanup()

    def _stub_call(self, text, metadata):
        def fake_call(*args, **kwargs):
            self.assertTrue(kwargs.get("return_metadata"))
            return text, metadata
        sc.rehearse._call = fake_call

    def test_max_tokens_parse_failure_is_cast_truncated(self):
        self._stub_call('{"title": "x"', {
            "provider": "anthropic",
            "model": "claude-opus-4-8",
            "stop_reason": "max_tokens",
            "max_tokens": 16000,
            "output_tokens": 16000,
        })

        with self.assertRaises(sc.CastParseError) as cm:
            sc.cast("idea", _brief(), "mvp", 1, _critiques(), [])

        err = cm.exception
        self.assertEqual(err.code, "cast_truncated")
        self.assertEqual(err.stop_reason, "max_tokens")
        self.assertTrue(err.likely_truncated)

    def test_malformed_complete_json_is_not_truncation(self):
        self._stub_call('{"title": !!!}', {
            "provider": "anthropic",
            "model": "claude-opus-4-8",
            "stop_reason": "end_turn",
            "max_tokens": 16000,
        })

        with self.assertRaises(sc.CastParseError) as cm:
            sc.cast("idea", _brief(), "mvp", 1, _critiques(), [])

        err = cm.exception
        self.assertEqual(err.code, "cast_malformed_json")
        self.assertEqual(err.stop_reason, "end_turn")

    def test_failure_ledger_redacts_and_omits_full_prompt_and_output(self):
        secret_tail = '{"title": "x", "api_key": "sk_test_SECRET123456789"'
        self._stub_call(secret_tail, {
            "provider": "anthropic",
            "model": "claude-opus-4-8",
            "stop_reason": "max_tokens",
            "max_tokens": 16000,
        })

        with self.assertRaises(sc.CastParseError):
            sc.cast("idea with private context", _brief(), "mvp", 1, _critiques(), [])

        with open(sc.CAST_FAILURES_PATH) as fh:
            ledger_text = fh.read()
        record = json.loads(ledger_text)

        self.assertEqual(record["code"], "cast_truncated")
        self.assertIn("response_sha256", record)
        self.assertIn("prompt_sha256", record)
        self.assertNotIn("prompt", record)
        self.assertNotIn("response", record)
        self.assertNotIn("idea with private context", ledger_text)
        self.assertNotIn("sk_test_SECRET", record["tail"])
        self.assertNotIn("sk_test_SECRET", ledger_text)
        self.assertIn("[REDACTED]", record["tail"])


class CompileStreamCastFailureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig_orders = sc.ORDERS_DIR
        self._orig_smelt = sc.smelt
        self._orig_assign = sc.rehearse._assign_adversary_models
        self._orig_run = sc.rehearse.run_adversaries
        self._orig_cast = sc.cast
        sc.ORDERS_DIR = os.path.join(self.tmp.name, "orders")
        sc.smelt = lambda *a, **k: (_brief(), "claude-sonnet-4-6", "{}")
        sc.rehearse._assign_adversary_models = lambda roles: {
            "critic": "claude-sonnet-4-6",
            "user": "gpt-5",
            "ops": "grok-4.3",
        }
        sc.rehearse.run_adversaries = lambda *a, **k: _critiques()

    def tearDown(self):
        sc.ORDERS_DIR = self._orig_orders
        sc.smelt = self._orig_smelt
        sc.rehearse._assign_adversary_models = self._orig_assign
        sc.rehearse.run_adversaries = self._orig_run
        sc.cast = self._orig_cast
        self.tmp.cleanup()

    def test_cast_failure_emits_code_and_persists_no_order(self):
        parse = sc.JsonObjectParseError(
            "unbalanced_json", '{"title": "x"', 1, True,
            "unbalanced JSON object in model response")

        def fail_cast(*args, **kwargs):
            raise sc.CastParseError("cast_truncated", parse, {
                "model": "claude-opus-4-8",
                "stop_reason": "max_tokens",
                "max_tokens": 16000,
            })

        sc.cast = fail_cast
        events = [json.loads(chunk[len("data: "):]) for chunk in sc.compile_stream("idea")]
        self.assertEqual(events[-1]["stage"], "error")
        self.assertEqual(events[-1]["code"], "cast_truncated")
        self.assertFalse(os.path.exists(sc.ORDERS_DIR))


class ModelCallShapeTest(unittest.TestCase):
    def test_rehearse_call_default_return_shape_remains_plain_text(self):
        orig_provider = argo_rehearse.observe.provider_for
        orig_generate = argo_rehearse.observe.generate_observations
        try:
            argo_rehearse.observe.provider_for = lambda model: {"name": "xai"}
            argo_rehearse.observe.generate_observations = (
                lambda prompt, model, temperature=1.0: "plain text")

            self.assertEqual(
                argo_rehearse._call("system", "prompt", "grok-4.3", 0.2),
                "plain text",
            )
            text, metadata = argo_rehearse._call(
                "system", "prompt", "grok-4.3", 0.2, return_metadata=True)
            self.assertEqual(text, "plain text")
            self.assertEqual(metadata["provider"], "xai")
        finally:
            argo_rehearse.observe.provider_for = orig_provider
            argo_rehearse.observe.generate_observations = orig_generate

    def test_chat_with_mcp_default_return_shape_remains_plain_text(self):
        orig_provider = argo_observe.provider_for
        orig_openai = argo_observe._chat_with_mcp_openai
        try:
            argo_observe.provider_for = lambda model: {"name": "openai"}

            def fake_openai(system, messages, model, mcp_servers, max_tokens,
                            temperature, return_tool_events,
                            return_metadata=False):
                if return_metadata:
                    return "plain text", {"provider": "openai", "model": model}
                return "plain text"

            argo_observe._chat_with_mcp_openai = fake_openai
            self.assertEqual(
                argo_observe.chat_with_mcp("system", [], "gpt-5"),
                "plain text",
            )
            text, metadata = argo_observe.chat_with_mcp(
                "system", [], "gpt-5", return_metadata=True)
            self.assertEqual(text, "plain text")
            self.assertEqual(metadata["provider"], "openai")
        finally:
            argo_observe.provider_for = orig_provider
            argo_observe._chat_with_mcp_openai = orig_openai


if __name__ == "__main__":
    unittest.main()
