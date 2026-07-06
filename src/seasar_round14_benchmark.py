"""Post-round14 scanner/verifier benchmark harness.

Pure stdlib, no model calls. Each case runs the affordance scanner, compiles a
minimal requirement-backed gate, runs the order verifier, and scores the spine
fields that post-round14 work made load-bearing.
"""

import argparse
import json

import seasar_requirements
import seasar_verify


CASES = (
    {
        "case_id": "pagination_cursor_inventory",
        "affordance": "pagination",
        "prompt": (
            "Build an incident importer that lists all records across pages using "
            "cursor tokens and records each page into the local archive."
        ),
    },
    {
        "case_id": "caching_profile_snapshot",
        "affordance": "caching",
        "prompt": (
            "Build a profile snapshot service that caches user profile lookups with "
            "a short TTL before rendering account summaries."
        ),
    },
    {
        "case_id": "retry_webhook_delivery",
        "affordance": "retry",
        "prompt": (
            "Build a webhook sender that retries transient delivery failures with "
            "exponential backoff when partners return HTTP 429."
        ),
    },
    {
        "case_id": "debounce_search_input",
        "affordance": "debounce",
        "prompt": (
            "Build a typeahead search box that debounces keystrokes before querying "
            "the API for matching records."
        ),
    },
)


def _fixture(path, ok):
    return {"path": path, "format": "json", "body": json.dumps({"ok": ok}, sort_keys=True)}


def _gate_for_requirement(req):
    gate_id = req.get("gate_id") or ("gate-" + req.get("affordance", "requirement"))
    test_path = "tests/gates/%s.py" % gate_id.replace("-", "_")
    golden = "tests/fixtures/%s-golden.json" % gate_id
    broken = "tests/fixtures/%s-broken.json" % gate_id
    run = "python3 " + test_path
    return {
        "name": gate_id,
        "threshold": "%s %s" % (req.get("requirement_id", ""), req.get("counter_cue", "")),
        "blocks_merge": True,
        "test_lang": "python",
        "test_path": test_path,
        "test_source": "def test_gate():\n    assert True\n",
        "fixture_refs": [],
        "gate_forge": {
            "forge_id": "forge-" + gate_id,
            "gate_id": gate_id,
            "requirement_id": req.get("requirement_id", ""),
            "counter_cue": req.get("counter_cue", ""),
            "status": "discriminates",
            "run_command": run,
            "golden_fixture_ref": golden,
            "broken_fixture_ref": broken,
            "attempts": [{
                "attempt": 1,
                "run_command": run,
                "test_path": test_path,
                "golden_fixture_ref": golden,
                "golden_exit_code": 0,
                "broken_fixture_ref": broken,
                "broken_exit_code": 1,
                "revision_note": "benchmark discriminating fixture pair",
            }],
        },
    }, (_fixture(golden, True), _fixture(broken, False))


def _order_for_requirements(reqs):
    gates, fixtures = [], []
    for raw in reqs:
        req = dict(raw)
        req["status"] = "satisfied"
        gate, pair = _gate_for_requirement(req)
        gates.append(gate)
        fixtures.extend(pair)
    return {
        "title": "Post-round14 Benchmark",
        "tasks": [{
            "id": "T1",
            "title": "Implement requirement-backed behavior",
            "wave": 1,
            "depends_on": [],
            "files": ["src/benchmark_subject.py"],
            "acceptance": "scanner and verifier fields pass",
            "test": "python3 -m unittest discover -s tests",
        }],
        "work_orders": [{
            "agent": "Agent A",
            "role": "Backend",
            "task_ids": ["T1"],
            "worktree": "wt/agent-a",
            "brief": "Implement the requirement-backed behavior.",
            "definition_of_done": "The requirement gate and verifier pass.",
        }],
        "orchestration": {
            "topology": "orchestrator-worker",
            "waves": [["T1"]],
            "handoff_protocol": "merge after gates",
            "contract_evolution": "owner proposes contract changes",
            "consistency_check": "run verifier before dispatch",
        },
        "quality_gates": gates,
        "fixtures": fixtures,
        "latent_requirements": [dict(r, status="satisfied") for r in reqs],
        "scaffold_files": [{"path": "pyproject.toml", "body": "[project]\nname='bench'\n"}],
    }


def _check_ok(verification, name):
    checks = {c.get("name"): c for c in verification.get("checks") or []}
    return bool(checks.get(name, {}).get("ok"))


def score_case(case):
    reqs = seasar_requirements.scan_sources({"idea": case["prompt"]})
    matched = [r for r in reqs if r.get("affordance") == case["affordance"]]
    req = matched[0] if matched else {}
    order = _order_for_requirements(matched)
    verification = seasar_verify.verify_order(order)
    scores = {
        "affordance_discovered": bool(req),
        "counter_cue_present": bool(req.get("counter_cue"))
        and req.get("counter_cue") not in case["prompt"],
        "gate_threaded": bool(req) and _check_ok(verification, "latent_requirements_gate_threaded"),
        "forge_evidence_present": bool(req) and _check_ok(verification, "gate_forge_discriminates"),
    }
    return {
        "case_id": case["case_id"],
        "affordance": case["affordance"],
        "requirement_id": req.get("requirement_id", ""),
        "gate_id": req.get("gate_id", ""),
        "scores": scores,
        "score": sum(1 for ok in scores.values() if ok),
        "max_score": len(scores),
    }


def run_benchmark(cases=CASES):
    case_results = [score_case(c) for c in cases]
    total = sum(r["score"] for r in case_results)
    max_score = sum(r["max_score"] for r in case_results)
    return {
        "benchmark": "post-round14-v0",
        "cases": case_results,
        "total_score": total,
        "max_score": max_score,
    }


def format_text(result):
    lines = ["Post-Round14 Benchmark v0"]
    for row in result["cases"]:
        lines.append("%s [%s] %d/%d" % (
            row["case_id"], row["affordance"], row["score"], row["max_score"]))
        for field in sorted(row["scores"]):
            lines.append("  %s: %s" % (field, "PASS" if row["scores"][field] else "FAIL"))
    lines.append("TOTAL %d/%d" % (result["total_score"], result["max_score"]))
    return "\n".join(lines) + "\n"


def format_json(result):
    return json.dumps(result, indent=2, sort_keys=True) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the post-round14 benchmark harness.")
    parser.add_argument("--json", action="store_true", help="emit deterministic JSON")
    args = parser.parse_args(argv)
    result = run_benchmark()
    if args.json:
        print(format_json(result), end="")
    else:
        print(format_text(result), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
