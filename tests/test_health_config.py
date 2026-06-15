"""Operator config-verification on /health: value-free secret fingerprints so you
can confirm the RUNNING process holds the token you just rotated (a 401 alone can't
tell "stale value" from "wrong scope"). The fingerprints are gated behind the
operator bearer and never include the secret itself.

Pure: no network, no Flask client -- the helpers are exercised directly.
Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import hashlib
import json
import os
import unittest
from types import SimpleNamespace
from unittest import mock

import argo_webhook as wh


class SecretFingerprintTest(unittest.TestCase):
    def test_none_when_absent(self):
        self.assertIsNone(wh._secret_fingerprint(""))
        self.assertIsNone(wh._secret_fingerprint(None))

    def test_len_and_sha8_never_leak_the_value(self):
        fp = wh._secret_fingerprint("ghp_secret_value")
        self.assertEqual(fp["len"], len("ghp_secret_value"))
        self.assertEqual(fp["sha8"],
                         hashlib.sha256(b"ghp_secret_value").hexdigest()[:8])
        self.assertNotIn("ghp_secret_value", json.dumps(fp))  # value never present

    def test_flags_whitespace_but_hashes_the_stripped_form(self):
        clean = wh._secret_fingerprint("tok")
        dirty = wh._secret_fingerprint("tok\n")  # trailing newline (paste footgun)
        self.assertTrue(dirty["has_surrounding_whitespace"])
        self.assertNotIn("has_surrounding_whitespace", clean)
        # hashes the .strip()-ed value, so it matches the operator's clean paste hash
        self.assertEqual(dirty["sha8"], clean["sha8"])
        self.assertEqual(dirty["len"], 3)


class OperatorAuthedTest(unittest.TestCase):
    def _req(self, header=None):
        return SimpleNamespace(headers={"Authorization": header} if header is not None else {})

    def test_true_with_correct_bearer(self):
        with mock.patch.object(wh, "ARGO_MCP_TOKEN", "secret-tok"):
            self.assertTrue(wh._operator_authed(self._req("Bearer secret-tok")))

    def test_false_with_wrong_or_missing_bearer(self):
        with mock.patch.object(wh, "ARGO_MCP_TOKEN", "secret-tok"):
            self.assertFalse(wh._operator_authed(self._req("Bearer nope")))
            self.assertFalse(wh._operator_authed(self._req(None)))

    def test_false_when_no_token_configured(self):
        with mock.patch.object(wh, "ARGO_MCP_TOKEN", None):
            self.assertFalse(wh._operator_authed(self._req("Bearer anything")))


class HealthConfigSectionTest(unittest.TestCase):
    def test_omitted_by_default(self):
        self.assertNotIn("config", wh._health_payload())  # public route stays clean

    def test_included_when_authed_and_no_value_leaks(self):
        with mock.patch.dict(os.environ,
                             {"GITHUB_TOKEN": "ghp_live", "OPENAI_API_KEY": ""},
                             clear=False):
            payload = wh._health_payload(include_config=True)
        self.assertIn("config", payload)
        self.assertIn("sha8", payload["config"]["GITHUB_TOKEN"])  # present -> fingerprint
        self.assertIsNone(payload["config"]["OPENAI_API_KEY"])     # empty -> None
        self.assertNotIn("ghp_live", json.dumps(payload))          # value never leaks


if __name__ == "__main__":
    unittest.main()
