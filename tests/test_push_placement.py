"""Placement-aware regression tests for the acted-on-push instrumentation (F1).

The bug these lock: the proactive senders (argo_project/argo_watch) run on GitHub
Actions (an ephemeral checkout), but the push store + the reader live on the
Railway volume -- so a LOCAL argo_pushes.record() on the Actions side wrote a
filesystem the reader never sees, and act_on_rate stayed pinned at 0.0 forever.

The fix: the Actions recorder POSTs each push to the running webhook's
authenticated /push endpoint, which calls argo_pushes.record against the VOLUME.

(a) the recorder side (argo_pushes.post_to_webhook), with WEBHOOK_URL + token set,
    performs the POST and does NOT write the local filesystem; and when the POST
    raises, it returns False without propagating, so the Telegram send completes.
(b) the /push endpoint handler writes to the tmp-overridden PUSHES_PATH via
    argo_pushes.record and is idempotent on retry (each POST records a new row).

Pure -- no real network/LLM/data: urllib is mocked on the recorder side, and
PUSHES_PATH is patched to a tmp dir for the endpoint side.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import argo_pushes


class _FakeResp:
    """Minimal context-manager stand-in for urllib.request.urlopen's return."""

    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return b"{}"


class RecorderPostsToVolumeTest(unittest.TestCase):
    """(a) Actions side: POST to the webhook, never the local FS; non-fatal."""

    def setUp(self):
        # A tmp PUSHES_PATH that MUST stay untouched -- the recorder side must not
        # write any local file; it only POSTs.
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.local_path = tmp / "argo_pushes.json"
        self.enterContext(mock.patch.object(argo_pushes, "PUSHES_PATH", self.local_path))
        self.enterContext(mock.patch.dict(
            "os.environ",
            {"WEBHOOK_URL": "https://argo.example", "ARGO_MCP_TOKEN": "tok-123"},
        ))

    def test_post_hits_authenticated_endpoint_and_skips_local_fs(self):
        with mock.patch("urllib.request.urlopen", return_value=_FakeResp(200)) as up:
            result = argo_pushes.post_to_webhook("project", "a fresh project push")

        self.assertTrue(result.recorded)
        self.assertFalse(result.suppressed)
        # Exactly one POST, to /push, bearer-authenticated, with the json body.
        self.assertEqual(up.call_count, 1)
        req = up.call_args.args[0]
        self.assertEqual(req.full_url, "https://argo.example/push")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.headers.get("Authorization"), "Bearer tok-123")
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body, {"kind": "project", "content": "a fresh project push"})
        # The local Actions filesystem was NOT written (this is the whole bug).
        self.assertFalse(self.local_path.exists())

    def test_post_failure_is_non_fatal_and_writes_nothing(self):
        # A timeout / network error must be swallowed (return False), so the caller's
        # Telegram send is never blocked -- and still no local write.
        with mock.patch("urllib.request.urlopen",
                        side_effect=urllib.error.URLError("timed out")):
            result = argo_pushes.post_to_webhook("watch", "an alert")
        self.assertFalse(result.recorded)
        self.assertFalse(result.suppressed)  # a failed POST is fail-open, not suppressed
        self.assertFalse(self.local_path.exists())

    def test_unset_env_skips_silently(self):
        # Local dev with no WEBHOOK_URL/token: skip, no error, no write, no POST.
        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch("urllib.request.urlopen") as up:
                result = argo_pushes.post_to_webhook("project", "x")
        self.assertFalse(result.recorded)
        self.assertFalse(result.suppressed)
        up.assert_not_called()
        self.assertFalse(self.local_path.exists())


class SenderSurvivesPostFailureTest(unittest.TestCase):
    """(a, send-completes half): a raising POST does not abort the proactive send.

    Drives argo_project.main far enough to send the Telegram message, then have the
    instrumentation POST raise -- the send must still complete (the try/except at
    the call site, mirroring argo_memory.record, must keep the path alive).
    """

    def test_project_send_completes_when_post_raises(self):
        import argo_project

        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        out = tmp / "projects"
        sent_calls = []
        with mock.patch.object(argo_project, "ROOT", tmp), \
             mock.patch.object(argo_project, "OUT_DIR", out), \
             mock.patch.object(argo_project, "PROJECTS_LOG", tmp / "projects.json"), \
             mock.patch.object(argo_project, "_refresh_signals"), \
             mock.patch.object(argo_project, "build_project_prompt",
                               return_value=("the prompt", [])), \
             mock.patch.object(argo_project, "generate_project",
                               return_value=(("the project body", "model-x"), None)), \
             mock.patch.object(argo_project, "log_project", return_value="P-9"), \
             mock.patch.object(argo_project, "project_invite", return_value=" rate it"), \
             mock.patch.object(argo_project.send_telegram, "send_message",
                               side_effect=lambda m: sent_calls.append(m)), \
             mock.patch.object(argo_project.argo_memory, "record"), \
             mock.patch.object(argo_project.argo_pushes, "post_to_webhook",
                               side_effect=RuntimeError("boom")):
            # Must not raise despite the instrumentation blowing up.
            argo_project.main()

        # The Telegram send happened exactly once, with the project + invite -- i.e.
        # the failing post_to_webhook did NOT abort the proactive send.
        self.assertEqual(len(sent_calls), 1)
        self.assertIn("the project body", sent_calls[0])


