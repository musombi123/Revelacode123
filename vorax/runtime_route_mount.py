"""Mount FastAPI routes and refresh the target OpenAPI schema."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from fastapi import APIRouter


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
    """Include source FastAPI routes and rebuild the target OpenAPI schema.

    FastAPI route registration and OpenAPI generation are separate concerns.
    A target app may already have cached its OpenAPI schema before a router is
    included (for example, during framework initialization). Including routes
    does not reliably invalidate that cached document. We therefore use the
    standard include_router API and explicitly clear openapi_schema before
    verifying the generated schema.

    The operation fails closed on missing source routes, route collisions, or
    missing OpenAPI operations. This prevents a partially exposed Runtime API
    from being treated as a successful Space startup.
    """
    source_router = getattr(source_app, "router", None)
    target_router = getattr(target_app, "router", None)
    include_router = getattr(target_app, "include_router", None)
    if source_router is None or target_router is None or not callable(include_router):
        raise TypeError("Both applications must expose compatible FastAPI routers")

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

    # Use FastAPI's supported router inclusion path so route metadata,
    # dependency graphs, validation and response models are registered by
    # FastAPI itself.
    runtime_router = APIRouter()
    runtime_router.routes.extend(source_routes)
    include_router(runtime_router)

    # The target may have cached OpenAPI before route inclusion. Force the next
    # openapi() call to rebuild from the complete route table.
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
        raise RuntimeError(f"Included Runtime routes are absent from OpenAPI: {formatted}")

    return len(source_routes)
