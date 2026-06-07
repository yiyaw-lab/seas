"""Chat-memory tests (argo_memory): the shared, append-only conversation log.

Pure -- no network, no real data/*.json: CHAT_LOG_PATH is patched to a tmp file.
The key contract is that proactive sends (str chat_id from TELEGRAM_CHAT_ID) and
webhook turns (int chat_id from Telegram) land in the SAME conversation.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import argo_memory


class MemoryStoreTest(unittest.TestCase):
    def setUp(self):
        base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        # nested path so we also exercise parent-dir creation on first write
        self.path = base / "sub" / "argo_chat.json"
        self.enterContext(mock.patch.object(argo_memory, "CHAT_LOG_PATH", self.path))

    def test_record_then_recent_roundtrip_and_order(self):
        argo_memory.record(1, "Yiya", "hi")
        argo_memory.record(1, "Argo", "hello")
        turns = argo_memory.recent(1)
        self.assertEqual([t["role"] for t in turns], ["Yiya", "Argo"])
        self.assertEqual(turns[-1]["text"], "hello")
        self.assertTrue(self.path.exists())                  # mkdir happened
        self.assertTrue(self.path.read_text().endswith("\n"))  # argo_store format

    def test_per_chat_filtering(self):
        argo_memory.record(1, "Argo", "for one")
        argo_memory.record(2, "Argo", "for two")
        self.assertEqual([t["text"] for t in argo_memory.recent(1)], ["for one"])
        self.assertEqual([t["text"] for t in argo_memory.recent(2)], ["for two"])

    def test_chat_id_normalized_int_and_str_unify(self):
        # The whole point of Fix 1: the webhook writes an int chat_id; the proactive
        # path writes the str TELEGRAM_CHAT_ID. They must read back as one thread.
        argo_memory.record(123, "Yiya", "webhook turn")       # int
        argo_memory.record("123", "Argo", "proactive push")   # str (env)
        texts = [t["text"] for t in argo_memory.recent(123)]
        self.assertEqual(texts, ["webhook turn", "proactive push"])
        self.assertEqual([t["text"] for t in argo_memory.recent("123")], texts)

    def test_recent_limit(self):
        for i in range(20):
            argo_memory.record(1, "Argo", f"m{i}")
        turns = argo_memory.recent(1, n=5)
        self.assertEqual([t["text"] for t in turns], [f"m{i}" for i in range(15, 20)])

    def test_falsy_chat_id_is_noop(self):
        # TELEGRAM_CHAT_ID unset on a proactive call: don't write an unkeyed turn.
        argo_memory.record(None, "Argo", "lost")
        argo_memory.record("", "Argo", "lost")
        self.assertFalse(self.path.exists())

    def test_corrupt_file_recovers(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{not valid json")
        self.assertEqual(argo_memory.recent(1), [])
        argo_memory.record(1, "Argo", "after corruption")  # must not crash
        self.assertEqual([t["text"] for t in argo_memory.recent(1)],
                         ["after corruption"])


if __name__ == "__main__":
    unittest.main()
