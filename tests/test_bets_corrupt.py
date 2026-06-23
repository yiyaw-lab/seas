"""load_bets_log must fail LOUD and SAFE on a corrupt bets ledger.

A corrupt data/argo_bets.json used to crash the caller with a raw
JSONDecodeError (loud but unsafe). The naive fix -- swallow and return [] --
is unsafe the other way: the next save_bets_log would persist that empty list
and silently erase real bet history. The contract this pins:

  loud  -> an ERROR is logged (operator sees the corruption),
  safe  -> no exception escapes, the corrupt bytes are preserved for recovery,
           and the original path is cleared so the next save writes cleanly.

Pure test: overrides argo.BETS_PATH to a tmp dir, no network, no real data file.
"""
import tempfile
import unittest
from pathlib import Path

import argo


class LoadBetsLogCorrupt(unittest.TestCase):
    def setUp(self):
        self._orig_path = argo.BETS_PATH
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        argo.BETS_PATH = self.tmp / "argo_bets.json"

    def tearDown(self):
        argo.BETS_PATH = self._orig_path

    # --- negative controls (the guard must not change the happy paths) ---
    def test_missing_file_returns_empty(self):
        self.assertEqual(argo.load_bets_log(), [])

    def test_valid_file_round_trips(self):
        argo.save_bets_log([{"id": "b1", "energy_actual": 7}])
        self.assertEqual(argo.load_bets_log(), [{"id": "b1", "energy_actual": 7}])

    # --- the fix ---
    def test_corrupt_file_is_loud_and_safe(self):
        argo.BETS_PATH.write_text("{ this is not valid json")

        with self.assertLogs("argo", level="ERROR") as cm:   # LOUD
            result = argo.load_bets_log()

        self.assertEqual(result, [])                          # SAFE: no crash, empty
        self.assertTrue(any("corrupt" in line for line in cm.output))

        backup = argo.BETS_PATH.with_name(argo.BETS_PATH.name + ".corrupt")
        self.assertTrue(backup.exists())                      # SAFE: bytes preserved
        self.assertEqual(backup.read_text(), "{ this is not valid json")
        # original cleared so a subsequent save can't clobber the preserved copy
        self.assertFalse(argo.BETS_PATH.exists())

    def test_save_after_corruption_does_not_touch_preserved_copy(self):
        argo.BETS_PATH.write_text("{corrupt")
        with self.assertLogs("argo", level="ERROR"):
            argo.load_bets_log()
        argo.save_bets_log([{"id": "fresh"}])                 # caller continues
        backup = argo.BETS_PATH.with_name(argo.BETS_PATH.name + ".corrupt")
        self.assertEqual(backup.read_text(), "{corrupt")      # recovery copy intact
        self.assertEqual(argo.load_bets_log(), [{"id": "fresh"}])


if __name__ == "__main__":
    unittest.main()
