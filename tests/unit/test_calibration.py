from ml.evaluation.calibration import calibrate_thresholds, rejection_reasons


def test_calibration_is_per_label_and_rejection_reasons_are_explicit() -> None:
    thresholds = calibrate_thresholds([[1, 0], [0, 1]], [[0.9, 0.2], [0.2, 0.8]], ["A", "B"])
    assert thresholds["A"] <= 0.9
    assert rejection_reasons(["SUPPLIER_MASTER_CHANGE"], [0.8], {"SUPPLIER_MASTER_CHANGE": 0.75}, {"SUPPLIER_MASTER_CHANGE"}) == ("HIGH_RISK_LABEL",)
    assert rejection_reasons(["OTHER_REVIEW"], [0.2], {"OTHER_REVIEW": 0.75}, set()) == ("LOW_CONFIDENCE",)
