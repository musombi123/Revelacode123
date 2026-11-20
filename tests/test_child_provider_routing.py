import os
import unittest
from unittest.mock import patch

from vorax import api
from vorax import tools


class LearningConnectorProviderTests(unittest.TestCase):
    def test_kaggle_learning_role_never_selects_a_local_gpu_provider(self):
        expected = {"status": "unknown", "verified": False}
        with patch.dict(os.environ, {"VORAX_CHILD_ROLE": "kaggle-learning"}), \
             patch.object(api, "execute_registered_tool", return_value=expected) as execute:
            result = api.runtime_tool_executor("image.generate", {"prompt": "cat"})
        self.assertEqual(result, expected)
        execute.assert_called_once_with("image.generate", {"prompt": "cat"})

    def test_local_kaggle_gpu_provider_fails_closed(self):
        with patch.object(tools, "execute_tool_dict") as execute:
            with self.assertRaises(tools.ToolError):
                tools.execute_tool(
                    "image.generate",
                    {"prompt": "cat", "width": 512, "height": 512},
                    provider="kaggle-gpu",
                )
        execute.assert_not_called()

    def test_no_diffusers_or_gpu_provider_source_is_loaded_by_learning_connector(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        connector = (root / "kaggle" / "child" / "vorax_child.py").read_text(encoding="utf-8").casefold()
        self.assertNotIn("diffusers", connector)
        self.assertNotIn("snapshot_download", connector)
        self.assertNotIn("nvidia-smi", connector)
        self.assertNotIn("requirements-gpu.txt", connector)
        self.assertNotIn("queue_issues(", connector)
        self.assertNotIn("vault.upload", connector)


if __name__ == "__main__":
    unittest.main()
