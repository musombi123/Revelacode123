import unittest

from fastapi import FastAPI

from vorax.hf_runtime import app as runtime_service_app
from vorax.runtime_route_mount import mount_fastapi_routes


REQUIRED_RUNTIME_ROUTES = {
    "/health": {"GET"},
    "/vorax/home-index": {"GET"},
    "/vorax/x": {"POST"},
    "/vorax/verify": {"POST"},
    "/vorax/learn": {"POST"},
    "/vorax/vault/reconcile": {"POST"},
    "/vorax/cycle/start": {"POST"},
    "/vorax/cycle/pending": {"GET"},
    "/vorax/cycle/status": {"GET", "POST"},
    "/vorax/cycle/hunter-report": {"POST"},
}

EXCLUDED_SERVER_PATHS = {
    "/",
    "/openapi.json",
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
}


class RuntimeRouteMountTests(unittest.TestCase):
    def test_all_runtime_routes_mount_and_appear_in_openapi(self):
        target_app = FastAPI(title="Test Gradio Server target")

        mounted_count = mount_fastapi_routes(
            runtime_service_app,
            target_app,
            excluded_paths=EXCLUDED_SERVER_PATHS,
            required_routes=REQUIRED_RUNTIME_ROUTES,
        )

        self.assertGreater(mounted_count, 0)
        schema_paths = target_app.openapi()["paths"]
        for path, methods in REQUIRED_RUNTIME_ROUTES.items():
            with self.subTest(path=path):
                self.assertIn(path, schema_paths)
                for method in methods:
                    self.assertIn(method.lower(), schema_paths[path])

    def test_missing_required_source_route_fails_closed(self):
        source_app = FastAPI()
        target_app = FastAPI()

        @source_app.get("/health")
        def health():
            return {"status": "ok"}

        with self.assertRaisesRegex(RuntimeError, "Source Runtime is missing required routes"):
            mount_fastapi_routes(
                source_app,
                target_app,
                required_routes={"/vorax/cycle/start": {"POST"}},
            )

        self.assertNotIn(
            "/health",
            target_app.openapi()["paths"],
            "A failed preflight must not leave a partially mounted source router.",
        )

    def test_route_collision_is_rejected_instead_of_shadowed(self):
        source_app = FastAPI()
        target_app = FastAPI()

        @source_app.get("/health")
        def source_health():
            return {"source": True}

        @target_app.get("/health")
        def target_health():
            return {"target": True}

        with self.assertRaisesRegex(RuntimeError, "Refusing to shadow existing target routes"):
            mount_fastapi_routes(
                source_app,
                target_app,
                required_routes={"/health": {"GET"}},
            )


if __name__ == "__main__":
    unittest.main()
