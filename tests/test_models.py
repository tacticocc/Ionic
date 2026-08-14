from __future__ import annotations

import pytest
from pydantic import ValidationError

from ionic.models import (
    Constraint,
    Contract,
    Dependency,
    Format,
    Severity,
    parse_version,
    slugify,
)


def test_severity_ordering():
    assert Severity.CRITICAL > Severity.HIGH > Severity.MEDIUM > Severity.LOW > Severity.INFO
    assert Severity.HIGH >= Severity.HIGH
    assert not (Severity.LOW >= Severity.HIGH)


def test_version_must_be_semver():
    with pytest.raises(ValidationError):
        Contract(id="a", version="not-a-version")
    assert Contract(id="a", version="1.2.3").version == "1.2.3"
    assert parse_version("2.10.4") == (2, 10, 4)
    assert parse_version("garbage") == (0, 0, 0)
    assert parse_version("1.0.0-rc.1") == (1, 0, 0)


def test_id_is_slugified():
    assert Contract(id="My Planner Agent!").id == "my-planner-agent"
    assert Contract(id="org/planner").id == "org/planner"
    assert slugify("  Weird   Name  ") == "weird-name"


def test_dependency_accepts_bare_string():
    contract = Contract(id="a", depends_on=["planner"])
    assert contract.depends_on == [Dependency(contract_id="planner")]
    assert contract.dependency_ids() == ["planner"]


def test_constraint_accepts_bare_string_and_derives_stable_id():
    first = Constraint.model_validate("Always cite sources.")
    second = Constraint.model_validate("Always cite sources.")
    assert first.id == second.id
    assert first.severity is Severity.HIGH


def test_format_aliases_and_unknown_fallback():
    contract = Contract(
        id="a",
        outputs=[
            {"name": "x", "format": "MD"},
            {"name": "y", "format": "protobuf"},
        ],
    )
    assert contract.outputs[0].format is Format.MARKDOWN
    assert contract.outputs[1].format is Format.OTHER


def test_schema_alias_is_accepted():
    contract = Contract(
        id="a", outputs=[{"name": "x", "schema": {"type": "object"}}]
    )
    assert contract.outputs[0].json_schema == {"type": "object"}


def test_fingerprint_ignores_bookkeeping_but_tracks_behaviour(planner):
    moved = planner.model_copy(update={"source": "elsewhere.md", "tags": ["new"]})
    assert moved.fingerprint() == planner.fingerprint()

    retooled = planner.model_copy(update={"tools": planner.tools[:1]})
    assert retooled.fingerprint() != planner.fingerprint()


def test_fingerprint_ignores_version_bump(planner):
    """A version bump alone is not a behavioural change."""
    assert planner.model_copy(update={"version": "9.9.9"}).fingerprint() == planner.fingerprint()


def test_bumped():
    contract = Contract(id="a", version="1.4.2")
    assert contract.bumped("major") == "2.0.0"
    assert contract.bumped("minor") == "1.5.0"
    assert contract.bumped("patch") == "1.4.3"
