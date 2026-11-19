from __future__ import annotations

import json
import unittest
from pathlib import Path

from legend.child import sign_template, verify_signature
from legend.home import HomeGate
from legend.tlang import Composer, TLangVerifier, find_template


ROOT = Path(__file__).resolve().parents[1]
SEEDS = json.loads((ROOT / "library" / "seeds.json").read_text(encoding="utf-8"))


class LegendLibraryTests(unittest.TestCase):
    def test_all_seed_templates_pass_verification(self):
        verifier = TLangVerifier()
        for template in SEEDS:
            with self.subTest(template=template["id"]):
                known = [x for x in SEEDS if x["id"] != template["id"]]
                self.assertTrue(verifier.verify(template, known).passed)

    def test_seed_library_contains_twenty_templates(self):
        self.assertEqual(len(SEEDS), 20)

    def test_composer_executes_known_method(self):
        template = find_template(SEEDS, "math.add")
        result = Composer().run(template, {"values": [4, 8, 12]})
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["result"], 24)

    def test_unknown_is_not_guessed(self):
        self.assertIsNone(find_template(SEEDS, "biology.open_question"))

    def test_unsupported_operation_is_rejected(self):
        candidate = json.loads(json.dumps(SEEDS[0]))
        candidate["steps"][0]["op"] = "python.eval"
        report = TLangVerifier().verify(candidate)
        self.assertFalse(report.passed)
        self.assertTrue(any("unsupported operation" in item for item in report.issues))

    def test_changed_expected_value_is_rejected(self):
        candidate = json.loads(json.dumps(SEEDS[0]))
        candidate["tests"][0]["expected"] = "deliberately wrong"
        self.assertFalse(TLangVerifier().verify(candidate).passed)

    def test_email_is_rejected(self):
        candidate = json.loads(json.dumps(SEEDS[0]))
        candidate["intent"] = "Send results to private.person@example.com"
        report = TLangVerifier().verify(candidate)
        self.assertFalse(report.passed)
        self.assertTrue(any("personal data" in item for item in report.issues))

    def test_equivalent_template_is_rejected_as_duplicate(self):
        candidate = json.loads(json.dumps(SEEDS[0]))
        candidate["id"] = "text.trim.copy"
        report = TLangVerifier().verify(candidate, SEEDS)
        self.assertFalse(report.passed)
        self.assertTrue(any("duplicate procedure" in item for item in report.issues))

    def test_division_by_zero_returns_error_not_a_guess(self):
        template = find_template(SEEDS, "math.divide")
        result = Composer().run(template, {"left": 8, "right": 0})
        self.assertEqual(result["status"], "ERROR")
        self.assertIn("division by zero", result["error"])


class HomeSubmissionTests(unittest.TestCase):
    def test_signed_child_submission_passes_home_gate(self):
        secret = "test-only-child-secret-keep-out-of-production"
        candidate = {
            "id": "text.trim-lower.child", "version": 1, "domain": "text", "intent": "Trim then lowercase", "triggers": ["normalize"],
            "inputs": {"value": {"type": "string", "required": True}},
            "steps": [
                {"id": "trimmed", "op": "text.trim", "args": {"value": {"$ref": "input.value"}}},
                {"id": "lowered", "op": "text.lower", "args": {"value": {"$ref": "step.trimmed"}}}
            ],
            "output": {"$ref": "step.lowered"},
            "tests": [
                {"inputs": {"value": "  HELLO "}, "expected": "hello"},
                {"inputs": {"value": " ReveLa "}, "expected": "revela"}
            ],
            "provenance": {"kind": "teacher", "source_label": "test method extraction"}
        }
        signed = sign_template(candidate, "test-child", secret)
        valid, identity = verify_signature(signed, {"test-child": secret})
        self.assertTrue(valid)
        self.assertEqual(identity, "test-child")
        result = HomeGate({"test-child": secret}).verify(signed, SEEDS)
        self.assertTrue(result["passed"])

    def test_wrong_secret_fails_home_gate(self):
        candidate = json.loads(json.dumps(SEEDS[0]))
        candidate["id"] = "text.trim.child"
        signed = sign_template(candidate, "test-child", "correct-secret")
        valid, reason = verify_signature(signed, {"test-child": "wrong-secret"})
        self.assertFalse(valid)
        self.assertEqual(reason, "submission signature mismatch")

    def test_blocked_child_fails_home_gate(self):
        candidate = json.loads(json.dumps(SEEDS[0]))
        candidate["id"] = "text.trim.child"
        signed = sign_template(candidate, "blocked-child", "test-secret")
        result = HomeGate({"blocked-child": "test-secret"}, {"blocked-child"}).verify(signed, SEEDS)
        self.assertFalse(result["passed"])
        self.assertIn("blocked", result["issues"][0])


if __name__ == "__main__":
    unittest.main()
