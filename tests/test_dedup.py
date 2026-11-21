import unittest

from vorax.weekly_cycle import ProposalDeduplicator, WeeklyCycleError, proposal_digest_key
from vorax.vlang import fingerprint


class ProposalDedupTests(unittest.TestCase):
    def test_same_digest_and_cycle_claimed_once_across_hunters(self):
        digest = "a" * 64
        ledger = ProposalDeduplicator()
        first = ledger.claim(digest, "2026-W47", "hunter-1")
        duplicate = ledger.claim(digest, "2026-W47", "hunter-2")
        self.assertTrue(first["claimed"])
        self.assertTrue(duplicate["duplicate"])
        self.assertFalse(duplicate["claimed"])

    def test_same_hunter_retry_is_idempotent(self):
        digest = "b" * 64
        ledger = ProposalDeduplicator()
        ledger.claim(digest, "2026-W47", "hunter-1")
        retry = ledger.claim(digest, "2026-W47", "hunter-1")
        self.assertTrue(retry["claimed"])
        self.assertTrue(retry["idempotent_retry"])

    def test_same_method_in_next_week_is_a_new_idempotency_key(self):
        digest = "c" * 64
        self.assertNotEqual(
            proposal_digest_key(digest, "2026-W47"),
            proposal_digest_key(digest, "2026-W48"),
        )

    def test_method_fingerprint_ignores_teacher_provenance(self):
        base = {
            "version": "vlang/1",
            "id": "math.add",
            "domain": "math",
            "task": "add two numbers",
            "triggers": ["add", "sum"],
            "inputs": {"a": "number", "b": "number"},
            "steps": [{"op": "add", "args": [{"input": "a"}, {"input": "b"}], "as": "result"}],
            "result": {"step": "result"},
            "tests": [{"input": {"a": 2, "b": 3}, "expect": 5}],
            "provenance": {"kind": "teacher", "child_id": "kaggle-child", "teacher": "teacher-a"},
        }
        another_provider = dict(base)
        another_provider["provenance"] = {
            "kind": "teacher", "child_id": "kaggle-child", "teacher": "teacher-b"
        }
        self.assertEqual(fingerprint(base), fingerprint(another_provider))

    def test_rejects_invalid_digest(self):
        with self.assertRaises(WeeklyCycleError):
            proposal_digest_key("not-a-sha", "2026-W47")


if __name__ == "__main__":
    unittest.main()
