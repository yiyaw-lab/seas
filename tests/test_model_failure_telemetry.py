"""Silent model-failure telemetry: a non-empty reply that should have been JSON
but didn't parse used to be dropped (return None) and 'disappear'. It now records
a model_failure incident so the diagnose loop can see it and the structured-outputs
prediction is measurable.

Regression guard for the gap Argo surfaced ("structured outputs not adopted --
silent JSON parse failures disappear"). Pure: incidents path is redirected to tmp.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import argo_incidents as inc  # noqa: E402
import argo_evolve as ev  # noqa: E402
import argo_self  # noqa: E402
import world_model as wm  # noqa: E402


def _kinds(store):
    return [c.get("kind") for c in store.values() if isinstance(c, dict)]


class RecordModelFailureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.enterContext(mock.patch.object(inc, "INCIDENTS_PATH",
                                            Path(self.tmp) / "inc.json"))

    def test_records_incident_for_unparseable_reply(self):
        key = inc.record_model_failure("evolve mapper: unparseable JSON",
                                       "here is a thought, but no json at all")
        self.assertIsNotNone(key)
        self.assertIn("model_failure", _kinds(inc._load()))

    def test_noop_on_empty_reply(self):
        key = inc.record_model_failure("evolve mapper: unparseable JSON", "   ")
        self.assertIsNone(key)
        self.assertEqual(inc._load(), {})  # an empty reply is infra failure, not a model failure

    def test_diagnose_signature_matches_prediction_cluster_key(self):
        # argo_diagnose records under this exact signature; it must fingerprint to the
        # cluster key the structured_outputs seed prediction watches, or the failures
        # are invisible to its incident_absent metric (Bugbot #58).
        key = inc.record_model_failure("diagnose json parse failed", "oops, not json")
        self.assertEqual(key, "model_failure|diagnose json parse failed")


class EvolveMapperTelemetryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.enterContext(mock.patch.object(inc, "INCIDENTS_PATH",
                                            Path(self.tmp) / "inc.json"))

    def test_unparseable_mapper_reply_records_model_failure(self):
        items = [{"source": "s", "title": "t", "summary": "u"}]
        fake_observe = mock.MagicMock()
        fake_observe.provider_for.return_value = {"name": "anthropic"}
        fake_observe.chat_with_mcp.return_value = "sure, here's a thought but no JSON"
        with mock.patch.object(ev, "_resolve_model", return_value="claude-sonnet-4-6"), \
                mock.patch.object(ev, "_active_features", return_value=set()), \
                mock.patch.object(ev, "_repo_files", return_value=[]), \
                mock.patch.object(argo_self, "format_self_for_prompt", return_value=""), \
                mock.patch.object(wm, "format_beliefs_for_prompt", return_value=""), \
                mock.patch.dict(sys.modules, {"argo_observe": fake_observe}):
            out = ev._map_levers(items)
        self.assertIsNone(out)  # unparseable reply still yields None (unchanged)
        self.assertIn("model_failure", _kinds(inc._load()))  # ... but no longer silent


if __name__ == "__main__":
    unittest.main()
