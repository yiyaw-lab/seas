"""Pure caller tests: real MCP framing over a mock HTTP transport, no network."""
import asyncio
import json
import os
import unittest
from unittest import mock

import httpx

import argo_mcp_server
import seas_finding as sf
import seas_search


class SeasSearchTest(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.results = [{"url": "https://arxiv.org/abs/1234.5678",
                         "title": "Related paper", "excerpts": ["Corroborating text"]}]
        self.error = False
        self.raw_result = None
        self.client_type = httpx.AsyncClient
        self.env = mock.patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def client(self, **kwargs):
        return self.client_type(transport=httpx.MockTransport(self.respond), **kwargs)

    def respond(self, request):
        self.requests.append(request)
        body = json.loads(request.content)
        method = body["method"]
        if method.startswith("notifications/"):
            return httpx.Response(202)
        if method == "initialize":
            result = {"protocolVersion": body["params"]["protocolVersion"],
                      "capabilities": {"tools": {}},
                      "serverInfo": {"name": "fixture", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": [{"name": "web_search", "inputSchema": {
                "type": "object", "properties": {"objective": {"type": "string"},
                "search_queries": {"type": "array", "items": {"type": "string"}}},
                "required": ["objective", "search_queries"]}}]}
        elif method == "tools/call":
            result = {"isError": self.error, "content": [{"type": "text", "text":
                      self.raw_result if self.raw_result is not None else
                      json.dumps({"results": self.results})}]}
        else:
            self.fail("Unexpected MCP method: " + method)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"],
                                        "result": result})

    def gather(self, investigate=False):
        with mock.patch("httpx.AsyncClient", side_effect=self.client), \
                mock.patch.object(sf, "_fetch_source", return_value="Original source"), \
                mock.patch.object(argo_mcp_server, "ALLOWED_HOSTS", {"arxiv.org"}):
            signal = {"title": "Agent evidence", "source": "arxiv",
                      "link": "https://arxiv.org/abs/9999.12345"}
            if investigate:
                return sf.investigate(signal, dry_run=True)
            return sf._gather_sources(signal, dry_run=True)

    def test_no_key_gathers_related_sources(self):
        sources = self.gather()
        self.assertEqual(len(sources), 2)
        self.assertEqual(sources[1]["text"], "Corroborating text")
        self.assertEqual(sources[1]["url"], self.results[0]["url"])
        methods = [json.loads(r.content)["method"] for r in self.requests]
        self.assertIn("initialize", methods)
        self.assertIn("tools/list", methods)
        self.assertIn("tools/call", methods)
        for req in self.requests:
            self.assertEqual(str(req.url), "https://search.parallel.ai/mcp")
            self.assertEqual(req.headers["user-agent"], "argo-seas/1.0")
            self.assertNotIn("authorization", req.headers)
        call = next(json.loads(r.content)["params"] for r in self.requests
                    if json.loads(r.content)["method"] == "tools/call")
        self.assertEqual(call["name"], "web_search")
        self.assertEqual(call["arguments"]["search_queries"], ["Agent evidence"])
        self.assertEqual(len(call["arguments"]["session_id"]), 32)

    def test_configured_firecrawl_preserves_category_and_failure(self):
        os.environ["FIRECRAWL_API_KEY"] = "saved-key"
        with mock.patch.object(seas_search.firecrawl_client, "search_related",
                               return_value=None) as search:
            self.assertEqual(len(self.gather()), 1)
        search.assert_called_once_with("Agent evidence", category="research",
                                       limit=5, allowed_hosts={"arxiv.org"})
        self.assertEqual(self.requests, [])

    def test_explicit_parallel_overrides_firecrawl_credentials(self):
        os.environ.update(FIRECRAWL_API_KEY="saved-key", SEAS_SEARCH_PROVIDER="parallel")
        with mock.patch.object(seas_search.firecrawl_client, "search_related") as search:
            self.assertEqual(len(self.gather()), 2)
        search.assert_not_called()

    def test_explicit_firecrawl_without_key_does_not_fall_back(self):
        os.environ["SEAS_SEARCH_PROVIDER"] = "firecrawl"
        self.assertEqual(len(self.gather()), 1)
        self.assertEqual(self.requests, [])

    def test_disabled_and_invalid_choices_never_search(self):
        for choice in ("none", "unknown"):
            with self.subTest(choice=choice):
                os.environ["SEAS_SEARCH_PROVIDER"] = choice
                self.assertEqual(len(self.gather()), 1)
        self.assertEqual(self.requests, [])

    def test_empty_permissions_or_query_never_search(self):
        with mock.patch("httpx.AsyncClient") as client:
            self.assertEqual(seas_search.search_related("topic", allowed_hosts=[]), [])
            self.assertEqual(seas_search.search_related("  ", allowed_hosts={"arxiv.org"}), [])
            self.assertEqual(seas_search.search_related("topic", limit=0,
                                                        allowed_hosts={"arxiv.org"}), [])
        client.assert_not_called()

    def test_only_approved_https_content_reaches_synthesis(self):
        bad_urls = ["https://arxiv.org.evil.test/a", "http://arxiv.org/a",
                    "https://evil.test/a", "https://arxiv.org:bad/a",
                    "https://user@arxiv.org/a", "https://@arxiv.org/a",
                    "https://arxiv.org:8443/a", "https://127.0.0.1/a"]
        self.results += [{"url": u, "title": "bad", "excerpts": ["Unapproved"]}
                         for u in bad_urls]
        self.results += [self.results[0], {"url": "https://arxiv.org/abs/9999.12345",
                                         "excerpts": ["duplicate original"]},
                         {"url": "https://arxiv.org/empty", "excerpts": [None, " "]},
                         {"url": "https://arxiv.org/invalid", "excerpts": "not a list"},
                         None]
        sources = self.gather()
        self.assertEqual(len(sources), 2)
        self.assertNotIn("Unapproved", str(sources))

    def test_content_and_result_limits(self):
        self.results = [{"url": "https://arxiv.org/" + str(i),
                         "excerpts": ["a" * 8000]} for i in range(10)]
        sources = self.gather()
        self.assertEqual(len(sources), 6)  # original + at most 5 related sources
        self.assertEqual(len(sources[1]["text"]), 6000)

    def test_arxiv_versions_and_formats_are_one_source(self):
        self.results = [{"url": u, "excerpts": ["Same paper"]} for u in (
            "https://arxiv.org/abs/1706.03762", "https://arxiv.org/html/1706.03762v2",
            "https://arxiv.org/pdf/1706.03762v5.pdf", "https://arxiv.org/pdf/1706.03762")]
        self.results.append({"url": "https://arxiv.org/pdf/9999.12345v1", "excerpts": ["own"]})
        # Both a related paper's aliases and an alias of the signal's own source
        # must be deduped before the model sees them.
        self.assertEqual(len(self.gather()), 2)

    def test_old_arxiv_ids_and_fragments_are_deduped(self):
        self.assertEqual(seas_search.source_key("https://arxiv.org/abs/hep-th/9901001v2"),
                         seas_search.source_key("https://arxiv.org/pdf/hep-th/9901001.pdf"))
        self.assertEqual(seas_search.source_key("https://github.com/example/repo#readme"),
                         "https://github.com/example/repo")

    def test_errors_and_malformed_results_keep_original_source(self):
        for payload in ("not json", "{}", '{"results": {}}'):
            with self.subTest(payload=payload):
                self.raw_result = payload
                with self.assertLogs("seas_search", level="WARNING"):
                    self.assertEqual(len(self.gather()), 1)
        self.raw_result = None
        self.error = True
        with self.assertLogs("seas_search", level="WARNING"):
            self.assertEqual(len(self.gather()), 1)

    def test_transport_error_does_not_switch_provider(self):
        def failed(request):
            raise httpx.ReadTimeout("fixture timeout", request=request)
        self.respond = failed
        with mock.patch.object(seas_search.firecrawl_client, "search_related") as search, \
                self.assertLogs("seas_search", level="WARNING"):
            self.assertEqual(len(self.gather()), 1)
        search.assert_not_called()

    def test_total_deadline_cancels_transport(self):
        closed = []

        async def stalled(request):
            try:
                await asyncio.Event().wait()
            finally:
                closed.append(True)

        self.respond = stalled
        with mock.patch.object(seas_search, "DEFAULT_TIMEOUT", 0.03), \
                self.assertLogs("seas_search", level="WARNING"):
            self.assertEqual(len(self.gather()), 1)
        self.assertTrue(closed)

    def test_cancellation_propagates_and_closes_transport(self):
        closed = []

        async def run():
            started = asyncio.Event()

            async def stalled(request):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    closed.append(True)

            self.respond = stalled
            with mock.patch("httpx.AsyncClient", side_effect=self.client):
                task = asyncio.create_task(seas_search._parallel_search(
                    "topic", {"arxiv.org"}, 5))
                await asyncio.wait_for(started.wait(), 1)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

        asyncio.run(run())
        self.assertTrue(closed)

    def test_scripted_synthesis_consumes_search_text_and_url(self):
        os.environ["OPENAI_API_KEY"] = "fixture-model-key"
        with mock.patch.object(sf.probes, "should_investigate", return_value=(True, "")), \
                mock.patch.object(sf.observe, "resolve_models", return_value=["gpt-5"]), \
                mock.patch.object(sf.observe, "generate_observations", return_value=
                                  '{"verdict":"premature","reason":"fixture"}') as model:
            result = self.gather(investigate=True)
        prompt = model.call_args.args[0]
        self.assertIn("Corroborating text", prompt)
        self.assertIn(self.results[0]["url"], prompt)
        self.assertIn("Original source", prompt)
        self.assertIn("probe premature", result)


if __name__ == "__main__":
    unittest.main()
