from __future__ import annotations

import json

from ionic.extract import (
    discover_agent_files,
    extract_contract,
    extract_from_file,
    render_markdown,
)
from ionic.models import Format, Severity

from conftest import DEMO_REPOS

SIMPLE = """\
# Research Agent

Executes a plan and returns sourced findings.

## Inputs

- `plan` (json) — The planner's execution plan.
- `locale` (text) (optional) — Preferred language.

## Outputs

- `findings` (json) — One entry per plan step.

## Tools

- `fetch_page` — Retrieve and clean a URL.
- `summarize_source` — Condense a source.

## Capabilities

- evidence gathering
- source attribution

## Constraints

- [cite-everything] Every claim carries a source URL.
- Never invent an answer. (medium)

## Persona

- Neutral and factual.

## Depends On

- `planner-agent` — tools: search_web, decompose; outputs: plan; format: json; constraints: source-required
"""


def test_headings_and_bullets_become_a_contract():
    contract = extract_contract(SIMPLE)
    assert contract.id == "research-agent"
    assert contract.name == "Research Agent"
    assert [t.name for t in contract.tools] == ["fetch_page", "summarize_source"]
    assert [o.name for o in contract.outputs] == ["findings"]
    assert contract.outputs[0].format is Format.JSON
    assert contract.capabilities == ["evidence gathering", "source attribution"]
    assert contract.persona_rules == ["Neutral and factual."]


def test_optional_inputs_are_detected():
    contract = extract_contract(SIMPLE)
    by_name = {i.name: i for i in contract.inputs}
    assert by_name["plan"].required is True
    assert by_name["locale"].required is False


def test_constraint_ids_and_severity_tags():
    contract = extract_contract(SIMPLE)
    by_id = {c.id: c for c in contract.constraints}
    assert "cite-everything" in by_id
    assert by_id["cite-everything"].statement == "Every claim carries a source URL."
    other = [c for c in contract.constraints if c.id != "cite-everything"][0]
    assert other.severity is Severity.MEDIUM
    assert other.statement == "Never invent an answer."


def test_prose_dependency_declaration_is_parsed():
    contract = extract_contract(SIMPLE)
    dep = contract.dependency_on("planner-agent")
    assert dep is not None
    assert dep.requires_tools == ["search_web", "decompose"]
    assert dep.expects_outputs == ["plan"]
    assert dep.expects_format is Format.JSON
    assert dep.requires_constraints == ["source-required"]


def test_ionic_block_overrides_heuristics():
    text = (
        "# Some Agent\n\n"
        "```ionic\n"
        "id: canonical-id\n"
        "version: 3.2.1\n"
        "tags: [core]\n"
        "```\n\n"
        "## Tools\n\n- `a` — first\n"
    )
    contract = extract_contract(text)
    assert contract.id == "canonical-id"
    assert contract.version == "3.2.1"
    assert contract.tags == ["core"]
    # heuristics still fill in what the block omits
    assert [t.name for t in contract.tools] == ["a"]


def test_ionic_block_is_stripped_from_prose():
    text = "# A\n\n```ionic\nid: a\n```\n\n## Tools\n\n- `t` — x\n"
    contract = extract_contract(text)
    assert "ionic" not in (contract.identity or "")


def test_frontmatter_is_read():
    text = "---\nid: fm-agent\nversion: 2.0.0\n---\n\n# Agent\n\n## Tools\n\n- `x` — y\n"
    contract = extract_contract(text)
    assert contract.id == "fm-agent"
    assert contract.version == "2.0.0"


def test_json_schema_block_is_attached_to_the_output():
    text = (
        "# A\n\n## Outputs\n\n- `result` (json) — The result.\n\n"
        "```json\n"
        + json.dumps({"type": "object", "properties": {"x": {"type": "string"}}})
        + "\n```\n"
    )
    contract = extract_contract(text)
    assert contract.outputs[0].json_schema == {
        "type": "object",
        "properties": {"x": {"type": "string"}},
    }


def test_fenced_code_does_not_create_phantom_sections():
    text = "# A\n\n## Tools\n\n- `x` — y\n\n```python\n# Outputs\nprint('hi')\n```\n"
    contract = extract_contract(text)
    assert contract.outputs == []


def test_heading_synonyms_map_to_the_same_field():
    text = "# A\n\n## Returns\n\n- `out` (json) — thing\n\n## Guardrails\n\n- Never lie.\n"
    contract = extract_contract(text)
    assert [o.name for o in contract.outputs] == ["out"]
    assert len(contract.constraints) == 1


def test_unknown_frontmatter_keys_land_in_metadata():
    text = "---\nid: a\nowner: platform-team\n---\n\n# A\n"
    contract = extract_contract(text)
    assert contract.metadata["owner"] == "platform-team"


def test_empty_document_still_yields_a_contract():
    contract = extract_contract("", source="notes.md")
    assert contract.id
    assert contract.version == "0.1.0"


def test_demo_files_extract(tmp_path):
    contract = extract_from_file(DEMO_REPOS / "planner-agent" / "AGENTS.md")
    assert contract.id == "planner-agent"
    assert "search_web" in contract.tool_names()
    assert "source-required" in contract.constraint_ids()


def test_structured_contract_files_record_their_source_and_honor_an_id_override(tmp_path):
    json_path = tmp_path / "contract.json"
    yaml_path = tmp_path / "ionic.yaml"
    json_path.write_text(json.dumps({"id": "json-agent"}), encoding="utf-8")
    yaml_path.write_text("id: yaml-agent\nversion: 1.0.0\n", encoding="utf-8")

    as_json = extract_from_file(json_path, contract_id="workspace/json-agent")
    as_yaml = extract_from_file(yaml_path, contract_id="workspace/yaml-agent")

    assert as_json.id == "workspace/json-agent"
    assert as_json.source == json_path.as_posix()
    assert as_yaml.id == "workspace/yaml-agent"
    assert as_yaml.source == yaml_path.as_posix()


def test_discover_agent_files_skips_noise(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "AGENTS.md").write_text("# A", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "CLAUDE.md").write_text("# nope", encoding="utf-8")
    found = discover_agent_files(tmp_path)
    assert [p.name for p in found] == ["AGENTS.md"]


def test_render_markdown_roundtrips(planner):
    rendered = render_markdown(planner)
    reparsed = extract_contract(rendered)
    assert reparsed.id == planner.id
    assert reparsed.tool_names() == planner.tool_names()
    assert reparsed.output_names() == planner.output_names()
    assert reparsed.constraint_ids() == planner.constraint_ids()
