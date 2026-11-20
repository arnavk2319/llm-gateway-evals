"""Load awareness for the router: what is in flight, how fast we are calling each provider, and how many retries we
may spend.

Everything here is in-process and synchronous (no awaits), so a check followed immediately by an acquire cannot be
interleaved with another request. The gateway runs as one process per environment; if it ever scales out, each
instance enforces its own share of a provider's limits, so set `max_concurrency` and `rpm_limit` per instance.

Three protections, from the narrowest to the widest:

- per provider: a cap on concurrent calls and on calls per minute (a provider's published limits), so one busy or
  slow source is not buried and does not drag everything else down with it;
- the whole process: a cap on requests in flight, so a flood is answered with a quick 503 and `Retry-After`
  instead of queueing until memory or latency collapses;
- retries: a budget that refills as a fraction of ordinary traffic, so when a source starts failing the fallbacks
  cannot multiply load on the next one (a retry storm).
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from typing import Any

WINDOW_S = 60.0


class Slot:
    """One in-flight call to a provider. Release it exactly once; releasing twice is harmless."""

    def __init__(self, release: Callable[[], None]) -> None:
        self._release = release
        self.released = False

    def release(self) -> None:
        if not self.released:
            self.released = True
            self._release()


class LoadTracker:
    def __init__(self, global_cap: int = 0, clock: Callable[[], float] = time.monotonic) -> None:
        self._cap = max(0, global_cap)  # 0 means unlimited
        self._clock = clock
        self._global = 0
        self._inflight: dict[str, int] = {}
        self._calls: dict[str, deque[float]] = {}  # start times of recent calls, for the per-minute budget
        self.shed_global = 0
        self.shed_provider: dict[str, int] = {}

    # ---- the whole process ------------------------------------------------------------------------------

    def enter(self) -> bool:
        """Admit one request, or refuse it because the process is full."""
        if self._cap and self._global >= self._cap:
            self.shed_global += 1
            return False
        self._global += 1
        return True

    def leave(self) -> None:
        self._global = max(0, self._global - 1)

    @property
    def global_inflight(self) -> int:
        return self._global

    # ---- one provider -----------------------------------------------------------------------------------

    def inflight(self, provider: str) -> int:
        return self._inflight.get(provider, 0)

    def _recent(self, provider: str, now: float) -> deque[float]:
        q = self._calls.setdefault(provider, deque())
        while q and now - q[0] >= WINDOW_S:
            q.popleft()
        return q

    def has_capacity(self, provider: str, spec: Any) -> bool:
        cap = getattr(spec, "max_concurrency", None)
        if cap and self.inflight(provider) >= cap:
            return False
        rpm = getattr(spec, "rpm_limit", None)
        return not (rpm and len(self._recent(provider, self._clock())) >= rpm)

    def acquire(self, provider: str, spec: Any) -> Slot | None:
        """Take a slot for a call that is about to start, or None when the provider is at a limit."""
        if not self.has_capacity(provider, spec):
            return None
        self._inflight[provider] = self.inflight(provider) + 1
        self._recent(provider, self._clock()).append(self._clock())

        def release() -> None:
            self._inflight[provider] = max(0, self.inflight(provider) - 1)

        return Slot(release)

    def saturated(self, providers: dict[str, Any]) -> set[str]:
        return {name for name, spec in providers.items() if not self.has_capacity(name, spec)}

    def retry_after(self, provider: str, spec: Any) -> float:
        """Seconds until this provider could take another call (at least one)."""
        now = self._clock()
        wait = 1.0
        rpm = getattr(spec, "rpm_limit", None)
        if rpm:
            q = self._recent(provider, now)
            if len(q) >= rpm:
                wait = max(wait, q[0] + WINDOW_S - now)
        return wait

    def load_factor(self, provider: str, spec: Any) -> float:
        """0 when idle up to 1 when full. Without a configured cap, 20 calls in flight counts as full."""
        cap = getattr(spec, "max_concurrency", None) or 20
        return min(1.0, self.inflight(provider) / cap)

    def note_shed(self, providers: set[str]) -> None:
        for p in providers:
            self.shed_provider[p] = self.shed_provider.get(p, 0) + 1

    def snapshot(self) -> dict[str, Any]:
        now = self._clock()
        return {
            "global_inflight": self._global,
            "global_cap": self._cap or None,
            "shed_global": self.shed_global,
            "providers": {
                p: {
                    "inflight": self.inflight(p),
                    "calls_last_minute": len(self._recent(p, now)),
                    "shed": self.shed_provider.get(p, 0),
                }
                for p in sorted(set(self._inflight) | set(self._calls) | set(self.shed_provider))
            },
        }


class RetryBudget:
    """Retries may use at most `ratio` of ordinary traffic, plus a small burst so a quiet system can still retry.

    Every request deposits `ratio` of a token (up to the burst size); every fallback or retry spends one. When it is
    empty the request fails fast instead of piling onto another provider. When a provider is down for good, the
    circuit breaker stops sending it traffic, so the budget only has to cover the few requests that find out first.
    """

    def __init__(self) -> None:
        self._tokens: float | None = None
        self.denied = 0

    def _level(self, burst: int) -> float:
        if self._tokens is None:
            self._tokens = float(burst)
        return self._tokens

    def deposit(self, ratio: float, burst: int) -> None:
        self._tokens = min(float(burst), self._level(burst) + ratio)

    def try_spend(self, burst: int) -> bool:
        level = self._level(burst)
        if level >= 1.0:
            self._tokens = level - 1.0
            return True
        self.denied += 1
        return False

    @property
    def tokens(self) -> float:
        return self._tokens or 0.0
