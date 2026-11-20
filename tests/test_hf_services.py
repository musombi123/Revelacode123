import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from vorax import hf_teacher, hf_runtime, vault
from vorax.vlang import dump_record


class TeacherServiceTests(unittest.TestCase):
    def test_discovery_returns_metadata_only(self):
        metadata = [
            {
                "id": "acme/example",
                "tags": ["text-generation"],
                "downloads": 123,
                "likes": 7,
                "pipeline_tag": "text-generation",
                "inferenceProviderMapping": {
                    "provider-a": {"status": "live", "providerId": "acme/example-a", "task": "conversational"},
                },
                "modelId": "ignored",
                "sha": "ignored",
            },
        ]
        router_models = [{
            "id": "acme/example",
            "providers": [{
                "provider": "provider-a",
                "status": "live",
                "provider_model_id": "acme/example",
                "task": "conversational",
                "context_length": 8192,
                "pricing": {"input": 0.1, "output": 0.2},
                "first_token_latency_ms": 100,
                "throughput": 20,
                "supports_tools": True,
                "supports_structured_output": True,
                "is_free": False,
            }],
        }]
        with patch.object(hf_teacher, "_http_json", return_value=(metadata, {})) as call, \
             patch.object(hf_teacher, "_discover_router_models", return_value=router_models):
            result = hf_teacher.discover(query="example", limit=3, source="models")
        self.assertEqual(result["status"], "metadata_only")
        self.assertEqual(result["models"], [{
            "id": "acme/example", "source_type": "model", "tags": ["text-generation"],
            "description": "", "downloads": 123, "likes": 7,
            "pipeline_tag": "text-generation", "library_name": None, "sdk": None,
            "providers": [{
                "provider": "provider-a", "status": "live",
                "provider_model_id": "acme/example", "task": "conversational",
                "context_length": 8192, "pricing": {"input": 0.1, "output": 0.2},
                "first_token_latency_ms": 100, "throughput": 20,
                "supports_tools": True, "supports_structured_output": True, "is_free": False,
            }],
        }])
        self.assertIs(result["weights_downloaded"], False)
        self.assertNotIn("files", result["models"][0])
        self.assertIn("search=example", call.call_args.args[0])

    def test_ask_uses_remote_router_and_authenticates_child(self):
        response = {"choices": [{"message": {"content": "A bounded teaching response."}}]}
        headers = {"x-inference-provider": "test-remote-provider", "x-request-id": "request-1"}
        with patch.dict(os.environ, {
                 "HF_TOKEN": "not-a-real-token",
                 "VORAX_CHILD_ID": "kaggle-child",
                 "VORAX_KAGGLE_CHILD_API_KEY": "child-test-key",
                 "VORAX_TEACHER_ATTESTATION_KEY": "test-teacher-hmac-secret-long-enough",
             }), \
             patch.object(hf_teacher, "_http_json", return_value=(response, headers)) as call, \
             patch.object(hf_teacher, "_log") as log:
            result = hf_teacher.ask(
                hf_teacher.TeacherAsk(prompt="Teach a reversible text method.", model_id="acme/example"),
                x_child_id="kaggle-child",
                x_child_key="child-test-key",
            )
        self.assertIs(result["remote_inference"], True)
        self.assertIs(result["kaggle_t4_used"], False)
        self.assertIs(result["weights_downloaded"], False)
        self.assertEqual(result["provider_used"], "test-remote-provider")
        self.assertEqual(result["attestation"]["algorithm"], "hmac-sha256")
        self.assertEqual(result["attestation"]["payload"]["model_id"], "acme/example")
        self.assertEqual(
            result["attestation"]["payload"]["response_sha256"],
            __import__("hashlib").sha256("A bounded teaching response.".encode()).hexdigest(),
        )
        self.assertEqual(call.call_args.args[0], hf_teacher.HF_CHAT_API)
        self.assertEqual(log.call_args.kwargs["kaggle_t4_used"], False)

    def test_ask_rejects_missing_child_authentication(self):
        with patch.dict(os.environ, {"VORAX_KAGGLE_CHILD_API_KEY": "expected-key"}):
            with self.assertRaises(HTTPException) as caught:
                hf_teacher.ask(
                    hf_teacher.TeacherAsk(prompt="teach", model_id="acme/example"),
                    x_child_id="kaggle-child",
                    x_child_key="wrong-key",
                )
        self.assertEqual(caught.exception.status_code, 403)


