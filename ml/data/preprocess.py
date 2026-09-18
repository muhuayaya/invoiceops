"""Deterministic text and metadata preprocessing for InvoiceOps v1.

The functions in this module deliberately use standard-library regular
expressions.  They are a PoC gate, not a claim that a production PII detector
has been solved for every supplier format.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Any, Mapping


ALLOWED_METADATA_KEYS = frozenset(
    {
        "channel",
        "source_system",
        "supplier_context_group",
        "country_code",
        "currency_code",
        "business_unit",
        "language_hint",
    }
)

_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ -]?[A-Z0-9]){11,30}\b", re.I)
_BANK_ACCOUNT = re.compile(r"(?<![A-Za-z0-9])\d(?:[ -]?\d){7,22}(?![A-Za-z0-9])")
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d ()-]{7,}\d)(?!\w)")
_INVOICE_ID = re.compile(
    r"(?i)(?:(?:invoice|inv|发票(?:号|编号)?)[ #:\-]*[A-Z0-9][A-Z0-9\-/]{3,})"
)
_PO_ID = re.compile(r"(?i)PO[ #:\-]*[A-Z0-9][A-Z0-9\-/]{2,}")
_SUPPLIER_ID = re.compile(
    r"(?i)(?:(?:supplier|vendor|供应商)[ #:\-]*(?:id[ #:\-]*)?[A-Z0-9][A-Z0-9\-/]{2,})"
)
_REDACTION_TOKEN = re.compile(r"<[A-Z_]+>")


@dataclass(frozen=True)
class RedactionResult:
    text: str
    redactions: tuple[str, ...]


def normalize_text(text: str) -> str:
    """Apply Unicode normalization and whitespace normalization only."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    normalized = unicodedata.normalize("NFKC", text)
    normalized = re.sub(r"[\u0000-\u001f\u007f]", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def detect_language(text: str) -> str:
    """Return ``zh``, ``en``, ``mixed``, or ``unknown`` deterministically."""
    normalized = normalize_text(text)
    has_zh = bool(re.search(r"[\u3400-\u9fff]", normalized))
    has_en = bool(re.search(r"[A-Za-z]", normalized))
    if has_zh and has_en:
        return "mixed"
    if has_zh:
        return "zh"
    if has_en:
        return "en"
    return "unknown"


def redact_text(text: str) -> RedactionResult:
    """Mask the PII patterns required by the stage-2 contract.

    The replacement tokens are stable model vocabulary and the returned
    redaction types make the result auditable without retaining matched text.
    """
    value = normalize_text(text)
    redactions: list[str] = []

    def replace(pattern: re.Pattern[str], token: str, kind: str, current: str) -> str:
        nonlocal redactions
        current, count = pattern.subn(token, current)
        redactions.extend([kind] * count)
        return current

    value = replace(_EMAIL, "<EMAIL>", "email", value)
    value = replace(_IBAN, "<IBAN>", "iban", value)
    value = replace(_PHONE, "<PHONE>", "phone", value)
    value = replace(_BANK_ACCOUNT, "<BANK_ACCOUNT>", "bank_account", value)
    value = replace(_INVOICE_ID, "<INVOICE_ID>", "invoice_id", value)
    value = replace(_PO_ID, "<PO_ID>", "purchase_order_id", value)
    value = replace(_SUPPLIER_ID, "<SUPPLIER_ID>", "supplier_id", value)
    return RedactionResult(normalize_text(value), tuple(redactions))


def sanitize_metadata(metadata: Mapping[str, Any] | None) -> dict[str, str]:
    """Keep only the explicit metadata allow-list and scalar string values.

    Unknown keys are dropped rather than copied into the model/LLM boundary.
    Values are normalized and bounded to prevent metadata becoming a hidden
    free-text channel.
    """
    if metadata is None:
        return {}
    clean: dict[str, str] = {}
    for key, value in metadata.items():
        if key not in ALLOWED_METADATA_KEYS or isinstance(value, (dict, list, tuple, set)):
            continue
        if value is None:
            continue
        text = normalize_text(str(value))
        if text and len(text) <= 128:
            clean[key] = text
    return clean


def preprocess_record(text: str, metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return the stable fields used by downstream dataset builders."""
    normalized = normalize_text(text)
    redacted = redact_text(normalized)
    return {
        "text": redacted.text,
        # Invoice/PO identifiers and their replacement tokens can contain
        # Latin characters even in an otherwise Chinese ticket.
        "language": detect_language(_REDACTION_TOKEN.sub(" ", redacted.text)),
        "redaction_types": list(redacted.redactions),
        "redaction_count": len(redacted.redactions),
        "metadata": sanitize_metadata(metadata),
    }
