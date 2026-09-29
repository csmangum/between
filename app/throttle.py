"""In-memory login throttle. Two accounts, one process: a dict is enough."""

from __future__ import annotations

import time
from threading import Lock

FREE_ATTEMPTS = 3
BASE_DELAY = 2.0
MAX_DELAY = 15 * 60.0
FORGET_AFTER = 60 * 60.0


class LoginThrottle:
    def __init__(self) -> None:
        self._lock = Lock()
        # key -> (failures, locked_until, last_failure)
        self._state: dict[str, tuple[int, float, float]] = {}

    def _prune(self, now: float) -> None:
        stale = [k for k, (_, _, last) in self._state.items() if now - last > FORGET_AFTER]
        for k in stale:
            del self._state[k]

    def retry_after(self, keys: list[str]) -> float:
        """Seconds the caller must still wait; 0 when an attempt is allowed."""
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            waits = [locked - now for k in keys for (_, locked, _) in [self._state.get(k, (0, 0.0, 0.0))]]
        return max([0.0, *waits])

    def failed(self, keys: list[str]) -> float:
        now = time.monotonic()
        longest = 0.0
        with self._lock:
            for k in keys:
                failures, _, _ = self._state.get(k, (0, 0.0, 0.0))
                failures += 1
                delay = 0.0
                if failures > FREE_ATTEMPTS:
                    delay = min(MAX_DELAY, BASE_DELAY * 2 ** (failures - FREE_ATTEMPTS - 1))
                self._state[k] = (failures, now + delay, now)
                longest = max(longest, delay)
        return longest

    def succeeded(self, keys: list[str]) -> None:
        with self._lock:
            for k in keys:
                self._state.pop(k, None)

    def reset(self) -> None:
        with self._lock:
            self._state.clear()


login_throttle = LoginThrottle()
