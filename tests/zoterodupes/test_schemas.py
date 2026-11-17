import copy
import json

import pytest
from jsonschema import Draft202012Validator, ValidationError

from notewrench.zoterodupes.cli import main
from notewrench.zoterodupes.decisions import carry_decisions, decision_template, export_confirmed
from notewrench.zoterodupes.schemas import KINDS, artifact_schema


@pytest.mark.parametrize("kind", KINDS)
def test_artifact_schemas_are_valid_self_contained_draft202012(kind):
    Draft202012Validator.check_schema(artifact_schema(kind))
    assert '"$ref"' not in json.dumps(artifact_schema(kind))


def test_realistic_outputs_validate_against_published_schemas(realistic_review):
    decisions = decision_template(realistic_review)
    decisions["decisions"][realistic_review["pairs"][0]["id"]]["status"] = "same"
    mapping, suggestions = export_confirmed(realistic_review, decisions)
    _, audit = carry_decisions(realistic_review, decisions, realistic_review)
    values = {"review": realistic_review, "decisions": decisions, "correspondences": mapping, "suggestions": suggestions, "carryover-audit": audit}
    for kind, value in values.items():
        Draft202012Validator(artifact_schema(kind)).validate(value)


def test_schema_rejects_boolean_version_and_invalid_status(realistic_review):
    decisions = decision_template(realistic_review)
    decisions["schema_version"] = True
    validator = Draft202012Validator(artifact_schema("decisions"))
    with pytest.raises(ValidationError): validator.validate(decisions)
    decisions["schema_version"] = 1
    decisions["decisions"][realistic_review["pairs"][0]["id"]]["status"] = "merge"
    with pytest.raises(ValidationError): validator.validate(decisions)


def test_cli_schema_is_json_for_offline_consumers(capsys):
    assert main(["schema", "correspondences"]) == 0
    schema = json.loads(capsys.readouterr().out)
    assert schema["$id"] == "urn:library-bridge:schema:correspondences:1"

