import json
import unittest
import urllib.error

from vorax.network import CircuitBreaker, CircuitOpenError, NetworkError, request_json


class FakeResponse:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return json.dumps(self.payload).encode("utf-8")


class NetworkRetryTests(unittest.TestCase):
    def test_retries_transport_failure_then_succeeds(self):
        calls = {"count": 0}

        def opener(request, timeout):
            calls["count"] += 1
            if calls["count"] == 1:
                raise urllib.error.URLError("offline")
            return FakeResponse({"status": "ok"})

        result = request_json(
            "https://example.test/health",
            opener=opener,
            sleeper=lambda _: None,
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(calls["count"], 2)

    def test_rejects_response_above_configured_size_limit(self):
        with self.assertRaises(NetworkError):
            request_json(
                "https://example.test/health",
                max_response_bytes=4,
                opener=lambda *a, **k: FakeResponse({"status": "ok"}),
                sleeper=lambda _: None,
            )

    def test_caps_network_timeout(self):
        with self.assertRaises(ValueError):
            request_json("https://example.test/health", timeout=31, opener=lambda *a, **k: None)

    def test_includes_response_headers_when_requested(self):
        class HeaderResponse(FakeResponse):
            headers = {"Link": '<https://example.test?page=2>; rel="next"'}

        result, headers = request_json(
            "https://example.test/health",
            include_headers=True,
            opener=lambda *a, **k: HeaderResponse({"status": "ok"}),
        )
        self.assertEqual(result["status"], "ok")
        self.assertIn("Link", headers)

    def test_rejects_plain_http(self):
        with self.assertRaises(NetworkError):
            request_json("http://example.test/health", opener=lambda *a, **k: None)

    def test_circuit_opens_after_five_failures_in_ten_minutes(self):
        now = {"value": 10.0}
        breaker = CircuitBreaker(clock=lambda: now["value"])
        for _ in range(5):
            breaker.record_failure()
        self.assertTrue(breaker.snapshot()["open"])
        with self.assertRaises(CircuitOpenError):
            breaker.before_request()
        now["value"] += 61
        breaker.before_request()
        breaker.record_success()
        self.assertFalse(breaker.snapshot()["open"])


if __name__ == "__main__":
    unittest.main()
