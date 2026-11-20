import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

import vorax.api
from vorax import github_home


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


class FreeChildRoutingTests(unittest.TestCase):
    def test_koyeb_routes_heavy_x_to_kaggle_queue_without_running_provider(self):
        with patch.dict(os.environ, {"VORAX_CHILD_ROLE": "koyeb"}), \
             patch.object(vorax.api, "classify_input_x",
                          return_value={"task_type": "image", "template_id": "image.generate",
                                        "tool": "image.generate", "classifier_template": "input.classifier"}), \
             patch.object(vorax.api, "actor", return_value="koyeb-child"), \
             patch.object(vorax.api, "enqueue_kaggle_x",
                          return_value={"status": "queued", "number": 77, "verification": "not_executed"}), \
             patch.object(vorax.api, "handle_input_x") as execute, \
             patch.object(vorax.api, "_refresh_home_catalog") as refresh:
            result = vorax.api.vorax_input_x({"x": "generate an image of a lighthouse"}, request_scope())

        self.assertEqual(result["status"], "queued")
        self.assertEqual(result["destination_child"], "kaggle-child")
        self.assertEqual(result["verification"], "not_executed")
        execute.assert_not_called()
        refresh.assert_not_called()

    def test_koyeb_keeps_light_text_on_the_shared_runtime(self):
        verified = {"status": "verified", "answer": "I am VORAX", "template_id": "greeting.identity"}
        with patch.dict(os.environ, {"VORAX_CHILD_ROLE": "koyeb"}), \
             patch.object(vorax.api, "classify_input_x",
                          return_value={"task_type": "greeting", "template_id": "greeting.identity",
                                        "tool": None, "classifier_template": "input.classifier"}), \
             patch.object(vorax.api, "_refresh_home_catalog", return_value={"status": "synced"}), \
             patch.object(vorax.api, "handle_input_x", return_value=verified):
            result = vorax.api.vorax_input_x({"x": "hello, who are you"}, request_scope())

        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["engine"], "VORAX")
        self.assertEqual(result["home_sync"]["status"], "synced")

    def test_koyeb_heavy_route_requires_child_authentication(self):
        with patch.dict(os.environ, {"VORAX_CHILD_ROLE": "koyeb"}), \
             patch.object(vorax.api, "classify_input_x",
                          return_value={"task_type": "video", "template_id": "video.generate",
                                        "tool": "video.generate", "classifier_template": "input.classifier"}), \
             patch.object(vorax.api, "actor", side_effect=HTTPException(403, "missing child key")), \
             patch.object(vorax.api, "enqueue_kaggle_x") as queue:
            with self.assertRaises(HTTPException) as error:
                vorax.api.vorax_input_x({"x": "make a video"}, request_scope())

        self.assertEqual(error.exception.status_code, 403)
        queue.assert_not_called()

    def test_queue_helper_records_x_as_not_executed(self):
        with patch.object(github_home, "_api", return_value={
            "number": 77, "html_url": "https://github.com/musombi123/Revelacode123/issues/77"
        }) as api:
            result = github_home.enqueue_kaggle_x(
                "musombi123/Revelacode123", "generate a small image", "test-write-token"
            )

        self.assertEqual(result["status"], "queued")
        self.assertEqual(result["verification"], "not_executed")
        body = api.call_args.args[3]
        self.assertIn("VORAX_X_QUEUE_V1", body["body"])
        self.assertIn('"status": "queued"', body["body"])

    def test_queue_helper_rejects_credential_like_input(self):
        with patch.object(github_home, "_api") as api:
            with self.assertRaises(ValueError):
                github_home.enqueue_kaggle_x(
                    "musombi123/Revelacode123", "use api_key=do-not-store", "test-write-token"
                )
        api.assert_not_called()

    def test_vault_default_is_case_sensitive_musombi_dataset(self):
        from vorax import vault

        with patch.dict(os.environ, {"HF_VAULT_TOKEN": "test-token"}, clear=True), \
             patch("huggingface_hub.HfApi") as api_class:
            api = api_class.return_value
            _, repo = vault._client()

        self.assertEqual(repo, "Musombi/revelacode-vorax-vault")
        api.create_repo.assert_called_once_with(
            repo_id="Musombi/revelacode-vorax-vault",
            repo_type="dataset",
            private=True,
            exist_ok=True,
        )


if __name__ == "__main__":
    unittest.main()
