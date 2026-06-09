"""
SEAS pipeline tests: opportunity ranking and inbox ingestion.

Pure — no network, no LLM, no real data/*.json. Paths are passed as parameters
so no module-level patching is needed.

Run:  PYTHONPATH=src python3 -m unittest discover -s tests
"""
import json
import tempfile
import unittest
from pathlib import Path

import opportunities
import process_inbox


def _signal(title, d=0, l=0, a=0, ac=0, n=0, link="https://example.com"):
    return {
        "title": title,
        "source": "test",
        "category": "",
        "summary": "",
        "link": link,
        "possible_capability_unlocked": "",
        "scores": {
            "durability": d, "leverage": l, "alignment": a,
            "accessibility": ac, "novelty": n,
        },
    }


class OpportunitiesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.sig = self.tmp / "signals.json"
        self.opp = self.tmp / "opportunities.json"

    def test_ranked_highest_first(self):
        self.sig.write_text(json.dumps([
            _signal("Low",  d=1, l=1, a=1),
            _signal("High", d=5, l=5, a=5, ac=5, n=5),
            _signal("Mid",  d=3, l=3, a=3),
        ]))
        opps = opportunities.build(self.sig, self.opp)
        self.assertEqual(opps[0]["title"], "High")
        self.assertGreater(opps[0]["weighted_score"], opps[1]["weighted_score"])
        self.assertGreater(opps[1]["weighted_score"], opps[2]["weighted_score"])

    def test_qualifies_flag_set_correctly(self):
        # qualifies requires weighted_score >= 4.0 AND d/l/a >= 3
        # d=4,l=4,a=4,ac=4,n=4 -> 4*1.0 = 4.0 (exactly at threshold)
        self.sig.write_text(json.dumps([
            _signal("Qualifies", d=4, l=4, a=4, ac=4, n=4),
            _signal("Fails gate", d=2, l=2, a=2),
        ]))
        opps = opportunities.build(self.sig, self.opp)
        by_title = {o["title"]: o for o in opps}
        self.assertTrue(by_title["Qualifies"]["qualifies"])
        self.assertFalse(by_title["Fails gate"]["qualifies"])

    def test_all_zero_scores_do_not_qualify(self):
        self.sig.write_text(json.dumps([_signal("Unscored")]))
        opps = opportunities.build(self.sig, self.opp)
        self.assertFalse(opps[0]["qualifies"])
        self.assertEqual(opps[0]["weighted_score"], 0.0)

    def test_written_to_file_and_readable(self):
        self.sig.write_text(json.dumps([_signal("A", d=3, l=3, a=3)]))
        opportunities.build(self.sig, self.opp)
        self.assertTrue(self.opp.exists())
        written = json.loads(self.opp.read_text())
        self.assertEqual(written[0]["title"], "A")

    def test_empty_signals_produces_empty_file(self):
        self.sig.write_text("[]")
        opps = opportunities.build(self.sig, self.opp)
        self.assertEqual(opps, [])
        self.assertTrue(self.opp.exists())


class ProcessInboxTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.inbox = self.tmp / "signals.md"
        self.signals = self.tmp / "signals.json"

    def test_adds_new_signals(self):
        self.inbox.write_text("# Inbox\n\n- Signal Alpha\n- Signal Beta\n")
        self.signals.write_text("[]")
        added, skipped = process_inbox.process(self.inbox, self.signals)
        self.assertEqual(added, 2)
        self.assertEqual(skipped, 0)
        pool = json.loads(self.signals.read_text())
        self.assertEqual({s["title"] for s in pool}, {"Signal Alpha", "Signal Beta"})

    def test_dedupes_against_existing(self):
        self.inbox.write_text("- Existing Title\n- Genuinely New\n")
        self.signals.write_text(json.dumps([_signal("Existing Title")]))
        added, skipped = process_inbox.process(self.inbox, self.signals)
        self.assertEqual(added, 1)
        self.assertEqual(skipped, 1)
        pool = json.loads(self.signals.read_text())
        self.assertEqual(len(pool), 2)

    def test_inbox_cleared_after_processing(self):
        self.inbox.write_text("- Something interesting\n")
        self.signals.write_text("[]")
        process_inbox.process(self.inbox, self.signals)
        content = self.inbox.read_text()
        self.assertNotIn("- Something interesting", content)
        self.assertIn("Last Processed", content)

    def test_missing_inbox_is_noop(self):
        added, skipped = process_inbox.process(
            self.tmp / "does_not_exist.md", self.signals)
        self.assertEqual(added, 0)
        self.assertEqual(skipped, 0)
        self.assertFalse(self.signals.exists())

    def test_inbox_signal_gets_zeroed_scores(self):
        self.inbox.write_text("- Brand new signal\n")
        self.signals.write_text("[]")
        process_inbox.process(self.inbox, self.signals)
        pool = json.loads(self.signals.read_text())
        scores = pool[0]["scores"]
        self.assertTrue(all(v == 0 for v in scores.values()))

    def test_source_set_to_inbox(self):
        self.inbox.write_text("- My manual signal\n")
        self.signals.write_text("[]")
        process_inbox.process(self.inbox, self.signals)
        pool = json.loads(self.signals.read_text())
        self.assertEqual(pool[0]["source"], "inbox")


if __name__ == "__main__":
    unittest.main()
