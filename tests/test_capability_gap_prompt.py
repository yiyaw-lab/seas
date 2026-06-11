"""Capability-gap contract tests (argo_webhook.build_system_prompt).

The owner's rule: when asked to do something Argo can't yet, it must treat the gap
as a request to grow -- propose the enabling PR (propose_change) when buildable,
refuse with explicit reasoning when technically infeasible, defend (not "fix") its
deliberate safety limits, and route config gaps to check_config. The behavior lives
in the system prompt, so this locks the load-bearing instructions into it -- a prompt
refactor that drops the block fails here before it ships a regression.

Pure: builds the prompt from a fixed profile; no network, no LLM, no MCP registry
requirement (the capability block degrades to '' outside the server process).

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import unittest

import argo_webhook as wh

_PROFILE = {"name": "Yiya", "one_liner": "a builder", "persona": "Plain.",
            "subject": "she", "object": "her", "possessive": "her"}


class CapabilityGapPromptTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prompt = wh.build_system_prompt(_PROFILE)

    def test_gap_block_present_with_all_four_branches(self):
        self.assertIn("CAPABILITY GAPS", self.prompt)
        self.assertIn("BUILDABLE", self.prompt)          # (a) propose the PR
        self.assertIn("NOT FEASIBLE", self.prompt)       # (b) honest, reasoned no
        self.assertIn("BY DESIGN", self.prompt)          # (c) defend the rails
        self.assertIn("CONFIG, not code", self.prompt)   # (d) check_config, not a PR

    def test_buildable_branch_routes_to_propose_change_with_human_merge(self):
        i = self.prompt.index("CAPABILITY GAPS")
        block = self.prompt[i:i + 1700]
        self.assertIn("propose_change", block)
        self.assertIn("human merges", block)
        # honesty rails: never claim the capability before the PR lands
        self.assertIn("Never pretend", block)

    def test_infeasible_branch_demands_reasoning_not_vague_refusal(self):
        i = self.prompt.index("NOT FEASIBLE")
        block = self.prompt[i:i + 700]
        self.assertIn("name the exact missing piece", block)
        self.assertIn("vague 'I can't'", block)

    def test_by_design_branch_forbids_removing_own_rails(self):
        i = self.prompt.index("BY DESIGN")
        block = self.prompt[i:i + 500]
        self.assertIn("do NOT", block)
        self.assertIn("rails", block)

    def test_literal_braces_in_prompt_survive(self):
        # The prompt mixes f-string lines with plain literals; a stray .format()
        # on the concatenation would crash on these literal braces (real bug,
        # caught at build time 2026-06-10).
        self.assertIn("{label, url}", self.prompt)


if __name__ == "__main__":
    unittest.main()
