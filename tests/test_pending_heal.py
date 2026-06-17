"""run_pending_heal claims the staged heal action atomically (no double-apply).

The diagnostic loop stages ONE heal action (e.g. propose_fix -> open a fix PR)
behind the CONFIRM/FIX gate. Each Telegram update is handled in its own thread, so
two near-simultaneous CONFIRM/FIX replies -- a double-tap, or FIX while the slow
first reply is still opening its PR -- used to both pass the existence check, read
the same record, and both run, opening two PRs for one incident.

run_pending_heal now CLAIMS the pending file with a single atomic os.replace before
reading it, so only one caller wins; the loser sees nothing staged.

The race is reproduced deterministically WITHOUT threads: the action is invoked
re-entrantly at the exact point a concurrent reply would land (while the first call
is parsing the staged record). With the atomic claim the file is already renamed
away by then, so the second caller gets "nothing staged"; the old read-then-unlink
left it visible, so the action ran twice.

Pure + hermetic: PENDING_HEAL_PATH overridden to a tmp dir, the slow fix is stubbed.
Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import argo_mcp_server as srv


class PendingHealClaimTest(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.path = Path(td.name) / "argo_pending_heal.json"
        self.enterContext(mock.patch.object(srv, "PENDING_HEAL_PATH", self.path))

    def test_concurrent_confirm_runs_action_once(self):
        srv.stage_fix_proposal({"incident_key": "k1", "summary": "boom"})
        calls = []
        reentrant = []           # holds the second caller's result (set once)
        real_loads = json.loads

        def loads_reentering_once(s, *a, **k):
            # Simulate a SECOND CONFIRM/FIX arriving while the first call is still
            # parsing the staged record -- the exact race window.
            if not reentrant:
                reentrant.append(None)               # guard: re-enter exactly once
                reentrant[0] = srv.run_pending_heal()
            return real_loads(s, *a, **k)

        with mock.patch.object(
                srv, "_run_propose_fix",
                side_effect=lambda payload, **k: (calls.append(payload),
                                                  "Opened PR http://pr/1")[1]), \
             mock.patch.object(srv.json, "loads", side_effect=loads_reentering_once):
            first = srv.run_pending_heal()

        self.assertEqual(len(calls), 1, "the staged heal action must run exactly once")
        self.assertIn("http://pr/1", first)
        self.assertIn("nothing staged", reentrant[0].lower())
        # the pending file is consumed and no .claim.* temp file is left behind
        self.assertFalse(self.path.exists())
        self.assertEqual(list(self.path.parent.glob("*.claim.*")), [])


if __name__ == "__main__":
    unittest.main()
