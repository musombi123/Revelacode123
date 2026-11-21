import unittest
from unittest.mock import patch

from vorax.scheduler import build_weekly_payload, start_runtime_cycle


class SchedulerTests(unittest.TestCase):
    def test_weekly_payload_assigns_bounded_modal_slices(self):
        payload = build_weekly_payload("2026-W48")
        self.assertEqual(payload["cycle_id"], "2026-W48")
        self.assertEqual(
            payload["discovery_slices"],
            ["text-docs", "vision-audio", "video-tools"],
        )
        self.assertFalse(payload["weights_downloaded"])
        self.assertFalse(payload["billable_inference_allowed"])

    def test_cycle_start_calls_runtime_only_with_child_auth(self):
        with patch("vorax.scheduler.request_json", return_value={"status": "discovering"}) as request:
            result = start_runtime_cycle(
                "https://runtime.example",
                child_id="kaggle-child",
                child_key="test-secret",
                cycle_id="2026-W48",
            )
        self.assertEqual(result["status"], "discovering")
        args, kwargs = request.call_args
        self.assertEqual(args[0], "https://runtime.example/vorax/cycle/start")
        self.assertEqual(kwargs["method"], "POST")
        self.assertEqual(kwargs["headers"]["x-child-id"], "kaggle-child")
        self.assertEqual(kwargs["headers"]["x-child-key"], "test-secret")
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(kwargs["attempts"], 3)

    def test_rejects_non_https_runtime(self):
        with self.assertRaises(ValueError):
            start_runtime_cycle(
                "http://runtime.example",
                child_id="kaggle-child",
                child_key="secret",
                cycle_id="2026-W48",
            )


if __name__ == "__main__":
    unittest.main()
