import unittest
from pathlib import Path

from legend_core.input_x import classify_input_x, handle_input_x
from legend_core.tlang import parse_document, validate_template, TemplateError, dump_record

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

    def test_identity_is_verified_from_home_without_a_teacher_or_tool(self):
        result = handle_input_x("hello, who are you", HOME_VLANG)
        self.assertEqual(result["status"], "verified")
        self.assertIn("I am VORAX", result["answer"])
        self.assertNotIn("model", result)

    def test_media_requests_use_the_home_call_tool_method(self):
        calls = []
        def verified_tool(name, arguments):
            calls.append((name, arguments))
            return {
                "status": "verified", "verified": True, "tool": name,
                "artifact": "test-only-artifact", "mime_type": "application/x-test",
            }
        examples = [
            ("generate music", "music.generate", "music.generate"),
            ("generate image of a cat", "image.generate", "image.generate"),
            ("generate video of a triangle", "video.generate", "video.generate"),
            ("make me a logo", "image.generate", "logo.generate"),
        ]
        for value, expected_tool, expected_template in examples:
            with self.subTest(value=value):
                result = handle_input_x(value, HOME_VLANG, tool_executor=verified_tool)
                self.assertEqual(result["status"], "verified")
                self.assertEqual(result["tool"], expected_tool)
                self.assertEqual(result["template_id"], expected_template)
        self.assertEqual(len(calls), 4)

    def test_audio_and_translation_route_through_registered_tools(self):
        calls = []
        def verified_tool(name, arguments):
            calls.append(name)
            return {"status": "verified", "verified": True, "tool": name}
        speech = handle_input_x("make a voice note", HOME_VLANG, tool_executor=verified_tool)
        translation = handle_input_x("translate this to Swahili", HOME_VLANG, tool_executor=verified_tool)
        self.assertEqual(speech["status"], "verified")
        self.assertEqual(translation["status"], "verified")
        self.assertEqual(calls, ["audio.tts", "translate"])

    def test_unverified_tool_result_becomes_unknown(self):
        def failed_tool(name, arguments):
            return {"status": "unknown", "verified": False, "reason": "provider unavailable"}
        result = handle_input_x("generate music", HOME_VLANG, tool_executor=failed_tool)
        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result["answer"])

    def test_general_code_generation_is_unknown_but_arithmetic_is_bounded_code(self):
        generation = classify_input_x("write code", HOME_VLANG)
        arithmetic = classify_input_x("8 * 7", HOME_VLANG)
        self.assertEqual(generation["task_type"], "code")
        self.assertIsNone(generation["tool"])
        self.assertEqual(arithmetic["tool"], "code.execute")
        self.assertEqual(arithmetic["task_type"], "code")
        result = handle_input_x("write code", HOME_VLANG)
        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result["answer"])

    def test_unimplemented_3d_and_instruction_tasks_remain_unknown(self):
        for value in ("make a 3D environment", "give me instructions to repair a pump"):
            result = handle_input_x(value, HOME_VLANG)
            self.assertEqual(result["status"], "unknown")
            self.assertIsNone(result["answer"])

    def test_vlang_round_trip_writes_vlang_version(self):
        record = dict(HOME_VLANG[0])
        encoded = dump_record(record)
        self.assertIn('"version":"vlang/1"', encoded)
        self.assertEqual(parse_document(encoded)[0]["id"], "greeting.identity")

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
