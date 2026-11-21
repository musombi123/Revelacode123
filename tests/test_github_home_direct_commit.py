import base64
import json
import unittest
from unittest.mock import patch

from vorax import github_home
from vorax.home_index import build_method_index


class GitHubHomePullRequestTests(unittest.TestCase):
    def _candidate(self):
        return {
            "version": "vlang/1",
            "id": "learned.pull_request_test",
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
            "tests": [
                {"input": {"value": "abc"}, "expect": "abc"},
                {"input": {"value": "vorax"}, "expect": "vorax"},
            ],
            "provenance": {"kind": "teacher", "teacher": "test"},
        }

    def test_verified_candidate_is_submitted_on_isolated_branch_as_home_pr(self):
        empty_index = build_method_index([])
        index_bytes = json.dumps(empty_index).encode("utf-8")
        calls = []
        proposal_sha = "a" * 40
        branch_names = []

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
            if method == "POST" and path.endswith("/git/refs"):
                branch_names.append(body["ref"].removeprefix("refs/heads/"))
                self.assertEqual(body["sha"], "b" * 40)
                return {"ref": body["ref"], "object": {"sha": "b" * 40}}
            if method == "POST" and path.endswith("/git/trees"):
                self.assertEqual(body["base_tree"], "c" * 40)
                self.assertEqual(
                    {item["path"] for item in body["tree"]},
                    {
                        "templates/library/index.json",
                        "templates/library/children/kaggle-child_learned_pull_request_test.vlang",
                    },
                )
                written_index = next(
                    item["content"] for item in body["tree"]
                    if item["path"] == "templates/library/index.json"
                )
                indexed = json.loads(written_index)
                self.assertEqual(indexed["methods_total"], 1)
                self.assertEqual(indexed["records"][0]["id"], "learned.pull_request_test")
                return {"sha": "d" * 40}
            if method == "POST" and path.endswith("/git/commits"):
                self.assertEqual(body["tree"], "d" * 40)
                self.assertEqual(body["parents"], ["b" * 40])
                return {"sha": proposal_sha}
            if method == "PATCH" and "/git/refs/heads/" in path:
                self.assertNotEqual(path, "/repos/musombi123/RevelaCode123/git/refs/heads/main")
                self.assertEqual(body, {"sha": proposal_sha, "force": False})
                return {"object": {"sha": proposal_sha}}
            if method == "POST" and path.endswith("/pulls"):
                self.assertEqual(body["base"], "main")
                self.assertEqual(body["head"], branch_names[0])
                return {
                    "number": 123,
                    "html_url": "https://github.com/musombi123/RevelaCode123/pull/123",
                }
            raise AssertionError(f"Unexpected GitHub API call: {method} {path}")

        with patch.object(github_home, "_api", side_effect=fake_api):
            result = github_home.create_home_pull_request(
                "musombi123/RevelaCode123",
                "main",
                "kaggle-child",
                self._candidate(),
                "write-token",
            )

        self.assertEqual(result["pr_number"], 123)
        self.assertEqual(result["pr_url"], "https://github.com/musombi123/RevelaCode123/pull/123")
        self.assertEqual(result["home_authority"], "pull_request_review_and_merge_required")
        self.assertTrue(result["branch"].startswith("vorax/proposals/kaggle-child-"))
        self.assertFalse(any(
            method == "PATCH" and path.endswith("/git/refs/heads/main")
            for method, path, _ in calls
        ))
        self.assertTrue(any(method == "POST" and path.endswith("/pulls") for method, path, _ in calls))

    def test_home_pr_must_be_merged_into_the_configured_base(self):
        merged_pr = {
            "number": 42,
            "state": "closed",
            "merged": True,
            "merge_commit_sha": "a" * 40,
            "base": {
                "ref": "main",
                "repo": {"full_name": "musombi123/Revelacode123"},
            },
            "head": {
                "ref": "vorax/proposals/kaggle-child-method",
                "repo": {"full_name": "musombi123/Revelacode123"},
            },
        }
        with patch.object(github_home, "_api", return_value=merged_pr) as api:
            result = github_home.is_merged_home_pull_request(
                "musombi123/Revelacode123",
                "https://github.com/musombi123/Revelacode123/pull/42",
                "main",
                "read-token",
            )
        self.assertIs(result, True)
        api.assert_called_once_with(
            "GET",
            "/repos/musombi123/Revelacode123/pulls/42",
            "read-token",
        )

    def test_home_pr_verification_rejects_unmerged_or_wrong_base_pr(self):
        base = {
            "number": 42,
            "state": "closed",
            "merged": False,
            "merge_commit_sha": None,
            "base": {
                "ref": "main",
                "repo": {"full_name": "musombi123/Revelacode123"},
            },
            "head": {
                "ref": "vorax/proposals/kaggle-child-method",
                "repo": {"full_name": "musombi123/Revelacode123"},
            },
        }
        with patch.object(github_home, "_api", return_value=base):
            result = github_home.is_merged_home_pull_request(
                "musombi123/Revelacode123",
                "https://github.com/musombi123/Revelacode123/pull/42",
                "main",
                "read-token",
            )
        self.assertIs(result, False)

        with self.assertRaises(github_home.GitHubHomeError):
            github_home.is_merged_home_pull_request(
                "musombi123/Revelacode123",
                "https://example.com/musombi123/Revelacode123/pull/42",
                "main",
                "read-token",
            )

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
                github_home.create_home_pull_request(
                    "musombi123/Revelacode123",
                    "main",
                    "kaggle-child",
                    candidate,
                    "write-token",
                )
        self.assertEqual(writes, [])


if __name__ == "__main__":
    unittest.main()
