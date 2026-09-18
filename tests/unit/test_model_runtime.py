from pathlib import Path

import pytest

from invoiceops.ml_runtime import DecisionPolicy, KeywordClassifier, ThresholdPolicy
from invoiceops.ml_runtime.registry import ModelRegistry, ModelVersion


ROOT = Path(__file__).parents[2]


def test_keyword_runtime_returns_bilingual_multilabel_result() -> None:
    result = KeywordClassifier().predict("The invoice amount is wrong，而且我们还没有收到付款。")
    labels = {item.label_code for item in result.predictions}
    assert {"TAX_CURRENCY_AMOUNT", "PAYMENT_STATUS"} <= labels
    assert result.language == "mixed"


def test_high_risk_result_is_forced_to_review() -> None:
    policy = DecisionPolicy(ThresholdPolicy.from_file(ROOT / "configs/thresholds/thresholds-v1.json"))
    result = KeywordClassifier().predict("Please change the supplier bank account details.")
    assert policy.decide(result) == ("needs_review", ("HIGH_RISK_LABEL",))


def test_registry_rejects_failed_promotion_and_rolls_back() -> None:
    registry = ModelRegistry()
    registry.register(ModelVersion("stable", "baseline", {"macro_f1": 0.81, "micro_f1": 0.86, "supplier_master_change_recall": 0.96}, "thresholds-v1"))
    registry.promote("stable")
    registry.register(ModelVersion("bad", "xlmr", {"macro_f1": 0.79, "micro_f1": 0.86, "supplier_master_change_recall": 0.99}, "thresholds-v1"))
    with pytest.raises(ValueError):
        registry.promote("bad")
    registry.register(ModelVersion("candidate", "xlmr", {"macro_f1": 0.82, "micro_f1": 0.87, "supplier_master_change_recall": 0.96}, "thresholds-v1"))
    registry.promote("candidate")
    assert registry.rollback().version == "stable"
