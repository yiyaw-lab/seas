"""Deterministic proposal-route tests (argo_webhook.handle_update).

Regression for the phantom-send bug: "give me a proposal" / "add my idea: X" must
deliver a REAL artifact straight from the webhook (make_proposal -> _deliver_proposal)
instead of relying on the model to fire new_project/add_project and then narrate
"sending" without doing it. Re-show asks ("the one you sent") must NOT generate a
new project -- they fall through to the model's get_latest_project.

Pure + hermetic: the two modules the route imports lazily (argo_project,
argo_mcp_server) are faked via sys.modules, so there's no mcp/FastMCP/LLM/network.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import argo_memory
import argo_observe as observe
import argo_webhook as wh


def _update(text, chat_id=777):
    return {"update_id": 1, "message": {"chat": {"id": chat_id}, "text": text}}


class ProposalRouteTest(unittest.TestCase):
    def setUp(self):
        base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.object(argo_memory, "CHAT_LOG_PATH", base / "chat.json"))
        self.enterContext(mock.patch.object(wh, "PROJECTS_LOG", base / "projects.json"))
        self.sent = []
        self.enterContext(mock.patch.object(
            wh.send_telegram, "send_message", lambda t: self.sent.append(t)))

        # Fake the two lazily-imported modules so the test is hermetic and we can
        # spy on the calls the route makes.
        self.made = []

        def fake_make(refresh=True, seed="", source="argo"):
            self.made.append({"refresh": refresh, "seed": seed, "source": source})
            return ("P-009", "pitch line", "body text", "FULL DOC", "claude-sonnet-4-6")
        fake_project = types.ModuleType("argo_project")
        fake_project.make_proposal = fake_make

        self.delivered = []

        def fake_deliver(pid, pitch, doc):
            self.delivered.append((pid, pitch, doc))
            return "[Pitch + full proposal sent to the user. Just acknowledge.]"
        fake_mcp = types.ModuleType("argo_mcp_server")
        fake_mcp._deliver_proposal = fake_deliver

        self.enterContext(mock.patch.dict(
            sys.modules, {"argo_project": fake_project, "argo_mcp_server": fake_mcp}))

        # The deterministic route must NOT reach the model.
        self.enterContext(mock.patch.object(
            observe, "chat_with_mcp",
            mock.Mock(side_effect=AssertionError("model path must not run"))))

    def test_new_bet_request_fires_delivery_deterministically(self):
        wh.handle_update(_update("give me another proposal"))
        self.assertEqual(len(self.made), 1)
        self.assertEqual(self.made[0]["seed"], "")
        self.assertEqual(self.made[0]["source"], "argo")
        self.assertEqual(self.delivered, [("P-009", "pitch line", "FULL DOC")])

    def test_bring_your_own_idea_routes_to_seeded_proposal(self):
        wh.handle_update(_update("add my idea: a latency benchmark for agent tools"))
        self.assertEqual(len(self.made), 1)
        self.assertEqual(self.made[0]["seed"], "a latency benchmark for agent tools")
        self.assertEqual(self.made[0]["source"], "yiya")
        self.assertEqual(len(self.delivered), 1)

    def test_i_want_to_build_routes_as_idea(self):
        wh.handle_update(_update("I want to build a dashboard for my reading"))
        self.assertEqual(len(self.made), 1)
        self.assertEqual(self.made[0]["seed"], "a dashboard for my reading")
        self.assertEqual(self.made[0]["source"], "yiya")

    def test_reshow_request_does_not_route(self):
        # A re-show falls through to the model (get_latest_project); stub that path
        # so the fallthrough is inert and assert NO new project was generated.
        self.enterContext(mock.patch.object(wh, "_reply_with_progress", lambda c, t: "ok"))
        wh.handle_update(_update("show me the one you sent again"))
        self.assertEqual(self.made, [])
        self.assertEqual(self.delivered, [])


class MatchProposalRequestTest(unittest.TestCase):
    """Unit-level matcher locks: explicit asks route, ambiguous/re-show don't."""

    def test_new_bet_phrases(self):
        for t in ["give me another proposal", "give me a proposal",
                  "propose a new project", "another project",
                  "the full proposal please"]:
            self.assertEqual(wh._match_proposal_request(t), ("new", ""), t)

    def test_idea_seed_phrases(self):
        self.assertEqual(wh._match_proposal_request("add my idea: X tool"),
                         ("idea", "X tool"))
        self.assertEqual(wh._match_proposal_request("my idea is a feed reader"),
                         ("idea", "a feed reader"))

    def test_build_me_a_project_is_new_not_seed(self):
        # "project"/"proposal" makes it a new bet, not a literal seed of "a project".
        self.assertEqual(wh._match_proposal_request("build me a project"), ("new", ""))

    def test_reshow_and_chitchat_dont_match(self):
        for t in ["show me the one you sent", "where's that project",
                  "resend the proposal", "hey what's up", "is this a counter to X?"]:
            self.assertIsNone(wh._match_proposal_request(t), t)


if __name__ == "__main__":
    unittest.main()
