from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter

from .provider import LLMProvider, LLMSuggestion


@dataclass
class CircuitBreaker:
    failure_limit: int = 3
    failures: int = 0
    opened: bool = False

    def record_success(self) -> None:
        self.failures = 0
        self.opened = False

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.failure_limit:
            self.opened = True


@dataclass
class LLMPolicy:
    enabled: bool = False
    budget_usd: float = 0.0
    spent_usd: float = 0.0
    timeout_seconds: float = 8.0
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)

    def request(self, provider: LLMProvider, *, sanitized: bool, text: str, allowed_labels: tuple[str, ...], prompt_version: str) -> LLMSuggestion | None:
        if not self.enabled or not sanitized or self.breaker.opened or self.spent_usd >= self.budget_usd:
            return None
        for _attempt in range(2):
            started = perf_counter()
            try:
                suggestion = provider.suggest(sanitized_text=text, allowed_labels=allowed_labels, prompt_version=prompt_version)
                elapsed = perf_counter() - started
                if elapsed >= self.timeout_seconds:
                    raise TimeoutError("LLM request exceeded timeout")
                if self.spent_usd + suggestion.cost_usd > self.budget_usd:
                    raise ValueError("LLM budget exceeded")
                self.spent_usd += suggestion.cost_usd
                self.breaker.record_success()
                return suggestion
            except Exception:
                self.breaker.record_failure()
                if self.breaker.opened:
                    break
        return None
