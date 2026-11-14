from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

# Failures that say "this provider is unhealthy", as opposed to a bad request.
_PROVIDER_FAILURES = frozenset({"server_error", "timeout", "connection"})


@dataclass
class _State:
    failures: list[float] = field(default_factory=list)
    opened_until: float = 0.0


class ProviderHealth:
    """Per-provider circuit breaker (in-process; each serverless instance keeps its own).

    - N failures within a window open the circuit for `open_s` seconds.
    - An auth error (bad or revoked key) opens it immediately for `auth_open_s`.
    - A 429 with Retry-After cools the provider down for that long (capped).
    - After the cooldown the circuit is half-open: the next failure reopens it.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        window_s: float = 60.0,
        open_s: float = 30.0,
        auth_open_s: float = 300.0,
        max_retry_after_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._threshold = failure_threshold
        self._window = window_s
        self._open_s = open_s
        self._auth_open_s = auth_open_s
        self._max_ra = max_retry_after_s
        self._clock = clock
        self._state: dict[str, _State] = {}

    def record(self, provider: str, outcome_class: str, retry_after_s: float | None = None) -> str | None:
        """Record an attempt outcome. Returns a reason string if the circuit just opened."""
        now = self._clock()
        st = self._state.setdefault(provider, _State())

        if outcome_class == "ok":
            st.failures.clear()
            return None

        if outcome_class == "auth_error":
            return self._open(st, now, self._auth_open_s, "authentication failed (bad or revoked key)")

        if outcome_class == "rate_limit" and retry_after_s:
            wait = min(retry_after_s, self._max_ra)
            return self._open(st, now, wait, f"rate limited, retry after {wait:g}s")

        if outcome_class in _PROVIDER_FAILURES:
            st.failures = [t for t in st.failures if now - t <= self._window] + [now]
            if len(st.failures) >= self._threshold:
                return self._open(st, now, self._open_s, f"{len(st.failures)} failures in {self._window:g}s")
        return None

    def _open(self, st: _State, now: float, seconds: float, reason: str) -> str | None:
        was_open = st.opened_until > now
        st.opened_until = max(st.opened_until, now + seconds)
        # Half-open afterwards: one more failure reopens immediately.
        st.failures = [now] * max(0, self._threshold - 1)
        return None if was_open else reason

    def is_open(self, provider: str) -> bool:
        st = self._state.get(provider)
        return bool(st and st.opened_until > self._clock())

    def open_providers(self) -> set[str]:
        now = self._clock()
        return {p for p, st in self._state.items() if st.opened_until > now}
