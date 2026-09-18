from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LLMSuggestion:
    labels: tuple[str, ...]
    rationale: str
    confidence: float
    provider: str
    model: str
    prompt_version: str
    cost_usd: float = 0.0
    error_code: str | None = None


class LLMProvider(Protocol):
    provider: str
    model: str

    def suggest(self, *, sanitized_text: str, allowed_labels: tuple[str, ...], prompt_version: str) -> LLMSuggestion:
        ...


class FakeLLMProvider:
    provider = "fake"
    model = "fake-v1"

    def __init__(self, response: dict[str, object] | None = None) -> None:
        self.response = response or {"labels": ["OTHER_REVIEW"], "rationale": "needs a reviewer", "confidence": 0.5}

    def suggest(self, *, sanitized_text: str, allowed_labels: tuple[str, ...], prompt_version: str) -> LLMSuggestion:
        labels = tuple(str(label) for label in self.response.get("labels", []))
        if not set(labels) <= set(allowed_labels):
            raise ValueError("provider returned an unknown label")
        confidence = float(self.response.get("confidence", 0.0))
        if not 0 <= confidence <= 1:
            raise ValueError("provider returned an invalid confidence")
        return LLMSuggestion(labels, str(self.response.get("rationale", "")), confidence, self.provider, self.model, prompt_version)
