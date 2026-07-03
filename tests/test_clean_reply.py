"""_clean_reply respacing regression tests (argo_webhook).

The sentence-respacing rules must not corrupt code identifiers -- live Telegram
replies came out as "fetch_signals. FEEDS" and "firecrawl_client. scrape()" --
while still repairing genuinely glued sentences after markdown stripping.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import unittest

from argo_webhook import _clean_reply


class CleanReplyRespacingTest(unittest.TestCase):
    def test_allcaps_identifier_tail_stays_glued(self):
        # Rule 1 used to fire on ANY uppercase after '.', splitting module
        # attribute access ("fetch_signals. FEEDS").
        self.assertEqual(_clean_reply("uses fetch_signals.FEEDS today"),
                         "uses fetch_signals.FEEDS today")

    def test_snake_case_method_stays_glued(self):
        # Rule 2 used to split any '.'-joined lowercase word not in the ext
        # allowlist ("firecrawl_client. scrape()").
        self.assertEqual(_clean_reply("firecrawl_client.scrape() is the seam"),
                         "firecrawl_client.scrape() is the seam")

    def test_method_call_on_plain_receiver_stays_glued(self):
        self.assertEqual(_clean_reply("call client.scrape(url) next"),
                         "call client.scrape(url) next")

    def test_glued_lowercase_sentence_still_repaired(self):
        self.assertEqual(_clean_reply("the tests pass.good work"),
                         "the tests pass. good work")

    def test_glued_uppercase_sentence_still_repaired(self):
        self.assertEqual(_clean_reply("nice work.Good catch"),
                         "nice work. Good catch")

    def test_glued_sentence_starting_with_i_still_repaired(self):
        self.assertEqual(_clean_reply("done.I think it works"),
                         "done. I think it works")

    def test_file_ext_and_domain_allowlist_still_intact(self):
        for s in ("argo_chat.json", "docs.x.ai", "example.com", "file.py"):
            self.assertEqual(_clean_reply(s), s)

    def test_initialisms_untouched(self):
        self.assertEqual(_clean_reply("U.S.A. policy"), "U.S.A. policy")


if __name__ == "__main__":
    unittest.main()
