#!/usr/bin/env python3
"""verify-build-order -- the executable gate the orchestrator runs on a compiled
Build Order BEFORE any agent spends a token. Thin CLI over seasar_verify: it loads
each order, runs verify_order, and prints the per-check report; the exit code is the
machine-readable verdict the orchestrator branches on.

Exit codes (the WORST across all orders given):
  0  all orders pass structural checks (and, with --strict, executability too)
  1  some order failed a structural (ERROR) check, or failed to load
  2  --strict only: structure is OK but an executability (WARN) check failed

Usage:
  scripts/verify-build-order.py data/seasar_orders/order-8c31a4.json
  scripts/verify-build-order.py --strict data/seasar_orders/*.json
  seasar_compile.py ... | scripts/verify-build-order.py -
"""

import json
import os
import sys

src_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, src_dir)
import seasar_verify


def main(argv):
    strict = "--strict" in argv
    paths = [a for a in argv if a != "--strict"]
    if not paths:
        print("usage: verify-build-order.py [--strict] <order.json> [...] | -", file=sys.stderr)
        return 1

    worst = 0
    for path in paths:
        if path == "-":
            name, raw = "<stdin>", sys.stdin.read()
        else:
            name = os.path.basename(path)
            try:
                with open(path) as f:
                    raw = f.read()
            except OSError as e:
                print(f"[ERROR] {path}: cannot load ({e})", file=sys.stderr)
                worst = max(worst, 1)
                continue
        try:
            order = json.loads(raw)
        except ValueError as e:
            print(f"[ERROR] {path}: cannot load ({e})", file=sys.stderr)
            worst = max(worst, 1)
            continue

        result = seasar_verify.verify_order(order)
        print(seasar_verify.format_report(result, name=name))
        print()
        if not result["ok"]:
            worst = max(worst, 1)
        elif strict and not result["strict_ok"]:
            worst = max(worst, 2)
    return worst


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
