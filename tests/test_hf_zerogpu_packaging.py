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

    def test_adapters_are_syntax_valid_and_use_gradio_launch_path(self):
        for relative in (
            "deploy/hf-teacher-gradio/app.py",
            "deploy/hf-runtime-gradio/app.py",
        ):
            source = self._read(relative)
            ast.parse(source, filename=relative)
            self.assertIn("import spaces", source)
            self.assertIn("@spaces.GPU", source)
            self.assertIn("demo.launch(prevent_thread_lock=True, ssr_mode=False)", source)
            self.assertIn("service_app.router.routes", source)
            self.assertIn("demo.block_thread()", source)

    def test_zero_gpu_adapter_does_not_start_second_uvicorn_server(self):
        for relative in (
            "deploy/hf-teacher-gradio/app.py",
            "deploy/hf-runtime-gradio/app.py",
        ):
            source = self._read(relative)
            self.assertNotIn("uvicorn.run(", source)
            self.assertNotIn("mount_gradio_app(", source)

    def test_requirements_do_not_override_platform_managed_packages_or_load_models(self):
        for relative in (
            "deploy/hf-teacher-gradio/requirements.txt",
            "deploy/hf-runtime-gradio/requirements.txt",
        ):
            requirements = self._read(relative).lower()
            for forbidden in ("gradio", "spaces", "huggingface_hub", "diffusers", "transformers"):
                self.assertNotIn(forbidden, requirements)

    def test_runtime_system_packages_include_ffmpeg_for_verified_mp4_inspection(self):
        packages = self._read("deploy/hf-runtime-gradio/packages.txt").splitlines()
        self.assertIn("ffmpeg", packages)
        self.assertIn("ca-certificates", packages)

    def test_deploy_script_requires_explicit_manual_approval_and_free_zero_gpu(self):
        source = self._read("scripts/deploy_hf_zerogpu_children.py")
        self.assertIn('"--manual-release-approval"', source)
        self.assertIn('space_hardware=ZERO_GPU', source)
        self.assertIn('ZERO_GPU = "zero-a10g"', source)
        self.assertIn('"VORAX_ALLOW_BILLABLE_INFERENCE": "false"', source)
        self.assertIn('"github_actions_verified": False', source)
        self.assertNotIn('space_hardware="cpu-basic"', source)

    def test_existing_docker_deployment_path_remains_present(self):
        self.assertTrue((ROOT / "deploy/hf-teacher/Dockerfile").is_file())
        self.assertTrue((ROOT / "deploy/hf-runtime/Dockerfile").is_file())


if __name__ == "__main__":
    unittest.main()
