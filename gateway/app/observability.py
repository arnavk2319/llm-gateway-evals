"""Sentry error reporting. Optional: does nothing unless SENTRY_DSN is set.

Every report is tagged with the same ids the gateway stamps on its own logs
(run_id, span_id, model, provider, policy_version, error_class, ...), so an error in
Sentry can be matched to its row in the gateway's log stream and vice versa.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .config import Settings
from .events import LogEvent

log = logging.getLogger("gateway.observability")

# Log kinds that are worth an alert-grade Sentry event. Everything else stays in the log stream.
REPORTED_KINDS = frozenset({"request_failed", "circuit_opened", "internal_error"})
_SENSITIVE_HEADERS = frozenset({"authorization", "x-api-key", "cookie", "set-cookie", "proxy-authorization"})


def scrub_event(event: dict[str, Any], hint: Any = None) -> dict[str, Any] | None:
    """Never ship prompts, credentials or cookies to Sentry."""
    req = event.get("request")
    if isinstance(req, dict):
        req.pop("data", None)
        req.pop("cookies", None)
        req.pop("query_string", None)
        headers = req.get("headers")
        if isinstance(headers, dict):
            req["headers"] = {k: v for k, v in headers.items() if k.lower() not in _SENSITIVE_HEADERS}
    return event


def init_sentry(settings: Settings, **sdk_overrides: Any) -> bool:
    if not settings.sentry_dsn:
        return False
    import sentry_sdk

    options: dict[str, Any] = {
        "dsn": settings.sentry_dsn,
        "environment": settings.environment,
        "release": os.environ.get("VERCEL_GIT_COMMIT_SHA") or os.environ.get("RENDER_GIT_COMMIT"),
        "traces_sample_rate": settings.sentry_traces_sample_rate,
        # No user identity and no request bodies (prompts) from integrations; scrub_event
        # additionally strips credentials, cookies and query strings.
        "data_collection": {"user_info": False, "http_bodies": []},
        "before_send": scrub_event,
    }
    options.update(sdk_overrides)
    sentry_sdk.init(**options)
    branch = os.environ.get("VERCEL_GIT_COMMIT_REF") or os.environ.get("RENDER_GIT_BRANCH")
    if branch:
        sentry_sdk.set_tag("git_branch", branch)  # tells `dev` from `main` and from feature previews
    return True


def sentry_active() -> bool:
    try:
        import sentry_sdk

        return sentry_sdk.is_initialized()
    except ImportError:
        return False


def capture(
    message: str,
    *,
    tags: dict[str, Any],
    fingerprint: list[str] | None = None,
    level: str = "error",
    exc: BaseException | None = None,
    extra: dict[str, Any] | None = None,
) -> str | None:
    """Send one event to Sentry. Returns its event id, or None if Sentry is off."""
    if not sentry_active():
        return None
    import sentry_sdk

    try:
        with sentry_sdk.new_scope() as scope:
            for k, v in tags.items():
                scope.set_tag(k, str(v))
            if fingerprint:
                scope.fingerprint = fingerprint
            if extra:
                scope.set_context("gateway", extra)
            if exc is not None:
                return sentry_sdk.capture_exception(exc)
            return sentry_sdk.capture_message(message, level=level)  # type: ignore[arg-type]
    except Exception:  # reporting must never break a request
        log.exception("sentry capture failed")
        return None


def flush(timeout: float = 2.0) -> None:
    """Deliver queued Sentry events now (serverless processes can be frozen right after a response)."""
    if not sentry_active():
        return
    try:
        import sentry_sdk

        sentry_sdk.flush(timeout=timeout)
    except Exception:
        log.debug("sentry flush failed", exc_info=True)


def report_logs(logs: list[LogEvent]) -> None:
    """Report the alert-worthy log events and stamp each with its Sentry event id."""
    if not sentry_active():
        return
    for e in logs:
        is_key_failure = e.kind == "attempt_failed" and e.tags.get("error_class") == "auth_error"
        if e.kind not in REPORTED_KINDS and not is_key_failure:
            continue
        tags = {**e.tags, "kind": e.kind, "source": e.source}
        if e.run_id:
            tags["run_id"] = e.run_id
        if e.span_id:
            tags["span_id"] = e.span_id
        event_id = capture(
            e.message,
            tags=tags,
            fingerprint=[e.fingerprint] if e.fingerprint else None,
            level="fatal" if e.severity == "critical" else "error",
            extra=e.payload,
        )
        if event_id:
            e.tags["sentry_event_id"] = event_id
