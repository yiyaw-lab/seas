"""Heavy MCP tool bodies must run OFF the event loop.

FastMCP runs a *sync* @mcp.tool body inline on the asyncio loop (func_metadata:
`return fn(...)`), so a tool that blocks for seconds (a GitHub PR chain, a feed
refresh + a full model call) freezes the loop and starves the streamable-HTTP
transport -- the Anthropic MCP connector then reports "Error while communicating
with MCP server" and the work (the PR, the project) silently never lands. The fix:
with_deadline now returns an ASYNC wrapper that offloads the body to a worker thread
under a wall-clock cap. These lock it in -- the decorator yields a coroutine, keeps
the loop live while the body blocks, caps overruns with a relayable string, and most
network/model tools are async (not left blocking the loop). propose_change is the one
deliberate exception -- see HeavyToolsAreAsyncTest's docstring below for why a bounded
deadline isn't the right shape for it.

Pure: bodies are stubbed; no network/GitHub/model.
Run from the repo root:  PYTHONPATH=src python3 -m unittest discover -s tests
"""

import asyncio
import time
import unittest
from unittest import mock

import argo_mcp_server as m


class WithDeadlineOffloadTest(unittest.TestCase):
    def test_decorator_yields_a_coroutine(self):
        @m.with_deadline(5)
        def body():
            return "ok"
        self.assertTrue(asyncio.iscoroutinefunction(body))

    def test_keeps_loop_live_while_body_blocks(self):
        @m.with_deadline(5)
        def slow_body():
            time.sleep(0.2)   # blocks the worker thread, NOT the loop
            return "done"

        async def drive():
            ticks = 0

            async def heartbeat():
                nonlocal ticks
                for _ in range(40):
                    await asyncio.sleep(0.005)
                    ticks += 1

            hb = asyncio.ensure_future(heartbeat())
            out = await slow_body()
            hb.cancel()
            return out, ticks

        out, ticks = asyncio.run(drive())
        self.assertEqual(out, "done")
        # If the body ran ON the loop, the await would block it and ticks==0.
        self.assertGreater(ticks, 0)

    def test_overrun_returns_relayable_timeout_string(self):
        @m.with_deadline(0)  # 0s cap -> immediate timeout
        def too_slow():
            time.sleep(0.5)
            return "never"
        out = asyncio.run(too_slow())
        self.assertIn("Timed out", out)

    def test_body_exception_becomes_relayable_string(self):
        @m.with_deadline(5)
        def boom():
            raise ValueError("nope")
        out = asyncio.run(boom())
        self.assertIn("failed", out)
        self.assertIn("nope", out)


class HeavyToolsAreAsyncTest(unittest.TestCase):
    """The tools doing network or model work must be coroutines so FastMCP awaits
    them off-loop instead of running them inline on the event loop.

    propose_change is a deliberate EXCEPTION, not an oversight: with_deadline's
    bounded-wait-then-abandon model still blocks the tool call for up to `seconds`,
    and the authoring model (claude-fable-5, thinking always on) routinely exceeds
    even a generous deadline -- the abandoned worker thread keeps running to
    completion, but its outcome was never delivered to the MCP client (the "300s of
    silence, no PR logged" incident). So propose_change is now a genuinely
    SYNCHRONOUS tool function that spawns its own daemon thread and returns an
    immediate ack; the real outcome is delivered later via Telegram, not through the
    tool's return value at all. See test_propose_change_async.py for that contract."""

    HEAVY = [
        "new_project", "project_too_complex", "add_project",
        "recommend_project", "scaffold_project", "rehearse_project",
        "run_reflection", "web_fetch", "study_url", "verify_feed",
        "github_read_file", "github_list", "get_webhook_health",
        "reregister_webhook", "refetch_signals",
    ]

    def test_heavy_tools_are_coroutine_functions(self):
        for name in self.HEAVY:
            self.assertTrue(asyncio.iscoroutinefunction(getattr(m, name)), name)

    def test_propose_change_is_sync_and_fires_a_background_thread(self):
        # NOT a coroutine function -- see the class docstring for why.
        self.assertFalse(asyncio.iscoroutinefunction(m.propose_change))
        with mock.patch.object(
                m, "_propose_change_impl",
                lambda *a: ("Opened PR for review: http://example/pr/1", {"n": 1})), \
             mock.patch("send_telegram.try_send_message", return_value=True):
            out = m.propose_change("title", "desc", '{"src/x.py": "content"}')
        self.assertIn("On it", out)


if __name__ == "__main__":
    unittest.main()
