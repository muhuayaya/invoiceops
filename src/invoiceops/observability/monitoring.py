"""Small in-process request counters without storing ticket text or credentials."""

from __future__ import annotations

import math
import time
from collections import Counter, defaultdict, deque
from threading import Lock


def language_slice(text: str) -> str:
    chinese = any("\u4e00" <= char <= "\u9fff" for char in text)
    latin = any(("a" <= char.lower() <= "z") for char in text)
    return "mixed" if chinese and latin else "zh" if chinese else "en"


class Metrics:
    def __init__(self):
        self._lock = Lock()
        self._counts: Counter[tuple[str, str, str, str]] = Counter()
        self._durations: dict[tuple[str, str, str, str], deque[float]] = defaultdict(lambda: deque(maxlen=1000))

    def observe(self, *, model_version: str, source: str, language: str, outcome: str, seconds: float) -> None:
        key = (model_version, source, language, outcome)
        with self._lock:
            self._counts[key] += 1
            self._durations[key].append(seconds)

    def snapshot(self) -> list[dict]:
        with self._lock:
            keys = list(self._counts)
            return [
                {
                    "model_version": key[0], "source": key[1], "language": key[2], "outcome": key[3],
                    "requests": self._counts[key],
                    "latency_seconds": {name: self._percentile(self._durations[key], percentile) for name, percentile in (("p50", 0.50), ("p95", 0.95), ("p99", 0.99))},
                }
                for key in keys
            ]

    @staticmethod
    def _percentile(values, fraction: float) -> float:
        ordered = sorted(values)
        if not ordered:
            return 0.0
        return ordered[min(len(ordered) - 1, max(0, math.ceil(len(ordered) * fraction) - 1))]


class RequestLimiter:
    def __init__(self, *, window_seconds: float = 60.0, max_requests: int = 20, max_keys: int = 10_000):
        self.window_seconds = window_seconds
        self.max_requests = max_requests
        self.max_keys = max_keys
        self._lock = Lock()
        self._requests: dict[str, deque[float]] = {}
        self._checks = 0

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            self._checks += 1
            if self._checks % 256 == 0:
                for stored_key, stored_times in list(self._requests.items()):
                    while stored_times and now - stored_times[0] > self.window_seconds:
                        stored_times.popleft()
                    if not stored_times:
                        self._requests.pop(stored_key, None)

            timestamps = self._requests.get(key)
            if timestamps is None:
                if len(self._requests) >= self.max_keys:
                    return False
                timestamps = deque()
                self._requests[key] = timestamps
            while timestamps and now - timestamps[0] > self.window_seconds:
                timestamps.popleft()
            if not timestamps:
                timestamps.clear()
            if len(timestamps) >= self.max_requests:
                return False
            timestamps.append(now)
            return True
