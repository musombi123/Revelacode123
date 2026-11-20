import hashlib
import unittest

from vorax.home_index import (
    append_method,
    build_method_index,
    find_duplicate,
    parse_method_index,
)
from vorax.vlang import dump_record


def sample_method(method_id="test.echo", task="echo supplied text", triggers=None):
    return {
        "version": "vlang/1",
        "id": method_id,
        "domain": "text",
        "task": task,
        "triggers": triggers or ["echo supplied text", "repeat this string"],
        "inputs": {"value": "string"},
        "steps": [{"op": "identity", "args": [{"input": "value"}], "as": "answer"}],
        "result": {"step": "answer"},
        "tests": [
            {"input": {"value": "alpha"}, "expect": "alpha"},
            {"input": {"value": "VORAX"}, "expect": "VORAX"},
        ],
        "provenance": {"kind": "seed"},
    }


class HomeMethodIndexTests(unittest.TestCase):
    def test_index_contains_metadata_and_a_digest_but_never_method_body(self):
        method = sample_method()
        index = build_method_index([method])
        item = index["records"][0]
        expected = hashlib.sha256(dump_record(method).encode("utf-8")).hexdigest()
        self.assertEqual(item["method_sha256"], expected)
        self.assertEqual(
            set(item),
            {"id", "domain", "task", "triggers", "method_sha256"},
        )
        self.assertNotIn("inputs", item)
        self.assertNotIn("steps", item)
        self.assertNotIn("tests", item)
        self.assertNotIn("provenance", item)

    def test_index_parser_rejects_missing_hash_and_executable_fields(self):
        index = build_method_index([sample_method()])
        missing_hash = {
            "version": index["version"],
            "methods_total": 1,
            "records": [{
                key: value for key, value in index["records"][0].items()
                if key != "method_sha256"
            }],
        }
        with self.assertRaisesRegex(ValueError, "metadata only"):
            parse_method_index(__import__("json").dumps(missing_hash))
        executable = __import__("json").loads(__import__("json").dumps(index))
        executable["records"][0]["steps"] = []
        with self.assertRaisesRegex(ValueError, "metadata only"):
            parse_method_index(__import__("json").dumps(executable))

    def test_append_updates_metadata_hash_without_exporting_method_body(self):
        current = build_method_index([sample_method()])
        addition = sample_method(
            method_id="test.echo_upper",
            task="echo text in uppercase",
            triggers=["uppercase supplied text", "convert text to uppercase"],
        )
        updated = append_method(current, addition)
        self.assertEqual(updated["methods_total"], 2)
        self.assertEqual(
            updated["records"][1]["method_sha256"],
            hashlib.sha256(dump_record(addition).encode("utf-8")).hexdigest(),
        )
        self.assertNotIn("steps", updated["records"][1])

    def test_duplicate_detection_uses_metadata_only(self):
        index = build_method_index([sample_method()])
        duplicate = sample_method(
            method_id="test.echo_variant",
            task="another task",
            triggers=["repeat this string"],
        )
        self.assertEqual(find_duplicate(index, duplicate), "method_trigger_exists_in_home")


if __name__ == "__main__":
    unittest.main()
