"""Seasar Foundry web-surface tests.

Foundry itself is currently a local, untracked build artifact. These tests cover
the Flask bridge that serves that build when present and exposes the API shape
the existing bundle already calls.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import argo_webhook as wh


class _FakeSeasar:
    def __init__(self):
        self.compile_calls = []
        self.load_calls = []
        self.build_calls = []
        self.order = {
            "id": "order-abc123",
            "title": "Demo Order",
            "buildability": {"score": 86, "grade": "A"},
            "latent_requirements": [],
            "quality_gates": [],
        }

    def compile_stream(self, idea, stack="", scope="mvp", agents=4):
        self.compile_calls.append({
            "idea": idea,
            "stack": stack,
            "scope": scope,
            "agents": agents,
        })
        yield 'data: {"stage":"smelt","status":"running"}\n\n'
        yield (
            'data: {"stage":"complete","order":{"id":"order-abc123",'
            '"title":"Demo Order","buildability":{"score":86,"grade":"A"},'
            '"latent_requirements":[],"quality_gates":[]}}\n\n'
        )

    def load_order(self, order_id):
        self.load_calls.append(order_id)
        if order_id == self.order["id"]:
            return self.order
        return None

    def build_bundle(self, order):
        self.build_calls.append(order)
        return b"zip-bytes"


class SeasarWebUiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        dist = self.tmp / "foundry-dist"
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text(
            '<div id="root"></div><script src="/assets/app.js"></script>',
            encoding="utf-8")
        (dist / "assets" / "app.js").write_text(
            'fetch("/api/compile"); fetch("/api/order/order-abc123");',
            encoding="utf-8")
        (dist / "favicon.svg").write_text("<svg />", encoding="utf-8")
        self.enterContext(mock.patch.dict(
            wh.os.environ, {"SEASAR_FOUNDRY_DIST": str(dist)}))

        self.fake = _FakeSeasar()
        self.enterContext(mock.patch.object(
            wh, "_seasar_compile_module", lambda: self.fake))
        self.client = wh.create_app().test_client()

    def test_foundry_dist_is_served_without_rebuilding_ui(self):
        res = self.client.get("/seasar", buffered=True)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.mimetype, "text/html")
        self.assertIn('/assets/app.js', res.get_data(as_text=True))

        asset = self.client.get("/assets/app.js", buffered=True)
        self.assertEqual(asset.status_code, 200)
        self.assertIn("/api/compile", asset.get_data(as_text=True))

    def test_compile_api_streams_real_compiler_events(self):
        res = self.client.post("/api/compile", json={
            "idea": "Build a pager",
            "stack": "Python",
            "scope": "weekend",
            "agents": 2,
        }, buffered=True)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.mimetype, "text/event-stream")
        self.assertEqual(self.fake.compile_calls, [{
            "idea": "Build a pager",
            "stack": "Python",
            "scope": "weekend",
            "agents": 2,
        }])
        body = res.get_data(as_text=True)
        self.assertIn('"stage":"smelt"', body)
        self.assertIn('"stage":"complete"', body)

    def test_compile_api_requires_configured_token(self):
        with mock.patch.object(wh, "ARGO_MCP_TOKEN", "secret"):
            denied = self.client.post("/api/compile", json={
                "idea": "Build a pager",
            }, buffered=True)
            self.assertEqual(denied.status_code, 403)
            self.assertEqual(self.fake.compile_calls, [])

            allowed = self.client.post("/api/compile", json={
                "idea": "Build a pager",
            }, headers={"Authorization": "Bearer secret"}, buffered=True)
            self.assertEqual(allowed.status_code, 200)
            self.assertEqual(len(self.fake.compile_calls), 1)

    def test_order_api_returns_persisted_order(self):
        res = self.client.get("/api/order/order-abc123")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["id"], "order-abc123")
        self.assertEqual(self.fake.load_calls, ["order-abc123"])

    def test_bundle_api_downloads_persisted_order_bundle(self):
        res = self.client.get("/api/order/order-abc123/bundle.zip")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.mimetype, "application/zip")
        self.assertEqual(res.get_data(), b"zip-bytes")
        self.assertIn("order-abc123-build-order.zip",
                      res.headers.get("Content-Disposition", ""))
        self.assertEqual(self.fake.load_calls, ["order-abc123"])
        self.assertEqual(self.fake.build_calls, [self.fake.order])

    def test_order_and_bundle_reject_non_order_ids(self):
        order = self.client.get("/api/order/not-an-order")
        bundle = self.client.get("/api/order/not-an-order/bundle.zip")
        self.assertEqual(order.status_code, 404)
        self.assertEqual(bundle.status_code, 404)
        self.assertEqual(self.fake.load_calls, [])

    def test_bundle_api_requires_configured_token(self):
        with mock.patch.object(wh, "ARGO_MCP_TOKEN", "secret"):
            denied = self.client.get("/api/order/order-abc123/bundle.zip")
            self.assertEqual(denied.status_code, 403)
            self.assertEqual(self.fake.load_calls, [])

            allowed = self.client.get(
                "/api/order/order-abc123/bundle.zip",
                headers={"Authorization": "Bearer secret"})
            self.assertEqual(allowed.status_code, 200)
            self.assertEqual(self.fake.load_calls, ["order-abc123"])


if __name__ == "__main__":
    unittest.main()
