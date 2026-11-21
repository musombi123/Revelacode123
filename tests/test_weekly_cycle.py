import unittest
from datetime import datetime, timedelta, timezone

from vorax.weekly_cycle import (
    WeeklyCycleError,
    aggregate_hunter_reports,
    build_cycle_manifest,
    current_cycle_id,
    prepare_cycle_retry,
    select_weekly_curriculum_task,
    update_hunter_assignment,
    validate_candidate_for_curriculum,
    validate_cycle_id,
    validate_hunter_report,
)


class WeeklyCycleTests(unittest.TestCase):
    def test_cycle_id_uses_iso_week(self):
        self.assertEqual(current_cycle_id(datetime(2026, 11, 21, tzinfo=timezone.utc)), "2026-W47")

    def test_rejects_invalid_cycle_id(self):
        with self.assertRaises(WeeklyCycleError):
            validate_cycle_id("2026-W99")

    def test_builds_three_bounded_assignments(self):
        manifest = build_cycle_manifest("2026-W48")
        self.assertEqual(len(manifest["hunter_assignments"]), 3)
        self.assertEqual(
            [item["discovery_slice"] for item in manifest["hunter_assignments"]],
            ["text-docs", "vision-audio", "video-tools"],
        )
        self.assertTrue(all(item["timeout_seconds"] == 7200 for item in manifest["hunter_assignments"]))
        self.assertEqual(manifest["curriculum_task"]["id"], "business.gross_margin_percent")

    def test_manifest_rejects_more_than_three_hunters(self):
        with self.assertRaises(WeeklyCycleError):
            build_cycle_manifest("2026-W48", hunter_count=4)

    def test_selects_a_new_task_and_skips_home_covered_task(self):
        first = select_weekly_curriculum_task("2026-W48", {"records": []})
        self.assertEqual(first["id"], "business.gross_margin_percent")
        index = {
            "records": [{
                "id": "business.gross_margin_percent",
                "domain": "business",
                "task": "calculate gross profit margin as a percentage of revenue",
                "triggers": ["gross margin", "gross profit margin", "margin percentage"],
            }]
        }
        second = select_weekly_curriculum_task("2026-W48", index)
        self.assertEqual(second["id"], "business.markup_percent")

    def test_pending_vault_task_is_not_relearned_next_cycle(self):
        task = select_weekly_curriculum_task(
            "2026-W48",
            {"records": []},
            pending_method_ids={"business.gross_margin_percent"},
        )
        self.assertEqual(task["id"], "business.markup_percent")

    def test_all_tasks_covered_returns_no_task(self):
        index = {"records": [
            {
                "id": item["id"],
                "domain": item["domain"],
                "task": item["task"],
                "triggers": item["triggers"],
                "method_sha256": "a" * 64,
            }
            for item in __import__("vorax.weekly_cycle", fromlist=["WEEKLY_CAPABILITY_CURRICULUM"]).WEEKLY_CAPABILITY_CURRICULUM
        ]}
        self.assertIsNone(select_weekly_curriculum_task("2026-W48", index))

    def test_candidate_must_keep_assigned_task_and_required_vectors(self):
        task = select_weekly_curriculum_task("2026-W48", {"records": []})
        candidate = {
            "id": task["id"],
            "domain": task["domain"],
            "task": task["task"],
            "inputs": task["inputs"],
            "triggers": task["triggers"],
            "tests": task["tests"],
        }
        validate_candidate_for_curriculum(candidate, task)
        candidate["tests"] = candidate["tests"][:-1]
        with self.assertRaises(WeeklyCycleError):
            validate_candidate_for_curriculum(candidate, task)

    def test_candidate_cannot_change_input_schema(self):
        task = select_weekly_curriculum_task("2026-W48", {"records": []})
        candidate = {
            "id": task["id"],
            "domain": task["domain"],
            "task": task["task"],
            "inputs": {"other": "number"},
            "triggers": task["triggers"],
            "tests": task["tests"],
        }
        with self.assertRaises(WeeklyCycleError):
            validate_candidate_for_curriculum(candidate, task)

    def test_report_schema_counts_lists_attempt_and_status(self):
        report = validate_hunter_report(
            {"discovered_count": 3, "missing_X_list": ["vision.ocr"], "status": "completed"},
            cycle_id="2026-W48",
            hunter_id="hunter-1",
            discovery_slice="text-docs",
            attempt=0,
        )
        self.assertEqual(report["discovered_count"], 3)
        self.assertEqual(report["missing_X_list"], ["vision.ocr"])
        self.assertEqual(report["attempt"], 0)
        self.assertFalse(report["weights_downloaded"])

    def test_report_rejects_negative_counts(self):
        with self.assertRaises(WeeklyCycleError):
            validate_hunter_report(
                {"verified_count": -1},
                cycle_id="2026-W48",
                hunter_id="hunter-1",
                discovery_slice="text-docs",
            )

    def test_report_rejects_unknown_execution_status(self):
        with self.assertRaises(WeeklyCycleError):
            validate_hunter_report(
                {"status": "successful_without_evidence"},
                cycle_id="2026-W48",
                hunter_id="hunter-1",
                discovery_slice="text-docs",
            )

    def test_all_successful_hunters_complete_cycle(self):
        manifest = build_cycle_manifest("2026-W48")
        for item in manifest["hunter_assignments"]:
            manifest = update_hunter_assignment(manifest, item["hunter_id"], status="completed")
        self.assertEqual(manifest["status"], "complete")

    def test_failed_cycle_schedules_one_retry_after_24h(self):
        now = datetime(2026, 11, 23, tzinfo=timezone.utc)
        manifest = build_cycle_manifest("2026-W48", now=now, hunter_count=1)
        manifest = update_hunter_assignment(
            manifest, "hunter-1", status="failed", reason="network_down", now=now
        )
        self.assertEqual(manifest["status"], "retry_scheduled")
        due = now + timedelta(hours=24)
        retried = prepare_cycle_retry(manifest, now=due)
        self.assertEqual(retried["retry_count"], 1)
        self.assertEqual(retried["hunter_assignments"][0]["status"], "queued")
        self.assertEqual(retried["hunter_assignments"][0]["attempt"], 1)
        self.assertIn("attempt-1", retried["hunter_assignments"][0]["report_path"])

    def test_second_failure_is_terminal(self):
        now = datetime(2026, 11, 23, tzinfo=timezone.utc)
        manifest = build_cycle_manifest("2026-W48", now=now, hunter_count=1)
        manifest = update_hunter_assignment(manifest, "hunter-1", status="failed", now=now)
        retried = prepare_cycle_retry(manifest, now=now + timedelta(hours=24))
        retried = update_hunter_assignment(retried, "hunter-1", status="failed", now=now + timedelta(hours=25))
        self.assertEqual(retried["status"], "failed")

    def test_aggregate_report_preserves_missing_x_and_failure_reasons(self):
        manifest = build_cycle_manifest("2026-W48", hunter_count=2)
        report = aggregate_hunter_reports(
            manifest,
            [
                {
                    "discovered_count": 4,
                    "missing_X_list": ["vision.ocr"],
                    "next_priority": ["OCR"],
                    "rejected": [{"reason": "no_remote_provider"}],
                },
                {"discovered_count": 2, "missing_X_list": ["vision.ocr", "audio.asr"]},
            ],
        )
        self.assertEqual(report["discovered_count"], 6)
        self.assertEqual(report["missing_X_list"], ["vision.ocr", "audio.asr"])
        self.assertEqual(report["rejected"][0]["reason"], "no_remote_provider")


if __name__ == "__main__":
    unittest.main()
