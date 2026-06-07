"""Tiny thread-safe TTL cache for the compute-heavy read endpoints.

The fleet dashboard rollup and the TF-IDF similarity search both recompute
from scratch on every request and serialize on the GIL under concurrency —
so a burst of concurrent/repeat reads (a fleet ops team watching the
dashboard) piles up. Caching the computed result for a few seconds lets
those reads share one computation (~1s -> ~microseconds on a hit).

Freshness: a new capture (ingest) or a resolution edit calls clear(), so the
next read recomputes — the dashboard stays current right after an edit. The
TTL is only a backstop for changes that don't flow through those paths (e.g.
the directory scanner indexing a local MCAP).
"""

from __future__ import annotations

import threading
import time
from typing import Any


class TTLCache:
    def __init__(self, ttl_seconds: float = 15.0) -> None:
        self._ttl = ttl_seconds
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self._key_locks: dict[str, threading.Lock] = {}

    def get(self, key: str) -> Any | None:
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return None
            expires_at, value = item
            if time.monotonic() >= expires_at:
                self._store.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._store[key] = (time.monotonic() + self._ttl, value)

    def clear(self) -> None:
        """Drop everything — called on writes so reads recompute fresh."""
        with self._lock:
            self._store.clear()

    def _key_lock(self, key: str) -> threading.Lock:
        with self._lock:
            lk = self._key_locks.get(key)
            if lk is None:
                lk = threading.Lock()
                self._key_locks[key] = lk
            return lk

    def get_or_compute(self, key: str, compute: Any) -> Any:
        """Single-flight: on a cache miss, exactly ONE caller runs ``compute``
        while concurrent callers for the same key wait, then read the
        freshly-cached value — instead of every concurrent reader recomputing
        at once (the thundering-herd that spikes p95 right after a clear).
        ``compute`` may raise (e.g. a 404); the exception propagates and is
        not cached."""
        value = self.get(key)
        if value is not None:
            return value
        with self._key_lock(key):
            value = self.get(key)  # re-check: a peer may have filled it while we waited
            if value is not None:
                return value
            value = compute()
            self.set(key, value)
            return value
