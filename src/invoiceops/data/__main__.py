"""Run the offline three-split contract check from the command line."""

import argparse
import json
from pathlib import Path

from .contract import DataContractError, validate_dataset


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate fixed train/dev/test invoice CSV files")
    parser.add_argument("--train", required=True, type=Path)
    parser.add_argument("--dev", required=True, type=Path)
    parser.add_argument("--test", required=True, type=Path)
    parser.add_argument("--taxonomy", default=Path("configs/taxonomy.json"), type=Path)
    parser.add_argument("--dataset-version", default="invoiceops-v1")
    parser.add_argument("--manifest", type=Path, help="write the validated manifest as JSON")
    args = parser.parse_args()
    try:
        manifest = validate_dataset(
            args.train, args.dev, args.test, args.taxonomy, dataset_version=args.dataset_version
        )
    except DataContractError as exc:
        parser.exit(1, f"data contract failed: {exc}\n")
    output = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    if args.manifest:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(output, encoding="utf-8")
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
