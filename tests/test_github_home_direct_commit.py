import base64
import json
import unittest
from unittest.mock import patch

from vorax import github_home
from vorax.home_index import build_method_index


class GitHubHomeDirectCommitTests(unittest.TestCase):
    def _candidate(self):
        return {
            "version": "vlang/1",
            "id": "learned.direct_home_test",
            "domain": "text",
            "task": "return supplied text unchanged",
            "triggers": ["return text unchanged", "identity text procedure"],
            "inputs": {"value": "string"},
            "steps": [
                {
                    "op": "identity",
                    "args": [{"input": "value"}],
                    "as": "answer",
                }
            ],
            "result": {"step": "answer"},
            "tests": [{"input": {"value": "abc"}, "expect": "abc"}],
            "provenance": {"kind": "teacher", "teacher": "test"},
        }

    def test_verified_candidate_is_written_with_index_in_one_home_commit(self):
        empty_index = build_method_index([])
        index_bytes = json.dumps(empty_index).encode("utf-8")
        calls = []
        commit_sha = "a" * 40

        def fake_api(method, path, token=None, body=None):
            calls.append((method, path, body))
            if method == "GET" and path.endswith("/git/ref/heads/main"):
                return {"object": {"sha": "b" * 40}}
            if method == "GET" and path.endswith("/git/commits/" + "b" * 40):
                return {"tree": {"sha": "c" * 40}}
            if method == "GET" and "/contents/templates/library/index.json?" in path:
                return {
                    "encoding": "base64",
                    "content": base64.b64encode(index_bytes).decode("ascii"),
                }
            if method == "GET" and "/contents/templates/library/children/" in path:
                raise github_home.GitHubHomeError("GitHub API GET failed (404): Not Found")
            if method == "POST" and path.endswith("/git/trees"):
                self.assertEqual(body["base_tree"], "c" * 40)
                self.assertEqual(
                    {item["path"] for item in body["tree"]},
                    {
                        "templates/library/index.json",
                        "templates/library/children/kaggle-child_learned.direct_home_test.vlang",
                    },
                )
                return {"sha": "d" * 40}
            if method == "POST" and path.endswith("/git/commits"):
                self.assertEqual(body["tree"], "d" * 40)
                self.assertEqual(body["parents"], ["b" * 40])
                return {
                    "sha": commit_sha,
                    "html_url": "https://github.com/musombi123/RevelaCode123/commit/" + commit_sha,
                }
            if method == "PATCH" and path.endswith("/git/refs/heads/main"):
                self.assertEqual(body, {"sha": commit_sha, "force": False})
                return {"object": {"sha": commit_sha}}
            raise AssertionError(f"Unexpected GitHub API call: {method} {path}")

        with patch.object(github_home, "_api", side_effect=fake_api):
            result = github_home.submit_template(
                "musombi123/RevelaCode123",
                "main",
                "kaggle-child",
                self._candidate(),
                "write-token",
            )

        self.assertEqual(result["commit_sha"], commit_sha)
        self.assertEqual(result["home_authority"], "committed_to_home_main")
        self.assertEqual(result["branch"], "main")
        self.assertFalse(any(path.endswith("/pulls") for _, path, _ in calls))
        tree_calls = [body for method, path, body in calls if method == "POST" and path.endswith("/git/trees")]
        self.assertEqual(len(tree_calls), 1)
        written_index = next(
            item["content"] for item in tree_calls[0]["tree"]
            if item["path"] == "templates/library/index.json"
        )
        indexed = json.loads(written_index)
        self.assertEqual(indexed["methods_total"], 1)
        self.assertEqual(indexed["records"][0]["id"], "learned.direct_home_test")

    def test_duplicate_candidate_is_rejected_before_any_home_write(self):
        candidate = self._candidate()
        existing_index = build_method_index([candidate])
        index_bytes = json.dumps(existing_index).encode("utf-8")
        writes = []

        def fake_api(method, path, token=None, body=None):
            if method == "GET" and path.endswith("/git/ref/heads/main"):
                return {"object": {"sha": "b" * 40}}
            if method == "GET" and path.endswith("/git/commits/" + "b" * 40):
                return {"tree": {"sha": "c" * 40}}
            if method == "GET" and "/contents/templates/library/index.json?" in path:
                return {
                    "encoding": "base64",
                    "content": base64.b64encode(index_bytes).decode("ascii"),
                }
            if method in {"POST", "PATCH", "PUT"}:
                writes.append((method, path))
                raise AssertionError("Duplicate candidate must not write to Home")
            raise AssertionError(f"Unexpected GitHub API call: {method} {path}")

        with patch.object(github_home, "_api", side_effect=fake_api):
            with self.assertRaises(github_home.GitHubHomeError):
                github_home.submit_template(
                    "musombi123/Revelacode123",
                    "main",
                    "kaggle-child",
                    candidate,
                    "write-token",
                )
        self.assertEqual(writes, [])


if __name__ == "__main__":
    unittest.main()
