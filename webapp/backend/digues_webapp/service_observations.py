"""Small process-local transport observations. Never retain URLs, payloads or errors."""

from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Lock
import time

import requests

_lock = Lock()
_observations = {}
SERVICES = ("meteofrance", "vigicrues", "hubeau", "topage")


@contextmanager
def observe(service):
    if service not in SERVICES:
        raise ValueError("Unknown observation source")
    started = time.monotonic()
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        item = _observations.setdefault(service, {})
        item["last_attempt"] = now
        item["in_flight"] = item.get("in_flight", 0) + 1
    error_code = None
    try:
        yield
    except Exception as error:
        error_code = "request_failed"
        # Transport exceptions may have been wrapped in a provider exception.
        for _ in range(4):
            if isinstance(error, requests.Timeout):
                error_code = "timeout"
                break
            error = getattr(error, "__cause__", None)
        raise
    finally:
        with _lock:
            item = _observations[service]
            item["in_flight"] -= 1
            item["last_latency_ms"] = round((time.monotonic() - started) * 1000)
            item["last_result"] = "error" if error_code else "ok"
            if error_code:
                item["last_error_code"] = error_code
            item["last_failure" if error_code else "last_success"] = datetime.now(timezone.utc).isoformat()


def snapshot():
    """No active probe. Unknown is not success; old success isn't current availability."""
    now = datetime.now(timezone.utc)
    with _lock:
        result = {name: dict(_observations.get(name, {})) for name in SERVICES}
    for item in result.values():
        item["status"] = item.get("last_result", "unknown")
        if item["status"] == "ok":
            age = (now - datetime.fromisoformat(item["last_success"])).total_seconds()
            if age > 86400:
                item["status"] = "degraded"
        item["check"] = "Dernier transport observé dans ce processus ; pas un probe fournisseur ni une validation des données."
    return result
