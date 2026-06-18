"""A model-error turn must still be written to chat history.

Live bug: Argo sent "(Argo hit an error reaching the model: ...)"; the user replied
"why this error" one turn later and Argo said "what error? paste it" -- total
amnesia. _generate_reply recorded both turns only on the success path (record_many
inside the try), so when every model raised, the error path returned the string
WITHOUT recording anything. The next turn then loaded a history with neither the
user's question nor the error reply. This locks the error path persisting both.

Pure: every model raises (stubbed); paths point at a tmp file; no network/LLM.
Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import argo_memory
import argo_observe as observe
import argo_webhook as wh


class PersistErrorTurnTest(unittest.TestCase):
    def setUp(self):
        base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.object(argo_memory, "CHAT_LOG_PATH", base / "chat.json"))
        # incidents go through _note_incident -> a real store; keep the test pure
        self.enterContext(mock.patch.object(wh, "_note_incident", lambda *a, **k: None))

    def test_error_turn_survives_into_history(self):
        def boom(*a, **k):
            raise RuntimeError("Error code: 400 - Error while communicating with MCP server.")

        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}, clear=True), \
                mock.patch.object(observe, "chat_with_mcp", boom), \
                mock.patch.object(observe, "resolve_models", lambda: []):
            reply = wh._generate_reply(424242, "give yourself image generation",
                                       "give yourself image generation")

        self.assertIn("hit an error", reply)
        turns = argo_memory.recent(424242)
        texts = " || ".join(t["text"] for t in turns)
        # both the user's question AND the error reply must be in history, so the
        # next turn ("why this error") can see what just happened.
        self.assertIn("image generation", texts)
        self.assertIn("hit an error", texts)

    def test_budget_cap_turn_survives_into_history(self):
        # The budget-cap exit is a sibling early-return with the same amnesia risk:
        # without recording, "why are you taking a breather?" loads empty history.
        def overbudget(*a, **k):
            raise observe.argo_guard.DailyBudget.BudgetExceeded()

        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}, clear=True), \
                mock.patch.object(observe, "chat_with_mcp", overbudget), \
                mock.patch.object(observe, "resolve_models", lambda: []):
            reply = wh._generate_reply(515151, "give me a project", "give me a project")

        self.assertIn("daily call budget", reply)
        texts = " || ".join(t["text"] for t in argo_memory.recent(515151))
        self.assertIn("give me a project", texts)
        self.assertIn("daily call budget", texts)


if __name__ == "__main__":
    unittest.main()
