"""Long-term recall: argo_memory.relevant() surfaces an OLDER turn (outside the
12-turn recency window) whose words overlap the current message, so a fact from
turn 3 isn't lost by turn 15.

Regression guard for the gap Argo surfaced ("memory across more than 12 turns --
you tell me something in turn 3 and by turn 15 I'm flying blind"). Pure stdlib,
no model call, no network: the full chat log already persists every turn; only
recency-windowed reads dropped the old ones. Chat-log path redirected to tmp.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import argo_memory as mem  # noqa: E402
import argo_observe as observe  # noqa: E402
import argo_webhook as wh  # noqa: E402


class RelevantRecallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.enterContext(mock.patch.object(mem, "CHAT_LOG_PATH",
                                            Path(self.tmp) / "chat.json"))
        self.chat = "42"

    def _seed_with_filler(self, fillers):
        # A distinctive early fact, then enough filler to push it past recent()'s window.
        mem.record(self.chat, "Yiya", "my dog's name is Mochi, a shiba inu")
        for i in range(fillers):
            mem.record(self.chat, "Yiya", f"some unrelated chatter number {i}")
            mem.record(self.chat, "Argo", f"noted that, {i}")

    def test_surfaces_older_relevant_turn_beyond_recency_window(self):
        self._seed_with_filler(12)  # 1 + 24 = 25 turns; the Mochi turn is well past the last 12
        recent_text = " ".join(t["text"] for t in mem.recent(self.chat))
        self.assertNotIn("Mochi", recent_text)  # precondition: it's out of the window
        hits = mem.relevant(self.chat, "wait what was my dog Mochi again?")
        self.assertTrue(any("Mochi" in t["text"] for t in hits),
                        "relevant() should recall the older Mochi turn")

    def test_no_resurfacing_when_fact_still_in_recency_window(self):
        mem.record(self.chat, "Yiya", "my dog's name is Mochi")
        # Still inside recent() -- the model already sees it; relevant() must not dupe it.
        self.assertEqual(mem.relevant(self.chat, "Mochi the dog"), [])

    def test_empty_or_stopword_query_returns_nothing(self):
        self._seed_with_filler(12)
        self.assertEqual(mem.relevant(self.chat, "the and of a to"), [])

    def test_caps_to_k(self):
        for i in range(20):
            mem.record(self.chat, "Yiya", f"project Falcon milestone {i}")
        hits = mem.relevant(self.chat, "Falcon project status", k=3)
        self.assertLessEqual(len(hits), 3)


class RecallQueryUsesUserTextTest(unittest.TestCase):
    """The recall query must be the user's ACTUAL words (log_user_text), not route_text,
    which can be a synthetic routing/recovery note (Bugbot #59)."""

    def setUp(self):
        self.enterContext(mock.patch.object(
            mem, "CHAT_LOG_PATH", Path(tempfile.mkdtemp()) / "chat.json"))

    def test_recall_queries_user_text_not_synthetic_note(self):
        captured = {}

        def fake_relevant(chat_id, query, *a, **k):
            captured["query"] = query
            return []

        def fake_chat(system, messages, model, mcp_servers=None,
                      return_tool_events=False, **kw):
            return ("ok", []) if return_tool_events else "ok"

        # The CONFIRM-dead-end path: final_content is a synthetic system note, while the
        # user's real words ("CONFIRM") are passed as log_user_text.
        note = ("CONFIRM\n\n[system note: the user replied CONFIRM but nothing is "
                "staged; reregister_webhook or refetch_signals ...]")
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}, clear=True), \
                mock.patch.object(observe, "chat_with_mcp", fake_chat), \
                mock.patch.object(observe, "resolve_models", lambda: []), \
                mock.patch.object(mem, "relevant", fake_relevant):
            wh._generate_reply(999, note, "CONFIRM")
        self.assertEqual(captured.get("query"), "CONFIRM")  # the user's words, not the note


if __name__ == "__main__":
    unittest.main()
