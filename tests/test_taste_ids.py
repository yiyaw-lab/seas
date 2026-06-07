"""Taste-store id tests (taste_signals): ids must not reset to T-001 or collide.

Pure -- TASTE_PATH is patched to a tmp file. Regression for the live bug where a
redeploy wiped the (non-volume) store and every screenshot came back as 'T-001',
plus the latent len()+1 collision when a signal is ever removed.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import taste_signals


class TasteIdTest(unittest.TestCase):
    def setUp(self):
        base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = base / "taste_signals.json"
        self.enterContext(mock.patch.object(taste_signals, "TASTE_PATH", self.path))

    def test_ids_increment_from_empty(self):
        a = taste_signals.save_signal("appA", "single-field capture",
                                      "low friction", "", "url")
        b = taste_signals.save_signal("appB", "calm density", "less is more", "", "url")
        self.assertEqual(a["id"], "T-001")
        self.assertEqual(b["id"], "T-002")
        self.assertTrue(self.path.exists())  # writes go to the patched (volume) path

    def test_next_id_is_max_not_len_survives_deletion(self):
        taste_signals.save_signal("a", "p1", "l", "", "url")  # T-001
        taste_signals.save_signal("b", "p2", "l", "", "url")  # T-002
        # remove T-001: a len()+1 scheme would now re-issue T-002 (collision);
        # max+1 must yield T-003.
        items = [s for s in taste_signals._load() if s["id"] != "T-001"]
        taste_signals._save(items)
        c = taste_signals.save_signal("c", "p3", "l", "", "url")
        self.assertEqual(c["id"], "T-003")
        self.assertEqual([s["id"] for s in taste_signals._load()], ["T-002", "T-003"])

    def test_parse_and_store_uses_hardened_id(self):
        taste_signals.save_signal("seed", "p", "l", "", "url")  # T-001
        extraction = json.dumps(
            {"what": "x", "pattern": "y", "liked": "z", "steal": "w"})
        sig, summary = taste_signals.parse_and_store(extraction)
        self.assertEqual(sig["id"], "T-002")
        self.assertTrue(summary)

    def test_next_id_ignores_malformed_ids(self):
        # a junk/missing id in the store must not blow up id derivation
        taste_signals._save([{"id": "weird"}, {"id": "T-004"}, {}])
        self.assertEqual(taste_signals._next_id(taste_signals._load()), "T-005")


if __name__ == "__main__":
    unittest.main()
