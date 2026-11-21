"""Bounded, observable network calls and peer-health monitoring for VORAX."""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Callable

LOGGER = logging.getLogger("vorax.network")
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_ATTEMPTS = 3
BREAKER_FAILURE_LIMIT = 5
BREAKER_WINDOW_SECONDS = 600
BREAKER_COOLDOWN_SECONDS = 60


class NetworkError(RuntimeError):
    """A bounded remote request failed without exposing credentials."""


class CircuitOpenError(NetworkError):
    """A peer has exceeded the configured rolling failure threshold."""


class CircuitBreaker:
    def __init__(
        self,
        *,
        failure_limit: int = BREAKER_FAILURE_LIMIT,
        window_seconds: int = BREAKER_WINDOW_SECONDS,
        cooldown_seconds: int = BREAKER_COOLDOWN_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.failure_limit = failure_limit
        self.window_seconds = window_seconds
        self.cooldown_seconds = cooldown_seconds
        self.clock = clock
        self._failures: deque[float] = deque()
        self._open_until = 0.0
        self._lock = threading.Lock()

    def before_request(self) -> None:
        now = self.clock()
        with self._lock:
            if self._open_until and now < self._open_until:
                raise CircuitOpenError("Network circuit is open")
            if self._open_until and now >= self._open_until:
                self._open_until = 0.0
                self._failures.clear()

    def record_success(self) -> None:
        with self._lock:
            self._failures.clear()
            self._open_until = 0.0

    def record_failure(self) -> None:
        now = self.clock()
        with self._lock:
            cutoff = now - self.window_seconds
            while self._failures and self._failures[0] < cutoff:
                self._failures.popleft()
            self._failures.append(now)
            if len(self._failures) >= self.failure_limit:
                self._open_until = now + self.cooldown_seconds

    def snapshot(self) -> dict:
        now = self.clock()
        with self._lock:
            cutoff = now - self.window_seconds
            recent = [stamp for stamp in self._failures if stamp >= cutoff]
            return {
                "failures_in_window": len(recent),
                "failure_limit": self.failure_limit,
                "window_seconds": self.window_seconds,
                "open": bool(self._open_until and now < self._open_until),
                "retry_after_seconds": max(0, int(self._open_until - now)),
            }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    headers: dict | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    attempts: int = DEFAULT_ATTEMPTS,
    breaker: CircuitBreaker | None = None,
    opener: Callable = urllib.request.urlopen,
    sleeper: Callable[[float], None] = time.sleep,
    max_response_bytes: int = 3 * 1024 * 1024,
    include_headers: bool = False,
) -> dict | list | tuple[dict | list, dict]:
    """Request JSON with a 30s cap, three tries, exponential backoff and redacted logs."""
    if not isinstance(url, str) or not url.startswith("https://"):
        raise NetworkError("VORAX network requests require an HTTPS URL")
    if timeout < 1 or timeout > DEFAULT_TIMEOUT_SECONDS:
        raise ValueError("Network timeout must be between 1 and 30 seconds")
    if attempts < 1 or attempts > DEFAULT_ATTEMPTS:
        raise ValueError("Network attempts must be between 1 and 3")
    if isinstance(max_response_bytes, bool) or not isinstance(max_response_bytes, int):
        raise ValueError("max_response_bytes must be an integer")
    if max_response_bytes < 1 or max_response_bytes > 3 * 1024 * 1024:
        raise ValueError("max_response_bytes must be between 1 byte and 3 MB")
    request_id = uuid.uuid4().hex
    request_headers = {
        "Accept": "application/json",
        "User-Agent": "VORAX-Network/1.0",
    }
    if headers:
        request_headers.update({str(key): str(value) for key, value in headers.items()})
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    if data is not None:
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method.upper())
    if breaker is None:
        breaker = _host_breaker(url)
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            if breaker:
                breaker.before_request()
            with opener(request, timeout=timeout) as response:
                raw = response.read(max_response_bytes + 1)
                if len(raw) > max_response_bytes:
                    raise NetworkError("Remote JSON response exceeded its configured size limit")
                value = json.loads(raw.decode("utf-8"))
                if not isinstance(value, (dict, list)):
                    raise NetworkError("Remote service returned an unsupported JSON response")
                status = getattr(response, "status", 200)
                response_headers = dict(getattr(response, "headers", {}) or {})
                if breaker:
                    breaker.record_success()
                LOGGER.info(json.dumps({
                    "event": "network_request",
                    "request_id": request_id,
                    "timestamp": _utc_now(),
                    "method": method.upper(),
                    "host": urllib.parse.urlparse(url).netloc,
                    "status": status,
                    "attempt": attempt,
                }))
                return (value, response_headers) if include_headers else value
        except CircuitOpenError:
            raise
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            last_error = NetworkError(f"Remote service returned HTTP {status}")
            retryable = status in {408, 425, 429} or status >= 500
            if breaker:
                # A normal 4xx response still proves the peer is reachable. Count
                # only transport/rate-limit/server failures toward the open circuit.
                if retryable:
                    breaker.record_failure()
                else:
                    breaker.record_success()
            LOGGER.warning(json.dumps({
                "event": "network_request",
                "request_id": request_id,
                "timestamp": _utc_now(),
                "method": method.upper(),
                "host": urllib.parse.urlparse(url).netloc,
                "status": status,
                "attempt": attempt,
            }))
            if not retryable:
                break
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, NetworkError) as exc:
            last_error = exc if isinstance(exc, NetworkError) else NetworkError(type(exc).__name__)
            if breaker:
                breaker.record_failure()
            LOGGER.warning(json.dumps({
                "event": "network_request",
                "request_id": request_id,
                "timestamp": _utc_now(),
                "method": method.upper(),
                "host": urllib.parse.urlparse(url).netloc,
                "status": "transport_error",
                "error": type(exc).__name__,
                "attempt": attempt,
            }))
        if attempt < attempts:
            sleeper(float(2 ** (attempt - 1)))

    raise NetworkError(str(last_error or "Remote request failed")) from last_error


