"""Project-selection regression tests — the "another" / wrong-project area.

Locks two fixes: a bare rating/SELECT targets the project the user is LOOKING at
(last shown, not last generated), and pasting a project Argo already sent
re-anchors to it instead of being misread as a brand-new idea.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import argo_webhook as wh


class TargetProjectTest(unittest.TestCase):
    def test_explicit_id_wins(self):
        log = [{"id": "P-001"}, {"id": "P-002", "shown_at": "2026-06-01T00:00:00"}]
        self.assertEqual(wh._target_project(log, "P-001")["id"], "P-001")

    def test_explicit_id_missing_returns_none(self):
        self.assertIsNone(wh._target_project([{"id": "P-001"}], "P-999"))

    def test_last_shown_not_last_generated(self):
        # P-004 is the newest in the log but was never shown; the rating belongs to
        # P-002, the most-recently-shown project the user is actually looking at.
        log = [
            {"id": "P-001", "shown_at": "2026-06-01T00:00:00"},
            {"id": "P-002", "shown_at": "2026-06-03T00:00:00"},
            {"id": "P-003"},
            {"id": "P-004"},  # newest generated, never shown
        ]
        self.assertEqual(wh._target_project(log)["id"], "P-002")

    def test_falls_back_to_last_when_none_shown(self):
        log = [{"id": "P-001"}, {"id": "P-002"}]
        self.assertEqual(wh._target_project(log)["id"], "P-002")

    def test_empty_log_returns_none(self):
        self.assertIsNone(wh._target_project([]))


class MatchExistingProjectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.tmp / "argo_projects.json"
        self.enterContext(mock.patch.object(wh, "PROJECTS_LOG", self.path))
        self.body = ("Build a small benchmark for agent tool-use latency. "
                     "It produces a public repo and a short writeup.")
        self.path.write_text(json.dumps([{"id": "P-001", "text": self.body}]))

    def test_paste_of_body_reanchors(self):
        paste = "build a small benchmark for agent tool-use latency"
        self.assertEqual(wh._match_existing_project(paste)["id"], "P-001")

    def test_unrelated_text_is_none(self):
        self.assertIsNone(wh._match_existing_project(
            "what's the latest from the frontier labs this week"))

    def test_too_short_is_none(self):
        self.assertIsNone(wh._match_existing_project("yes"))

    def test_missing_file_is_none(self):
        self.path.unlink()
        self.assertIsNone(wh._match_existing_project(self.body))


if __name__ == "__main__":
    unittest.main()
