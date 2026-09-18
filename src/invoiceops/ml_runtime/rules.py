from __future__ import annotations

import time

from .contracts import InferenceResult, LabelScore
from .language import detect_language


_KEYWORDS: dict[str, tuple[str, ...]] = {
    "PRICE_VARIANCE": ("price", "unit price", "价格", "单价"),
    "QUANTITY_RECEIPT_VARIANCE": ("quantity", "received", "收货", "数量"),
    "TAX_CURRENCY_AMOUNT": ("tax", "vat", "currency", "amount", "税", "币种", "金额"),
    "DUPLICATE_INVOICE": ("duplicate", "重复", "twice"),
    "MISSING_PO_OR_RECEIPT": ("missing po", "purchase order", "receipt", "采购单", "凭证"),
    "PAYMENT_STATUS": ("payment", "paid", "付款", "支付"),
    "SUPPLIER_MASTER_CHANGE": ("bank account", "supplier master", "vendor master", "银行账户", "收款信息"),
    "OTHER_REVIEW": ("unclear", "其他", "不明确"),
}


class KeywordClassifier:
    """Deterministic local fallback used until a registered model is active."""

    def __init__(self, model_version: str = "keyword-baseline-0.1", threshold_version: str = "thresholds-v1") -> None:
        self.model_version = model_version
        self.threshold_version = threshold_version

    def predict(self, text: str, *, sanitized: bool = True) -> InferenceResult:
        started = time.perf_counter()
        normalized = text.casefold()
        scores: list[LabelScore] = []
        for label, keywords in _KEYWORDS.items():
            hits = sum(1 for keyword in keywords if keyword.casefold() in normalized)
            if hits:
                scores.append(LabelScore(label, min(0.55 + 0.1 * hits, 0.95)))
        if not scores:
            scores = [LabelScore("OTHER_REVIEW", 0.2)]
        return InferenceResult(
            predictions=tuple(scores),
            language=detect_language(text),
            model_version=self.model_version,
            threshold_version=self.threshold_version,
            inference_ms=(time.perf_counter() - started) * 1000,
            sanitized=sanitized,
        )
