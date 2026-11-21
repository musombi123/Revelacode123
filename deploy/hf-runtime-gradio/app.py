"""Gradio Server-mode host for the unchanged VORAX Runtime FastAPI routes."""
from __future__ import annotations

import os

# Hugging Face Spaces' Node SSR proxy can intercept custom FastAPI routes.
# Disable it through the documented environment variable before Gradio imports.
os.environ.setdefault("GRADIO_SSR_MODE", "false")

from fastapi import APIRouter
from gradio import Server
from vorax.hf_runtime import app as service_app

# Server mode gives the Runtime full FastAPI control within the supported
# Gradio Space process. V-Lang and bounded tools execute on CPU.
app = Server(title="VORAX Runtime Service")

api_router = APIRouter()
_RESERVED_SERVER_PATHS = {"/", "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
for route in service_app.router.routes:
    if getattr(route, "path", None) not in _RESERVED_SERVER_PATHS:
        api_router.routes.append(route)

# Preserve the original Runtime route objects and all their authentication,
# validation, request and response contracts.
app.include_router(api_router)

# Runtime startup reconciliation is part of the source application's
# lifecycle. Register it before Gradio starts serving requests.
for callback in service_app.router.on_startup:
    app.add_event_handler("startup", callback)
for callback in service_app.router.on_shutdown:
    app.add_event_handler("shutdown", callback)


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
