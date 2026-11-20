import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

import vorax.api
from vorax import github_home, vault


def request_scope():
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/vorax/x",
        "raw_path": b"/vorax/x",
        "query_string": b"",
        "headers": [],
        "client": ("testclient", 12345),
        "server": ("testserver", 80),
    })


class AwayChildPolicyTests(unittest.TestCase):
    def test_runtime_tool_executor_never_selects_a_local_kaggle_gpu(self):
        expected = {"status": "unknown", "verified": False}
        with patch.dict(os.environ, {"VORAX_CHILD_ROLE": "kaggle-learning"}), \
             patch.object(vorax.api, "execute_registered_tool", return_value=expected) as execute:
            result = vorax.api.runtime_tool_executor("image.generate", {"prompt": "cat"})
        self.assertEqual(result, expected)
        execute.assert_called_once_with("image.generate", {"prompt": "cat"})

    def test_shared_api_does_not_read_home_method_bodies(self):
        with patch.object(vorax.api, "actor", return_value="kaggle-child"), \
             patch.object(vorax.api, "_home_metadata_index", return_value={
                 "version": "vorax-home-method-index/v1",
                 "methods_total": 30,
                 "records": [],
             }):
            result = vorax.api.vorax_input_x({"x": "hello, who are you"}, request_scope())
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["method"], "runtime.identity")
        self.assertNotIn("templates", result)
        self.assertIs(result["verification"]["home_method_bodies_loaded"], False)

    def test_shared_api_unknown_x_is_not_auto_queued_or_self_learned(self):
        with patch.object(vorax.api, "actor", return_value="kaggle-child"), \
             patch.object(vorax.api, "_home_metadata_index", return_value=None):
            result = vorax.api.vorax_input_x({"x": "build a complex irrigation controller"}, request_scope())
        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result["artifact_sha256"])
        self.assertNotIn("queue", result)
        self.assertNotIn("learning", result)

    def test_metadata_fetch_reads_only_the_home_index_endpoint(self):
        with patch.object(github_home, "_api", return_value={
            "encoding": "base64",
            "content": __import__("base64").b64encode(
                __import__("json").dumps({
                    "version": "vorax-home-method-index/v1",
                    "methods_total": 0,
                    "records": [],
                }).encode()
            ).decode(),
        }) as call:
            index = github_home.fetch_method_index("musombi123/Revelacode123", "main", "read-token")
        self.assertEqual(index["methods_total"], 0)
        self.assertIn("/contents/templates/library/index.json", call.call_args.args[1])
        self.assertNotIn(".vlang", call.call_args.args[1])
        self.assertNotIn(".tlang", call.call_args.args[1])

    def test_vault_client_opens_existing_private_dataset_without_creating_or_converting(self):
        fake_api = SimpleNamespace(
            repo_info=lambda **kwargs: SimpleNamespace(private=True),
            list_repo_files=lambda **kwargs: [
                "vlang/index.json",
                "vlang/pending/index.json",
                "manifest/checksums.json",
            ],
        )
        with patch.dict(os.environ, {
            "HF_VAULT_TOKEN": "dataset-token",
            "HF_VAULT_REPO": "Musombi/revelacode-vorax-vault",
        }), patch("huggingface_hub.HfApi", return_value=fake_api) as api_class:
            api, repo = vault._client()
        self.assertIs(api, fake_api)
        self.assertEqual(repo, "Musombi/revelacode-vorax-vault")
        api_class.assert_called_once()
        self.assertFalse(hasattr(fake_api, "create_repo"))

    def test_vault_refuses_public_dataset(self):
        fake_api = SimpleNamespace(
            repo_info=lambda **kwargs: SimpleNamespace(private=False),
            list_repo_files=lambda **kwargs: [],
        )
        with patch.dict(os.environ, {"HF_VAULT_TOKEN": "dataset-token"}, clear=True), \
             patch("huggingface_hub.HfApi", return_value=fake_api):
            with self.assertRaisesRegex(vault.VaultError, "is public"):
                vault._client()

    def test_kaggle_worker_source_has_no_local_gpu_or_queue_execution(self):
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] / "kaggle" / "child" / "vorax_child.py").read_text(
            encoding="utf-8"
        ).casefold()
        for forbidden in (
            "diffusers",
            "requirements-gpu.txt",
            "nvidia-smi",
            "queue_issues(",
            "vault_sync",
            "pip install",
            "git clone",
            "hf_model_token",
            "hf_inference_token",
            "snapshot_download",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
