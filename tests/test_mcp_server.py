"""The MCP server is the primary surface, so its tool contract is tested directly."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import ionic.mcp_server as mcp
from ionic.config import Config
from ionic.registry import Registry

from conftest import DEMO_REPOS


@pytest.fixture
def server(tmp_path: Path, monkeypatch):
    """A server bound to a throwaway registry."""
    registry = Registry(tmp_path / "registry.db")
    monkeypatch.setattr(mcp, "_registry", registry)
    monkeypatch.setattr(mcp, "_config", Config(registry_path=tmp_path / "registry.db"))
    yield mcp.server
    registry.close()


def call(server, name: str, arguments: dict | None = None) -> dict:
    result = asyncio.run(server.call_tool(name, arguments or {}))
    assert result.is_error is False, result.content
    return result.structured_content


def test_all_documented_tools_are_exposed(server):
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert {
        "register_contract",
        "update_contract",
        "list_contracts",
        "check_compatibility",
        "get_dependency_graph",
        "scan_workspace",
        "check_workspace_compatibility",
        "sync_workspace",
    } <= names


def test_every_tool_has_a_description(server):
    for tool in asyncio.run(server.list_tools()):
        assert tool.description and len(tool.description) > 40, tool.name


def test_workspace_tool_descriptions_distinguish_source_scan_from_sync_plan(server):
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    scan_description = tools["scan_workspace"].description
    sync_tool = tools["sync_workspace"]

    assert "source snapshot only" in scan_description
    assert "cannot authorize `sync_workspace` apply" in scan_description
    assert "reviewed sync plan token" in sync_tool.description
    assert "`report.source_scan_id`" in sync_tool.description
    assert "cannot authorize apply" in sync_tool.description

    expected_scan_schema = sync_tool.input_schema["properties"]["expected_scan"]
    assert expected_scan_schema["title"] == "Reviewed sync plan token"
    assert "exact report.scan_id" in expected_scan_schema["description"]
    assert "Never use report.source_scan_id" in expected_scan_schema["description"]


def test_register_then_list(server):
    payload = call(server, "register_contract", {"contract": {"id": "planner", "version": "1.0.0"}})
    assert payload["ok"] is True
    assert payload["contract"]["id"] == "planner"

    listed = call(server, "list_contracts")
    assert listed["count"] == 1


def test_register_rejects_duplicates_then_accepts_force(server):
    contract = {"id": "planner", "version": "1.0.0"}
    call(server, "register_contract", {"contract": contract})

    duplicate = call(server, "register_contract", {"contract": contract})
    assert duplicate["ok"] is False
    assert "already registered" in duplicate["error"]

    forced = call(server, "register_contract", {"contract": contract, "force": True})
    assert forced["ok"] is True


def test_register_reports_invalid_contracts(server):
    payload = call(server, "register_contract", {"contract": {"id": "x", "version": "banana"}})
    assert payload["ok"] is False
    assert "invalid contract" in payload["error"]


def test_update_contract(server):
    call(server, "register_contract", {"contract": {"id": "planner", "version": "1.0.0"}})
    payload = call(
        server, "update_contract", {"contract_id": "planner", "changes": {"version": "1.1.0"}}
    )
    assert payload["contract"]["version"] == "1.1.0"

    missing = call(server, "update_contract", {"contract_id": "ghost", "changes": {}})
    assert missing["ok"] is False


def test_extract_and_register_from_a_file(server):
    payload = call(
        server,
        "extract_contract",
        {"path": str(DEMO_REPOS / "planner-agent" / "AGENTS.md"), "register": True},
    )
    assert payload["contract"]["id"] == "planner-agent"
    assert payload["registered"] is True
    assert call(server, "list_contracts")["count"] == 1


def test_check_compatibility_from_markdown(server):
    for path in (
        DEMO_REPOS / "planner-agent" / "AGENTS.md",
        DEMO_REPOS / "researcher-agent" / "CLAUDE.md",
    ):
        call(server, "extract_contract", {"path": str(path), "register": True})

    proposed = (DEMO_REPOS / "planner-agent" / "AGENTS.proposed.md").read_text(encoding="utf-8")
    payload = call(
        server,
        "check_compatibility",
        {"contract_id": "planner-agent", "proposed_markdown": proposed},
    )
    report = payload["report"]
    assert report["verdict"] == "REQUEST_CHANGES"
    assert "researcher-agent" in report["dependents_checked"]
    assert any(f["severity"] == "critical" for f in report["findings"])
    assert "REQUEST_CHANGES" in report["markdown"]
    assert report["judge"]["enabled"] is False


def test_check_compatibility_defaults_to_structural_analysis(server, monkeypatch):
    call(server, "register_contract", {"contract": {"id": "planner", "version": "1.0.0"}})
    captured = {}

    def build_judge(config, *, enabled):
        captured["enabled"] = enabled
        return None

    monkeypatch.setattr(mcp, "build_judge", build_judge)
    payload = call(
        server,
        "check_compatibility",
        {
            "contract_id": "planner",
            "proposed": {"id": "planner", "version": "1.1.0"},
        },
    )
    assert payload["ok"] is True
    assert captured["enabled"] is False


def test_check_compatibility_requires_a_proposal(server):
    payload = call(server, "check_compatibility", {"contract_id": "planner"})
    assert payload["ok"] is False
    assert "proposed" in payload["error"]


def test_check_compatibility_forces_the_named_id(server):
    """A proposal whose body names a different agent still checks the named one."""
    call(server, "register_contract", {"contract": {"id": "planner", "version": "1.0.0"}})
    payload = call(
        server,
        "check_compatibility",
        {
            "contract_id": "planner",
            "proposed": {"id": "something-else", "version": "1.1.0"},
            "use_llm": False,
        },
    )
    assert payload["report"]["contract_id"] == "planner"


def test_dependency_graph(server):
    for path in (
        DEMO_REPOS / "planner-agent" / "AGENTS.md",
        DEMO_REPOS / "researcher-agent" / "CLAUDE.md",
        DEMO_REPOS / "publisher-agent" / "AGENTS.md",
    ):
        call(server, "extract_contract", {"path": str(path), "register": True})

    graph = call(server, "get_dependency_graph", {"contract_id": "planner-agent"})["graph"]
    assert sorted(graph["transitive_dependents_of_root"]) == [
        "publisher-agent",
        "researcher-agent",
    ]
    assert graph["unresolved_edges"] == []


def test_graph_reports_unresolved_edges(server):
    call(
        server,
        "extract_contract",
        {"path": str(DEMO_REPOS / "researcher-agent" / "CLAUDE.md"), "register": True},
    )
    graph = call(server, "get_dependency_graph")["graph"]
    assert len(graph["unresolved_edges"]) == 1


def test_render_contract_roundtrips(server):
    call(
        server,
        "extract_contract",
        {"path": str(DEMO_REPOS / "planner-agent" / "AGENTS.md"), "register": True},
    )
    markdown = call(server, "render_contract", {"contract_id": "planner-agent"})["markdown"]
    assert "## Tools" in markdown
    assert "search_web" in markdown


def test_registry_status_declares_no_telemetry(server):
    payload = call(server, "registry_status")
    assert payload["telemetry"] == "none"
    assert payload["registry"]["contracts"] == 0


def test_scan_workspace_is_local_read_only_and_preserves_agent_identity(
    server, tmp_path, monkeypatch
):
    captured = {}

    def scan_workspace(repositories, workspace_id="local"):
        captured["repositories"] = repositories
        return {
            "operation": "scan",
            "status": "ready",
            "scan_id": "scan-1",
            "network": {"used": False},
            "agents": [
                {
                    "ref": "a/planner",
                    "instance_id": "a/AGENTS.md#planner",
                },
                {
                    "ref": "b/planner",
                    "instance_id": "b/AGENTS.md#planner",
                },
            ],
        }

    monkeypatch.setattr(mcp, "workspace_engine", SimpleNamespace(scan_workspace=scan_workspace))
    payload = call(
        server,
        "scan_workspace",
        {
            "repositories": [
                {"id": "a", "path": str(tmp_path / "a")},
                {"id": "b", "path": str(tmp_path / "b")},
            ]
        },
    )

    assert payload["ok"] is True
    assert payload["telemetry"] == "none"
    assert payload["report"]["network"]["used"] is False
    assert [agent["ref"] for agent in payload["report"]["agents"]] == ["a/planner", "b/planner"]
    assert [repo["id"] for repo in captured["repositories"]] == ["a", "b"]


def test_workspace_blockers_are_ok_results_not_mcp_errors(server, tmp_path, monkeypatch):
    def scan_workspace(repositories, workspace_id="local"):
        return {
            "operation": "scan",
            "status": "blocked",
            "scan_id": "blocked",
            "conflicts": [
                {
                    "kind": "duplicate_agent_identity",
                    "severity": "high",
                    "blocking": True,
                    "message": "duplicate",
                }
            ],
        }

    monkeypatch.setattr(mcp, "workspace_engine", SimpleNamespace(scan_workspace=scan_workspace))
    payload = call(
        server,
        "scan_workspace",
        {"repositories": [{"id": "repo", "path": str(tmp_path)}]},
    )
    assert payload["ok"] is True
    assert payload["report"]["status"] == "blocked"


def test_scan_workspace_missing_repository_is_a_stable_error(server, tmp_path):
    payload = call(
        server,
        "scan_workspace",
        {
            "repositories": [
                {"id": "missing", "path": str(tmp_path / "does-not-exist")}
            ]
        },
    )
    assert payload["ok"] is False
    assert payload["error_code"] == "REPOSITORY_NOT_FOUND"
    assert payload["report"]["status"] == "blocked"


def test_check_workspace_passes_a_registry_and_closes_it(server, tmp_path, monkeypatch):
    import sqlite3

    captured = {}

    def workspace_check(repositories, registry, *, fail_on, transitive, workspace_id="local"):
        captured["registry"] = registry
        captured["transitive"] = transitive
        return {"operation": "check", "status": "checked", "checks": []}

    monkeypatch.setattr(mcp, "workspace_engine", SimpleNamespace(workspace_check=workspace_check))
    payload = call(
        server,
        "check_workspace_compatibility",
        {
            "repositories": [{"id": "repo", "path": str(tmp_path)}],
            "registry_path": str(tmp_path / "workspace.db"),
            "transitive": True,
        },
    )

    assert payload["ok"] is True
    assert captured["transitive"] is True
    with pytest.raises(sqlite3.ProgrammingError):
        captured["registry"].stats()


def test_sync_workspace_apply_requires_reviewed_plan_token(server, tmp_path):
    payload = call(
        server,
        "sync_workspace",
        {
            "repositories": [{"id": "repo", "path": str(tmp_path)}],
            "apply": True,
        },
    )
    assert payload["ok"] is False
    assert payload["error_code"] == "EXPECTED_SCAN_REQUIRED"
    assert payload["telemetry"] == "none"
    assert "reviewed sync plan token" in payload["error"]
    assert "report.source_scan_id" in payload["error"]
    assert "cannot authorize apply" in payload["error"]


def test_sync_workspace_defaults_to_a_read_only_plan(server, tmp_path, monkeypatch):
    captured = {}

    def sync_workspace(
        repositories,
        registry,
        expected_scan_id=None,
        selected_refs=None,
        *,
        apply=False,
        prune=False,
        workspace_id="local",
    ):
        captured.update(apply=apply, expected_scan_id=expected_scan_id)
        return {
            "operation": "sync",
            "status": "ready",
            "scan_id": "reviewed-plan-1",
            "source_scan_id": "source-scan-1",
            "applied": False,
            "actions": [],
        }

    monkeypatch.setattr(mcp, "workspace_engine", SimpleNamespace(sync_workspace=sync_workspace))
    payload = call(
        server,
        "sync_workspace",
        {
            "repositories": [{"id": "repo", "path": str(tmp_path)}],
            "registry_path": str(tmp_path / "workspace.db"),
        },
    )

    assert payload["ok"] is True
    assert payload["report"]["operation"] == "sync"
    assert payload["report"]["applied"] is False
    assert payload["report"]["scan_id"] == "reviewed-plan-1"
    assert payload["report"]["source_scan_id"] == "source-scan-1"
    assert payload["report"]["scan_id"] != payload["report"]["source_scan_id"]
    assert captured == {"apply": False, "expected_scan_id": None}


def test_sync_workspace_passes_qualified_agent_selection(server, tmp_path, monkeypatch):
    captured = {}

    def sync_workspace(
        repositories,
        registry,
        expected_scan_id,
        selected_refs=None,
        *,
        apply=False,
        prune=False,
        workspace_id="local",
    ):
        captured.update(
            expected_scan_id=expected_scan_id,
            selected_refs=selected_refs,
            prune=prune,
            apply=apply,
        )
        return {"operation": "sync", "status": "synced", "scan_id": expected_scan_id}

    monkeypatch.setattr(mcp, "workspace_engine", SimpleNamespace(sync_workspace=sync_workspace))
    payload = call(
        server,
        "sync_workspace",
        {
            "repositories": [{"id": "repo", "path": str(tmp_path)}],
            "registry_path": str(tmp_path / "workspace.db"),
            "apply": True,
            "expected_scan": "reviewed-plan-2",
            "selected_refs": ["repo/planner"],
            "prune": True,
        },
    )

    assert payload["ok"] is True
    assert captured == {
        "expected_scan_id": "reviewed-plan-2",
        "selected_refs": ["repo/planner"],
        "prune": True,
        "apply": True,
    }


@pytest.mark.parametrize(
    ("stale_kind", "expected_code"),
    [("stale_scan", "STALE_SCAN"), ("stale_plan", "STALE_PLAN"), ("stale_registry", "STALE_REGISTRY")],
)
def test_sync_workspace_stale_apply_is_a_stable_operational_error(
    server, tmp_path, monkeypatch, stale_kind, expected_code
):
    def sync_workspace(
        repositories,
        registry,
        expected_scan_id=None,
        selected_refs=None,
        *,
        apply=False,
        prune=False,
        workspace_id="local",
    ):
        return {
            "operation": "sync",
            "status": "blocked",
            "scan_id": "new-scan",
            "applied": False,
            "conflicts": [
                {
                    "kind": stale_kind,
                    "severity": "critical",
                    "blocking": True,
                    "message": "workspace changed",
                }
            ],
        }

    monkeypatch.setattr(mcp, "workspace_engine", SimpleNamespace(sync_workspace=sync_workspace))
    payload = call(
        server,
        "sync_workspace",
        {
            "repositories": [{"id": "repo", "path": str(tmp_path)}],
            "registry_path": str(tmp_path / "workspace.db"),
            "apply": True,
            "expected_scan": "old-scan",
        },
    )

    assert payload["ok"] is False
    assert payload["error_code"] == expected_code
    assert payload["retryable"] is True
    assert payload["report"]["status"] == "blocked"


def test_sync_workspace_instruction_conflicts_remain_ok_results(
    server, tmp_path, monkeypatch
):
    def sync_workspace(
        repositories,
        registry,
        expected_scan_id=None,
        selected_refs=None,
        *,
        apply=False,
        prune=False,
        workspace_id="local",
    ):
        return {
            "operation": "sync",
            "status": "blocked",
            "scan_id": "scan",
            "applied": False,
            "errors": [],
            "conflicts": [
                {
                    "kind": "divergent_agent_documents",
                    "severity": "critical",
                    "blocking": True,
                    "message": "conflicting instructions",
                }
            ],
        }

    monkeypatch.setattr(mcp, "workspace_engine", SimpleNamespace(sync_workspace=sync_workspace))
    payload = call(
        server,
        "sync_workspace",
        {
            "repositories": [{"id": "repo", "path": str(tmp_path)}],
            "registry_path": str(tmp_path / "workspace.db"),
        },
    )

    assert payload["ok"] is True
    assert payload["report"]["status"] == "blocked"


def test_scan_workspace_returns_stable_operational_errors(server):
    payload = call(server, "scan_workspace", {"repositories": []})
    assert payload["ok"] is False
    assert payload["error_code"] == "INVALID_INPUT"
    assert payload["retryable"] is False
