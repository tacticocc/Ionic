"""The deterministic engine is the load-bearing half, so it gets the most tests."""

from __future__ import annotations

from ionic.diff import structural_findings
from ionic.models import Severity


def kinds(findings, kind):
    return [f for f in findings if f.kind == kind]


def only(findings, kind):
    matches = kinds(findings, kind)
    assert len(matches) == 1, f"expected exactly one {kind}, got {len(matches)}"
    return matches[0]


def without_tool(contract, name):
    return contract.revise(tools=[t for t in contract.tools if t.name != name])


def test_no_change_produces_nothing_blocking(planner, researcher):
    findings = structural_findings(planner, planner, [researcher])
    assert [f for f in findings if f.severity >= Severity.MEDIUM] == []


def test_removing_a_required_tool_is_critical_and_names_the_victim(planner, researcher):
    proposed = without_tool(planner, "search_web")
    finding = only(structural_findings(planner, proposed, [researcher]), "tool_removed")
    assert finding.severity is Severity.CRITICAL
    assert finding.affected_contract == "researcher"
    assert "search_web" in finding.summary
    assert finding.recommendation


def test_removing_an_undeclared_tool_is_only_medium(planner):
    proposed = without_tool(planner, "search_web")
    finding = only(structural_findings(planner, proposed, []), "tool_removed")
    assert finding.severity is Severity.MEDIUM
    assert finding.affected_contract is None


def test_output_format_change_breaks_declared_consumers(planner, researcher):
    proposed = planner.revise(outputs=[planner.outputs[0].revise(format="markdown")])
    finding = only(structural_findings(planner, proposed, [researcher]), "output_format_changed")
    assert finding.severity is Severity.CRITICAL
    assert finding.affected_contract == "researcher"


def test_output_format_change_without_consumers_is_high(planner):
    proposed = planner.revise(outputs=[planner.outputs[0].revise(format="markdown")])
    finding = only(structural_findings(planner, proposed, []), "output_format_changed")
    assert finding.severity is Severity.HIGH


def test_removing_a_required_constraint_is_critical(planner, researcher):
    proposed = planner.revise(
        constraints=[c for c in planner.constraints if c.id != "source-required"]
    )
    finding = only(structural_findings(planner, proposed, [researcher]), "constraint_removed")
    assert finding.severity is Severity.CRITICAL
    assert finding.affected_contract == "researcher"


def test_removing_a_required_capability_is_critical(planner, researcher):
    proposed = planner.revise(capabilities=["scope estimation"])
    finding = only(structural_findings(planner, proposed, [researcher]), "capability_removed")
    assert finding.severity is Severity.CRITICAL


def test_new_required_input_is_high_when_callers_exist(planner, researcher):
    proposed = planner.revise(
        inputs=[*planner.inputs, {"name": "budget", "format": "text", "required": True}]
    )
    finding = only(structural_findings(planner, proposed, [researcher]), "required_input_added")
    assert finding.severity is Severity.HIGH


def test_new_optional_input_is_not_flagged(planner, researcher):
    proposed = planner.revise(
        inputs=[*planner.inputs, {"name": "budget", "format": "text", "required": False}]
    )
    findings = structural_findings(planner, proposed, [researcher])
    assert kinds(findings, "required_input_added") == []


def test_schema_field_removal_is_detected(planner, researcher):
    narrowed = planner.outputs[0].revise(
        json_schema={"type": "object", "properties": {"steps": {"type": "array"}}}
    )
    proposed = planner.revise(outputs=[narrowed])
    finding = only(structural_findings(planner, proposed, [researcher]), "output_schema_narrowed")
    assert "cost" in finding.summary
    assert finding.severity is Severity.CRITICAL


def test_schema_type_change_is_detected(planner):
    changed = planner.outputs[0].revise(
        json_schema={
            "type": "object",
            "properties": {"steps": {"type": "string"}, "cost": {"type": "number"}},
            "required": ["steps"],
        }
    )
    findings = structural_findings(planner, planner.revise(outputs=[changed]), [])
    assert any("changed type" in f.summary for f in kinds(findings, "output_schema_narrowed"))


def test_nested_schema_paths_are_walked(planner):
    def wrap(inner):
        return {
            "type": "object",
            "properties": {"steps": {"type": "array", "items": inner}},
            "required": ["steps"],
        }

    before = planner.outputs[0].revise(
        json_schema=wrap({"type": "object", "properties": {"id": {"type": "string"}}})
    )
    after = planner.outputs[0].revise(json_schema=wrap({"type": "object", "properties": {}}))
    findings = structural_findings(
        planner.revise(outputs=[before]),
        planner.revise(outputs=[after]),
        [],
    )
    assert any("steps.[].id" in f.summary for f in kinds(findings, "output_schema_narrowed"))