class RuntimeServiceTests(unittest.TestCase):
    def test_input_x_uses_shared_vlang_runtime_and_requires_child_auth(self):
        with patch.dict(os.environ, {"VORAX_CHILD_ROLE": "hf-runtime", "HF_VAULT_TOKEN": "vault-token", "VORAX_TEACHER_ATTESTATION_KEY": "test-teacher-hmac-secret-long-enough"}), \
             patch.object(hf_runtime, "CHILD_ID", "kaggle-child"), \
             patch.object(hf_runtime, "CHILD_KEY", "child-test-key"), \
             patch.object(hf_runtime, "HOME_READ_TOKEN", "read-token"), \
             patch.object(hf_runtime, "HOME_WRITE_TOKEN", "write-token"), \
             patch.object(hf_runtime, "_refresh_home_index", return_value=(None, {"status": "metadata_index_read", "methods_total": 30})):
            result = hf_runtime.input_x(
                {"x": "hello, who are you", "child_id": "kaggle-child", "api_key": "child-test-key"},
                None,
                None,
            )
            self.assertEqual(result["status"], "verified")
            self.assertEqual(result["method"], "runtime.identity")
            self.assertIsNone(result["artifact_sha256"])
            self.assertEqual(result["verification"]["authority"], "trusted_vlang_interpreter")

    def test_input_x_rejects_unauthenticated_child(self):
        with patch.dict(os.environ, {"VORAX_CHILD_ROLE": "hf-runtime"}), \
             patch.object(hf_runtime, "CHILD_ID", "kaggle-child"), \
             patch.object(hf_runtime, "CHILD_KEY", "child-test-key"), \
             patch.object(hf_runtime, "HOME_READ_TOKEN", "read-token"), \
             patch.object(hf_runtime, "HOME_WRITE_TOKEN", "write-token"):
            with self.assertRaises(HTTPException) as caught:
                hf_runtime.input_x({"x": "hello, who are you"}, None, None)
        self.assertEqual(caught.exception.status_code, 403)


    def _signed_teacher_receipt(self, response: str):
        import hashlib
        import hmac
        import json
        import time
        payload = {
            "model_id": "acme/example",
            "provider_used": "test-provider",
            "remote_inference": True,
            "kaggle_t4_used": False,
            "weights_downloaded": False,
            "response_sha256": hashlib.sha256(response.encode("utf-8")).hexdigest(),
            "request_id": "teacher-request-1",
            "issued_at": int(time.time()),
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        secret = "test-teacher-hmac-secret-long-enough"
        signature = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
        return {"algorithm": "hmac-sha256", "payload": payload, "signature": signature}

    def test_runtime_accepts_valid_teacher_attestation(self):
        response = json.dumps({
            "version": "vlang/1",
            "id": "learned.attested",
            "domain": "text",
            "task": "return supplied text unchanged",
            "triggers": ["return text unchanged", "identity text procedure"],
            "inputs": {"value": "string"},
            "steps": [{"op": "identity", "args": [{"input": "value"}], "as": "answer"}],
            "result": {"step": "answer"},
            "tests": [
                {"input": {"value": "one"}, "expect": "one"},
                {"input": {"value": "two"}, "expect": "two"},
            ],
            "provenance": {"kind": "teacher", "teacher": "acme/example"},
        })
        attestation = self._signed_teacher_receipt(response)
        with patch.dict(os.environ, {"VORAX_TEACHER_ATTESTATION_KEY": "test-teacher-hmac-secret-long-enough"}):
            proof = hf_runtime._verify_teacher_attestation(response, attestation, "acme/example")
        self.assertIs(proof["remote_inference"], True)
        self.assertIs(proof["kaggle_t4_used"], False)
        self.assertIs(proof["weights_downloaded"], False)

    def test_runtime_rejects_forged_or_modified_teacher_response(self):
        response = '{"candidate":"not trusted"}'
        attestation = self._signed_teacher_receipt(response)
        with patch.dict(os.environ, {"VORAX_TEACHER_ATTESTATION_KEY": "test-teacher-hmac-secret-long-enough"}):
            with self.assertRaises(HTTPException) as caught:
                hf_runtime._verify_teacher_attestation(response + " altered", attestation, "acme/example")
        self.assertEqual(caught.exception.status_code, 403)


    def test_runtime_rejects_a_method_different_from_teacher_signed_response(self):
        candidate = {
            "version": "vlang/1",
            "id": "learned.actual_method",
            "domain": "text",
            "task": "return supplied text unchanged",
            "triggers": ["return text unchanged", "identity text procedure"],
            "inputs": {"value": "string"},
            "steps": [{"op": "identity", "args": [{"input": "value"}], "as": "answer"}],
            "result": {"step": "answer"},
            "tests": [
                {"input": {"value": "one"}, "expect": "one"},
                {"input": {"value": "two"}, "expect": "two"},
            ],
            "provenance": {"kind": "teacher", "teacher": "acme/example"},
        }
        teacher_response = json.dumps(candidate)
        attestation = self._signed_teacher_receipt(teacher_response)
        altered = dict(candidate)
        altered["task"] = "claim a completely different method"
        with patch.dict(os.environ, {"VORAX_TEACHER_ATTESTATION_KEY": "test-teacher-hmac-secret-long-enough"}):
            with self.assertRaises(HTTPException) as caught:
                hf_runtime._validate_teacher_candidate(
                    teacher_response,
                    attestation,
                    altered,
                    "acme/example",
                )
        self.assertEqual(caught.exception.status_code, 403)

    def test_runtime_rejects_teacher_attestation_when_kaggle_gpu_is_claimed(self):
        response = "signed teacher response"
        attestation = self._signed_teacher_receipt(response)
        attestation["payload"]["kaggle_t4_used"] = True
        import hashlib
        import hmac
        import json
        canonical = json.dumps(attestation["payload"], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        attestation["signature"] = hmac.new(
            b"test-teacher-hmac-secret-long-enough", canonical.encode(), hashlib.sha256
        ).hexdigest()
        with patch.dict(os.environ, {"VORAX_TEACHER_ATTESTATION_KEY": "test-teacher-hmac-secret-long-enough"}):
            with self.assertRaises(HTTPException) as caught:
                hf_runtime._verify_teacher_attestation(response, attestation, "acme/example")
        self.assertEqual(caught.exception.status_code, 403)

    def test_verified_candidate_is_not_added_to_runtime_catalog_before_home_merge(self):
        candidate = {
            "version": "vlang/1",
            "id": "learned.reverse_text",
            "domain": "text",
            "task": "reverse supplied text",
            "triggers": ["reverse supplied text", "reverse text sample"],
            "inputs": {"text": "string"},
            "steps": [{"op": "reverse_text", "args": [{"input": "text"}], "as": "answer"}],
            "result": {"step": "answer"},
            "tests": [
                {"input": {"text": "abc"}, "expect": "cba"},
                {"input": {"text": "vorax"}, "expect": "xarov"},
            ],
            "provenance": {"kind": "teacher", "teacher": "test-teacher"},
        }
        with patch.object(hf_runtime, "promote_pending_home_methods", return_value={
                 "status": "no_home_merged_pending_methods", "promoted_method_ids": [],
             }), \
             patch.object(hf_runtime, "pending_duplicate_reason", return_value=None), \
             patch.object(hf_runtime, "write_pending_method", return_value={"status": "written_pending"}), \
             patch.object(hf_runtime, "submit_template", return_value={
                 "pull_request": "https://github.com/musombi123/Revelacode123/pull/999",
                 "number": 999, "branch": "child/kaggle-child/test",
             }), \
             patch.object(hf_runtime, "mark_pending_home_submission"), \
             patch.object(hf_runtime, "_refresh_home_index", return_value=({
                 "version": "vorax-home-method-index/v1", "methods_total": 30, "records": [],
             }, {"status": "metadata_index_read", "methods_total": 30})), \
             patch.object(hf_runtime.STORE, "add", side_effect=AssertionError("pending must not enter executable catalogue")), \
             patch.object(hf_runtime.STORE, "record_success"):
            result = hf_runtime._verify_and_submit(candidate, "kaggle-child", "acme/example", "reverse text")
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["verification"]["home_certification"], "pending")
        self.assertIs(result["verification"]["child_self_certification"], False)
        self.assertEqual(result["home_submission"]["status"], "submitted_for_home_review")


class VaultProtectionTests(unittest.TestCase):
    def test_client_opens_existing_dataset_without_creating_repo(self):
        fake_api = SimpleNamespace(
            repo_info=lambda **kwargs: SimpleNamespace(private=True),
            list_repo_files=lambda **kwargs: [
                "vlang/index.json", "vlang/pending/index.json", "manifest/checksums.json",
                "manifest/runtime-version.json", "manifest/sync-status.json",
                "reports/devour/latest.json", "reports/verification/latest.json",
                "reports/go-no-go/latest.json",
            ],
        )
        with patch.dict(os.environ, {"HF_VAULT_TOKEN": "dataset-token", "HF_VAULT_REPO": "Musombi/revelacode-vorax-vault"}), \
             patch("huggingface_hub.HfApi", return_value=fake_api) as api_class:
            proof = vault.inspect_vault()
        api_class.assert_called_once()
        self.assertEqual(proof["repo_type"], "dataset")
        self.assertIs(proof["private"], True)
        self.assertEqual(proof["missing_files"], [])

    def test_runtime_pending_write_preserves_existing_pending_entries_and_checksum_map(self):
        existing = {"version": "vlang-index/1", "records": [{"id": "older.method", "sha256": "a" * 64}]}
        checksum_map = {"version": "sha256/v1", "files": {"older/path": "b" * 64}}
        uploaded = []
        fake_api = SimpleNamespace(
            repo_info=lambda **kwargs: SimpleNamespace(private=True),
            list_repo_files=lambda **kwargs: ["vlang/pending/index.json", "manifest/checksums.json"],
        )
        record = {
            "version": "vlang/1",
            "id": "learned.pending_test",
            "domain": "text",
            "task": "return input unchanged",
            "triggers": ["pending write smoke test", "unchanged input test"],
            "inputs": {"value": "string"},
            "steps": [{"op": "identity", "args": [{"input": "value"}], "as": "answer"}],
            "result": {"step": "answer"},
            "tests": [
                {"input": {"value": "first"}, "expect": "first"},
                {"input": {"value": "second"}, "expect": "second"},
            ],
            "provenance": {"kind": "teacher", "teacher": "test-teacher", "child_id": "kaggle-child"},
        }
        canonical_sha = __import__("hashlib").sha256(dump_record(record).encode("utf-8")).hexdigest()
        def read_json(_api, _repo, path, default):
            if path == "vlang/pending/index.json":
                return {"version": existing["version"], "records": list(existing["records"])}
            if path == "manifest/checksums.json":
                return {"version": checksum_map["version"], "files": dict(checksum_map["files"])}
            return default
        with patch.dict(os.environ, {"HF_VAULT_TOKEN": "dataset-token", "HF_VAULT_REPO": "Musombi/revelacode-vorax-vault"}), \
             patch("huggingface_hub.HfApi", return_value=fake_api), \
             patch.object(vault, "_read_json", side_effect=read_json), \
             patch.object(vault, "_upload", side_effect=lambda _api, _repo, path, data, _message: uploaded.append((path, data))):
            receipt = vault.write_pending_method(
                record,
                child_id="kaggle-child",
                verification={"status": "runtime_tests_passed", "method_sha256": canonical_sha},
            )
        self.assertEqual(receipt["status"], "written_pending")
        self.assertTrue(any(path == "vlang/pending/learned.pending_test.vlang" for path, _ in uploaded))
        pending_bytes = next(data for path, data in uploaded if path == "vlang/pending/index.json")
        pending = __import__("json").loads(pending_bytes.decode("utf-8"))
        self.assertEqual({entry["id"] for entry in pending["records"]}, {"older.method", "learned.pending_test"})
        checksum_bytes = next(data for path, data in uploaded if path == "manifest/checksums.json")
        checksums = __import__("json").loads(checksum_bytes.decode("utf-8"))["files"]
        self.assertEqual(checksums["older/path"], "b" * 64)
        self.assertEqual(checksums["vlang/pending/learned.pending_test.vlang"], canonical_sha)


    def test_pending_method_promotes_only_when_home_index_summary_matches(self):
        import hashlib
        record = {
            "version": "vlang/1",
            "id": "learned.home_approved",
            "domain": "text",
            "task": "return supplied text unchanged",
            "triggers": ["return text unchanged", "identity text procedure"],
            "inputs": {"value": "string"},
            "steps": [{"op": "identity", "args": [{"input": "value"}], "as": "answer"}],
            "result": {"step": "answer"},
            "tests": [
                {"input": {"value": "one"}, "expect": "one"},
                {"input": {"value": "two"}, "expect": "two"},
            ],
            "provenance": {"kind": "teacher", "teacher": "remote-teacher", "child_id": "kaggle-child"},
        }
        raw = dump_record(record).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        pending = {
            "version": "vlang-index/1",
            "records": [{
                "id": record["id"],
                "path": f"vlang/pending/{record['id']}.vlang",
                "sha256": digest,
                "verification_report": "reports/verification/pending/learned.home_approved.json",
                "status": "submitted_for_home_review",
                "home_pr": "https://github.com/musombi123/Revelacode123/pull/42",
            }],
        }
        verified = {"version": "vlang-index/1", "records": []}
        checksums = {"version": "sha256/v1", "files": {}}
        files = []
        fake_api = SimpleNamespace(
            repo_info=lambda **kwargs: SimpleNamespace(private=True),
            list_repo_files=lambda **kwargs: [
                "vlang/pending/learned.home_approved.vlang",
                "vlang/pending/index.json",
                "vlang/index.json",
                "manifest/checksums.json",
            ],
        )
        def read_json(_api, _repo, path, default):
            if path == "vlang/pending/index.json":
                return pending
            if path == "vlang/index.json":
                return verified
            if path == "manifest/checksums.json":
                return checksums
            return default
        home_index = {
            "version": "vorax-home-method-index/v1",
            "methods_total": 1,
            "records": [{
                "id": record["id"],
                "domain": record["domain"],
                "task": record["task"],
                "triggers": record["triggers"],
            }],
        }
        with patch.dict(os.environ, {"HF_VAULT_TOKEN": "dataset-token"}), \
             patch("huggingface_hub.HfApi", return_value=fake_api), \
             patch.object(vault, "_read_json", side_effect=read_json), \
             patch.object(vault, "_read_file", return_value=raw), \
             patch.object(vault, "_upload", side_effect=lambda _api, _repo, path, data, _message: files.append((path, data))):
            result = vault.promote_pending_home_methods(home_index)
        self.assertEqual(result["status"], "promoted")
        self.assertEqual(result["promoted_method_ids"], [record["id"]])
        self.assertTrue(any(path == f"vlang/verified/{record['id']}.vlang" for path, _ in files))

    def test_pending_method_is_not_promoted_when_home_metadata_does_not_match(self):
        import hashlib
        record = {
            "version": "vlang/1",
            "id": "learned.home_mismatch",
            "domain": "text",
            "task": "return supplied text unchanged",
            "triggers": ["return text unchanged", "identity text procedure"],
            "inputs": {"value": "string"},
            "steps": [{"op": "identity", "args": [{"input": "value"}], "as": "answer"}],
            "result": {"step": "answer"},
            "tests": [
                {"input": {"value": "one"}, "expect": "one"},
                {"input": {"value": "two"}, "expect": "two"},
            ],
            "provenance": {"kind": "teacher", "teacher": "remote-teacher", "child_id": "kaggle-child"},
        }
        raw = dump_record(record).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        fake_api = SimpleNamespace(
            repo_info=lambda **kwargs: SimpleNamespace(private=True),
            list_repo_files=lambda **kwargs: ["vlang/pending/index.json"],
        )
        pending = {"version": "vlang-index/1", "records": [{
            "id": record["id"],
            "path": f"vlang/pending/{record['id']}.vlang",
            "sha256": digest,
        }]}
        def read_json(_api, _repo, path, default):
            if path == "vlang/pending/index.json":
                return pending
            return {"version": "vlang-index/1", "records": []} if path == "vlang/index.json" else {"version": "sha256/v1", "files": {}}
        wrong_home = {
            "version": "vorax-home-method-index/v1",
            "methods_total": 1,
            "records": [{
                "id": record["id"], "domain": record["domain"],
                "task": "a different Home task", "triggers": record["triggers"],
            }],
        }
        with patch.dict(os.environ, {"HF_VAULT_TOKEN": "dataset-token"}), \
             patch("huggingface_hub.HfApi", return_value=fake_api), \
             patch.object(vault, "_read_json", side_effect=read_json), \
             patch.object(vault, "_read_file", return_value=raw), \
             patch.object(vault, "_upload") as upload:
            with self.assertRaisesRegex(vault.VaultError, "does not match Home"):
                vault.promote_pending_home_methods(wrong_home)
        upload.assert_not_called()


if __name__ == "__main__":
    unittest.main()
