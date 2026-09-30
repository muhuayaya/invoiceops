from invoiceops.application.batch import InvalidBatch, parse_batch
from invoiceops.application.classifier import ModelUnavailable, classify


LABELS = (
    "DUPLICATE_INVOICE",
    "MISSING_PO_OR_RECEIPT",
    "OTHER_REVIEW",
    "PAYMENT_STATUS",
    "PRICE_VARIANCE",
    "QUANTITY_RECEIPT_VARIANCE",
    "SUPPLIER_MASTER_CHANGE",
    "TAX_CURRENCY_AMOUNT",
)


class StubModel:
    labels = LABELS
    thresholds = (0.5,) * 8
    threshold_version = "thresholds-1"
    engineering_approved = True

    def __init__(self, version, scores=None, failure=False):
        self.version = version
        self.scores = scores or (0.1,) * 8
        self.failure = failure
        self.calls = 0

    def predict_proba(self, texts):
        self.calls += 1
        if self.failure:
            raise RuntimeError("model failure")
        return [self.scores]


def test_two_labels_and_low_confidence_stays_on_primary():
    primary = StubModel("xlmr-v1", (0.9, 0.1, 0.1, 0.51, 0.1, 0.1, 0.1, 0.1))
    fallback = StubModel("tfidf-v1")
    result = classify("Check the duplicate invoice and payment", primary=primary, fallback=fallback, label_order=LABELS, taxonomy_version="v1")
    assert result.labels == ("DUPLICATE_INVOICE", "PAYMENT_STATUS")
    assert result.review_status == "pending_review"
    assert "low_confidence" in result.review_reasons
    assert result.degraded is False
    assert fallback.calls == 0


def test_failed_primary_uses_only_approved_fallback():
    primary = StubModel("xlmr-v1", failure=True)
    fallback = StubModel("tfidf-v1")
    fallback.engineering_approved = False
    try:
        classify("Payment pending", primary=primary, fallback=fallback, label_order=LABELS, taxonomy_version="v1")
    except ModelUnavailable:
        pass
    else:
        raise AssertionError("unapproved fallback served a prediction")
    fallback.engineering_approved = True
    result = classify("Payment pending", primary=primary, fallback=fallback, label_order=LABELS, taxonomy_version="v1")
    assert result.degraded is True
    assert result.model_version == "tfidf-v1"
    assert result.review_status == "pending_review"


def test_mixed_csv_keeps_failed_row_and_original_line_number():
    rows = parse_batch(b"text,external_id\nDuplicate invoice,1\n,2\nPayment pending,3\n")
    assert [row.row_number for row in rows] == [2, 3, 4]
    assert [row.error_code for row in rows] == [None, "empty_text", None]
    try:
        parse_batch(b"external_id\n1\n")
    except InvalidBatch:
        pass
    else:
        raise AssertionError("CSV without text was accepted")


def test_batch_rejects_unsupported_headers_and_long_external_ids():
    try:
        parse_batch(b"text,labels\nInvoice,OTHER_REVIEW\n")
    except InvalidBatch as exc:
        assert "unsupported headers" in str(exc)
    else:
        raise AssertionError("CSV with unsupported headers was accepted")

    rows = parse_batch(("text,external_id\nInvoice," + "x" * 256 + "\n").encode())
    assert rows[0].error_code == "external_id_too_long"
