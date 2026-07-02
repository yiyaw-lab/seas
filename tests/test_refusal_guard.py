"""Refusal-guard tests -- claude-fable-5 can return stop_reason == 'refusal' with
empty or partial `content` on an HTTP 200 (no exception from the SDK). Before this
guard, every Anthropic response-unpack site in argo_observe joined response.content
with a generator expression, so a refusal silently degraded to "" instead of
surfacing -- the caller could not tell a refusal from a genuinely empty answer.
_check_refusal() closes that: called right after _guarded() returns, it raises a
clear RuntimeError on stop_reason == 'refusal' so the failure takes the same
`except Exception` path every other model-call failure already takes (see
argo_webhook._llm_reply's last_error handling), and is a no-op for every normal
stop_reason. Pure -- a stub response object, no network or real key.

Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import unittest

import argo_observe as observe


class _RefusedResponse:
    """Stub: what an Anthropic SDK response looks like on a refusal -- stop_reason
    set, content empty (the partial-content case is content with fewer/shorter
    blocks than expected, not exercised separately since _check_refusal only
    looks at stop_reason)."""
    stop_reason = "refusal"
    content = []


class _NormalResponse:
    stop_reason = "end_turn"
    content = []


class RefusalGuardTest(unittest.TestCase):
    def test_unguarded_unpack_does_not_indexerror_on_refusal_stub(self):
        # Failing-first control: confirms what the unguarded generator-join unpack
        # used at every call site actually does on a refusal stub. It does NOT
        # IndexError (the sites here never index response.content[0]) -- it silently
        # returns "", which is the actual bug _check_refusal closes: a refusal reads
        # as an empty-but-successful reply, not a failure.
        text = "".join(
            block.text for block in _RefusedResponse.content
            if getattr(block, "type", None) == "text"
        )
        self.assertEqual(text, "")

    def test_check_refusal_raises_on_refusal_stop_reason(self):
        with self.assertRaises(RuntimeError) as ctx:
            observe._check_refusal(_RefusedResponse(), "chat/claude-fable-5")
        self.assertIn("refused", str(ctx.exception))

    def test_check_refusal_noop_on_normal_stop_reason(self):
        observe._check_refusal(_NormalResponse(), "chat/claude-sonnet-4-6")  # no raise

    def test_check_refusal_noop_when_stop_reason_absent(self):
        class NoStopReason:
            content = []
        observe._check_refusal(NoStopReason(), "chat/claude-sonnet-4-6")  # no raise


if __name__ == "__main__":
    unittest.main()
