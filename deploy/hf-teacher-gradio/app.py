"""Gradio-SDK adapter for the unchanged VORAX Teacher FastAPI routes."""
from __future__ import annotations

import asyncio
import inspect
import os

# Disable the Gradio Node SSR proxy so non-Gradio API paths reach Python/FastAPI.
os.environ.setdefault("GRADIO_SSR_MODE", "false")

import gradio as gr
import spaces
from hf_teacher import app as service_app

# ZeroGPU's Gradio runtime requires a GPU-decorated function to be present.
# This marker is intentionally never called; VORAX Teacher is CPU/API-only.
@spaces.GPU
def _zerogpu_platform_marker() -> None:
    """Platform compatibility marker only. Never invoke this function."""
    return None


with gr.Blocks(title="VORAX Teacher Service") as demo:
    gr.Markdown(
        """
        # VORAX Teacher Service

        **Role:** remote capability proposal service.

        This service discovers public model/provider metadata and, when
        authenticated, asks a remote inference provider for a candidate
        V-Lang procedure. It does not certify or store VORAX knowledge.

        - Health: [/health](/health)
        - API documentation: [/docs](/docs)
        - Metadata discovery: [/teacher/discover?query=language&limit=3](/teacher/discover?query=language&limit=3)

        **Trust boundary:** the separate Runtime validates proposals; accepted
        knowledge is brought Home through GitHub review and merge.
        """
    )


def _run_service_startup() -> None:
    """Run the source FastAPI app's registered startup hooks exactly once."""
    for callback in getattr(service_app.router, "on_startup", []):
        result = callback()
        if inspect.isawaitable(result):
            asyncio.run(result)


def _mount_service_routes() -> None:
    """Expose the original FastAPI endpoints without changing their contracts."""
    if demo.app is None:
        raise RuntimeError("Gradio app was not initialized before API route mounting")
    current = list(demo.app.router.routes)
    source_routes = [
        route
        for route in service_app.router.routes
        if getattr(route, "path", None) != "/"
    ]
    # Put the service's exact routes first so /health and /teacher/* resolve
    # before Gradio's catch-all/static routes.
    demo.app.router.routes[:] = source_routes + current


if __name__ == "__main__":
    # Gradio launch() is required by the ZeroGPU SDK runtime. Do not start a
    # second Uvicorn process or call mount_gradio_app() on a separate server.
    demo.launch(prevent_thread_lock=True, ssr_mode=False)
    _mount_service_routes()
    _run_service_startup()
    demo.block_thread()
