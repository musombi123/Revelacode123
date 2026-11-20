import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[1]
CONNECTOR_PATH = ROOT / "kaggle" / "child" / "vorax_child.py"
SPEC = importlib.util.spec_from_file_location("vorax_kaggle_learning_connector", CONNECTOR_PATH)
connector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(connector)


class KaggleLearningConnectorTests(unittest.TestCase):
    def test_teacher_panel_selects_distinct_live_conversational_providers(self):
        models = [
            {
                "id": "acme/alpha",
                "providers": [
                    {"provider": "provider-a", "status": "live", "task": "conversational", "throughput": 25, "is_free": True},
                    {"provider": "provider-b", "status": "live", "task": "conversational", "throughput": 12, "is_free": True},
                    {"provider": "provider-c", "status": "staging", "task": "conversational", "throughput": 90},
                ],
            },
            {
                "id": "acme/beta",
                "providers": [
                    {"provider": "provider-b", "status": "live", "task": "text-generation", "throughput": 17, "is_free": True},
                ],
            },
            {
                "id": "acme/gamma",
                "providers": [
                    {"provider": "provider-c", "status": "live", "task": "conversational", "throughput": 10, "is_free": True},
                ],
            },
            {
                "id": "acme/vision-only",
                "providers": [
                    {"provider": "vision-provider", "status": "live", "task": "image-to-text"},
                ],
            },
        ]
        selected = connector.choose_teacher_models(models, count=3)
        self.assertEqual(
            selected,
            ["acme/alpha:provider-a", "acme/beta:provider-b", "acme/gamma:provider-c"],
        )
        self.assertEqual(len(selected), len(set(selected)))

    def test_requested_teacher_model_is_first_in_panel(self):
        models = [
            {"id": "acme/alpha", "providers": [{"provider": "p1", "status": "live", "task": "conversational", "is_free": True}]},
            {"id": "acme/beta", "providers": [{"provider": "p2", "status": "live", "task": "conversational", "is_free": True}]},
        ]
        selected = connector.choose_teacher_models(models, requested="acme/beta:p2", count=2)
        self.assertEqual(selected[0], "acme/beta:p2")

    def test_paid_provider_is_skipped_by_default(self):
        models = [
            {
                "id": "acme/paid",
                "providers": [{
                    "provider": "paid-a",
                    "status": "live",
                    "task": "conversational",
                    "is_free": False,
                    "throughput": 100,
                }],
            },
            {
                "id": "acme/free",
                "providers": [{
                    "provider": "free-b",
                    "status": "live",
                    "task": "conversational",
                    "is_free": True,
                    "throughput": 10,
                }],
            },
        ]
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(
                connector.choose_teacher_models(models, count=2),
                ["acme/free:free-b"],
            )

    def test_paid_provider_is_available_only_after_explicit_opt_in(self):
        models = [{
            "id": "acme/paid",
            "providers": [{
                "provider": "paid-a",
                "status": "live",
                "task": "conversational",
                "is_free": False,
            }],
        }]
        with patch.dict("os.environ", {"VORAX_ALLOW_BILLABLE_INFERENCE": "true"}):
            self.assertEqual(
                connector.choose_teacher_models(models, count=1),
                ["acme/paid:paid-a"],
            )

    def test_no_live_chat_provider_fails_closed(self):
        models = [{
            "id": "acme/staging",
            "providers": [{"provider": "p1", "status": "staging", "task": "conversational"}],
        }]
        with self.assertRaises(connector.ConnectorError):
            connector.choose_teacher_models(models, count=1)

    def test_discovery_pages_models_spaces_and_datasets_separately(self):
        responses = [
            (200, {
                "status": "metadata_only",
                "models": [{"id": "acme/model", "providers": []}],
                "resources": [{"id": "acme/model", "source_type": "model"}],
                "next_cursors": {"models": "m-next", "spaces": "s-next", "datasets": "d-next"},
                "weights_downloaded": False,
            }),
            (200, {
                "status": "metadata_only", "models": [{"id": "acme/second"}],
                "resources": [{"id": "acme/second"}],
                "next_cursors": {"models": None}, "weights_downloaded": False,
            }),
            (200, {
                "status": "metadata_only", "resources": [{"id": "acme/space"}],
                "next_cursors": {"spaces": None}, "weights_downloaded": False,
            }),
            (200, {
                "status": "metadata_only", "resources": [{"id": "acme/dataset"}],
                "next_cursors": {"datasets": None}, "weights_downloaded": False,
            }),
        ]
        with patch.object(connector, "request_json", side_effect=responses) as request:
            result = connector.discover_world("capability", page_limit=10)
        self.assertEqual(result["pages_by_source"], {"models": 2, "spaces": 2, "datasets": 2})
        self.assertEqual(result["models_found"], 2)
        self.assertEqual(result["resources_found"], 4)
        self.assertFalse(result["pagination_remaining"])
        self.assertFalse(result["weights_downloaded"])
        requested_sources = [
            parse_qs(urlparse(call.args[1]).query).get("source", ["all"])[0]
            for call in request.call_args_list
        ]
        self.assertEqual(requested_sources, ["all", "models", "spaces", "datasets"])

    def test_discovery_stops_before_learning_if_any_catalogue_is_unfinished(self):
        responses = [
            (200, {
                "status": "metadata_only",
                "models": [{"id": "acme/model", "providers": []}],
                "resources": [],
                "next_cursors": {"models": "m-next", "spaces": None, "datasets": None},
                "weights_downloaded": False,
            }),
            (200, {
                "status": "metadata_only",
                "models": [{"id": "acme/second"}],
                "resources": [],
                "next_cursors": {"models": "m-next-2"},
                "weights_downloaded": False,
            }),
        ]
        with patch.object(connector, "request_json", side_effect=responses):
            result = connector.discover_world("capability", page_limit=2)
        self.assertTrue(result["pagination_remaining"])
        self.assertTrue(result["pagination_remaining_by_source"]["models"])


if __name__ == "__main__":
    unittest.main()
