import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

ROOT = Path(__file__).parents[2]
TAXONOMY_PATH = ROOT / "configs/taxonomy/invoiceops-v1.json"
TAXONOMY_SCHEMA_PATH = ROOT / "configs/taxonomy/invoiceops-v1.schema.json"
ROUTES_PATH = ROOT / "configs/taxonomy/invoiceops-v1.routes.json"
ROUTES_SCHEMA_PATH = ROOT / "configs/taxonomy/invoiceops-v1.routes.schema.json"
THRESHOLDS_PATH = ROOT / "configs/thresholds/thresholds-v1.json"
THRESHOLDS_SCHEMA_PATH = ROOT / "configs/thresholds/thresholds-v1.schema.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_invoiceops_taxonomy_and_routes_match_schema() -> None:
    taxonomy = load_json(TAXONOMY_PATH)
    taxonomy_schema = load_json(TAXONOMY_SCHEMA_PATH)
    routes = load_json(ROUTES_PATH)
    routes_schema = load_json(ROUTES_SCHEMA_PATH)
    thresholds = load_json(THRESHOLDS_PATH)
    thresholds_schema = load_json(THRESHOLDS_SCHEMA_PATH)

    Draft202012Validator.check_schema(taxonomy_schema)
    Draft202012Validator.check_schema(routes_schema)
    Draft202012Validator.check_schema(thresholds_schema)
    Draft202012Validator(taxonomy_schema).validate(taxonomy)
    Draft202012Validator(routes_schema).validate(routes)
    Draft202012Validator(thresholds_schema).validate(thresholds)

    label_codes = {label["code"] for label in taxonomy["labels"]}
    assert label_codes == set(routes["routes"])
    assert routes["taxonomy_version"] == taxonomy["version"]
    assert all(
        label["thresholds"]["candidate_threshold"]
        < label["thresholds"]["auto_accept_threshold"]
        for label in taxonomy["labels"]
    )
    assert thresholds["candidate_threshold"] < thresholds["auto_accept_threshold"]
    assert set(thresholds["high_risk_labels"]) == {"SUPPLIER_MASTER_CHANGE"}


@pytest.mark.parametrize(
    ("path", "mutate"),
    [
        ("taxonomy", lambda data: data["labels"][0].update(code="UNKNOWN_LABEL")),
        ("taxonomy", lambda data: data["labels"][0].update(risk="critical")),
        (
            "taxonomy",
            lambda data: data["labels"][0]["thresholds"].update(candidate_threshold=1.01),
        ),
        (
            "thresholds",
            lambda data: data.update(auto_accept_threshold=-0.1),
        ),
        ("routes", lambda data: data["routes"].update(UNKNOWN_LABEL={})),
    ],
)
def test_invalid_taxonomy_values_are_rejected(path: str, mutate) -> None:
    if path == "taxonomy":
        data = load_json(TAXONOMY_PATH)
        schema = load_json(TAXONOMY_SCHEMA_PATH)
    elif path == "thresholds":
        data = load_json(THRESHOLDS_PATH)
        schema = load_json(THRESHOLDS_SCHEMA_PATH)
    else:
        data = load_json(ROUTES_PATH)
        schema = load_json(ROUTES_SCHEMA_PATH)

    invalid = copy.deepcopy(data)
    mutate(invalid)

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(invalid)
