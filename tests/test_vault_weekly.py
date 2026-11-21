import hashlib
import unittest
from unittest.mock import patch

from vorax import vault
from vorax.vlang import dump_record


def sample_method():
    return {
        "version": "vlang/1",
        "id": "weekly.add_two_numbers",
        "domain": "math",
        "task": "add two numbers",
        "triggers": ["add two numbers", "sum two values"],
        "inputs": {"a": "number", "b": "number"},
        "steps": [
            {"op": "add", "args": [{"input": "a"}, {"input": "b"}], "as": "sum"}
        ],
        "result": {"step": "sum"},
        "tests": [
            {"input": {"a": 1, "b": 2}, "expect": 3},
            {"input": {"a": 5, "b": 6}, "expect": 11},
        ],
        "provenance": {"kind": "teacher", "teacher": "test-teacher"},
    }


class WeeklyVaultMetadataTests(unittest.TestCase):
    def test_pending_method_coverage_returns_metadata_only(self):
        pending = {
            "version": "vlang-index/1",
            "records": [{
                "id": "weekly.add_two_numbers",
                "domain": "math",
                "task": "add two numbers",
                "triggers": ["add two numbers", "sum two values"],
                "path": "vlang/pending/weekly.add_two_numbers.vlang",
                "sha256": "a" * 64,
            }],
        }
        with patch.object(vault, "_client", return_value=(object(), "repo")), \
             patch.object(vault, "_read_json", return_value=pending), \
             patch.object(vault, "_read_file") as read_file:
            result = vault.pending_method_coverage()
        self.assertEqual(result, [{
            "id": "weekly.add_two_numbers",
            "domain": "math",
            "task": "add two numbers",
            "triggers": ["add two numbers", "sum two values"],
        }])
        read_file.assert_not_called()

    def test_pending_method_lookup_reexecutes_and_checks_canonical_digest(self):
        record = sample_method()
        raw = dump_record(record).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        pending = {
            "version": "vlang-index/1",
            "records": [{
                "id": record["id"],
                "domain": record["domain"],
                "task": record["task"],
                "triggers": record["triggers"],
                "path": "vlang/pending/weekly.add_two_numbers.vlang",
                "sha256": digest,
            }],
        }
        with patch.object(vault, "_client", return_value=(object(), "repo")), \
             patch.object(vault, "_read_json", return_value=pending), \
             patch.object(vault, "_read_file", return_value=raw):
            result = vault.pending_method_by_sha256(digest)
        self.assertEqual(result["record"]["id"], record["id"])
        self.assertEqual(result["method_sha256"], digest)
        self.assertEqual(result["path"], "vlang/pending/weekly.add_two_numbers.vlang")

    def test_pending_method_lookup_rejects_bad_digest_and_missing_digest(self):
        with self.assertRaises(vault.VaultError):
            vault.pending_method_by_sha256("not-a-sha256")
        with patch.object(vault, "_client", return_value=(object(), "repo")), \
             patch.object(vault, "_read_json", return_value={"version": "vlang-index/1", "records": []}), \
             patch.object(vault, "_read_file") as read_file:
            result = vault.pending_method_by_sha256("b" * 64)
        self.assertIsNone(result)
        read_file.assert_not_called()


if __name__ == "__main__":
    unittest.main()
