"""Gradio Server-mode host for the unchanged VORAX Teacher FastAPI routes."""
from __future__ import annotations

import hmac
import os

# Hugging Face Spaces' Node SSR proxy can intercept custom FastAPI routes.
# Disable it before importing Gradio.
os.environ.setdefault("GRADIO_SSR_MODE", "false")

# Import ZeroGPU support before Gradio or any libraries that may initialize
# CUDA. The registered probe below is not used by ordinary Teacher requests.
import spaces
from fastapi import APIRouter, HTTPException
from gradio import Server
from hf_teacher import app as service_app

# Gradio Server exposes FastAPI-compatible lifecycle hooks through its
# constructor. Preserve the source Teacher application's startup/shutdown.
app = Server(
    title="VORAX Teacher Service",
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


api_router = APIRouter()
_RESERVED_SERVER_PATHS = {"/", "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
for route in service_app.router.routes:
    if getattr(route, "path", None) not in _RESERVED_SERVER_PATHS:
        api_router.routes.append(route)

# Include original Teacher endpoint objects so paths, validation,
# authentication and response schemas remain unchanged.
app.include_router(api_router)


@app.get("/", tags=["service"])
def service_root():
    return {
        "service": "VORAX Teacher Service",
        "role": "remote capability proposal",
        "health": "/health",
        "discovery": "/teacher/discover",
        "ask": "/teacher/ask",
        "docs": "/docs",
        "weights_downloaded": False,
        "local_model_loaded": False,
    }


# Hugging Face Gradio Spaces recognizes the demo variable.
demo = app

if __name__ == "__main__":
    # Teacher uses remote inference providers and CPU-side metadata routing.
    # The private ZeroGPU compatibility probe is not called during launch.
    demo.launch(ssr_mode=False)
