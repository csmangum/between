from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class FailureWindow:
    """Counts recent failures per key inside a sliding window. In-process only."""

    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        hits = self._hits[key]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if not hits:
            self._hits.pop(key, None)
        return hits

    def blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._prune(key, time.monotonic())) >= self.limit

    def record_failure(self, key: str) -> None:
        with self._lock:
            now = time.monotonic()
            self._prune(key, now)
            self._hits[key].append(now)

    def clear(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


class LoginThrottle:
    """Slows password guessing: a small budget per client address, a larger one per name."""

    def __init__(self, ip_limit: int, user_limit: int, window_seconds: int) -> None:
        self.by_ip = FailureWindow(ip_limit, window_seconds)
        self.by_user = FailureWindow(user_limit, window_seconds)

    def blocked(self, ip: str, username: str) -> bool:
        return self.by_ip.blocked(ip) or self.by_user.blocked(username)

    def record_failure(self, ip: str, username: str) -> None:
        self.by_ip.record_failure(ip)
        self.by_user.record_failure(username)

    def record_success(self, ip: str, username: str) -> None:
        self.by_ip.clear(ip)
        self.by_user.clear(username)

    def reset(self) -> None:
        self.by_ip.reset()
        self.by_user.reset()