def test_new_required_schema_field_is_flagged_on_inputs(planner):
    def schema(required):
        return {
            "type": "object",
            "properties": {"brief": {"type": "string"}, "budget": {"type": "number"}},
            "required": required,
        }

    before = planner.revise(inputs=[planner.inputs[0].revise(json_schema=schema(["brief"]))])
    after = planner.revise(
        inputs=[planner.inputs[0].revise(json_schema=schema(["brief", "budget"]))]
    )
    finding = only(structural_findings(before, after, []), "input_schema_narrowed")
    assert "became required" in finding.summary


def test_enum_narrowing_is_detected(planner):
    def schema(values):
        return {
            "type": "object",
            "properties": {"mode": {"type": "string", "enum": values}},
        }

    before = planner.revise(outputs=[planner.outputs[0].revise(json_schema=schema(["a", "b"]))])
    after = planner.revise(outputs=[planner.outputs[0].revise(json_schema=schema(["a"]))])
    finding = only(structural_findings(before, after, []), "output_schema_narrowed")
    assert "dropped enum" in finding.summary


def test_persona_removal_is_medium(planner):
    proposed = planner.revise(persona_rules=[])
    finding = only(structural_findings(planner, proposed, []), "persona_rule_removed")
    assert finding.severity is Severity.MEDIUM


def test_breaking_change_without_major_bump(planner, researcher):
    proposed = without_tool(planner, "search_web").revise(version="1.1.0")
    finding = only(
        structural_findings(planner, proposed, [researcher]),
        "breaking_change_without_major_bump",
    )
    assert "2.0.0" in finding.recommendation


def test_major_bump_suppresses_the_version_warning(planner, researcher):
    proposed = without_tool(planner, "search_web").revise(version="2.0.0")
    findings = structural_findings(planner, proposed, [researcher])
    assert kinds(findings, "breaking_change_without_major_bump") == []


def test_changed_without_version_bump_is_flagged(planner):
    proposed = planner.revise(capabilities=[*planner.capabilities, "new thing"])
    finding = only(structural_findings(planner, proposed, []), "version_not_bumped")
    assert finding.severity is Severity.LOW


def test_version_regression_is_flagged(planner):
    proposed = planner.revise(version="0.9.0")
    assert only(structural_findings(planner, proposed, []), "version_regression")


def test_additive_changes_are_info_only(planner, researcher):
    proposed = planner.revise(
        version="1.1.0",
        tools=[*planner.tools, {"name": "estimate_cost"}],
        capabilities=[*planner.capabilities, "budgeting"],
    )
    findings = structural_findings(planner, proposed, [researcher])
    assert all(f.severity <= Severity.LOW for f in findings)


def test_every_finding_carries_the_changed_contract(planner, researcher):
    proposed = planner.revise(tools=[], version="2.0.0")
    findings = structural_findings(planner, proposed, [researcher])
    assert findings
    assert all(f.changed_contract == "planner" for f in findings)


# ---------------------------------------------------------------------------
# constraint renames
# ---------------------------------------------------------------------------


def test_a_reworded_constraint_is_a_rename_not_a_removal(planner, researcher):
    """Ids derived from statement text change when the wording does. Reporting
    that as a removed guarantee sends people to the wrong fix."""
    reworded = [
        c.revise(id="source-flagged", statement="Every step flags sources for claims.")
        if c.id == "source-required"
        else c
        for c in planner.constraints
    ]
    proposed = planner.revise(constraints=reworded)
    findings = structural_findings(planner, proposed, [researcher])

    finding = only(findings, "constraint_id_changed")
    assert finding.severity is Severity.CRITICAL
    assert finding.affected_contract == "researcher"
    assert "rename" in finding.detail
    assert "source-required" in finding.recommendation  # keep the old id

    # and it must not double-report as a removal or an addition
    assert kinds(findings, "constraint_removed") == []
    assert kinds(findings, "constraint_added") == []


def test_a_reworded_constraint_nobody_references_is_low(planner):
    reworded = [
        c.revise(id="source-flagged", statement="Every step flags sources for claims.")
        if c.id == "source-required"
        else c
        for c in planner.constraints
    ]
    finding = only(
        structural_findings(planner, planner.revise(constraints=reworded), []),
        "constraint_reworded",
    )
    assert finding.severity is Severity.LOW


def test_a_genuinely_different_constraint_is_still_a_removal(planner, researcher):
    """The rename heuristic must not swallow an actual dropped guarantee."""
    replaced = [
        c.revise(id="be-fast", statement="Respond within two seconds.")
        if c.id == "source-required"
        else c
        for c in planner.constraints
    ]
    findings = structural_findings(planner, planner.revise(constraints=replaced), [researcher])
    assert only(findings, "constraint_removed").severity is Severity.CRITICAL
    assert kinds(findings, "constraint_id_changed") == []
    assert only(findings, "constraint_added")
