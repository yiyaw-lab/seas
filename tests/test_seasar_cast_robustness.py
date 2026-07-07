"""CAST failure-classification tests.

Pure stdlib, no model calls. These tests lock the first CAST robustness slice:
truncated/malformed CAST output is classified, safely logged, and never persists
an invalid build order.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

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


def _minimal_partial():
    return {
        "title": "Tiny CLI",
        "tagline": "Small and testable",
        "stack": "Python",
        "scope": "mvp",
        "agent_count": 1,
        "constitution": [],
        "spec": {
            "what": "Build a tiny CLI.",
            "why": "Exercise CAST recovery.",
            "acceptance_criteria": [
                "WHEN the command runs THE SYSTEM SHALL print ok"
            ],
            "non_goals": [],
            "examples": [{"input": "run", "output": "ok"}],
        },
        "latent_requirements": [],
        "repo_scaffold": [],
        "contracts": [],
        "tasks": [{
            "id": "T1",
            "title": "Build CLI",
            "wave": 1,
            "depends_on": [],
            "files": ["src/cli.py"],
            "agent_role": "Backend",
            "test": "PYTHONPATH=src python -m unittest",
            "acceptance": "tests pass",
        }],
        "work_orders": [{
            "agent": "Agent A",
            "role": "Backend",
            "task_ids": ["T1"],
            "worktree": "wt/agent-a",
            "brief": "Build the CLI.",
            "definition_of_done": "Tests pass.",
        }],
        "quality_gates": [],
        "orchestration": {
            "topology": "orchestrator-worker",
            "waves": [["T1"]],
            "consistency_check": "Task ids match waves.",
            "handoff_protocol": "Single agent commits after tests.",
            "contract_evolution": "No shared contracts.",
        },
        "fixtures": [],
        "scaffold_files": [],
        "decisions": [],
        "hardening": [],
        "provisions": [],
    }


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
        self._orig_retries = sc.CAST_RECOVERY_RETRIES
        self._orig_recovery_tokens = sc.CAST_RECOVERY_MAX_TOKENS
        self.tmp = tempfile.TemporaryDirectory()
        sc.CAST_FAILURES_PATH = os.path.join(self.tmp.name, "cast-failures.jsonl")
        sc.rehearse._runnable = lambda model: model
        sc.CAST_RECOVERY_RETRIES = 1
        sc.CAST_RECOVERY_MAX_TOKENS = 16000

    def tearDown(self):
        sc.rehearse._call = self._orig_call
        sc.rehearse._runnable = self._orig_runnable
        sc.CAST_FAILURES_PATH = self._orig_failures
        sc.CAST_RECOVERY_RETRIES = self._orig_retries
        sc.CAST_RECOVERY_MAX_TOKENS = self._orig_recovery_tokens
        self.tmp.cleanup()

    def _stub_call(self, text, metadata):
        def fake_call(*args, **kwargs):
            self.assertTrue(kwargs.get("return_metadata"))
            return text, metadata
        sc.rehearse._call = fake_call

    def test_max_tokens_parse_failure_is_cast_truncated(self):
        sc.CAST_RECOVERY_RETRIES = 0
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
        self.assertEqual(err.attempt, 1)
        self.assertTrue(err.retries_exhausted)

    def test_malformed_complete_json_is_not_truncation(self):
        calls = []

        def fake_call(*args, **kwargs):
            calls.append(kwargs)
            return '{"title": !!!}', {
                "provider": "anthropic",
                "model": "claude-opus-4-8",
                "stop_reason": "end_turn",
                "max_tokens": 16000,
            }

        sc.rehearse._call = fake_call

        with self.assertRaises(sc.CastParseError) as cm:
            sc.cast("idea", _brief(), "mvp", 1, _critiques(), [])

        err = cm.exception
        self.assertEqual(err.code, "cast_malformed_json")
        self.assertEqual(err.stop_reason, "end_turn")
        self.assertEqual(len(calls), 1)

    def test_truncated_cast_retries_once_and_returns_recovered_order(self):
        calls = []
        recovered = json.dumps(_minimal_partial())

        def fake_call(system, prompt, model, temperature, max_tokens,
                      return_metadata=False):
            calls.append({"prompt": prompt, "max_tokens": max_tokens})
            self.assertTrue(return_metadata)
            if len(calls) == 1:
                return '{"title": "x"', {
                    "provider": "anthropic",
                    "model": "claude-opus-4-8",
                    "stop_reason": "max_tokens",
                    "max_tokens": 16000,
                }
            return recovered, {
                "provider": "anthropic",
                "model": "claude-opus-4-8",
                "stop_reason": "end_turn",
                "max_tokens": 16000,
            }

        sc.rehearse._call = fake_call

        partial, model, raw, attempts = sc.cast(
            "idea", _brief(), "mvp", 1, _critiques(), [], return_attempts=True)

        self.assertEqual(partial["title"], "Tiny CLI")
        self.assertEqual(model, "claude-opus-4-8")
        self.assertEqual(raw, recovered)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0]["stage"], "cast")
        self.assertEqual(attempts[1]["stage"], "cast:recovery1")
        self.assertIn("Retry from scratch", calls[1]["prompt"])

        with open(sc.CAST_FAILURES_PATH) as fh:
            record = json.loads(fh.read())
        self.assertEqual(record["attempt"], 1)
        self.assertEqual(record["attempt_kind"], "initial")
        self.assertFalse(record["final"])
        self.assertTrue(record["will_retry"])

    def test_truncated_recovery_exhaustion_stays_typed(self):
        responses = [
            ('{"title": "x"', "max_tokens"),
            ('{"title": "still open"', "max_tokens"),
        ]

        def fake_call(*args, **kwargs):
            text, stop_reason = responses.pop(0)
            return text, {
                "provider": "anthropic",
                "model": "claude-opus-4-8",
                "stop_reason": stop_reason,
                "max_tokens": 16000,
            }

        sc.rehearse._call = fake_call

        with self.assertRaises(sc.CastParseError) as cm:
            sc.cast("idea", _brief(), "mvp", 1, _critiques(), [])

        err = cm.exception
        self.assertEqual(err.code, "cast_truncated")
        self.assertEqual(err.attempt, 2)
        self.assertEqual(err.attempt_kind, "recovery")
        self.assertTrue(err.recovery_attempted)
        self.assertTrue(err.retries_exhausted)
        self.assertEqual(err.to_event()["max_attempts"], 2)

        with open(sc.CAST_FAILURES_PATH) as fh:
            records = [json.loads(line) for line in fh]
        self.assertEqual([r["attempt"] for r in records], [1, 2])
        self.assertEqual([r["will_retry"] for r in records], [True, False])
        self.assertEqual([r["final"] for r in records], [False, True])

    def test_malformed_recovery_does_not_retry_again(self):
        calls = []

        def fake_call(*args, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return '{"title": "x"', {
                    "provider": "anthropic",
                    "model": "claude-opus-4-8",
                    "stop_reason": "max_tokens",
                    "max_tokens": 16000,
                }
            return '{"title": !!!}', {
                "provider": "anthropic",
                "model": "claude-opus-4-8",
                "stop_reason": "end_turn",
                "max_tokens": 16000,
            }

        sc.CAST_RECOVERY_RETRIES = 2
        sc.rehearse._call = fake_call

        with self.assertRaises(sc.CastParseError) as cm:
            sc.cast("idea", _brief(), "mvp", 1, _critiques(), [])

        self.assertEqual(cm.exception.code, "cast_malformed_json")
        self.assertEqual(cm.exception.attempt, 2)
        self.assertEqual(len(calls), 2)

    def test_legacy_three_tuple_return_shape_is_preserved(self):
        self._stub_call(json.dumps(_minimal_partial()), {
            "provider": "anthropic",
            "model": "claude-opus-4-8",
            "stop_reason": "end_turn",
            "max_tokens": 16000,
        })

        result = sc.cast("idea", _brief(), "mvp", 1, _critiques(), [])
        self.assertEqual(len(result), 3)

    def test_malformed_complete_json_is_not_truncation_legacy_stub(self):
        sc.CAST_RECOVERY_RETRIES = 0
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
        sc.CAST_RECOVERY_RETRIES = 0
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
        self._orig_record_cost = sc._record_cost
        sc.ORDERS_DIR = Path(self.tmp.name) / "orders"
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
        sc._record_cost = self._orig_record_cost
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

    def test_compile_stream_threads_recovered_cast_attempts_to_costing(self):
        captured = {}
        attempts = [
            {"stage": "cast", "model": "claude-opus-4-8",
             "input": "initial prompt", "output": "truncated"},
            {"stage": "cast:recovery1", "model": "claude-opus-4-8",
             "input": "recovery prompt", "output": json.dumps(_minimal_partial())},
        ]

        def recovered_cast(*args, **kwargs):
            self.assertTrue(kwargs.get("return_attempts"))
            return _minimal_partial(), "claude-opus-4-8", attempts[-1]["output"], attempts

        def record_cost(order, stages, compile_ms):
            captured["stages"] = stages

        sc.cast = recovered_cast
        sc._record_cost = record_cost

        events = [json.loads(chunk[len("data: "):]) for chunk in sc.compile_stream("idea")]
        self.assertEqual(events[-1]["stage"], "complete")
        self.assertTrue(os.path.exists(sc.ORDERS_DIR))

        cast_stages = [s["stage"] for s in captured["stages"]
                       if str(s["stage"]).startswith("cast")]
        self.assertEqual(cast_stages, ["cast", "cast:recovery1"])


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
