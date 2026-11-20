"""Local Kaggle CUDA image inference. Remote inference is never reported as Kaggle GPU work."""
from __future__ import annotations

import io
import os
import re
import subprocess
import time
from typing import Any

_PIPELINES: dict[str, Any] = {}


class KaggleGPUError(RuntimeError):
    """Local Kaggle GPU inference could not run or could not prove its device."""


def _nvidia_smi() -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,uuid,memory.total,memory.used,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=8,
            check=True,
        )
        devices = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
        return {"available": bool(devices), "source": "nvidia-smi", "devices": devices}
    except Exception as exc:
        return {
            "available": False,
            "source": "nvidia-smi",
            "devices": [],
            "failure": type(exc).__name__,
        }


def _load_pipeline(model_id: str, torch: Any):
    cached = _PIPELINES.get(model_id)
    if cached is not None:
        return cached

    try:
        from diffusers import StableDiffusionPipeline
    except ImportError as exc:
        raise KaggleGPUError(
            "Diffusers is not installed in this Kaggle session; GPU image adapter is unavailable"
        ) from exc

    token = (
        os.getenv("HF_MODEL_TOKEN")
        or os.getenv("HF_TOKEN")
        or os.getenv("HF_INFERENCE_TOKEN")
        or None
    )
    kwargs: dict[str, Any] = {
        "torch_dtype": torch.float16,
        "use_safetensors": True,
    }
    if token:
        kwargs["token"] = token

    try:
        pipeline = StableDiffusionPipeline.from_pretrained(model_id, **kwargs)
        pipeline = pipeline.to("cuda")
        if hasattr(pipeline, "enable_attention_slicing"):
            pipeline.enable_attention_slicing()
        if hasattr(pipeline, "enable_vae_slicing"):
            pipeline.enable_vae_slicing()
        pipeline.set_progress_bar_config(disable=True)
    except Exception as exc:
        raise KaggleGPUError(
            "Could not load the approved local image model: "
            + type(exc).__name__
            + ": "
            + str(exc)[:180]
        ) from exc

    try:
        parameter = next(pipeline.unet.parameters())
        if parameter.device.type != "cuda":
            raise KaggleGPUError("Image model UNet is not resident on a CUDA device")
    except StopIteration as exc:
        raise KaggleGPUError("Image model has no UNet parameters to verify") from exc

    _PIPELINES[model_id] = pipeline
    return pipeline


def generate_image(
    prompt: str,
    width: int,
    height: int,
    *,
    seed: int | None = None,
    steps: int | None = None,
) -> dict[str, Any]:
    """Generate one PNG using a locally loaded Diffusers model on a real CUDA device."""
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 12_000:
        raise KaggleGPUError("Image prompt must be a non-empty string of at most 12000 characters")
    if (
        isinstance(width, bool)
        or isinstance(height, bool)
        or not isinstance(width, int)
        or not isinstance(height, int)
        or width < 256
        or height < 256
        or width > 768
        or height > 768
        or width % 8
        or height % 8
        or width * height > 589_824
    ):
        raise KaggleGPUError("Kaggle image dimensions must be multiples of 8 between 256 and 768, up to 0.59 MP")

    model_id = os.getenv(
        "VORAX_KAGGLE_IMAGE_MODEL",
        "stable-diffusion-v1-5/stable-diffusion-v1-5",
    ).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", model_id):
        raise KaggleGPUError("VORAX_KAGGLE_IMAGE_MODEL is not a valid approved Hub model identifier")

    try:
        requested_steps = int(steps if steps is not None else os.getenv("VORAX_KAGGLE_IMAGE_STEPS", "24"))
    except (TypeError, ValueError) as exc:
        raise KaggleGPUError("VORAX_KAGGLE_IMAGE_STEPS must be an integer") from exc
    if not 12 <= requested_steps <= 40:
        raise KaggleGPUError("Image inference steps must be between 12 and 40")

    try:
        import torch
    except ImportError as exc:
        raise KaggleGPUError("PyTorch is unavailable in this Kaggle session") from exc

    if not torch.cuda.is_available():
        raise KaggleGPUError("torch.cuda.is_available() is false; CPU fallback is prohibited")

    device_index = torch.cuda.current_device()
    gpu_name = torch.cuda.get_device_name(device_index)
    torch.cuda.synchronize(device_index)
    torch.cuda.reset_peak_memory_stats(device_index)
    memory_before = int(torch.cuda.memory_allocated(device_index))
    started = time.monotonic()

    pipeline = _load_pipeline(model_id, torch)
    try:
        for component_name in ("unet", "text_encoder", "vae"):
            parameter = next(getattr(pipeline, component_name).parameters())
            if parameter.device.type != "cuda" or parameter.device.index != device_index:
                raise KaggleGPUError(f"Loaded image model {component_name} is not on the observed Kaggle CUDA device")

        actual_seed = seed if seed is not None else int.from_bytes(os.urandom(4), "big") & 0x7FFFFFFF
        generator = torch.Generator(device=f"cuda:{device_index}").manual_seed(actual_seed)
        output = pipeline(
            prompt=prompt,
            width=width,
            height=height,
            num_inference_steps=requested_steps,
            guidance_scale=7.5,
            generator=generator,
        )
        torch.cuda.synchronize(device_index)
        images = getattr(output, "images", None)
        if not isinstance(images, list) or not images:
            raise KaggleGPUError("Diffusers completed without returning an image")
        image = images[0]

        nsfw = getattr(output, "nsfw_content_detected", None)
        if isinstance(nsfw, list) and nsfw and nsfw[0] is True:
            raise KaggleGPUError("The model safety checker flagged this generated image")

        extrema = image.convert("RGB").getextrema()
        if all(low == high for low, high in extrema):
            raise KaggleGPUError("Generated image is a single-colour blank")

        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        data = buffer.getvalue()
        if not data:
            raise KaggleGPUError("Diffusers returned an empty PNG")
    except KaggleGPUError:
        raise
    except Exception as exc:
        raise KaggleGPUError(
            "Local CUDA image inference failed: " + type(exc).__name__ + ": " + str(exc)[:180]
        ) from exc

    elapsed_ms = int((time.monotonic() - started) * 1000)
    memory_after = int(torch.cuda.memory_allocated(device_index))
    memory_reserved = int(torch.cuda.max_memory_reserved(device_index))
    smi = _nvidia_smi()
    telemetry = {
        "gpu_used": True,
        "gpu_name": gpu_name,
        "device": f"cuda:{device_index}",
        "telemetry_source": ["torch.cuda", "nvidia-smi"],
        "cuda_available": True,
        "model_parameter_devices": {
            name: str(next(getattr(pipeline, name).parameters()).device)
            for name in ("unet", "text_encoder", "vae")
        },
        "memory_allocated_before_bytes": memory_before,
        "memory_allocated_after_bytes": memory_after,
        "max_memory_reserved_bytes": memory_reserved,
        "nvidia_smi": smi,
        "execution_time_ms": elapsed_ms,
    }
    if not smi.get("available"):
        # PyTorch device placement and synchronization still prove CUDA execution.
        telemetry["nvidia_smi_warning"] = smi.get("failure", "GPU details unavailable")

    return {
        "data": data,
        "model_id": model_id,
        "seed": actual_seed,
        "steps": requested_steps,
        "width": width,
        "height": height,
        "elapsed_ms": elapsed_ms,
        "gpu_telemetry": telemetry,
        "provider": "kaggle-local-diffusers",
    }
