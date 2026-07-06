"""Post-Round14 Benchmark Harness v0 tests."""

import json
import os
import subprocess
import sys
import unittest

import seasar_requirements
import seasar_round14_benchmark as bench


class Round14BenchmarkTest(unittest.TestCase):
    def test_cases_cover_the_four_proven_seams(self):
        self.assertEqual([c["affordance"] for c in bench.CASES],
                         list(seasar_requirements.AFFORDANCE_ORDER))
        for case in bench.CASES:
            req = seasar_requirements.scan_sources({"idea": case["prompt"]})[0]
            self.assertNotIn(req["counter_cue"], case["prompt"])

    def test_benchmark_scores_are_stable_across_repeated_runs(self):
        first = bench.run_benchmark()
        second = bench.run_benchmark()
        self.assertEqual(first, second)
        self.assertEqual(first["total_score"], 16)
        self.assertEqual(first["max_score"], 16)
        for row in first["cases"]:
            self.assertTrue(all(row["scores"].values()))

    def test_text_and_json_output_are_deterministic(self):
        result = bench.run_benchmark()
        self.assertEqual(bench.format_json(result), bench.format_json(result))
        self.assertEqual(bench.format_text(result), bench.format_text(result))
        self.assertIn("TOTAL 16/16", bench.format_text(result))
        parsed = json.loads(bench.format_json(result))
        self.assertEqual(parsed, result)

    def test_cli_json_output_is_stable(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = os.path.abspath("src")
        first = subprocess.run(
            [sys.executable, "-m", "seasar_round14_benchmark", "--json"],
            cwd=os.getcwd(), env=env, text=True, capture_output=True, check=True)
        second = subprocess.run(
            [sys.executable, "-m", "seasar_round14_benchmark", "--json"],
            cwd=os.getcwd(), env=env, text=True, capture_output=True, check=True)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(json.loads(first.stdout)["total_score"], 16)


if __name__ == "__main__":
    unittest.main()
