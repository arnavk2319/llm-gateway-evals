"""Replay of recent responses for repeated Idempotency-Key requests, bounded in size and scoped to the caller.

Two properties matter and the first version of this cache had neither:

- It is scoped to the credential that made the request. The key is chosen by the caller, so without that a second
  user who sends the same key (`1` is a likely guess) would be handed the first user's response.
- It cannot grow without limit: entries expire, there is a maximum count, and very long keys or very large
  responses are simply not cached.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

MAX_KEY_CHARS = 200
MAX_BODY_BYTES = 256 * 1024


def namespace(authorization: str | None) -> str:
    """A stable, non-reversible tag for 'this credential' (or 'no credential' when auth is off)."""
    return hashlib.sha256((authorization or "").encode()).hexdigest()[:24]


class IdempotencyCache:
    def __init__(self, ttl_s: float, max_entries: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl_s
        self._max = max(1, max_entries)
        self._clock = clock
        # Insertion order is expiry order (one TTL for everyone), so expired entries are always at the front.
        self._items: OrderedDict[tuple[str, str], tuple[float, dict[str, Any], dict[str, str]]] = OrderedDict()

    def __len__(self) -> int:
        return len(self._items)

    def _expire(self) -> None:
        now = self._clock()
        while self._items:
            first = next(iter(self._items.values()))
            if first[0] > now:
                break
            self._items.popitem(last=False)

    def get(self, ns: str, key: str) -> tuple[dict[str, Any], dict[str, str]] | None:
        if len(key) > MAX_KEY_CHARS:
            return None
        self._expire()
        hit = self._items.get((ns, key))
        return (hit[1], hit[2]) if hit else None

    def put(self, ns: str, key: str, body: dict[str, Any], headers: dict[str, str]) -> bool:
        """Remember a response. False when it was not kept (key too long, or the response too large)."""
        if len(key) > MAX_KEY_CHARS or len(json.dumps(body, default=str)) > MAX_BODY_BYTES:
            return False
        self._expire()
        self._items.pop((ns, key), None)
        while len(self._items) >= self._max:
            self._items.popitem(last=False)  # the oldest goes first
        self._items[(ns, key)] = (self._clock() + self._ttl, body, headers)
        return True
