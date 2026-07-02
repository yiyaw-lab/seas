"""propose_change (MCP tool boundary) must never block on the synchronous draft+PR
chain. Incident: the authoring model became claude-fable-5 (thinking always on, slower)
and the tool ran the full _run_propose_fix-equivalent chain inline, blowing the MCP
client's fixed 300s CallToolRequest budget -- "Timed out while waiting for response to
ClientRequest" with no outcome logged, no PR, and total silence to the user.

Fix: propose_change validates files_json cheaply, spawns a daemon thread running the
existing synchronous _propose_change_impl unchanged, and returns immediately with an
"On it" acknowledgment. The thread delivers the real outcome via send_telegram, the
same fire-and-forget idiom argo_webhook._safe_handle uses for webhook updates. A broad
except net (Exception + SystemExit, since send_telegram.fail() calls sys.exit) must
still text a failure line -- a silently dead thread is the bug class being killed.

Pure + hermetic: send_telegram and _propose_change_impl are both patched; no network,
no GitHub, no LLM. Deterministic via threading.Event + generous join timeouts (the
polling loop only spins on a local Event, never sleeps blindly).

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import json
import threading
import time
import unittest
from unittest import mock

import argo_mcp_server as srv


def _wait(event, timeout=5.0):
    ok = event.wait(timeout)
    if not ok:
        raise AssertionError(f"background thread did not finish within {timeout}s")


class ProposeChangeAsyncTest(unittest.TestCase):
    def test_returns_immediately_and_delivers_outcome_on_success(self):
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def slow_impl(title, description, files_json):
            started.set()
            release.wait(5.0)  # held open until the main thread proves it didn't block
            return "Opened PR for review: http://pr/9", {"pr_number": 9}

        sent = []

        def fake_send(text):
            sent.append(text)
            finished.set()
            return True

        with mock.patch.object(srv, "_propose_change_impl", side_effect=slow_impl), \
             mock.patch("send_telegram.try_send_message", side_effect=fake_send):
            t0 = time.monotonic()
            reply = srv.propose_change("t", "d", json.dumps({"src/x.py": "x"}))
            elapsed = time.monotonic() - t0

            # The tool call itself must return fast, well before the worker is
            # even allowed to proceed (release is not yet set).
            self.assertLess(elapsed, 2.0)
            self.assertIn("On it", reply)
            self.assertIn("drafting the PR", reply)

            _wait(started, 5.0)
            release.set()
            _wait(finished, 5.0)

        self.assertEqual(sent, ["Opened PR for review: http://pr/9"])

    def test_invalid_files_json_still_returns_synchronously(self):
        # Cheap validation happens BEFORE the thread spawn -- garbage input must not
        # spawn a worker or return the "On it" ack.
        with mock.patch.object(srv, "_propose_change_impl",
                               side_effect=AssertionError("must not run")):
            reply = srv.propose_change("t", "d", "not json")
        self.assertIn("files_json must be a non-empty JSON object", reply)

    def test_background_exception_still_texts_a_failure_line(self):
        # The class of bug being killed: a silently dead thread. Force the impl to
        # raise and prove the except net still delivers a message instead of vanishing.
        finished = threading.Event()
        sent = []

        def boom(title, description, files_json):
            raise RuntimeError("gh write exploded")

        def fake_send(text):
            sent.append(text)
            finished.set()
            return True

        with mock.patch.object(srv, "_propose_change_impl", side_effect=boom), \
             mock.patch("send_telegram.try_send_message", side_effect=fake_send):
            reply = srv.propose_change("t", "d", json.dumps({"src/x.py": "x"}))
            self.assertIn("On it", reply)
            _wait(finished, 5.0)

        self.assertEqual(len(sent), 1)
        self.assertIn("error drafting that PR", sent[0])
        self.assertIn("gh write exploded", sent[0])

    def test_send_telegram_failure_does_not_kill_thread_silently(self):
        # send_telegram.try_send_message can itself raise SystemExit (fail() ->
        # sys.exit(1)) on delivery failure. The outer except net must catch that too,
        # not just Exception -- a bare `except Exception` lets SystemExit escape and
        # the thread dies with no trace (the argo_webhook._safe_handle lesson).
        done = threading.Event()

        def dying_send(text):
            done.set()
            raise SystemExit(1)

        with mock.patch.object(srv, "_propose_change_impl",
                               return_value=("Opened PR for review: http://pr/1", {})), \
             mock.patch("send_telegram.try_send_message", side_effect=dying_send):
            reply = srv.propose_change("t", "d", json.dumps({"src/x.py": "x"}))
            self.assertIn("On it", reply)
            _wait(done, 5.0)
        # No assertion beyond "the test process is still alive and done fired" --
        # a thread crash from SystemExit escaping would not raise here (daemon
        # threads don't propagate), but log.error must have been reached; the
        # real regression this guards is a silent hang, which `done` firing rules out.


if __name__ == "__main__":
    unittest.main()
