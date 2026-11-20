import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

import vorax.api


def scope():
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/tools/execute",
        "raw_path": b"/v1/tools/execute",
        "query_string": b"",
        "headers": [],
        "client": ("testclient", 12345),
        "server": ("testserver", 80),
    })


class ChildProviderRoutingTests(unittest.TestCase):
    def test_kaggle_role_forces_local_provider_for_media_tools(self):
        expected = {"status": "verified", "verified": True, "provider": "kaggle-local-diffusers"}
        with patch.dict(os.environ, {"VORAX_CHILD_ROLE": "kaggle"}), \
             patch.object(vorax.api, "execute_registered_tool", return_value=expected) as execute:
            result = vorax.api.runtime_tool_executor("image.generate", {"prompt": "cat"})
        self.assertEqual(result, expected)
        execute.assert_called_once_with("image.generate", {"prompt": "cat"}, provider="kaggle-gpu")

    def test_non_kaggle_child_does_not_claim_local_gpu(self):
        expected = {"status": "unknown", "verified": False}
        with patch.dict(os.environ, {"VORAX_CHILD_ROLE": "koyeb"}), \
             patch.object(vorax.api, "execute_registered_tool", return_value=expected) as execute:
            result = vorax.api.runtime_tool_executor("image.generate", {"prompt": "cat"})
        self.assertEqual(result, expected)
        execute.assert_called_once_with("image.generate", {"prompt": "cat"})

    def test_kaggle_endpoint_ignores_remote_provider_override(self):
        expected = {"status": "verified", "verified": True, "provider": "kaggle-local-diffusers"}
        with patch.dict(os.environ, {"VORAX_CHILD_ROLE": "kaggle"}), \
             patch.object(vorax.api, "actor", return_value="kaggle-child"), \
             patch.object(vorax.api, "execute_registered_tool", return_value=expected) as execute:
            result = vorax.api.tool_execute(
                {"tool": "image.generate", "inputs": {"prompt": "cat"}, "provider": "huggingface"},
                scope(),
            )
        self.assertEqual(result, expected)
        execute.assert_called_once_with("image.generate", {"prompt": "cat"}, provider="kaggle-gpu")


    def test_koyeb_cannot_execute_media_tool_directly(self):
        with patch.dict(os.environ, {"VORAX_CHILD_ROLE": "koyeb"}), \
             patch.object(vorax.api, "actor", return_value="koyeb-child"), \
             patch.object(vorax.api, "execute_registered_tool") as execute:
            with self.assertRaises(HTTPException) as caught:
                vorax.api.tool_execute(
                    {"tool": "image.generate", "inputs": {"prompt": "cat"}},
                    scope(),
                )
        self.assertEqual(caught.exception.status_code, 409)
        execute.assert_not_called()

    def test_non_kaggle_endpoint_cannot_request_kaggle_provider(self):
        with patch.dict(os.environ, {"VORAX_CHILD_ROLE": "koyeb"}), \
             patch.object(vorax.api, "actor", return_value="koyeb-child"), \
             patch.object(vorax.api, "execute_registered_tool") as execute:
            with self.assertRaises(HTTPException) as caught:
                vorax.api.tool_execute(
                    {"tool": "image.generate", "inputs": {"prompt": "cat"}, "provider": "kaggle-gpu"},
                    scope(),
                )
        self.assertEqual(caught.exception.status_code, 403)
        execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
