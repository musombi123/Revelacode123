import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request
import vorax.api

from vorax.input_x import classify_input_x, handle_input_x
from vorax.vlang import parse_document, validate_template, TemplateError, dump_record

ROOT = Path(__file__).resolve().parents[1]
HOME_VLANG = parse_document((ROOT / "templates" / "library" / "seed.vlang").read_text(encoding="utf-8"))


class InputXTests(unittest.TestCase):
    def test_one_input_classifies_the_eight_requested_examples(self):
        examples = {
            "hello, who are you": "greeting",
            "generate music": "music",
            "generate image of a cat": "image",
            "generate video of a triangle": "video",
            "make me a logo": "logo",
            "make 3D": "3d",
            "write code": "code",
            "give me instructions": "instructions",
        }
        for value, expected in examples.items():
            with self.subTest(value=value):
                self.assertEqual(classify_input_x(value, HOME_VLANG)["task_type"], expected)

    def test_identity_is_verified_without_loading_home_method_bodies(self):
        from vorax.builtins import handle_builtin_input
        result = handle_builtin_input("hello, who are you")
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["method"], "runtime.identity")
        self.assertIn("I am VORAX", result["answer"])
        self.assertIs(result["verification"]["home_method_body_loaded"], False)

    def test_unknown_runtime_input_is_not_resolved_from_home_method_bodies(self):
        from vorax.builtins import handle_builtin_input
        result = handle_builtin_input("build a complex irrigation controller")
        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result["answer"])

    def test_single_input_http_handler_uses_vorax_route_without_home_sync(self):
        routes = {route.path: route.methods for route in vorax.api.app.routes}
        self.assertIn("/vorax/x", routes)
        self.assertIn("POST", routes["/vorax/x"])
        scope = {
            "type": "http", "http_version": "1.1", "method": "POST",
            "scheme": "http", "path": "/vorax/x", "raw_path": b"/vorax/x",
            "query_string": b"", "headers": [],
            "client": ("testclient", 12345), "server": ("testserver", 80),
        }
        with patch.object(vorax.api, "actor", return_value="kaggle-child"), \
             patch.object(vorax.api, "_home_metadata_index", return_value=None):
            result = vorax.api.vorax_input_x({"x": "hello, who are you"}, Request(scope))
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["engine"], "VORAX")
        self.assertEqual(result["method"], "runtime.identity")
        self.assertIs(result["verification"]["home_method_bodies_loaded"], False)

    def test_vorax_input_x_requires_registered_child_authentication(self):
        scope = {
            "type": "http", "http_version": "1.1", "method": "POST",
            "scheme": "http", "path": "/vorax/x", "raw_path": b"/vorax/x",
            "query_string": b"", "headers": [],
            "client": ("testclient", 12345), "server": ("testserver", 80),
        }
        with patch.object(vorax.api, "CHILD_ID", ""), \
             patch.object(vorax.api, "CHILD_KEY", ""), \
             patch.object(vorax.api, "ADMIN_KEY", ""):
            with self.assertRaises(HTTPException) as error:
                vorax.api.vorax_input_x({"x": "hello, who are you"}, Request(scope))
        self.assertEqual(error.exception.status_code, 403)

    def test_unknown_call_tool_name_is_rejected(self):
        candidate = {
            "version": "vlang/1", "id": "unsafe.tool", "domain": "test",
            "task": "unsafe tool call", "triggers": ["unsafe tool"],
            "inputs": {"prompt": "string"},
            "steps": [{"op": "call_tool", "tool": "shell.exec",
                       "args": {"command": {"input": "prompt"}}, "as": "result"}],
            "result": {"step": "result"},
            "tests": [{"input": {"prompt": "echo nope"}, "expect": {}},
                      {"input": {"prompt": "whoami"}, "expect": {}}],
            "provenance": {"kind": "manual"},
        }
        with self.assertRaises(TemplateError):
            validate_template(candidate)


if __name__ == "__main__":
    unittest.main()
