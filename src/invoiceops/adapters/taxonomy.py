from __future__ import annotations

import json
from pathlib import Path

from invoiceops.domain.entities import LabelDefinition
from invoiceops.domain.errors import ValidationError


class TaxonomyCatalog:
    """Loads the frozen v1 taxonomy and routing contract without modifying it."""

    def __init__(self, taxonomy_path: Path | None = None, routes_path: Path | None = None) -> None:
        root = Path(__file__).resolve().parents[3]
        taxonomy_path = taxonomy_path or root / "configs/taxonomy/invoiceops-v1.json"
        routes_path = routes_path or root / "configs/taxonomy/invoiceops-v1.routes.json"
        self.taxonomy = json.loads(taxonomy_path.read_text(encoding="utf-8"))
        self.routes = json.loads(routes_path.read_text(encoding="utf-8"))
        self.version = self.taxonomy["version"]
        self.threshold_version = self.taxonomy["threshold_version"]
        self._labels = {
            item["code"]: LabelDefinition(
                code=item["code"],
                name=item["name"],
                risk=item["risk"],
                description=item["description"],
            )
            for item in self.taxonomy["labels"]
        }

    @property
    def labels(self) -> tuple[LabelDefinition, ...]:
        return tuple(self._labels.values())

    @property
    def label_codes(self) -> frozenset[str]:
        return frozenset(self._labels)

    @property
    def queue_codes(self) -> frozenset[str]:
        queues = set()
        for route in self.routes["routes"].values():
            queues.add(route["primary_queue"])
            queues.update(route["collaborator_queues"])
        return frozenset(queues)

    def validate_queue(self, queue: str) -> None:
        if queue not in self.queue_codes:
            raise ValidationError(f"unknown primary queue: {queue}")

    def validate_version(self, version: str) -> None:
        if version != self.version:
            raise ValidationError(f"unknown taxonomy version: {version}")

    def validate_labels(self, labels: list[str] | tuple[str, ...]) -> None:
        unknown = sorted(set(labels) - self.label_codes)
        if unknown:
            raise ValidationError(f"unknown label code(s): {', '.join(unknown)}")

    def risk_for(self, labels: list[str] | tuple[str, ...]) -> str:
        ranks = {"low": 1, "medium": 2, "high": 3}
        return max((self._labels[label].risk for label in labels), key=ranks.get, default="medium")

    def route_for(self, labels: list[str] | tuple[str, ...]) -> tuple[str, tuple[str, ...], str]:
        self.validate_labels(labels)
        selected = [self.routes["routes"][label] for label in labels]
        if not selected:
            return "MANUAL_TRIAGE", (), self.routes["version"]
        # Preserve deterministic priority: first label is the primary owner, then unique collaborators.
        primary = selected[0]["primary_queue"]
        collaborators: list[str] = []
        for route in selected:
            for queue in [route["primary_queue"], *route["collaborator_queues"]]:
                if queue != primary and queue not in collaborators:
                    collaborators.append(queue)
        return primary, tuple(collaborators), self.routes["version"]

    def as_dict(self) -> dict[str, object]:
        return {
            "taxonomy_version": self.version,
            "routing_version": self.routes["version"],
            "labels": self.taxonomy["labels"],
        }
