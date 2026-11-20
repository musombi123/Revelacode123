import unittest

from scripts.require_green_release import latest_verify_check, validate_context


class ReleaseGateTests(unittest.TestCase):
    def test_requires_tag_ref(self):
        with self.assertRaisesRegex(RuntimeError, "Git tag"):
            validate_context("branch", "main", "a" * 40, "musombi123/Revelacode123")

    def test_rejects_incomplete_sha(self):
        with self.assertRaisesRegex(RuntimeError, "full 40-character"):
            validate_context("tag", "v1.0.0", "abc123", "musombi123/Revelacode123")

    def test_uses_latest_verify_attempt(self):
        runs = [
            {
                "id": 1, "name": "verify", "status": "completed", "conclusion": "success",
                "started_at": "2026-11-20T08:00:00Z",
                "check_suite": {"head_sha": "a" * 40},
            },
            {
                "id": 2, "name": "verify", "status": "completed", "conclusion": "failure",
                "started_at": "2026-11-20T09:00:00Z",
                "check_suite": {"head_sha": "a" * 40},
            },
        ]
        with self.assertRaisesRegex(RuntimeError, "not green"):
            latest_verify_check(runs, "a" * 40)

    def test_ignores_checks_for_another_sha(self):
        runs = [{
            "id": 1, "name": "verify", "status": "completed", "conclusion": "success",
            "started_at": "2026-11-20T09:00:00Z",
            "check_suite": {"head_sha": "b" * 40},
        }]
        with self.assertRaisesRegex(RuntimeError, "exact tagged commit"):
            latest_verify_check(runs, "a" * 40)

    def test_requires_success_for_exact_sha(self):
        runs = [{
            "id": 3, "name": "verify", "status": "completed", "conclusion": "success",
            "started_at": "2026-11-20T09:00:00Z",
            "check_suite": {"head_sha": "a" * 40},
        }]
        self.assertEqual(latest_verify_check(runs, "a" * 40)["id"], 3)


if __name__ == "__main__":
    unittest.main()
