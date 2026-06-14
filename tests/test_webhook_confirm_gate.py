"""CONFIRM / CANCEL gate tests (argo_webhook.handle_update).

CONFIRM with a staged heal runs it deterministically, upstream of the model.
CONFIRM with NOTHING staged (the model offered CONFIRM in free text without
calling a heal tool) must not dead-end: the turn routes to the model so it can
stage the action for real, and a freshly staged SAFE heal (reregister_webhook /
refetch_signals) runs immediately on the okay the user already gave. A staged
propose_fix never auto-runs through this path.

Pure + hermetic: argo_mcp_server is faked via sys.modules for the gate tests;
the pending_heal_action() accessor is tested against the real module with
PENDING_HEAL_PATH pointed at a tmp dir.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import argo_observe as observe
import argo_webhook as wh


def _update(text, chat_id=777):
    return {"update_id": 1, "message": {"chat": {"id": chat_id}, "text": text}}


class ConfirmGateTest(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.enterContext(mock.patch.object(
            wh.send_telegram, "send_message", lambda t: self.sent.append(t)))

        self.calls = []
        self.pending = None  # what pending_heal_action() reports
        fake_mcp = types.ModuleType("argo_mcp_server")
        fake_mcp.pending_heal_action = lambda: self.pending
        fake_mcp.run_pending_heal = lambda: (self.calls.append("run") or
                                             "Re-registered the webhook.")
        fake_mcp.clear_pending_heal = lambda: self.calls.append("clear")
        self.enterContext(mock.patch.dict(sys.modules, {"argo_mcp_server": fake_mcp}))

        # The staged path and CANCEL must never reach the model.
        self.enterContext(mock.patch.object(
            observe, "chat_with_mcp",
            mock.Mock(side_effect=AssertionError("model path must not run"))))
        # Incident notes hit the real ledger; keep tests pure.
        self.noted = []
        self.enterContext(mock.patch.object(
            wh, "_note_incident", lambda *a, **k: self.noted.append(a)))

    def test_confirm_with_staged_action_runs_it(self):
        self.pending = "reregister_webhook"
        reply_mock = self.enterContext(mock.patch.object(wh, "_generate_reply"))
        wh.handle_update(_update("CONFIRM"))
        self.assertEqual(self.calls, ["run"])
        self.assertEqual(self.sent, ["Re-registered the webhook."])
        reply_mock.assert_not_called()

    def test_confirm_nothing_staged_routes_to_model(self):
        reply_mock = self.enterContext(mock.patch.object(
            wh, "_generate_reply", mock.Mock(return_value="On it now.")))
        wh.handle_update(_update("confirm"))
        self.assertEqual(self.calls, [])  # no exec: model staged nothing
        reply_mock.assert_called_once()
        chat_id, content, log_user_text = reply_mock.call_args.args[:3]
        self.assertIn("[system note", content)
        self.assertEqual(log_user_text, "confirm")
        self.assertEqual(self.sent, ["On it now."])
        self.assertTrue(self.noted)  # confirm_dead_end recorded

    def test_recovery_stages_safe_heal_and_runs_once(self):
        def stage_during_turn(*a, **k):
            self.pending = "refetch_signals"
            return "Refetching the feeds for you."
        self.enterContext(mock.patch.object(
            wh, "_generate_reply", mock.Mock(side_effect=stage_during_turn)))
        wh.handle_update(_update("CONFIRM"))
        self.assertEqual(self.calls, ["run"])
        self.assertEqual(self.sent,
                         ["Refetching the feeds for you.",
                          "Re-registered the webhook."])

    def test_recovery_never_auto_runs_propose_fix(self):
        def stage_fix(*a, **k):
            self.pending = "propose_fix"
            return "I drafted a fix, reply FIX to open the PR."
        self.enterContext(mock.patch.object(
            wh, "_generate_reply", mock.Mock(side_effect=stage_fix)))
        wh.handle_update(_update("CONFIRM"))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.sent, ["I drafted a fix, reply FIX to open the PR."])

    def test_recovery_without_model_sends_plain_fallback(self):
        self.enterContext(mock.patch.object(
            wh, "_generate_reply", mock.Mock(return_value=None)))
        wh.handle_update(_update("CONFIRM"))
        self.assertEqual(self.calls, [])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Nothing was staged", self.sent[0])

    def test_cancel_clears_pending(self):
        wh.handle_update(_update("CANCEL"))
        self.assertEqual(self.calls, ["clear"])
        self.assertEqual(self.sent, ["Okay, dropped it."])


class PendingHealActionTest(unittest.TestCase):
    """pending_heal_action() against the real module, pending file in a tmp dir."""

    def setUp(self):
        import argo_mcp_server as srv
        self.srv = srv
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(tmp) / "argo_pending_heal.json"
        self.enterContext(mock.patch.object(srv, "PENDING_HEAL_PATH", self.path))

    def test_none_when_absent(self):
        self.assertIsNone(self.srv.pending_heal_action())

    def test_returns_staged_name(self):
        self.srv._stage_pending("reregister_webhook")
        self.assertEqual(self.srv.pending_heal_action(), "reregister_webhook")

    def test_none_on_corrupt_file(self):
        self.path.write_text("{not json")
        self.assertIsNone(self.srv.pending_heal_action())


class PhantomClaimGateTest(unittest.TestCase):
    """The claim<->receipt gate (wh._classify_claim / _guard_phantom_send):
    suppress action-claims with no backing tool in tool_events, pass backed ones."""

    def setUp(self):
        self.noted = []
        self.enterContext(mock.patch.object(
            wh, "_note_incident", lambda *a, **k: self.noted.append(a)))
        # The CONFIRM row imports argo_mcp_server for the staging check; fake it
        # (nothing staged by default; a test sets self.pending to stage one).
        self.pending = None
        fake_mcp = types.ModuleType("argo_mcp_server")
        fake_mcp.pending_heal_action = lambda: self.pending
        self.enterContext(mock.patch.dict(sys.modules, {"argo_mcp_server": fake_mcp}))

    def test_pr_claim_without_propose_change_blocked(self):
        msg = "I'm opening a PR for that now."
        out = wh._guard_phantom_send(msg, [])
        self.assertNotEqual(out, msg)
        self.assertIn("haven't actually opened a PR", out)
        self.assertEqual(self.noted[0][0], "phantom_claim")

    def test_link_claim_without_fetch_blocked(self):
        out = wh._guard_phantom_send("I read the article and it says X.", [])
        self.assertIn("didn't actually fetch", out)
        self.assertEqual(self.noted[0][0], "phantom_claim")

    def test_pr_claim_with_receipt_passes(self):
        msg = "Opened the PR for review: github.com/x/y/pull/3"
        self.assertEqual(wh._guard_phantom_send(msg, ["propose_change"]), msg)
        self.assertFalse(self.noted)

    def test_link_claim_with_receipt_passes(self):
        msg = "Per the page, the latest release is v2."
        self.assertEqual(wh._guard_phantom_send(msg, ["web_fetch"]), msg)
        self.assertFalse(self.noted)

    def test_confirm_prompt_without_staging_blocked(self):
        out = wh._guard_phantom_send("Reply CONFIRM and I'll fix it.", [])
        self.assertIn("nothing staged behind a CONFIRM", out)
        self.assertEqual(self.noted[0][0], "phantom_claim")

    def test_confirm_prompt_with_staged_heal_passes(self):
        self.pending = "reregister_webhook"
        msg = "I've staged it. Reply CONFIRM to run it."
        self.assertEqual(wh._guard_phantom_send(msg, []), msg)
        self.assertFalse(self.noted)

    def test_confirm_prompt_with_heal_tool_fired_passes(self):
        msg = "Staged the reregister. Reply CONFIRM to run it."
        self.assertEqual(wh._guard_phantom_send(msg, ["reregister_webhook"]), msg)
        self.assertFalse(self.noted)

    def test_project_legacy_phantom_send_unchanged(self):
        out = wh._guard_phantom_send("Sending your proposal over now.", [])
        self.assertIn("didn't actually build anything", out)
        self.assertEqual(self.noted[0][0], "phantom_send")  # legacy kind preserved

    def test_gpt4o_path_action_claim_blocked(self):
        # The tool-less branch always yields tool_events == [], so any PR claim
        # there is a bluff the terminal guard must catch.
        out = wh._guard_phantom_send("I'm opening a PR for that.", [])
        self.assertIn("haven't actually opened a PR", out)

    def test_put_up_pr_claim_blocked(self):  # colloquial verb (recall)
        out = wh._guard_phantom_send("I put up a PR with the changes.", [])
        self.assertIn("haven't actually opened a PR", out)
        self.assertEqual(self.noted[0][0], "phantom_claim")

    def test_pr_as_verb_claim_blocked(self):  # "PR" used as a verb (screenshot)
        out = wh._guard_phantom_send(
            "I'll PR it into feeds.json and send the link right after.", [])
        self.assertIn("haven't actually opened a PR", out)
        self.assertEqual(self.noted[0][0], "phantom_claim")

    def test_feeds_write_claim_blocked(self):  # repo write == propose_change (screenshot)
        out = wh._guard_phantom_send(
            "I'm going to actually add the blog feed to feeds.json right now.", [])
        self.assertIn("haven't actually opened a PR", out)
        self.assertEqual(self.noted[0][0], "phantom_claim")

    def test_feeds_write_with_receipt_passes(self):
        msg = "Added the blog feed to feeds.json, PR is up for review."
        self.assertEqual(wh._guard_phantom_send(msg, ["propose_change"]), msg)
        self.assertFalse(self.noted)

    def test_conditional_feeds_write_not_blocked(self):  # FP guard: offer, not claim
        msg = "I could add it to feeds.json if you want."
        self.assertEqual(wh._guard_phantom_send(msg, []), msg)
        self.assertFalse(self.noted)

    def test_pr_verb_prose_not_blocked(self):  # FP guard: "PR the/a" in prose (review #1)
        for msg in ("we PR the changes via the dashboard.",
                    "I PR a lot of repos in general."):
            self.assertEqual(wh._guard_phantom_send(msg, []), msg)
        self.assertFalse(self.noted)

    def test_looked_it_up_claim_blocked(self):  # colloquial read verb (recall)
        out = wh._guard_phantom_send("I looked it up and the latest is v3.", [])
        self.assertIn("didn't actually fetch", out)
        self.assertEqual(self.noted[0][0], "phantom_claim")

    def test_pr_workflow_advice_not_blocked(self):  # FP guard: advice, not a claim
        msg = ("The workflow involves creating a branch, committing changes, "
               "and opening a PR for review.")
        self.assertEqual(wh._guard_phantom_send(msg, []), msg)
        self.assertFalse(self.noted)

    def test_conditional_pr_offer_not_blocked(self):
        msg = "I can open a PR if you want."
        self.assertEqual(wh._guard_phantom_send(msg, []), msg)
        self.assertFalse(self.noted)

    def test_honest_pr_blocker_not_blocked(self):
        msg = "I can't open a PR: ARGO_PROPOSE_TOKEN is missing."
        self.assertEqual(wh._guard_phantom_send(msg, []), msg)
        self.assertFalse(self.noted)


class AntiBluffReattemptTest(unittest.TestCase):
    """_generate_reply re-prompts the model ONCE when a PR/CONFIRM claim has no
    backing tool, then suppresses if it still bluffs. Pure: the brain is stubbed."""

    def setUp(self):
        self.noted = []
        self.enterContext(mock.patch.object(
            wh, "_note_incident", lambda *a, **k: self.noted.append(a)))
        fake_mcp = types.ModuleType("argo_mcp_server")
        fake_mcp.pending_heal_action = lambda: None  # nothing staged
        self.enterContext(mock.patch.dict(sys.modules, {"argo_mcp_server": fake_mcp}))
        # Force exactly one runnable anthropic model, and a pure brain.
        self.enterContext(mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test"}))
        self.enterContext(mock.patch.object(wh, "_route_model", lambda t: "claude-x"))
        self.enterContext(mock.patch.object(observe, "resolve_models", lambda: ["claude-x"]))
        self.enterContext(mock.patch.object(
            observe, "provider_for",
            lambda m: {"name": "anthropic", "key_env": "ANTHROPIC_API_KEY"}))
        self.enterContext(mock.patch.object(wh, "build_system_prompt", lambda: "SYS"))
        self.enterContext(mock.patch.object(wh, "_recent_turns", lambda c: []))
        self.enterContext(mock.patch.object(wh, "_clean_reply", lambda s: s))
        self.enterContext(mock.patch.object(wh.profile, "name", lambda: "User"))
        self.enterContext(mock.patch.object(
            wh.argo_memory, "record_many", lambda *a, **k: None))

    def test_reattempt_then_real_pr(self):
        chat = mock.Mock(side_effect=[
            ("I'm opening a PR now.", []),                       # 1st: bluff
            ("Opened the PR for review: link", ["propose_change"]),  # 2nd: real
        ])
        self.enterContext(mock.patch.object(observe, "chat_with_mcp", chat))
        out = wh._generate_reply(7, "open a PR for the fix", "open a PR for the fix")
        self.assertEqual(chat.call_count, 2)
        retry_messages = chat.call_args_list[1].args[1]
        self.assertIn("propose_change", retry_messages[-1]["content"])
        self.assertIn("[system note", retry_messages[-1]["content"])
        self.assertEqual(out, "Opened the PR for review: link")
        self.assertFalse(self.noted)  # self-corrected -> no incident

    def test_reattempt_still_bluffs_suppressed_once(self):
        chat = mock.Mock(side_effect=[
            ("I'm opening a PR now.", []),  # 1st: bluff
            ("Yeah, the PR is up.", []),    # 2nd: still a bluff
        ])
        self.enterContext(mock.patch.object(observe, "chat_with_mcp", chat))
        out = wh._generate_reply(7, "open a PR", "open a PR")
        self.assertEqual(chat.call_count, 2)  # recursion guard: no 3rd call
        self.assertEqual(out, wh._PR_NUDGE)
        self.assertEqual(self.noted[0][0], "phantom_claim")


class FallbackDegradeNoticeTest(unittest.TestCase):
    """When the tool-capable (Claude) brain errors and Argo answers on a tool-less
    fallback, it tells the user it's degraded instead of silently bluffing."""

    def setUp(self):
        self.noted = []
        self.enterContext(mock.patch.object(
            wh, "_note_incident", lambda *a, **k: self.noted.append(a)))
        fake_mcp = types.ModuleType("argo_mcp_server")
        fake_mcp.pending_heal_action = lambda: None
        self.enterContext(mock.patch.dict(sys.modules, {"argo_mcp_server": fake_mcp}))
        self.enterContext(mock.patch.dict(
            os.environ, {"ANTHROPIC_API_KEY": "a", "OPENAI_API_KEY": "o"}))
        # Two runnable models: a tool-capable claude, then a tool-less gpt fallback.
        self.enterContext(mock.patch.object(wh, "_route_model", lambda t: "claude-x"))
        self.enterContext(mock.patch.object(observe, "resolve_models", lambda: ["gpt-x"]))
        self.enterContext(mock.patch.object(
            observe, "provider_for",
            lambda m: {"name": "anthropic", "key_env": "ANTHROPIC_API_KEY"}
            if m == "claude-x" else {"name": "openai", "key_env": "OPENAI_API_KEY"}))
        self.enterContext(mock.patch.object(wh, "build_system_prompt", lambda: "SYS"))
        self.enterContext(mock.patch.object(wh, "_recent_turns", lambda c: []))
        self.enterContext(mock.patch.object(wh, "_clean_reply", lambda s: s))
        self.enterContext(mock.patch.object(wh.profile, "name", lambda: "User"))
        self.enterContext(mock.patch.object(
            wh.argo_memory, "record_many", lambda *a, **k: None))

    def test_fallback_notice_when_claude_errors(self):
        self.enterContext(mock.patch.object(
            observe, "chat_with_mcp",
            mock.Mock(side_effect=Exception("credit balance is too low"))))
        self.enterContext(mock.patch.object(
            observe, "generate_observations", lambda *a, **k: "here is what I know."))
        out = wh._generate_reply(7, "read https://x.com and summarize", "read it")
        self.assertTrue(out.startswith(wh._FALLBACK_NOTICE))
        self.assertIn("here is what I know.", out)
        self.assertTrue(any(n[0] == "model_failure" for n in self.noted))

    def test_no_notice_when_claude_succeeds(self):
        self.enterContext(mock.patch.object(
            observe, "chat_with_mcp", mock.Mock(return_value=("all good.", []))))
        out = wh._generate_reply(7, "hi", "hi")
        self.assertEqual(out, "all good.")
        self.assertFalse(any(n[0] == "model_failure" for n in self.noted))

    def test_fallback_prompt_carries_no_tools_constraint(self):
        # The tool-less brain must be told it has no tools, so it offers actions
        # instead of promising them (the self-contradiction in the screenshot).
        self.enterContext(mock.patch.object(
            observe, "chat_with_mcp",
            mock.Mock(side_effect=Exception("credit balance is too low"))))
        captured = {}
        self.enterContext(mock.patch.object(
            observe, "generate_observations",
            lambda prompt, model: captured.setdefault("prompt", prompt) or "from memory."))
        wh._generate_reply(7, "add the feed", "add the feed")
        self.assertIn(wh._NO_TOOLS_CONSTRAINT, captured["prompt"])


if __name__ == "__main__":
    unittest.main()
