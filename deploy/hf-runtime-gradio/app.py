"""Gradio-SDK adapter for the unchanged VORAX Runtime FastAPI routes."""
from __future__ import annotations

import asyncio
import inspect
import os

# Custom FastAPI routes must bypass the Gradio Node SSR proxy.
os.environ.setdefault("GRADIO_SSR_MODE", "false")

import gradio as gr
import spaces
from vorax.hf_runtime import app as service_app

# ZeroGPU requires a decorated function in a Gradio Space. It is a no-op
# marker and is intentionally never called; VORAX Runtime uses CPU execution.
@spaces.GPU
def _zerogpu_platform_marker() -> None:
    """Platform compatibility marker only. Never invoke this function."""
    return None


with gr.Blocks(title="VORAX Runtime Service") as demo:
    gr.Markdown(
        """
        # VORAX Runtime Service

        **Role:** trusted shared V-Lang execution and Home-first knowledge
        reconciliation.

        This service does not rely on a model to decide what is executable.
        Teacher output is an untrusted proposal; Runtime verifies provenance,
        executes tests, and records pending methods. Only Home review/merge
        authorizes promotion.

        - Health: [/health](/health)
        - API documentation: [/docs](/docs)
        - Home metadata index: [/vorax/home-index](/vorax/home-index) (Child authentication required)
        """
    )


def _run_service_startup() -> None:
    """Run the source Runtime startup reconciliation exactly once."""
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
    demo.app.router.routes[:] = source_routes + current


if __name__ == "__main__":
    # Use the Gradio SDK launch path required by ZeroGPU; do not start a
    # separate Uvicorn process. The original Runtime startup hook performs
    # only its normal Home-index/Vault reconciliation.
    demo.launch(prevent_thread_lock=True, ssr_mode=False)
    _mount_service_routes()
    _run_service_startup()
    demo.block_thread()
