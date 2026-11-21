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

    def test_adapters_do_not_request_or_fake_gpu_work(self):
        for relative in (
            "deploy/hf-teacher-gradio/app.py",
            "deploy/hf-runtime-gradio/app.py",
        ):
            source = self._read(relative)
            self.assertNotIn("import spaces", source)
            self.assertNotIn("@spaces.GPU", source)
            self.assertNotIn("uvicorn.run(", source)

    def test_adapters_register_source_lifecycle_hooks_before_launch(self):
        for relative in (
            "deploy/hf-teacher-gradio/app.py",
            "deploy/hf-runtime-gradio/app.py",
        ):
            source = self._read(relative)
            self.assertIn("service_app.router.on_startup", source)
            self.assertIn('app.add_event_handler("startup", callback)', source)
            self.assertIn("service_app.router.on_shutdown", source)
            self.assertIn('app.add_event_handler("shutdown", callback)', source)

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
        self.assertIn('space_hardware=ZERO_GPU', source)
        self.assertIn('ZERO_GPU = "zero-a10g"', source)
        self.assertIn('"VORAX_ALLOW_BILLABLE_INFERENCE": "false"', source)
        self.assertNotIn('space_hardware="cpu-basic"', source)

    def test_existing_docker_deployment_path_remains_present(self):
        self.assertTrue((ROOT / "deploy/hf-teacher/Dockerfile").is_file())
        self.assertTrue((ROOT / "deploy/hf-runtime/Dockerfile").is_file())


if __name__ == "__main__":
    unittest.main()
