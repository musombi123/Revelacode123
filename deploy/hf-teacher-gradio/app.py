"""Gradio Server-mode host for the unchanged VORAX Teacher FastAPI routes."""
from __future__ import annotations

import os

# Hugging Face Spaces' Node SSR proxy can intercept custom FastAPI routes.
# Disable it through the documented environment variable before Gradio imports.
os.environ.setdefault("GRADIO_SSR_MODE", "false")

from fastapi import APIRouter
from gradio import Server
from hf_teacher import app as service_app

# Server mode is a FastAPI server with Gradio hosting support. VORAX Teacher
# is an HTTP/metadata/remote-provider service and does not need GPU execution.
app = Server(title="VORAX Teacher Service")

api_router = APIRouter()
_RESERVED_SERVER_PATHS = {"/", "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
for route in service_app.router.routes:
    if getattr(route, "path", None) not in _RESERVED_SERVER_PATHS:
        api_router.routes.append(route)

# Include the original Teacher endpoint objects so their paths, validation,
# authentication, response schema and OpenAPI contracts remain unchanged.
app.include_router(api_router)

# Carry over source-app lifecycle hooks rather than invoking them after the
# server is already running.
for callback in service_app.router.on_startup:
    app.add_event_handler("startup", callback)
for callback in service_app.router.on_shutdown:
    app.add_event_handler("shutdown", callback)


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
