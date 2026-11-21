import importlib.util
import unittest
from pathlib import Path

CONNECTOR_PATH = Path(__file__).resolve().parents[1] / "kaggle" / "child" / "vorax_child.py"
SPEC = importlib.util.spec_from_file_location("vorax_child_test_module", CONNECTOR_PATH)
CONNECTOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONNECTOR)


class KaggleWeeklyHunterTests(unittest.TestCase):
    def test_capability_assessment_states_unknown_inputs_and_outputs(self):
        assessments, understood = CONNECTOR.assess_discovered_capabilities([
            {
                "id": "org/text-model",
                "source_type": "model",
                "tags": ["text-generation", "transformers"],
                "description": "A text generation model",
                "pipeline_tag": "text-generation",
                "library_name": "transformers",
                "sdk": "gradio",
                "providers": [{"provider": "provider-a"}],
            }
        ])
        self.assertEqual(understood, 1)
        self.assertEqual(assessments[0]["inputs"], "not_declared_by_public_metadata")
        self.assertEqual(assessments[0]["outputs"], "text-generation")
        self.assertFalse(assessments[0]["execution_verified"])
        self.assertIn("metadata_only", assessments[0]["limitations"])

    def test_metadata_without_task_and_requirements_is_not_understood(self):
        assessments, understood = CONNECTOR.assess_discovered_capabilities([
            {
                "id": "org/unknown",
                "source_type": "dataset",
                "tags": [],
                "description": "",
                "pipeline_tag": None,
                "library_name": None,
                "sdk": None,
                "providers": [],
            }
        ])
        self.assertEqual(understood, 0)
        self.assertEqual(assessments[0]["inputs"], "not_declared_by_public_metadata")
        self.assertFalse(assessments[0]["metadata_understood"])

    def test_duplicate_resources_are_counted_once(self):
        item = {
            "id": "org/resource",
            "source_type": "space",
            "tags": ["vision"],
            "description": "A vision Space",
            "pipeline_tag": "image-to-text",
            "library_name": "transformers",
            "sdk": "gradio",
            "providers": [],
        }
        assessments, understood = CONNECTOR.assess_discovered_capabilities([item, dict(item)])
        self.assertEqual(len(assessments), 1)
        self.assertEqual(understood, 1)

    def test_weekly_limits_and_slice_configuration_are_explicit(self):
        self.assertEqual(CONNECTOR.MAX_HUNTER_SECONDS, 7200)
        self.assertEqual(set(CONNECTOR.SLICE_QUERIES), {"text-docs", "vision-audio", "video-tools"})
        self.assertTrue(all("VORAX_X" not in query for query in CONNECTOR.SLICE_QUERIES.values()))


if __name__ == "__main__":
    unittest.main()
