"""Lightweight in-memory sliding-window rate limiting.

Per-instance only: on Cloud Run each instance enforces its own window, which
is enough to cap abuse cost (Gemini calls, log spam) without external
dependencies. Redis-backed cross-instance precision is deliberately not used
because Redis is optional in this deployment (aiogram FSM already falls back
to memory when it is down), and session affinity is enabled in service.tf so
repeat traffic from the same client tends to hit the same instance anyway.
"""

import threading
import time
from collections import deque

# Registry of all limiter instances so tests can reset shared state.
_limiters: list["SlidingWindowRateLimiter"] = []


class SlidingWindowRateLimiter:
    """Thread-safe sliding-window limiter with bounded per-key memory.

    Keys are evicted least-recently-used beyond max_keys, mirroring the
    bounded _user_locks pattern in tools.py.
    """

    def __init__(self, max_requests: int, window_seconds: float, max_keys: int = 10_000):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        _limiters.append(self)

    def allow(self, key: str) -> bool:
        """Record a request for the key; return False if over the limit."""
        now = time.monotonic()
        with self._lock:
            dq = self._hits.pop(key, None)
            if dq is None:
                dq = deque()
            # Re-insert so the dict stays ordered most-recently-used last.
            self._hits[key] = dq
            while dq and now - dq[0] > self.window_seconds:
                dq.popleft()
            if len(dq) >= self.max_requests:
                return False
            dq.append(now)
            while len(self._hits) > self.max_keys:
                self._hits.pop(next(iter(self._hits)))
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


def reset_all() -> None:
    """Clear all limiter state (used by the test suite between tests)."""
    for limiter in _limiters:
        limiter.reset()


# ── Shared limiter instances ──────────────────────────────────────────────────

# Browser-facing unauthenticated endpoints, keyed by client IP.
oauth_callback_limiter = SlidingWindowRateLimiter(max_requests=20, window_seconds=60)
feedback_limiter = SlidingWindowRateLimiter(max_requests=10, window_seconds=60)

# Agent runs (every Gemini call goes through _run_agent), keyed by telegram_id.
agent_run_limiter = SlidingWindowRateLimiter(max_requests=10, window_seconds=60)
