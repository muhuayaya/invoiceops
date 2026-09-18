from ml.data.preprocess import detect_language, preprocess_record, redact_text, sanitize_metadata
from ml.data.split import assert_no_group_leakage, grouped_multilabel_split


def test_redaction_covers_required_patterns_and_metadata_allowlist() -> None:
    result = redact_text("Email a.person@example.com, phone +86 138 0013 8000, IBAN DE89370400440532013000, INV-1001")
    assert "@" not in result.text
    assert "DE89370400440532013000" not in result.text
    assert "INV-1001" not in result.text
    assert detect_language("Amount 金额") == "mixed"
    assert preprocess_record("发票 INV-1001 疑似重复提交")["language"] == "zh"
    assert sanitize_metadata({"channel": "email", "secret": "do-not-forward"}) == {"channel": "email"}


def test_grouped_split_never_crosses_template_or_supplier_context() -> None:
    rows = [
        {"text": "price issue one", "labels": ["PRICE_VARIANCE"], "template_group": "t1", "translation_group": "tr1", "supplier_context_group": "s1"},
        {"text": "price issue two", "labels": ["PRICE_VARIANCE"], "template_group": "t1", "translation_group": "tr1", "supplier_context_group": "s1"},
        {"text": "payment issue", "labels": ["PAYMENT_STATUS"], "template_group": "t2", "translation_group": "tr2", "supplier_context_group": "s2"},
        {"text": "bank change", "labels": ["SUPPLIER_MASTER_CHANGE"], "template_group": "t3", "translation_group": "tr3", "supplier_context_group": "s3"},
    ]
    splits = grouped_multilabel_split(rows)
    assert_no_group_leakage(splits)
