"""Incoming-file tests (argo_webhook): a file sent to Argo is read and saved.

The regression: a non-image document (PDF, notes, csv...) has no `text`, so
handle_update's text guard silently dropped it — the user sent Argo a file and
got nothing back. Now documents route to _handle_document, which downloads the
file, saves it under FILES_DIR, and feeds its content through the normal brain.

Pure + hermetic: the Telegram download, the LLM reply, and outbound Telegram are
all patched; FILES_DIR points at a tmp dir. No network, no real data/.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import argo_webhook as wh


def _doc_update(name, mime, data=b"hello", caption=""):
    msg = {
        "chat": {"id": 42},
        "document": {"file_id": "F1", "file_name": name, "mime_type": mime,
                     "file_size": len(data)},
    }
    if caption:
        msg["caption"] = caption
    return {"message": msg}


class RoutingTest(unittest.TestCase):
    def test_non_image_document_routes_to_handle_document(self):
        with mock.patch.object(wh, "_handle_document") as hd, \
             mock.patch.object(wh, "_handle_photo") as hp:
            wh.handle_update(_doc_update("notes.pdf", "application/pdf"))
        hd.assert_called_once()
        hp.assert_not_called()

    def test_image_document_still_routes_to_photo(self):
        with mock.patch.object(wh, "_handle_document") as hd, \
             mock.patch.object(wh, "_handle_photo") as hp:
            wh.handle_update(_doc_update("shot.png", "image/png"))
        hp.assert_called_once()
        hd.assert_not_called()


class HandleDocumentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.files_dir = Path(self.tmp.name) / "files"
        patches = [
            mock.patch.object(wh, "FILES_DIR", self.files_dir),
            mock.patch.object(wh.send_telegram, "send_message"),
            mock.patch.object(wh, "_generate_reply", return_value="ok"),
            mock.patch.object(wh, "_download_telegram_file",
                              return_value=(b"col1,col2\n1,2\n", "docs/f.csv")),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _handle(self, update):
        wh._handle_document(42, update["message"])

    def test_text_file_is_saved_and_read_inline(self):
        self._handle(_doc_update("data.csv", "text/csv", caption="thoughts?"))
        saved = self.files_dir / "data.csv"
        self.assertTrue(saved.exists())
        self.assertEqual(saved.read_bytes(), b"col1,col2\n1,2\n")
        (_, content, _), kwargs = wh._generate_reply.call_args
        self.assertIn("col1,col2", content)       # file body reaches the model
        self.assertIn("thoughts?", content)       # caption too
        self.assertFalse(kwargs.get("anthropic_only"))
        wh.send_telegram.send_message.assert_called_once_with("ok")

    def test_pdf_becomes_document_block_anthropic_only(self):
        wh._download_telegram_file.return_value = (b"%PDF-1.4", "docs/f.pdf")
        self._handle(_doc_update("paper.pdf", "application/pdf"))
        (_, content, _), kwargs = wh._generate_reply.call_args
        self.assertTrue(kwargs.get("anthropic_only"))
        self.assertEqual(content[0]["type"], "document")
        self.assertEqual(content[0]["source"]["media_type"], "application/pdf")
        self.assertTrue((self.files_dir / "paper.pdf").exists())

    def test_unreadable_binary_is_saved_with_honest_note(self):
        wh._download_telegram_file.return_value = (b"\x00\x01", "docs/a.bin")
        self._handle(_doc_update("a.bin", "application/octet-stream"))
        (_, content, _), kwargs = wh._generate_reply.call_args
        self.assertIn("can't read inline", content)
        self.assertFalse(kwargs.get("anthropic_only"))
        self.assertTrue((self.files_dir / "a.bin").exists())

    def test_download_failure_asks_for_resend(self):
        wh._download_telegram_file.return_value = (None, None)
        self._handle(_doc_update("data.csv", "text/csv"))
        wh._generate_reply.assert_not_called()
        (sent,), _ = wh.send_telegram.send_message.call_args
        self.assertIn("couldn't pull it down", sent)

    def test_same_name_is_uniquified_not_overwritten(self):
        first = wh._save_incoming_file("notes.txt", b"one")
        second = wh._save_incoming_file("notes.txt", b"two")
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_bytes(), b"one")
        self.assertEqual(second.read_bytes(), b"two")

    def test_long_text_is_clipped(self):
        wh._download_telegram_file.return_value = (b"x" * 20000, "docs/big.txt")
        self._handle(_doc_update("big.txt", "text/plain"))
        (_, content, _), _ = wh._generate_reply.call_args
        self.assertIn("clipped", content)
        self.assertLess(len(content), 14000)


if __name__ == "__main__":
    unittest.main()