_HOST_BREAKERS: dict[str, CircuitBreaker] = {}
_HOST_BREAKERS_LOCK = threading.Lock()


def _host_breaker(url: str) -> CircuitBreaker:
    host = urllib.parse.urlparse(url).netloc.casefold()
    with _HOST_BREAKERS_LOCK:
        breaker = _HOST_BREAKERS.get(host)
        if breaker is None:
            breaker = CircuitBreaker()
            _HOST_BREAKERS[host] = breaker
        return breaker


class PeerHealthMonitor:
    """Poll one trusted peer every 60 seconds; failure remains visible."""

    def __init__(self, url: str, peer_name: str, *, interval_seconds: int = 60) -> None:
        if not url.startswith("https://"):
            raise ValueError("Peer health URL must use HTTPS")
        self.url = url.rstrip("/")
        self.peer_name = peer_name
        self.interval_seconds = max(10, int(interval_seconds))
        self.breaker = CircuitBreaker()
        self._state = {
            "peer": peer_name,
            "live": False,
            "status": "not_checked",
            "last_checked": None,
            "last_error": None,
        }
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def check_once(self) -> dict:
        try:
            response = request_json(self.url, timeout=10, attempts=1, breaker=self.breaker)
            if response.get("status") != "ok" or response.get("service") != self.peer_name:
                raise NetworkError("Peer health identity did not match")
            state = {
                "peer": self.peer_name,
                "live": True,
                "status": "healthy",
                "last_checked": _utc_now(),
                "last_error": None,
            }
        except Exception as exc:
            state = {
                "peer": self.peer_name,
                "live": False,
                "status": "unavailable",
                "last_checked": _utc_now(),
                "last_error": type(exc).__name__,
                "circuit": self.breaker.snapshot(),
            }
        with self._lock:
            self._state = state
        return dict(state)

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._state)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.check_once()
        self._thread = threading.Thread(target=self._run, name=f"vorax-{self.peer_name}-health", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self.check_once()
