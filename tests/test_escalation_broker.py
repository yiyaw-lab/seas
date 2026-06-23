"""F7 escalation broker: ask_owner / get_owner_answers on Argo's /mcp.

A credential-less cloud caller (e.g. a scheduled /vacation run) has neither the
Railway volume nor the Telegram secrets, so it brokers owner decisions through
Argo: ask_owner Telegrams a question and records an OPEN pending decision;
get_owner_answers reads the chat log, matches the owner's reply to the
MOST-RECENT open decision, and marks it answered.

Pure + hermetic: PENDING_DECISIONS_PATH and the chat log (argo_memory.CHAT_LOG_PATH)
are overridden to a tmp dir, and send_telegram is mocked -- no network, no real
data/*.json. The @mcp.tool bodies are async (the with_deadline wrapper), so they
are driven with asyncio.run, exactly as test_tool_offload does.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import argo_memory
import argo_mcp_server as srv
import argo_store


class EscalationBrokerTest(unittest.TestCase):
    def setUp(self):
        base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.decisions = base / "argo_pending_decisions.json"
        self.chat = base / "argo_chat.json"
        self.enterContext(
            mock.patch.object(srv, "PENDING_DECISIONS_PATH", self.decisions))
        self.enterContext(
            mock.patch.object(argo_memory, "CHAT_LOG_PATH", self.chat))
        # get_owner_answers scopes the chat read to TELEGRAM_CHAT_ID (the owner's
        # conversation); the test logs turns under chat_id "123", so point it there.
        self.enterContext(
            mock.patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "123"}))

    def _load_decisions(self):
        return argo_store.load_json(self.decisions, [])

    def _ask(self, question, sent=True):
        """ask_owner with send_telegram.try_send_message mocked to `sent`.
        Returns the mock so callers can assert it was actually called."""
        with mock.patch("send_telegram.try_send_message",
                        return_value=sent) as send:
            out = asyncio.run(srv.ask_owner(question))
        return out, send

    # (a) ask_owner records an OPEN pending decision AND calls send -------------
    def test_ask_owner_records_open_decision_and_sends(self):
        out, send = self._ask("Ship the redesign or hold?")
        send.assert_called_once()                       # Telegram send happened
        self.assertIn("the redesign", send.call_args[0][0])  # question relayed
        recs = self._load_decisions()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["status"], "open")
        self.assertEqual(recs[0]["question"], "Ship the redesign or hold?")
        self.assertTrue(recs[0]["id"].startswith("D-"))
        self.assertIn(recs[0]["id"], out)               # id returned to poll on

    def test_ids_are_deterministic_and_increment(self):
        self._ask("q1")
        self._ask("q2")
        ids = [r["id"] for r in self._load_decisions()]
        self.assertEqual(ids, ["D-001", "D-002"])

    def test_empty_question_refused_and_records_nothing(self):
        out = asyncio.run(srv.ask_owner("   "))
        self.assertIn("Refused", out)
        self.assertEqual(self._load_decisions(), [])

    # (b) get_owner_answers matches a reply to the MOST-RECENT open decision -----
    def test_get_owner_answers_matches_and_marks_answered(self):
        self._ask("Should I merge PR 41?")
        decision_id = self._load_decisions()[0]["id"]
        # The owner replies AFTER the question was asked (later ts).
        argo_memory.record("123", "Yiya", "yes go ahead")
        out = json.loads(asyncio.run(srv.get_owner_answers()))
        self.assertEqual(out, {"id": decision_id, "answer": "yes go ahead"})
        # Marked answered + answer stored; not matchable again.
        rec = self._load_decisions()[0]
        self.assertEqual(rec["status"], "answered")
        self.assertEqual(rec["answer"], "yes go ahead")

    def test_matches_most_recent_open_decision(self):
        # Two open decisions; the owner's next reply answers the NEWER one.
        self._ask("older question")
        self._ask("newer question")
        older_id, newer_id = (r["id"] for r in self._load_decisions())
        argo_memory.record("123", "Yiya", "answer to the newer one")
        out = json.loads(asyncio.run(srv.get_owner_answers()))
        self.assertEqual(out["id"], newer_id)
        statuses = {r["id"]: r["status"] for r in self._load_decisions()}
        self.assertEqual(statuses[newer_id], "answered")
        self.assertEqual(statuses[older_id], "open")    # older untouched

    # (c) no open decision / no matching reply -> graceful no-match -------------
    def test_no_open_decision_returns_no_match(self):
        argo_memory.record("123", "Yiya", "a stray message")  # log not empty
        out = asyncio.run(srv.get_owner_answers())
        self.assertNotIn("{", out)                      # not a JSON match payload
        self.assertIn("No open decisions", out)

    def test_reply_before_question_does_not_match(self):
        # An owner message that PRE-dates the question can't be its answer. Use
        # explicit, distinct-second timestamps so the floor (the question's ts)
        # excludes the stale reply -- the real production case (whole-second log).
        argo_store.save_json(self.chat, [
            {"ts": "2026-06-20T11:59:00Z", "chat_id": "123",
             "role": "Yiya", "text": "stale chatter from before"},
        ])
        argo_store.save_json(self.decisions, [
            {"id": "D-001", "ts": "2026-06-20T12:00:00Z",
             "question": "a brand new question", "status": "open"},
        ])
        out = asyncio.run(srv.get_owner_answers())
        self.assertIn("still open", out)
        self.assertEqual(self._load_decisions()[0]["status"], "open")

    def test_reply_from_other_chat_does_not_match(self):
        # A message in a DIFFERENT conversation must never be matched as the
        # owner's answer (the read is scoped to TELEGRAM_CHAT_ID).
        self._ask("owner-only question")
        argo_memory.record("999", "Someone", "reply in another chat")
        out = asyncio.run(srv.get_owner_answers())
        self.assertIn("still open", out)
        self.assertEqual(self._load_decisions()[0]["status"], "open")

    def test_argo_own_message_is_not_treated_as_reply(self):
        self._ask("waiting on you")
        argo_memory.record("123", "Argo", "a follow-up nudge from Argo")
        out = asyncio.run(srv.get_owner_answers())
        self.assertIn("still open", out)                # Argo's own turn ignored

    def test_since_filters_out_earlier_replies(self):
        self._ask("decide this")
        argo_memory.record("123", "Yiya", "an early reply")
        # `since` far in the future excludes the reply -> still open.
        out = asyncio.run(srv.get_owner_answers(since="2999-01-01T00:00:00Z"))
        self.assertIn("still open", out)

    # the new tools ride the SAME bearer-gated /mcp mount -----------------------
    def test_tools_registered_on_mcp_instance_and_deadline_wrapped(self):
        # Both tools are @mcp.tool() on the one `mcp` FastMCP instance that
        # create_asgi_app mounts under the BearerAuth middleware, so they inherit
        # the bearer gate (no new endpoint, no bypass). They are with_deadline-
        # wrapped, so the body is async -- same posture as every other tool.
        for name in ("ask_owner", "get_owner_answers"):
            fn = getattr(srv, name)
            self.assertTrue(asyncio.iscoroutinefunction(fn), name)

    # send-failure path: no phantom OPEN decision -------------------------------
    def test_send_failure_marks_decision_unmatched(self):
        out, send = self._ask("undeliverable question", sent=False)
        send.assert_called_once()
        self.assertIn("could NOT deliver", out)
        rec = self._load_decisions()[0]
        self.assertEqual(rec["status"], "send_failed")
        # A reply now must NOT be matched to the failed (never-delivered) decision.
        argo_memory.record("123", "Yiya", "a later message")
        ans = asyncio.run(srv.get_owner_answers())
        self.assertIn("No open decisions", ans)


class PendingDecisionsPathTest(unittest.TestCase):
    """The new store must be volume/env-overridable like every other Argo store,
    and argo_mcp_server must source its path from argo_paths (mirrors
    test_paths_pending_heal)."""

    def _reload_clean(self):
        import importlib
        import os
        import argo_paths
        env = dict(os.environ)
        env.pop("ARGO_PENDING_DECISIONS_PATH", None)
        with mock.patch.dict(os.environ, env, clear=True):
            importlib.reload(argo_paths)

    def setUp(self):
        self.addCleanup(self._reload_clean)

    def test_honors_volume_override(self):
        import importlib
        import os
        import argo_paths
        with mock.patch.dict(
                os.environ,
                {"ARGO_PENDING_DECISIONS_PATH": "/vol/data/argo_pending_decisions.json"}):
            importlib.reload(argo_paths)
            self.assertEqual(str(argo_paths.PENDING_DECISIONS_PATH),
                             "/vol/data/argo_pending_decisions.json")

    def test_mcp_server_sources_path_from_argo_paths(self):
        import argo_paths
        self.assertEqual(srv.PENDING_DECISIONS_PATH, argo_paths.PENDING_DECISIONS_PATH)


if __name__ == "__main__":
    unittest.main()