class WatchRecordsBeforeSendTest(unittest.TestCase):
    """(a, ordering half): argo_watch.main records the push BEFORE the send.

    The 2nd home of Bugbot PR #36 finding 2: like argo_project.main, the watcher
    must call argo_pushes.post_to_webhook("watch", ...) before
    send_telegram.send_message(...), so the push row + its timestamp always precede
    any reply -- otherwise a fast user reply can timestamp ahead of its own push,
    link_reply finds no open push to link, and act_on_rate undercounts. The
    chat-memory record stays AFTER the send.
    """

    def test_post_to_webhook_precedes_send_when_an_alert_fires(self):
        import argo_watch

        order = []
        alert = "a frontier-relevant thing happened"
        with mock.patch.object(argo_watch, "load_seen", return_value={}), \
             mock.patch.object(argo_watch, "collect_new",
                               return_value=[{"title": "t", "link": "http://x"}]), \
             mock.patch.object(argo_watch, "collect_grok", return_value=[]), \
             mock.patch.object(argo_watch, "judge", return_value=[alert]), \
             mock.patch.object(argo_watch, "save_seen"), \
             mock.patch.object(
                 argo_watch.argo_pushes, "post_to_webhook",
                 side_effect=lambda *a, **k: (order.append(("post",) + a)
                                              or argo_pushes.PushResult(True, False))), \
             mock.patch.object(argo_watch.send_telegram, "send_message",
                               side_effect=lambda m: order.append(("send", m))), \
             mock.patch.object(argo_watch.argo_memory, "record",
                               side_effect=lambda *a, **k: order.append(("memory",))), \
             mock.patch.object(argo_watch.sys, "argv", ["argo_watch.py"]):
            argo_watch.main()

        # The push was recorded, the message was sent, and the memory write ran --
        # and the record-before-send ordering holds, with memory last.
        kinds = [step[0] for step in order]
        self.assertEqual(kinds, ["post", "send", "memory"])
        # The push was recorded with the "watch" kind and the alert content...
        self.assertEqual(order[0], ("post", "watch", f"🛰️ Argo spotted something:\n\n{alert}"))
        # ...and the very same message reached send_telegram.
        self.assertEqual(order[1][1], f"🛰️ Argo spotted something:\n\n{alert}")


class PushEndpointWritesVolumeTest(unittest.TestCase):
    """(b) Railway side: /push handler records to PUSHES_PATH; idempotent on retry."""

    def setUp(self):
        import argo_webhook

        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = tmp / "argo_pushes.json"
        self.enterContext(mock.patch.object(argo_pushes, "PUSHES_PATH", self.path))
        # The endpoint gates on the same bearer token as /mcp.
        self.enterContext(mock.patch.object(argo_webhook, "ARGO_MCP_TOKEN", "tok-123"))
        self.client = argo_webhook.create_app().test_client()

    def _rows(self):
        import argo_store
        return argo_store.load_json(self.path, [])

    def _post(self, body, token="tok-123"):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return self.client.post("/push", json=body, headers=headers)

    def test_authenticated_post_records_to_volume(self):
        r = self._post({"kind": "project", "content": "hello"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["id"], 1)
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "project")
        self.assertFalse(rows[0]["linked"])

    def test_retry_collapses_to_one_row(self):
        # Idempotent on retry: an at-least-once re-POST of identical (kind, content)
        # within RECORD_DEDUP_SECONDS (the first POST committed but its 2xx was lost,
        # so post_to_webhook retried) collapses to ONE row with the SAME id, so
        # act_on_rate's denominator never double-counts the one push.
        r1 = self._post({"kind": "watch", "content": "alert one"})
        r2 = self._post({"kind": "watch", "content": "alert one"})
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.get_json()["id"], r1.get_json()["id"])
        rows = self._rows()
        self.assertEqual([row["id"] for row in rows], [1])

    def test_missing_bearer_is_forbidden_and_writes_nothing(self):
        r = self._post({"kind": "project", "content": "x"}, token=None)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self._rows(), [])

    def test_wrong_bearer_is_forbidden(self):
        r = self._post({"kind": "project", "content": "x"}, token="nope")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self._rows(), [])

    def test_missing_kind_is_bad_request(self):
        r = self._post({"content": "no kind here"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self._rows(), [])

    def test_health_route_stays_open(self):
        # Only the write route is gated; '/' must not require a bearer token.
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
