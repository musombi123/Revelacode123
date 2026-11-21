"""Gradio Server-mode host for the unchanged VORAX Runtime FastAPI routes."""
from __future__ import annotations

import os

# Hugging Face Spaces' Node SSR proxy can intercept custom FastAPI routes.
# Disable it through the documented environment variable before Gradio imports.
os.environ.setdefault("GRADIO_SSR_MODE", "false")

from fastapi import APIRouter
from gradio import Server
from vorax.hf_runtime import app as service_app

# Gradio Server exposes FastAPI-compatible lifecycle hooks through its
# constructor. It does not expose FastAPI.add_event_handler() on the Server
# instance. Preserve Runtime's startup reconciliation by transferring the
# source router hooks at construction time.
app = Server(
    title="VORAX Runtime Service",
    on_startup=list(service_app.router.on_startup),
    on_shutdown=list(service_app.router.on_shutdown),
)

api_router = APIRouter()
_RESERVED_SERVER_PATHS = {"/", "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
for route in service_app.router.routes:
    if getattr(route, "path", None) not in _RESERVED_SERVER_PATHS:
        api_router.routes.append(route)

# Preserve the original Runtime route objects and all their authentication,
# validation, request and response contracts.
app.include_router(api_router)


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
        "home_method_bodies_loaded": False,
    }


demo = app

if __name__ == "__main__":
    # No GPU function is decorated or invoked: V-Lang execution, Home-index
    # reads and Vault reconciliation are CPU/network/storage operations.
    demo.launch(ssr_mode=False)
