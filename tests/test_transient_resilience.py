"""Transient-error resilience: argo_http.get_bytes retry classification and
CircuitBreaker counting only transient failures.

Regression guard for the gap Argo surfaced: MCP web fetches / webhook health had
no (or naive) retry, and a billing-400 storm tripped the circuit breaker the same
as a real provider outage. Both should now distinguish transient from permanent.

Pure: no network, no real data/*.json (record_incident is patched).
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import argo_http  # noqa: E402
import argo_guard  # noqa: E402


class _FakeResp:
    """Minimal urlopen() context-manager stand-in."""

    def __init__(self, data):
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._data


def _http_error(code):
    """A permanent HTTP-style error _is_transient() classifies as non-retryable."""
    e = Exception(f"HTTP {code}")
    e.code = code
    return e


class GetBytesRetryTest(unittest.TestCase):
    def test_retries_once_on_transient_then_succeeds(self):
        calls = []

        def fake_urlopen(req, timeout=None, context=None):
            calls.append(1)
            if len(calls) == 1:
                raise TimeoutError("read timed out")
            return _FakeResp(b"ok")

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen), \
                mock.patch("time.sleep"):
            out = argo_http.get_bytes("https://x/y", timeout=5, retries=1)
        self.assertEqual(out, b"ok")
        self.assertEqual(len(calls), 2)  # one retry then success

    def test_no_retry_on_permanent(self):
        calls = []

        def fake_urlopen(req, timeout=None, context=None):
            calls.append(1)
            raise _http_error(401)

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen), \
                mock.patch("time.sleep"):
            with self.assertRaises(Exception):
                argo_http.get_bytes("https://x/y", timeout=5, retries=1)
        self.assertEqual(len(calls), 1)  # failed fast, no retry on a permanent 401

    def test_retries_zero_means_single_attempt(self):
        calls = []

        def fake_urlopen(req, timeout=None, context=None):
            calls.append(1)
            raise TimeoutError("x")

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen), \
                mock.patch("time.sleep"):
            with self.assertRaises(TimeoutError):
                argo_http.get_bytes("https://x/y", timeout=5, retries=0)
        self.assertEqual(len(calls), 1)


class CircuitBreakerClassifyTest(unittest.TestCase):
    @mock.patch("argo_incidents.record_incident")
    def test_billing_400_does_not_trip_breaker(self, _rec):
        cb = argo_guard.CircuitBreaker("anthropic", threshold=4)

        def billing():
            e = Exception("Your credit balance is too low")
            e.status_code = 400
            raise e

        for _ in range(6):  # well past the threshold
            with self.assertRaises(Exception):
                cb.call(billing)
        self.assertEqual(cb.failures, 0)        # non-transient never advances the breaker
        self.assertEqual(cb._state(), "closed")  # so a recovered balance isn't masked

    @mock.patch("argo_incidents.record_incident")
    def test_transient_503_opens_breaker(self, _rec):
        cb = argo_guard.CircuitBreaker("anthropic", threshold=4)

        def outage():
            e = Exception("service unavailable")
            e.status_code = 503
            raise e

        for _ in range(4):
            with self.assertRaises(Exception):
                cb.call(outage)
        self.assertEqual(cb.failures, 4)
        self.assertEqual(cb._state(), "open")  # real outages still trip it


if __name__ == "__main__":
    unittest.main()
