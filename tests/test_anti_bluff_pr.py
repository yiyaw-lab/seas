"""Anti-bluff gate: PR-claim detection + config-blocker honesty.

Two regressions, both seen live when Argo was asked to open a PR for itself:
  1. It said "Let me just try the PR now" / "Now I'll write the PR" and the gate
     missed it -- "try" and "write" weren't in the PR-claim verb list, so the bluff
     reached the user unsuppressed.
  2. When propose_change literally can't run (no MCP server / no token / placeholder
     repo), a generic "say 'propose it'" nudge is useless and a re-attempt is wasted.
     _pr_blocker names the real reason and _classify_claim skips the retry.

Pure: no network/LLM; only the regex and the env-driven blocker helper.
Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import os
import unittest
from unittest import mock

import argo_webhook as wh


class PRClaimRegexTest(unittest.TestCase):
    def test_soft_verbs_trip_only_on_direct_object(self):
        # The live bluffs: a soft verb acting directly on "the PR".
        for phrase in [
            "Let me just try the PR now directly.",
            "Now I'll write the PR.",
            "I'll wrap up the PR and push it.",
        ]:
            self.assertTrue(wh._claim_unbacked(wh._PR_CLAIM_RE, phrase), phrase)

    def test_existing_phrasings_still_trip(self):
        for phrase in ["I'm opening the PR now.", "The PR is up."]:
            self.assertTrue(wh._claim_unbacked(wh._PR_CLAIM_RE, phrase), phrase)

    def test_soft_verbs_do_not_overmatch_honest_mentions(self):
        # A soft verb whose object is NOT the PR -- the PR is merely mentioned
        # downstream -- is honest talk, not a claim, and must NOT be suppressed.
        for phrase in [
            "I'll try to explain how the PR process works.",
            "I'll write you a summary of the PR once I understand it.",
            "I'm working on understanding the PR you mentioned.",
        ]:
            self.assertFalse(wh._claim_unbacked(wh._PR_CLAIM_RE, phrase), phrase)

    def test_offers_and_advice_do_not_trip(self):
        for phrase in [
            "I can open a PR if you want.",
            "Want me to write the PR?",
            "Opening a PR for review usually involves a branch.",
        ]:
            self.assertFalse(wh._claim_unbacked(wh._PR_CLAIM_RE, phrase), phrase)


class PRBlockerTest(unittest.TestCase):
    def test_no_mcp_server_is_a_blocker(self):
        with mock.patch.object(wh, "MCP_SERVERS", None):
            self.assertIn("MCP server", wh._pr_blocker())

    def test_missing_token_is_a_blocker(self):
        with mock.patch.object(wh, "MCP_SERVERS", [{}]), \
                mock.patch.dict(os.environ, {}, clear=True):
            self.assertIn("ARGO_PROPOSE_TOKEN", wh._pr_blocker())

    def test_placeholder_repo_is_a_blocker(self):
        with mock.patch.object(wh, "MCP_SERVERS", [{}]), \
                mock.patch.dict(os.environ,
                                {"ARGO_PROPOSE_TOKEN": "t",
                                 "ARGO_PROPOSE_REPO": "your-org/your-repo"}, clear=True):
            self.assertIn("placeholder", wh._pr_blocker())

    def test_complete_config_has_no_blocker(self):
        with mock.patch.object(wh, "MCP_SERVERS", [{}]), \
                mock.patch.dict(os.environ,
                                {"ARGO_PROPOSE_TOKEN": "t",
                                 "ARGO_PROPOSE_REPO": "me/myrepo"}, clear=True):
            self.assertIsNone(wh._pr_blocker())


class PRClaimClassificationTest(unittest.TestCase):
    def test_config_blocked_claim_is_honest_and_not_reattempted(self):
        with mock.patch.object(wh, "MCP_SERVERS", None):
            v = wh._classify_claim("I'll open the PR now.", [])
        self.assertIsNotNone(v)
        self.assertFalse(v.reattemptable)          # retry can't fix missing config
        self.assertIn("can't right now", v.replacement)
        self.assertIn("MCP server", v.replacement)

    def test_unblocked_claim_is_reattempted(self):
        with mock.patch.object(wh, "MCP_SERVERS", [{}]), \
                mock.patch.dict(os.environ,
                                {"ARGO_PROPOSE_TOKEN": "t",
                                 "ARGO_PROPOSE_REPO": "me/myrepo"}, clear=True):
            v = wh._classify_claim("I'll open the PR now.", [])
        self.assertIsNotNone(v)
        self.assertTrue(v.reattemptable)

    def test_backed_claim_passes(self):
        self.assertIsNone(wh._classify_claim("I'll open the PR now.", ["propose_change"]))


if __name__ == "__main__":
    unittest.main()
