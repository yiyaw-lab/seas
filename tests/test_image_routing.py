"""Image-handling tests (argo_webhook._handle_photo): a screenshot must go through
Argo's normal conversational, tool-enabled brain -- NOT get force-converted into a
taste lesson.

Pure -- the photo download and the model call are stubbed; CHAT_LOG_PATH and
TASTE_PATH are patched to tmp files; no network/LLM. Regression for the live bug
where every image became a 'taste lesson' (and couldn't be discussed/brainstormed).

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
import taste_signals


def _photo_update(chat_id=777, caption=""):
    msg = {"chat": {"id": chat_id}, "photo": [{"file_id": "f1"}]}
    if caption:
        msg["caption"] = caption
    return {"update_id": 1, "message": msg}


class ImageRoutingTest(unittest.TestCase):
    def setUp(self):
        base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.chat_path = base / "argo_chat.json"
        self.taste_path = base / "taste_signals.json"
        self.enterContext(mock.patch.object(argo_memory, "CHAT_LOG_PATH", self.chat_path))
        self.enterContext(mock.patch.object(taste_signals, "TASTE_PATH", self.taste_path))
        self.sent = []
        self.enterContext(mock.patch.object(
            wh.send_telegram, "send_message", lambda t: self.sent.append(t)))
        # stub the network download: raw bytes + media type, no Telegram call
        self.enterContext(mock.patch.object(
            wh, "_download_telegram_photo", lambda msg: (b"\x89PNG\r\n", "image/png")))

    def test_image_routes_conversational_no_forced_taste(self):
        captured = {}

        def fake_chat(system, messages, model, mcp_servers=None,
                      return_tool_events=False, **kw):
            captured["messages"] = messages
            captured["model"] = model
            text = "that's the lockdown piece I sent. re: Mythos, looking now."
            return (text, []) if return_tool_events else text

        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}, clear=True), \
                mock.patch.object(observe, "chat_with_mcp", fake_chat), \
                mock.patch.object(observe, "resolve_models", lambda: []):
            wh.handle_update(_photo_update(caption="is this a counter to Mythos?"))

        # the conversational (chat_with_mcp) path was taken on a Claude model
        self.assertIn("messages", captured)
        self.assertTrue(captured["model"].startswith("claude"))
        # final user turn carries an image block + the caption text block
        final = captured["messages"][-1]
        self.assertEqual(final["role"], "user")
        blocks = final["content"]
        self.assertIsInstance(blocks, list)
        self.assertEqual(blocks[0]["type"], "image")
        self.assertEqual(blocks[0]["source"]["media_type"], "image/png")
        self.assertEqual(blocks[1]["type"], "text")
        self.assertIn("Mythos", blocks[1]["text"])
        # the model's reply was delivered
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Mythos", self.sent[0])
        # NO taste signal was force-written (the old reflex)
        self.assertFalse(self.taste_path.exists())
        # both turns are in chat memory, keyed to this chat
        turns = argo_memory.recent(777)
        self.assertEqual([t["role"] for t in turns], [wh.profile.name(), "Argo"])
        self.assertTrue(turns[0]["text"].startswith("[image]"))

    def test_prior_history_is_included(self):
        argo_memory.record(777, "Argo", "earlier: I sent you the OpenAI lockdown piece")
        captured = {}

        def fake_chat(system, messages, model, mcp_servers=None,
                      return_tool_events=False, **kw):
            captured["messages"] = messages
            text = "yes, that one."
            return (text, []) if return_tool_events else text

        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}, clear=True), \
                mock.patch.object(observe, "chat_with_mcp", fake_chat), \
                mock.patch.object(observe, "resolve_models", lambda: []):
            wh.handle_update(_photo_update(caption="what was that article?"))

        # the earlier Argo turn precedes the image as an assistant message
        self.assertGreaterEqual(len(captured["messages"]), 2)
        self.assertEqual(captured["messages"][0]["role"], "assistant")

    def test_graceful_when_no_vision_model(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(observe, "chat_with_mcp") as spy, \
                mock.patch.object(observe, "resolve_models", lambda: []):
            wh.handle_update(_photo_update(caption="hi"))
        spy.assert_not_called()
        self.assertEqual(len(self.sent), 1)
        self.assertIn("can't see it", self.sent[0])
        self.assertFalse(self.taste_path.exists())


if __name__ == "__main__":
    unittest.main()
