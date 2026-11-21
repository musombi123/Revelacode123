"""Gradio Server-mode host for the unchanged VORAX Teacher FastAPI routes."""
from __future__ import annotations

import os

# Hugging Face Spaces' Node SSR proxy can intercept custom FastAPI routes.
# Disable it through the documented environment variable before Gradio imports.
os.environ.setdefault("GRADIO_SSR_MODE", "false")

from fastapi import APIRouter
from gradio import Server
from hf_teacher import app as service_app

# Gradio Server exposes FastAPI-compatible lifecycle hooks through its
# constructor. It does not expose FastAPI.add_event_handler() on the Server
# instance, so transfer the source router hooks at construction time.
app = Server(
    title="VORAX Teacher Service",
    on_startup=list(service_app.router.on_startup),
    on_shutdown=list(service_app.router.on_shutdown),
)

api_router = APIRouter()
_RESERVED_SERVER_PATHS = {"/", "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
for route in service_app.router.routes:
    if getattr(route, "path", None) not in _RESERVED_SERVER_PATHS:
        api_router.routes.append(route)

# Include the original Teacher endpoint objects so their paths, validation,
# authentication, response schema and OpenAPI contracts remain unchanged.
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
    # No GPU function is decorated or invoked: Teacher uses remote inference
    # providers and CPU-side metadata routing only.
    demo.launch(ssr_mode=False)
