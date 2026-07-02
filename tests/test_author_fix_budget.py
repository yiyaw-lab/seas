"""Anchor-drift fix: _author_fix_edits used to cap each suspected file at 40KB, then
JSON-dump the whole files dict and hard-truncate the RESULT to 30,000 chars
(json.dumps(current, indent=2)[:30000]). A big module's tail -- or a whole later file
in the dict -- went silently invisible to the authoring model, so any 'old' anchor it
drafted against that invisible text matched 0 times in _resolve_edits (anchor-miss).

Fix: _budget_files_for_prompt divides a total character budget EVENLY across the
suspected files up front, so every file is represented and a cut is a visible
"...[truncated]" marker (+ a log line), never silence.

Pure: no network/LLM. Run:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import unittest

import argo_mcp_server as srv


class AuthorFixBudgetTest(unittest.TestCase):
    def test_oversized_file_gets_visible_truncation_marker_others_stay_whole(self):
        small_a = "a" * 100
        small_b = "b" * 100
        huge = "x" * 50_000  # alone would eat the whole 30k global budget pre-fix
        current = {
            "src/small_a.py": small_a,
            "src/small_b.py": small_b,
            "src/huge.py": huge,
        }
        budgeted = srv._budget_files_for_prompt(current, total_budget=9_000)

        # All three files are present -- none silently dropped.
        self.assertEqual(set(budgeted), set(current))
        # The huge file was cut and carries an explicit marker.
        self.assertIn("...[truncated]", budgeted["src/huge.py"])
        self.assertLess(len(budgeted["src/huge.py"]), len(huge))
        # The small files fit comfortably under their share and are untouched.
        self.assertEqual(budgeted["src/small_a.py"], small_a)
        self.assertEqual(budgeted["src/small_b.py"], small_b)
        self.assertNotIn("...[truncated]", budgeted["src/small_a.py"])
        self.assertNotIn("...[truncated]", budgeted["src/small_b.py"])

    def test_no_truncation_when_everything_fits(self):
        current = {"src/a.py": "small", "src/b.py": "also small"}
        budgeted = srv._budget_files_for_prompt(current, total_budget=30_000)
        self.assertEqual(budgeted, current)

    def test_empty_files_dict_is_a_noop(self):
        self.assertEqual(srv._budget_files_for_prompt({}, total_budget=30_000), {})

    def test_pre_fix_global_dump_truncation_would_have_dropped_a_later_file(self):
        # Reconstructs the OLD failure mode directly to prove what the fix replaces:
        # json.dumps(current, indent=2)[:30000] on two files each at the real 40KB
        # per-file cap (MAX_PROPOSE_BYTES) means the first alone exceeds the 30k global
        # budget, so the second file's content NEVER appears at all -- not even
        # truncated, just gone, with no marker.
        import json
        current = {"src/first.py": "y" * 40_000, "src/second.py": "z" * 40_000}
        old_style = json.dumps(current, indent=2)[:30000]
        self.assertNotIn("z", old_style)  # second.py is entirely invisible
        self.assertNotIn("[truncated]", old_style)  # and there is no marker at all

        # The fix's per-file budget keeps both files represented, each marked.
        budgeted = srv._budget_files_for_prompt(current, total_budget=30_000)
        self.assertIn("z", budgeted["src/second.py"])
        self.assertIn("...[truncated]", budgeted["src/first.py"])
        self.assertIn("...[truncated]", budgeted["src/second.py"])


if __name__ == "__main__":
    unittest.main()
