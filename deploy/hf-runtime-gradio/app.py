"""Gradio Server-mode host for the unchanged VORAX Runtime FastAPI routes."""
from __future__ import annotations

import hmac
import os

# Hugging Face Spaces' Node SSR proxy can intercept custom FastAPI routes.
# Disable it before importing Gradio.
os.environ.setdefault("GRADIO_SSR_MODE", "false")

# Import ZeroGPU support before Gradio or any libraries that may initialize
# CUDA. The registered probe below is not used by ordinary Runtime requests.
import spaces
from fastapi import HTTPException
from gradio import Server
from vorax.hf_runtime import app as service_app
from vorax.runtime_route_mount import mount_fastapi_routes


_REQUIRED_RUNTIME_ROUTES = {
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
_RESERVED_SERVER_PATHS = {
    "/",
    "/openapi.json",
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
}

# Gradio Server exposes FastAPI-compatible lifecycle hooks through its
# constructor. Preserve Runtime's startup Vault/Home reconciliation.
app = Server(
    title="VORAX Runtime Service",
    on_startup=list(service_app.router.on_startup),
    on_shutdown=list(service_app.router.on_shutdown),
)


@spaces.GPU(duration=1)
def _zerogpu_startup_compat() -> dict:
    """ZeroGPU registration marker; returns a static value without model inference."""
    return {"status": "zerogpu_registered"}


@app.api(
    name="zerogpu_startup_compat",
    description="Private authenticated ZeroGPU runtime compatibility probe.",
    api_visibility="private",
    concurrency_limit=1,
)
def run_zerogpu_startup_compat(child_id: str, api_key: str) -> dict:
    """Call the inert ZeroGPU probe only for an authenticated VORAX child."""
    expected_id = os.getenv("VORAX_CHILD_ID", "kaggle-child").strip()
    expected_key = os.getenv("VORAX_KAGGLE_CHILD_API_KEY", "").strip()
    if (
        not expected_key
        or child_id != expected_id
        or not api_key
        or not hmac.compare_digest(api_key, expected_key)
    ):
        raise HTTPException(status_code=403, detail="Authenticated VORAX child required")
    return _zerogpu_startup_compat()


# Mount the original FastAPI route objects directly on Gradio Server's own
# router. The helper verifies required HTTP methods in the generated OpenAPI
# document before the Space can start, so missing weekly endpoints fail early.
_mounted_runtime_routes = mount_fastapi_routes(
    source_app=service_app,
    target_app=app,
    excluded_paths=_RESERVED_SERVER_PATHS,
    required_routes=_REQUIRED_RUNTIME_ROUTES,
)


@app.get("/", tags=["service"])
def service_root():
    return {
        "service": "VORAX Runtime Service",
        "role": "trusted shared V-Lang runtime",
        "health": "/health",
        "input_x": "/vorax/x",
        "learn": "/vorax/learn",
        "verify": "/vorax/verify",
        "home_index": "/vorax/home-index",
        "docs": "/docs",
        "runtime_routes_mounted": _mounted_runtime_routes,
        "home_method_bodies_loaded": False,
    }


demo = app

if __name__ == "__main__":
    # V-Lang, Home-index and Vault operations use CPU/network/storage.
    # The private ZeroGPU compatibility probe is not called during launch.
    demo.launch(ssr_mode=False)
