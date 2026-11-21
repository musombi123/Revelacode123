from pathlib import Path
import ast
import unittest

ROOT = Path(__file__).resolve().parents[1]


class HuggingFaceZeroGpuPackageTests(unittest.TestCase):
    def _read(self, relative):
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_teacher_uses_gradio_and_supported_zerogpu_python(self):
        readme = self._read("deploy/hf-teacher-gradio/README.md")
        self.assertIn("sdk: gradio", readme)
        self.assertIn('python_version: "3.12"', readme)
        self.assertIn("no local model weights", readme.lower())

    def test_runtime_uses_gradio_and_describes_home_as_approval_authority(self):
        readme = self._read("deploy/hf-runtime-gradio/README.md")
        self.assertIn("sdk: gradio", readme)
        self.assertIn("GitHub Home", readme)
        self.assertIn("canonical SHA-256", readme)

    def test_adapters_use_supported_gradio_server_mode_and_preserve_fastapi_routes(self):
        for relative in (
            "deploy/hf-teacher-gradio/app.py",
            "deploy/hf-runtime-gradio/app.py",
        ):
            source = self._read(relative)
            ast.parse(source, filename=relative)
            self.assertIn("from gradio import Server", source)
            self.assertIn("app = Server(", source)
            self.assertIn("app.include_router(api_router)", source)
            self.assertIn("service_app.router.routes", source)
            self.assertIn("demo = app", source)
            self.assertIn("demo.launch(ssr_mode=False)", source)
            self.assertIn('os.environ.setdefault("GRADIO_SSR_MODE", "false")', source)

    def test_adapters_register_private_authenticated_zerogpu_startup_probe(self):
        for relative in (
            "deploy/hf-teacher-gradio/app.py",
            "deploy/hf-runtime-gradio/app.py",
        ):
            source = self._read(relative)
            ast.parse(source, filename=relative)
            self.assertIn("import spaces", source)
            self.assertIn("@spaces.GPU(duration=1)", source)
            self.assertIn('@app.api(', source)
            self.assertIn('name="zerogpu_startup_compat"', source)
            self.assertIn('api_visibility="private"', source)
            self.assertIn("return _zerogpu_startup_compat()", source)
            self.assertIn("hmac.compare_digest(api_key, expected_key)", source)

            probe = source.split("@spaces.GPU(duration=1)", 1)[1].split("@app.api(", 1)[0]
            for forbidden in ("torch", "cuda", "from_pretrained", "diffusers", "transformers"):
                self.assertNotIn(forbidden, probe.lower())

    def test_zerogpu_probe_is_only_called_by_its_authenticated_private_api_wrapper(self):
        for relative in (
            "deploy/hf-teacher-gradio/app.py",
            "deploy/hf-runtime-gradio/app.py",
        ):
            source = self._read(relative)
            self.assertEqual(source.count("_zerogpu_startup_compat()"), 2)
            self.assertIn("api_visibility=\"private\"", source)
            self.assertIn("expected_key = os.getenv(\"VORAX_KAGGLE_CHILD_API_KEY\", \"\").strip()", source)
            self.assertIn("status_code=403", source)

    def test_adapters_do_not_load_models_or_execute_gpu_work_in_business_routes(self):
        for relative in (
            "deploy/hf-teacher-gradio/app.py",
            "deploy/hf-runtime-gradio/app.py",
        ):
            source = self._read(relative).lower()
            for forbidden in ("import torch", "import diffusers", "transformers.pipeline", ".from_pretrained(", ".cuda("):
                self.assertNotIn(forbidden, source)
            self.assertNotIn("uvicorn.run(", source)

    def test_adapters_transfer_source_lifecycle_hooks_through_server_constructor(self):
        for relative in (
            "deploy/hf-teacher-gradio/app.py",
            "deploy/hf-runtime-gradio/app.py",
        ):
            source = self._read(relative)
            self.assertIn("on_startup=list(service_app.router.on_startup)", source)
            self.assertIn("on_shutdown=list(service_app.router.on_shutdown)", source)
            self.assertNotIn("app.add_event_handler(", source)

    def test_runtime_declares_vault_client_dependency(self):
        requirements = self._read("deploy/hf-runtime-gradio/requirements.txt").lower()
        self.assertIn("huggingface_hub>=0.27,<2", requirements)

    def test_requirements_do_not_override_platform_managed_packages_or_load_models(self):
        for relative in (
            "deploy/hf-teacher-gradio/requirements.txt",
            "deploy/hf-runtime-gradio/requirements.txt",
        ):
            requirements = self._read(relative).lower()
            for forbidden in ("gradio", "spaces", "diffusers", "transformers"):
                self.assertNotIn(forbidden, requirements)

    def test_runtime_system_packages_include_ffmpeg_for_verified_mp4_inspection(self):
        packages = self._read("deploy/hf-runtime-gradio/packages.txt").splitlines()
        self.assertIn("ffmpeg", packages)
        self.assertIn("ca-certificates", packages)

    def test_deploy_script_requires_explicit_local_approval_and_fresh_tests(self):
        source = self._read("scripts/deploy_hf_zerogpu_children.py")
        self.assertIn('"--deploy"', source)
        self.assertIn('"--manual-local-approval"', source)
        self.assertIn("DEPLOY VORAX", source)
        self.assertIn('git", "rev-parse", "origin/main"', source)
        self.assertIn('"test_hf_zerogpu_packaging.py"', source)
        self.assertIn('"github_actions_verified": False', source)
        self.assertNotIn("require_green_release.py", source)
        self.assertNotIn("VORAX_RELEASE_TAG", source)
        self.assertIn("space_hardware=ZERO_GPU", source)
        self.assertIn('ZERO_GPU = "zero-a10g"', source)
        self.assertIn('"VORAX_ALLOW_BILLABLE_INFERENCE": "false"', source)
        self.assertNotIn('space_hardware="cpu-basic"', source)

    def test_deploy_script_adds_repository_root_before_importing_vorax(self):
        source = self._read("scripts/deploy_hf_zerogpu_children.py")
        root_line = source.index('ROOT = Path(__file__).resolve().parents[1]')
        path_line = source.index('sys.path.insert(0, str(ROOT))')
        vault_import = source.index('from vorax.vault import inspect_vault')
        self.assertLess(root_line, path_line)
        self.assertLess(path_line, vault_import)

    def test_existing_docker_deployment_path_remains_present(self):
        self.assertTrue((ROOT / "deploy/hf-teacher/Dockerfile").is_file())
        self.assertTrue((ROOT / "deploy/hf-runtime/Dockerfile").is_file())

    def test_deploy_script_reports_failure_stage_and_redacted_http_details(self):
        source = self._read("scripts/deploy_hf_zerogpu_children.py")
        self.assertIn("DEPLOYMENT_STAGE", source)
        self.assertIn("def set_deployment_stage", source)
        self.assertIn("def http_error_summary", source)
        self.assertIn('getattr(response, "status_code", None)', source)
        self.assertIn("parsed.path", source)
        self.assertIn("[REDACTED_HF_TOKEN]", source)
        self.assertIn("Teacher metadata discovery smoke test", source)
        self.assertIn("authenticated Runtime Input X smoke test", source)
        self.assertIn("Health check did not pass", source)


if __name__ == "__main__":
    unittest.main()
