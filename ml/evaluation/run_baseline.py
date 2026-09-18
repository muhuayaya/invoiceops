"""CLI for the reproducible InvoiceOps v1 baseline report."""
from __future__ import annotations

import argparse
from pathlib import Path

if __package__ in {None, ""}:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.evaluation.baseline import train_and_evaluate, write_report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("ml/data/processed/invoiceops-v1-dataset.csv"))
    parser.add_argument("--output", type=Path, default=Path("docs/data/invoiceops-v1-baseline-report.json"))
    args = parser.parse_args()
    _, report = train_and_evaluate(args.dataset)
    write_report(report, args.output)
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
