"""Safe mounting of an existing FastAPI application's routes into another app.

Gradio Server subclasses FastAPI. Registering the original route objects on its
router preserves FastAPI's dependency graphs, request validation and OpenAPI
metadata without creating an intermediate APIRouter.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def _route_methods(route: Any) -> set[str]:
    methods = getattr(route, "methods", None) or set()
    return {str(method).upper() for method in methods}


def _route_keys(routes: Iterable[Any], excluded_paths: set[str]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for route in routes:
        path = getattr(route, "path", None)
        if not isinstance(path, str) or path in excluded_paths:
            continue
        keys.update((path, method) for method in _route_methods(route))
    return keys


def mount_fastapi_routes(
    source_app: Any,
    target_app: Any,
    *,
    excluded_paths: Iterable[str] = (),
    required_routes: Mapping[str, Iterable[str]] | None = None,
) -> int:
    """Mount source routes directly on target and verify their OpenAPI contract.

    The operation is intentionally fail-closed: missing source routes, route
    collisions, or routes absent from the generated OpenAPI schema raise
    RuntimeError during application startup rather than silently shipping a
    partially mounted API.
    """
    source_router = getattr(source_app, "router", None)
    target_router = getattr(target_app, "router", None)
    if source_router is None or target_router is None:
        raise TypeError("Both source_app and target_app must expose a FastAPI router")

    excluded = set(excluded_paths)
    source_routes = [
        route
        for route in list(source_router.routes)
        if getattr(route, "path", None) not in excluded
    ]

    required = {
        (path, str(method).upper())
        for path, methods in (required_routes or {}).items()
        for method in methods
    }
    source_keys = _route_keys(source_routes, set())
    missing_source = sorted(required - source_keys)
    if missing_source:
        formatted = ", ".join(f"{method} {path}" for path, method in missing_source)
        raise RuntimeError(f"Source Runtime is missing required routes: {formatted}")

    existing_keys = _route_keys(list(target_router.routes), set())
    collisions = sorted(existing_keys & source_keys)
    if collisions:
        formatted = ", ".join(f"{method} {path}" for path, method in collisions)
        raise RuntimeError(f"Refusing to shadow existing target routes: {formatted}")

    # Register the original FastAPI route objects directly on Gradio Server's
    # FastAPI router. This retains dependencies, response models and schema
    # metadata exactly as defined by the Runtime service.
    target_router.routes.extend(source_routes)
    target_app.openapi_schema = None

    schema = target_app.openapi()
    schema_paths = schema.get("paths", {})
    missing_schema = sorted(
        (path, method)
        for path, method in required
        if method.lower() not in schema_paths.get(path, {})
    )
    if missing_schema:
        formatted = ", ".join(f"{method} {path}" for path, method in missing_schema)
        raise RuntimeError(f"Mounted Runtime routes are absent from OpenAPI: {formatted}")

    return len(source_routes)
