"""CircuitBreaker refusal-exemption tests -- adversarial-reviewer finding on PR #84:
a ModelRefusal (argo_observe._check_refusal's RuntimeError) propagating through
CircuitBreaker.call counted toward the 4-consecutive-failure threshold that opens
a provider breaker for 60s. A refusal is a per-request content outcome (the
provider answered fine), not a provider outage -- 4 borderline asks in a row would
wrongly fail-fast ALL calls to that provider. Fix: CircuitBreaker.call now
recognizes argo_observe.ModelRefusal and re-raises it without touching
failures/opened_at in either direction (no failure-count advance, no
success-reset) -- genuine transient/permanent errors still drive the breaker
exactly as before.

Pure: no network/key; ModelRefusal and plain exceptions raised directly through
breaker.call(fn).

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import unittest

import argo_guard
from argo_observe import ModelRefusal


class BreakerRefusalExemptionTest(unittest.TestCase):
    def test_four_consecutive_refusals_do_not_open_breaker(self):
        breaker = argo_guard.CircuitBreaker("anthropic")

        def refuse():
            raise ModelRefusal("model refused: chat/claude-fable-5")

        for _ in range(4):
            with self.assertRaises(ModelRefusal):
                breaker.call(refuse)

        self.assertEqual(breaker._state(), "closed")
        self.assertEqual(breaker.failures, 0)

    def test_four_consecutive_genuine_errors_still_open_breaker(self):
        # Regression guard: the refusal exemption must not blunt the breaker for
        # a REAL provider outage.
        breaker = argo_guard.CircuitBreaker("anthropic")

        def fail():
            raise TimeoutError("connection timed out")

        for _ in range(4):
            with self.assertRaises(TimeoutError):
                breaker.call(fail)

        self.assertEqual(breaker._state(), "open")

    def test_refusal_does_not_reset_an_in_progress_failure_streak(self):
        # A refusal is not evidence the provider recovered -- it must not zero out
        # a failure count that a genuine transient error already built up.
        breaker = argo_guard.CircuitBreaker("anthropic")

        def fail():
            raise TimeoutError("connection timed out")

        def refuse():
            raise ModelRefusal("model refused: chat/claude-fable-5")

        with self.assertRaises(TimeoutError):
            breaker.call(fail)
        with self.assertRaises(TimeoutError):
            breaker.call(fail)
        self.assertEqual(breaker.failures, 2)

        with self.assertRaises(ModelRefusal):
            breaker.call(refuse)
        self.assertEqual(breaker.failures, 2)  # unchanged, not reset to 0

    def test_refusal_after_open_breaker_still_fails_fast(self):
        # An already-open breaker must keep failing fast regardless of what the
        # next call would have raised -- the exemption only applies to the
        # try/except accounting, not the open-state short-circuit.
        breaker = argo_guard.CircuitBreaker("anthropic")

        def fail():
            raise TimeoutError("connection timed out")

        for _ in range(4):
            with self.assertRaises(TimeoutError):
                breaker.call(fail)
        self.assertEqual(breaker._state(), "open")

        def refuse():
            raise ModelRefusal("model refused: chat/claude-fable-5")

        with self.assertRaises(RuntimeError) as ctx:
            breaker.call(refuse)
        self.assertIn("is open", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
