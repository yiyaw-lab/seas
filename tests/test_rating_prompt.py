"""Rating-prompt regression tests — the disappearing-prompt + decimal-rating area.

Locks: (1) the canonical invite text and Argo-voice rules, (2) that a bare decimal
like "7.5" parses as a rating (the bug where it fell through to the LLM and
triggered a new project), and (3) that the invite is appended on EVERY delivery
path (the bug where the rating prompt vanished on the on-demand path).

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import inspect
import unittest
from pathlib import Path

import argo_project
import argo_webhook

SRC = Path(__file__).resolve().parent.parent / "src"


class ProjectInviteTest(unittest.TestCase):
    def test_invite_contains_the_three_replies(self):
        s = argo_project.project_invite("P-007")
        self.assertIn("P-007", s)
        self.assertIn("1-10", s)
        self.assertIn("SELECT", s)
        self.assertIn("another", s)

    def test_invite_respects_argo_voice(self):
        # Plain text only: no markdown bold, no em dash (Argo output rules).
        s = argo_project.project_invite("P-007")
        self.assertNotIn("**", s)
        self.assertNotIn("—", s)  # em dash


class ParseRatingTest(unittest.TestCase):
    def test_decimal_parses(self):
        self.assertEqual(argo_webhook._parse_rating("7.5"), 7.5)

    def test_integer_parses_as_int(self):
        val = argo_webhook._parse_rating("8")
        self.assertEqual(val, 8)
        self.assertIsInstance(val, int)

    def test_whitespace_tolerated(self):
        self.assertEqual(argo_webhook._parse_rating("  9 "), 9)

    def test_out_of_range_is_none(self):
        self.assertIsNone(argo_webhook._parse_rating("0"))
        self.assertIsNone(argo_webhook._parse_rating("11"))

    def test_prose_is_not_a_rating(self):
        self.assertIsNone(argo_webhook._parse_rating("build 3 things"))
        self.assertIsNone(argo_webhook._parse_rating("7.5 build"))
        self.assertIsNone(argo_webhook._parse_rating(""))
        self.assertIsNone(argo_webhook._parse_rating(None))


class InviteOnEveryDeliveryPathTest(unittest.TestCase):
    """Tripwire: each project-delivery path must append project_invite. Asserted
    over the source files (argo_mcp_server is heavyweight to import and its tools
    are decorator-wrapped, so a file read is both lighter and more robust). Fails
    loudly if a future edit drops the invite from a delivery path (the regression
    that made the rating prompt disappear)."""

    def test_weekly_main_appends_invite(self):
        src = inspect.getsource(argo_project.main)
        self.assertIn("project_invite(", src)

    def test_mcp_delivery_appends_invite(self):
        # get_latest_project's body sends the latest project to Telegram and must
        # offer the rating loop. Asserted over the file so we never import the MCP
        # server (which builds the FastMCP app at import time).
        src = (SRC / "argo_mcp_server.py").read_text()
        self.assertIn("project_invite(", src)


if __name__ == "__main__":
    unittest.main()
