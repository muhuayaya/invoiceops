"""Generate fictional, bilingual supplier-invoice tickets for this PoC only."""
from __future__ import annotations

import csv
import random
from pathlib import Path

LABELS = {
    "PRICE_VARIANCE": ["PO {po} lists {expected} but invoice {invoice} charges {actual}.", "采购单{po}价格为{expected}，但发票{invoice}写成{actual}。"],
    "QUANTITY_RECEIPT_VARIANCE": ["We received {received} units but invoice {invoice} bills {billed}.", "我们只收货{received}件，发票{invoice}却开了{billed}件。"],
    "TAX_CURRENCY_AMOUNT": ["The VAT/currency total on invoice {invoice} is incorrect.", "发票{invoice}的税额或币种金额不正确。"],
    "DUPLICATE_INVOICE": ["Invoice {invoice} appears to be a duplicate submission.", "发票{invoice}疑似重复提交，请核查。"],
    "MISSING_PO_OR_RECEIPT": ["Invoice {invoice} is pending because the PO or receipt is missing.", "发票{invoice}缺少采购单或收货凭证。"],
    "PAYMENT_STATUS": ["Could you confirm the payment status for invoice {invoice}?", "请确认发票{invoice}的付款状态。"],
    "SUPPLIER_MASTER_CHANGE": ["Please change supplier bank account details for future payments.", "请变更供应商收款银行账户信息。"],
    "OTHER_REVIEW": ["There is a strange issue with invoice {invoice}; please review manually.", "发票{invoice}情况不明确，需要人工复核。"],
}

def render(template: str, index: int, marker: str = "case") -> str:
    # Keep a deterministic, non-sensitive case marker so near-duplicate
    # grouping does not collapse every language/template variant together
    # after invoice and PO identifiers are redacted.
    return template.format(
        po=f"PO-{1000 + index}",
        invoice=f"INV-{2026000 + index}",
        expected=f"{100 + index} USD",
        actual=f"{120 + index} USD",
        received=8 + index % 3,
        billed=10 + index % 3,
    ) + f" ({marker} {index})"

def main() -> None:
    rng = random.Random(20260915)
    rows = []
    for label, templates in LABELS.items():
        for i in range(30):
            language = "en" if i % 3 == 0 else "zh" if i % 3 == 1 else "mixed"
            template = templates[0] if language == "en" else templates[1]
            marker = "case" if language != "zh" else "样本"
            primary = render(template, i, marker)
            if language == "mixed":
                primary = f"{render(templates[0], i, marker)} 请尽快处理。"
            labels = [label]
            if i % 5 == 0 and label != "PAYMENT_STATUS":
                payment_template = LABELS["PAYMENT_STATUS"][0] if language == "en" else LABELS["PAYMENT_STATUS"][1]
                primary += " " + render(payment_template, i, marker)
                labels.append("PAYMENT_STATUS")
            rows.append({"request_id": f"syn-{label.lower()}-{i:03d}", "source": "synthetic", "text": primary, "language": language, "labels": "|".join(labels), "template_group": f"{label}-{i % 6}", "taxonomy_version": "invoiceops-v1", "review_status": "synthetic_candidate"})
    rng.shuffle(rows)
    output = Path(__file__).parent / "generated" / "invoiceops-synthetic-v1.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"generated {len(rows)} rows at {output}")

if __name__ == "__main__":
    main()
