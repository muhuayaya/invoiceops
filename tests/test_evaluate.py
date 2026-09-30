from ml.evaluate import _frozen_gate_for_model, _quality_gate_result


def _criteria():
    return {
        "macro_f1": {"operator": ">=", "threshold": 0.80},
        "micro_f1": {"operator": ">=", "threshold": 0.85},
        "supplier_master_change_recall": {"operator": ">=", "threshold": 0.95},
        "zh_en_macro_f1_gap": {"operator": "<=", "threshold": 0.08},
    }


def test_frozen_gate_is_pinned_to_candidate_version():
    gate = {
        "frozen": True,
        "scope": "engineering_quality_only",
        "gate_id": "quality-v1",
        "candidate_models": {"candidate-1": {"role": "primary"}},
        "criteria": _criteria(),
    }

    candidate, criteria = _frozen_gate_for_model(gate, "candidate-1")

    assert candidate["role"] == "primary"
    assert criteria["macro_f1"]["threshold"] == 0.80


def test_quality_gate_passes_inclusive_boundaries_without_approving_model():
    report = {
        "macro_f1": 0.80,
        "micro_f1": 0.85,
        "supplier_master_change_recall": 0.95,
        "language_slices": {
            "zh": {"macro_f1": 0.81},
            "en": {"macro_f1": 0.73},
        },
    }

    result = _quality_gate_result(report, _criteria())

    assert result["status"] == "pass_quality_only"
    assert result["criteria"]["zh_en_macro_f1_gap"]["actual"] == 0.08
    assert result["engineering_approval_granted"] is False
    assert result["real_business_approval_granted"] is False


def test_quality_gate_fails_closed_when_a_slice_or_metric_is_missing():
    report = {
        "macro_f1": 0.99,
        "micro_f1": 0.99,
        "supplier_master_change_recall": 1.0,
        "language_slices": {"zh": {"macro_f1": 0.99}},
    }

    result = _quality_gate_result(report, _criteria())

    assert result["status"] == "fail_quality"
    assert result["criteria"]["zh_en_macro_f1_gap"]["actual"] is None
    assert result["criteria"]["zh_en_macro_f1_gap"]["passed"] is False


def test_unfrozen_or_unpinned_gate_is_rejected():
    gate = {
        "frozen": False,
        "scope": "engineering_quality_only",
        "gate_id": "quality-v1",
        "candidate_models": {"candidate-1": {"role": "primary"}},
        "criteria": _criteria(),
    }

    try:
        _frozen_gate_for_model(gate, "candidate-1")
    except ValueError as exc:
        assert "frozen=true" in str(exc)
    else:
        raise AssertionError("an unfrozen gate must be rejected")

    gate["frozen"] = True
    try:
        _frozen_gate_for_model(gate, "candidate-2")
    except ValueError as exc:
        assert "not pinned" in str(exc)
    else:
        raise AssertionError("an unpinned model version must be rejected")
