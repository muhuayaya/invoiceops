from types import SimpleNamespace

import numpy as np
import sklearn

from ml.training import _report, _versions


def test_slice_metrics_report_zero_support_labels_explicitly():
    labels = [
        "DUPLICATE_INVOICE", "MISSING_PO_OR_RECEIPT", "OTHER_REVIEW", "PAYMENT_STATUS",
        "PRICE_VARIANCE", "QUANTITY_RECEIPT_VARIANCE", "SUPPLIER_MASTER_CHANGE", "TAX_CURRENCY_AMOUNT",
    ]
    truth = np.eye(len(labels), dtype=int)
    split = SimpleNamespace(
        labels=truth,
        metadata=[
            {"language": "zh" if index < 4 else "en",
             "source": "portal" if index < 4 else "email",
             "request_id": f"sample-{index}"}
            for index in range(len(labels))
        ],
    )
    probabilities = np.where(truth == 1, 0.9, 0.1)

    report = _report(split, probabilities, labels, [0.5] * len(labels), samples=1)

    portal = report["source_slices"]["portal"]
    assert portal["micro_f1"] == 1.0
    assert portal["macro_f1"] == 0.5
    assert portal["per_label_support"] == {
        label: int(index < 4) for index, label in enumerate(labels)
    }


def test_model_version_includes_scikit_learn_version(monkeypatch):
    args = SimpleNamespace(seed=42, max_features=250000, c=4.0)
    common = {"model_type": "tfidf", "args": args, "train_hash": "train", "dev_hash": "dev", "taxonomy": {"labels": ["A"]}}
    monkeypatch.setattr(sklearn, "__version__", "1.9.0")
    old_version = _versions(**common)
    monkeypatch.setattr(sklearn, "__version__", "1.9.1")
    new_version = _versions(**common)
    assert old_version != new_version
