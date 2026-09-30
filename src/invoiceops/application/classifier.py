"""Shared single-ticket and batch classification decision logic."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, Sequence


class ModelUnavailable(RuntimeError):
    """Neither the primary nor an approved fallback can make a prediction."""


class InvalidText(ValueError):
    pass


class PredictionModel(Protocol):
    labels: Sequence[str]
    thresholds: Sequence[float]
    version: str
    threshold_version: str
    engineering_approved: bool
    serving_allowed: bool

    def predict_proba(self, texts: Sequence[str]): ...


@dataclass(frozen=True)
class ClassificationResult:
    probabilities: dict[str, float]
    labels: tuple[str, ...]
    model_version: str
    threshold_version: str
    taxonomy_version: str
    degraded: bool
    fallback_reason: str | None
    review_status: str
    review_reasons: tuple[str, ...]
    redacted_text: str


def redact_text(text: str) -> str:
    """Mask common direct identifiers before storage and inference."""
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[EMAIL]", text)
    text = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "[PHONE]", text)
    text = re.sub(r"(?<!\d)\d{12,19}(?!\d)", "[ACCOUNT]", text)
    return text


def _scores(model: PredictionModel, text: str, expected_labels: Sequence[str]) -> tuple[float, ...]:
    if tuple(model.labels) != tuple(expected_labels) or len(model.thresholds) != 8:
        raise ValueError("model taxonomy mismatch")
    values = model.predict_proba([text])[0]
    if len(values) != 8:
        raise ValueError("model must return eight probabilities")
    scores = tuple(float(value) for value in values)
    if any(not 0.0 <= value <= 1.0 for value in scores):
        raise ValueError("model returned invalid probability")
    return scores


def _model_is_usable(model: PredictionModel | None) -> bool:
    """A model is usable when it may serve (serving_allowed) or is engineering-approved."""
    if model is None:
        return False
    return bool(getattr(model, "serving_allowed", getattr(model, "engineering_approved", False)))


def classify(
    text: str,
    *,
    primary: PredictionModel | None,
    fallback: PredictionModel | None,
    label_order: Sequence[str],
    taxonomy_version: str,
    max_text_length: int = 10_000,
    low_confidence_margin: float = 0.05,
) -> ClassificationResult:
    if not isinstance(text, str) or not text.strip():
        raise InvalidText("text must not be empty")
    if len(text) > max_text_length:
        raise InvalidText("text exceeds maximum length")
    if len(label_order) != 8 or len(set(label_order)) != 8:
        raise ValueError("eight distinct labels required")
    redacted = redact_text(text.strip())

    degraded = False
    failure: str | None = None
    selected = primary
    try:
        if not _model_is_usable(primary):
            raise ModelUnavailable("primary model is not approved or available")
        scores = _scores(primary, redacted, label_order)
    except (Exception) as exc:
        failure = type(exc).__name__
        if not _model_is_usable(fallback):
            raise ModelUnavailable("no approved model is available") from exc
        try:
            scores = _scores(fallback, redacted, label_order)
        except Exception as fallback_error:
            raise ModelUnavailable("both models are unavailable") from fallback_error
        selected = fallback
        degraded = True

    assert selected is not None
    thresholds = tuple(float(value) for value in selected.thresholds)
    labels = tuple(label for label, score, threshold in zip(label_order, scores, thresholds) if score >= threshold)
    reasons: list[str] = []
    if "SUPPLIER_MASTER_CHANGE" in labels:
        reasons.append("supplier_master_change")
    if not labels:
        reasons.append("no_label")
    if any(abs(score - threshold) <= low_confidence_margin for score, threshold in zip(scores, thresholds)):
        reasons.append("low_confidence")
    if degraded:
        reasons.append("model_fallback")
    return ClassificationResult(
        probabilities=dict(zip(label_order, scores)),
        labels=labels,
        model_version=selected.version,
        threshold_version=selected.threshold_version,
        taxonomy_version=taxonomy_version,
        degraded=degraded,
        fallback_reason=failure,
        review_status="pending_review" if reasons else "not_required",
        review_reasons=tuple(reasons),
        redacted_text=redacted,
    )
