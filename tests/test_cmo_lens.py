"""CMO role-lens tests (argo_cmo + argo_webhook.handle_update + build_system_prompt).

B-007's cheapest demand test: a per-chat SYSTEM-PROMPT lens (Argo reasons as the
builder's CMO), NOT a data pipeline. The /cmo gate is deterministic and sits
UPSTREAM of the model -- the confirmation never reaches the LLM -- and every
switch is timestamped so a later manual grade can read the return cadence.

Covers:
  (a) /cmo activates + persists + plain-text confirmation + model NOT called;
  (b) /cmo off deactivates;
  (c) build_system_prompt(cmo_mode=True) contains the lens, =False does not;
  (d) each switch is logged with a timestamp;
  (e) /cmo@bot and case-insensitive variants work; a message merely containing
      "cmo" does NOT misfire.

Pure + hermetic: CMO_MODES_PATH is overridden to a tmp file, send_message is
mocked, and observe.chat_with_mcp is wired to raise if the model is ever reached.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import argo_cmo
import argo_observe as observe
import argo_webhook as wh


def _update(text, chat_id=777):
    return {"update_id": 1, "message": {"chat": {"id": chat_id}, "text": text}}


class CmoGateTest(unittest.TestCase):
    def setUp(self):
        base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        # argo_cmo helpers read the module global at call time, so the override bites.
        self.enterContext(mock.patch.object(argo_cmo, "CMO_MODES_PATH",
                                            base / "cmo_modes.json"))
        self.sent = []
        self.enterContext(mock.patch.object(
            wh.send_telegram, "send_message", lambda t: self.sent.append(t)))
        # The /cmo gate must never reach the model. A non-/cmo message DOES, so
        # stub the single model entrypoint (_reply_with_progress) and record whether
        # it was reached -- a fired gate returns BEFORE it, a fall-through hits it.
        self.model_calls = []
        self.enterContext(mock.patch.object(
            wh, "_reply_with_progress",
            lambda cid, t: (self.model_calls.append(t) or "(model reply)")))

    # (a) activate + persist + confirmation + model not called
    def test_cmo_activates_persists_and_confirms(self):
        wh.handle_update(_update("/cmo"))
        self.assertEqual(len(self.sent), 1)
        self.assertIn("CMO lens on", self.sent[0])
        self.assertTrue(argo_cmo.is_active(777))
        self.assertEqual(self.model_calls, [])  # gate short-circuited the model
        # Plain text: no markdown, no em/en dash (the house rules / _clean_reply).
        self.assertNotIn("**", self.sent[0])
        self.assertNotIn("—", self.sent[0])
        self.assertNotIn("–", self.sent[0])

    def test_cmo_on_arg_activates(self):
        wh.handle_update(_update("/cmo on"))
        self.assertTrue(argo_cmo.is_active(777))
        self.assertIn("CMO lens on", self.sent[0])

    # (b) deactivate
    def test_cmo_off_deactivates(self):
        argo_cmo.set_active(777, True)
        wh.handle_update(_update("/cmo off"))
        self.assertFalse(argo_cmo.is_active(777))
        self.assertEqual(len(self.sent), 1)
        self.assertIn("back to normal", self.sent[0])

    def test_cmo_off_synonyms_deactivate(self):
        for syn in ("stop", "exit", "normal"):
            argo_cmo.set_active(777, True)
            self.sent.clear()
            wh.handle_update(_update(f"/cmo {syn}"))
            self.assertFalse(argo_cmo.is_active(777), syn)
            self.assertIn("back to normal", self.sent[0])

    # (e) @bot suffix + case-insensitive
    def test_cmo_at_bot_suffix_works(self):
        wh.handle_update(_update("/cmo@argobot"))
        self.assertTrue(argo_cmo.is_active(777))
        self.assertIn("CMO lens on", self.sent[0])

    def test_cmo_case_insensitive(self):
        wh.handle_update(_update("/CMO ON"))
        self.assertTrue(argo_cmo.is_active(777))

    def test_at_bot_with_off_arg(self):
        argo_cmo.set_active(777, True)
        wh.handle_update(_update("/cmo@ArgoBot OFF"))
        self.assertFalse(argo_cmo.is_active(777))

    # adversarial: a message merely CONTAINING "cmo" must NOT toggle the lens; it
    # falls through to the model (proving the gate only fires on a LEADING /cmo).
    def test_message_containing_cmo_does_not_misfire(self):
        wh.handle_update(_update("what should my cmo do this quarter?"))
        self.assertFalse(argo_cmo.is_active(777))
        self.assertEqual(len(self.model_calls), 1)  # reached the model, not the gate
        self.assertNotIn("CMO lens on", "".join(self.sent))

    def test_plain_cmo_word_does_not_misfire(self):
        wh.handle_update(_update("cmo"))  # no leading slash
        self.assertFalse(argo_cmo.is_active(777))
        self.assertEqual(len(self.model_calls), 1)

    # per-chat keying is consistent (str): a different chat is independent.
    def test_per_chat_keying(self):
        wh.handle_update(_update("/cmo", chat_id=111))
        self.assertTrue(argo_cmo.is_active(111))
        self.assertFalse(argo_cmo.is_active(222))


class CmoSwitchLogTest(unittest.TestCase):
    def setUp(self):
        base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.object(argo_cmo, "CMO_MODES_PATH",
                                            base / "cmo_modes.json"))

    # (d) each switch is logged with an iso8601 timestamp
    def test_switches_are_timestamped(self):
        argo_cmo.set_active(555, True)
        argo_cmo.set_active(555, False)
        argo_cmo.set_active(555, True)
        import argo_store
        store = argo_store.load_json(argo_cmo.CMO_MODES_PATH, {})
        switches = store["555"]["switches"]
        self.assertEqual([s["to"] for s in switches], ["on", "off", "on"])
        for s in switches:
            # UTC iso8601, same format as argo_predictions/argo_pushes.
            self.assertRegex(s["ts"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertTrue(argo_cmo.is_active(555))

    def test_missing_store_reads_off(self):
        self.assertFalse(argo_cmo.is_active(999))

    def test_corrupt_store_reads_off(self):
        argo_cmo.CMO_MODES_PATH.parent.mkdir(parents=True, exist_ok=True)
        argo_cmo.CMO_MODES_PATH.write_text("{ not json")
        self.assertFalse(argo_cmo.is_active(999))  # must not raise

    def test_wrong_shape_store_reads_off(self):
        import argo_store
        argo_store.save_json(argo_cmo.CMO_MODES_PATH, ["not", "a", "dict"])
        self.assertFalse(argo_cmo.is_active(999))


class CmoPromptTest(unittest.TestCase):
    # (c) the lens is in the prompt only when cmo_mode=True
    def test_lens_present_only_when_on(self):
        on = wh.build_system_prompt(cmo_mode=True)
        off = wh.build_system_prompt(cmo_mode=False)
        self.assertIn(wh.CMO_LENS_FRAGMENT, on)
        self.assertNotIn(wh.CMO_LENS_FRAGMENT, off)
        self.assertIn("Chief Marketing Officer", on)
        self.assertNotIn("Chief Marketing Officer", off)
        # Default is off, so non-CMO chats / proactive senders are unchanged.
        self.assertEqual(wh.build_system_prompt(), off)

    def test_lens_is_plain_text(self):
        # The fragment itself carries no markdown / em-dash that would survive
        # _clean_reply (the house voice rule the lens explicitly restates).
        self.assertNotIn("**", wh.CMO_LENS_FRAGMENT)
        self.assertNotIn("—", wh.CMO_LENS_FRAGMENT)
        self.assertNotIn("–", wh.CMO_LENS_FRAGMENT)


if __name__ == "__main__":
    unittest.main()
