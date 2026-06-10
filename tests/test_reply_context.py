"""Reply-to-message context tests (argo_reply_context + its webhook wiring).

The module existed but was never called -- when Yiya REPLIED to one of Argo's
messages, Argo never saw what she was reacting to. These lock both the extractor
and the wiring: handle_update must feed the reply-augmented text to the model.

Pure: the model call is stubbed; paths point at tmp files; no network/LLM.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import argo_memory
import argo_observe as observe
import argo_reply_context as rc
import argo_webhook as wh


class ExtractUserTextTest(unittest.TestCase):
    def test_bare_text_when_no_reply(self):
        self.assertEqual(rc.extract_user_text({"text": "hello"}), "hello")

    def test_caption_fallback(self):
        self.assertEqual(rc.extract_user_text({"caption": "pic note"}), "pic note")

    def test_prepends_reply_excerpt(self):
        out = rc.extract_user_text(
            {"text": "is this a counter?", "reply_to_message": {"text": "OpenAI shipped X"}})
        self.assertTrue(out.startswith('[replying to: "'))
        self.assertIn("OpenAI shipped X", out)
        self.assertIn("is this a counter?", out)

    def test_empty_quoted_falls_back_to_bare(self):
        self.assertEqual(
            rc.extract_user_text({"text": "hi", "reply_to_message": {}}), "hi")

    def test_long_quote_truncated(self):
        out = rc.extract_user_text(
            {"text": "?", "reply_to_message": {"text": "x" * 200}})
        self.assertIn("...", out)
        self.assertLessEqual(len(out), rc.MAX_REPLY_EXCERPT + 30)


class ReplyContextWiringTest(unittest.TestCase):
    """handle_update must pass the reply-augmented text into the model turn."""

    def setUp(self):
        base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.object(argo_memory, "CHAT_LOG_PATH", base / "chat.json"))
        self.enterContext(mock.patch.object(wh, "PROJECTS_LOG", base / "projects.json"))
        self.enterContext(mock.patch.object(wh.send_telegram, "send_message", lambda t: None))

    def test_reply_context_reaches_the_model(self):
        captured = {}

        def fake_chat(system, messages, model, mcp_servers=None,
                      return_tool_events=False, **kw):
            captured["messages"] = messages
            text = "noted."
            return (text, []) if return_tool_events else text

        update = {"update_id": 1, "message": {
            "chat": {"id": 777},
            "text": "does this hold up?",
            "reply_to_message": {"text": "Anthropic released Claude X"}}}
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}, clear=True), \
                mock.patch.object(observe, "chat_with_mcp", fake_chat), \
                mock.patch.object(observe, "resolve_models", lambda: []):
            wh.handle_update(update)

        final = captured["messages"][-1]["content"]
        self.assertIn("[replying to:", final)
        self.assertIn("Anthropic released Claude X", final)
        self.assertIn("does this hold up?", final)


if __name__ == "__main__":
    unittest.main()
