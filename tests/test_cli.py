from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from ionic.cli import app

from conftest import DEMO_REPOS

runner = CliRunner()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path / "registry.db"


def run(*args: str, **kwargs):
    return runner.invoke(app, list(args), **kwargs)


def test_init_creates_a_registry_and_config(tmp_path):
    result = run("init", str(tmp_path))
    assert result.exit_code == 0
    assert (tmp_path / ".ionic" / "registry.db").exists()
    assert (tmp_path / ".ionic" / "config.toml").exists()
    assert "Ionic initialised" in result.stdout


def test_register_a_directory_then_list(workspace):
    result = run("register", str(DEMO_REPOS), "--registry", str(workspace))
    assert result.exit_code == 0
    assert "3 registered" in result.stdout

    listed = run("list", "--json", "--registry", str(workspace))
    assert listed.exit_code == 0
    assert {contract["id"] for contract in json.loads(listed.stdout)} == {
        "planner-agent",
        "researcher-agent",
        "publisher-agent",
    }


def test_register_refuses_duplicates_without_force(workspace):
    args = ("register", str(DEMO_REPOS / "planner-agent" / "AGENTS.md"), "--registry", str(workspace))
    assert run(*args).exit_code == 0
    second = run(*args)
    assert second.exit_code == 1
    assert run(*args, "--force").exit_code == 0


def test_show_json(workspace):
    run("register", str(DEMO_REPOS), "--registry", str(workspace))
    result = run("show", "planner-agent", "--json", "--registry", str(workspace))
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["id"] == "planner-agent"


def test_show_missing_contract_exits_nonzero(workspace):
    result = run("show", "nope", "--registry", str(workspace))
    assert result.exit_code == 1


def test_check_blocks_a_breaking_change(workspace):
    run("register", str(DEMO_REPOS), "--registry", str(workspace))
    result = run(
        "check",
        "planner-agent",
        "--against",
        str(DEMO_REPOS / "planner-agent" / "AGENTS.proposed.md"),
        "--no-llm",
        "--registry",
        str(workspace),
    )
    assert result.exit_code == 1
    assert "REQUEST_CHANGES" in result.stdout
    assert "search_web" in result.stdout


def test_check_approves_an_unchanged_contract(workspace):
    run("register", str(DEMO_REPOS), "--registry", str(workspace))
    result = run(
        "check",
        "planner-agent",
        "--against",
        str(DEMO_REPOS / "planner-agent" / "AGENTS.md"),
        "--no-llm",
        "--registry",
        str(workspace),
    )
    assert result.exit_code == 0
    assert "APPROVED" in result.stdout


def test_check_defaults_to_structural_and_requires_explicit_llm_opt_in(
    workspace, monkeypatch
):
    import ionic.cli as cli
    from ionic.judge import NullJudge

    run("register", str(DEMO_REPOS), "--registry", str(workspace))
    enabled_values = []

    def capture_judge(_config, *, enabled=True):
        enabled_values.append(enabled)
        return NullJudge()

    monkeypatch.setattr(cli, "build_judge", capture_judge)
    common = (
        "check",
        "planner-agent",
        "--against",
        str(DEMO_REPOS / "planner-agent" / "AGENTS.md"),
        "--format",
        "json",
        "--registry",
        str(workspace),
    )

    structural = run(*common)
    semantic = run(*common, "--llm")

    assert structural.exit_code == 0
    assert json.loads(structural.stdout)["judge"]["enabled"] is False
    assert semantic.exit_code == 0
    assert enabled_values == [False, True]


def test_check_json_and_markdown_formats(workspace):
    run("register", str(DEMO_REPOS), "--registry", str(workspace))
    common = (
        "check",
        "planner-agent",
        "--against",
        str(DEMO_REPOS / "planner-agent" / "AGENTS.proposed.md"),
        "--no-llm",
        "--registry",
        str(workspace),
    )
    as_json = run(*common, "--format", "json")
    payload = json.loads(as_json.stdout)
    assert payload["verdict"] == "REQUEST_CHANGES"
    assert payload["judge"]["enabled"] is False

    as_md = run(*common, "--format", "markdown")
    assert "## Ionic compatibility check" in as_md.stdout


def test_check_defaults_to_structural_analysis(workspace):
    run("register", str(DEMO_REPOS), "--registry", str(workspace))
    result = run(
        "check",
        "planner-agent",
        "--against",
        str(DEMO_REPOS / "planner-agent" / "AGENTS.md"),
        "--format",
        "json",
        "--registry",
        str(workspace),
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout)["judge"]["enabled"] is False


def test_check_respects_fail_on(workspace):
    run("register", str(DEMO_REPOS), "--registry", str(workspace))
    # An identical contract has no findings at all, so even the strictest
    # threshold approves it.
    result = run(
        "check",
        "planner-agent",
        "--against",
        str(DEMO_REPOS / "planner-agent" / "AGENTS.md"),
        "--no-llm",
        "--fail-on",
        "info",
        "--registry",
        str(workspace),
    )
    assert result.exit_code == 0


def test_check_rejects_a_bad_fail_on(workspace):
    result = run("check", "x", "--fail-on", "catastrophic", "--registry", str(workspace))
    assert result.exit_code == 2


def test_check_defaults_to_the_recorded_source(workspace):
    run("register", str(DEMO_REPOS), "--registry", str(workspace))
    result = run("check", "planner-agent", "--no-llm", "--registry", str(workspace))
    assert result.exit_code == 0  # source file is unchanged


def test_graph_formats(workspace):
    run("register", str(DEMO_REPOS), "--registry", str(workspace))

    tree = run("graph", "--registry", str(workspace))
    assert "planner-agent" in tree.stdout
    assert "used by" in tree.stdout

    as_json = run("graph", "--format", "json", "--registry", str(workspace))
    payload = json.loads(as_json.stdout)
    assert len(payload["nodes"]) == 3

    dot = run("graph", "--format", "dot", "--registry", str(workspace))
    assert "digraph ionic" in dot.stdout


def test_extract_writes_json(tmp_path):
    out = tmp_path / "contract.json"
    result = run(
        "extract",
        str(DEMO_REPOS / "researcher-agent" / "CLAUDE.md"),
        "-o",
        str(out),
    )
    assert result.exit_code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["id"] == "researcher-agent"


def test_rm(workspace):
    run("register", str(DEMO_REPOS), "--registry", str(workspace))
    assert run("rm", "publisher-agent", "--registry", str(workspace)).exit_code == 0
    assert "publisher-agent" not in run("list", "--registry", str(workspace)).stdout


def test_status_reports_no_telemetry(workspace):
    result = run("status", "--registry", str(workspace))
    assert result.exit_code == 0
    assert "telemetry" in result.stdout
    assert "none" in result.stdout


def test_status_json_reports_desktop_protocol_four(workspace):
    result = run("status", "--json", "--registry", str(workspace))
    assert result.exit_code == 0
    assert json.loads(result.stdout)["desktop_protocol"] == 4


def test_status_json_reports_the_selected_provider_credential(workspace, monkeypatch):
    monkeypatch.setenv("IONIC_JUDGE_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "status-only-secret")

    result = run("status", "--json", "--registry", str(workspace))

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["judge"]["provider"] == "openai"
    assert payload["judge"]["model"] == "gpt-5.2"
    assert payload["judge"]["credentials_present"] is True
    assert "status-only-secret" not in result.stdout


def test_status_json_reports_subscription_runtime_without_stale_api_provider(
    workspace, monkeypatch
):
    monkeypatch.setenv("IONIC_MODEL_ACCESS", "subscription")
    monkeypatch.setenv("IONIC_SUBSCRIPTION_RUNTIME", "xai-grok-build")
    monkeypatch.setenv("IONIC_JUDGE_MODEL", "grok-4.5")

    result = run("status", "--json", "--registry", str(workspace))

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["judge"] == {
        "access": "subscription",
        "provider": "xai-grok-build",
        "model": "grok-4.5",
        "description": "xAI Grok Build subscription runtime",
        "credentials_present": False,
    }


# ---------------------------------------------------------------------------
# local multi-repository workspace surface
# ---------------------------------------------------------------------------


def test_workspace_scan_accepts_repeated_repositories_and_json_alias(tmp_path, monkeypatch):
    import ionic.cli as cli

    captured = {}

    def scan_workspace(repositories, workspace_id="local"):
        captured["repositories"] = repositories
        captured["workspace_id"] = workspace_id
        return {
            "operation": "scan",
            "status": "ready",
            "scan_id": "scan-1",
            "summary": {"repositories": 2, "blocking_conflicts": 0},
        }

    monkeypatch.setattr(cli, "workspace_engine", SimpleNamespace(scan_workspace=scan_workspace))
    result = run(
        "workspace",
        "scan",
        "--repo",
        f"alpha={tmp_path / 'alpha'}",
        "--repo",
        f"beta={tmp_path / 'beta'}",
        "--json",
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["scan_id"] == "scan-1"
    assert payload["telemetry"] == "none"
    assert [repo["id"] for repo in captured["repositories"]] == ["alpha", "beta"]


def test_workspace_scan_parses_a_manifest_relative_to_it(tmp_path, monkeypatch):
    import ionic.cli as cli

    manifest = tmp_path / "ionic-workspace.json"
    manifest.write_text(
        json.dumps(
            {
                "workspace_id": "system-one",
                "repositories": [{"id": "planner", "path": "repos/planner"}],
            }
        ),
        encoding="utf-8",
    )
    captured = {}

    def scan_workspace(repositories, workspace_id="local"):
        captured.update(repositories=repositories, workspace_id=workspace_id)
        return {"operation": "scan", "status": "ready", "scan_id": "m1"}

    monkeypatch.setattr(cli, "workspace_engine", SimpleNamespace(scan_workspace=scan_workspace))
    result = run("workspace", "scan", "--manifest", str(manifest), "--json")

    assert result.exit_code == 0, result.stdout
    assert captured["workspace_id"] == "system-one"
    assert Path(captured["repositories"][0]["path"]) == (tmp_path / "repos/planner").resolve()


def test_workspace_scan_blockers_are_a_trustworthy_exit_one(tmp_path, monkeypatch):
    import ionic.cli as cli

    def scan_workspace(repositories, workspace_id="local"):
        return {
            "operation": "scan",
            "status": "blocked",
            "scan_id": "blocked-1",
            "conflicts": [
                {
                    "kind": "duplicate_agent_identity",
                    "severity": "high",
                    "blocking": True,
                    "message": "two agents share an id",
                }
            ],
        }

    monkeypatch.setattr(cli, "workspace_engine", SimpleNamespace(scan_workspace=scan_workspace))
    result = run("workspace", "scan", "--repo", f"a={tmp_path}", "--json")

    assert result.exit_code == 1
    assert json.loads(result.stdout)["status"] == "blocked"


def test_workspace_rejects_an_unqualified_repo_as_json(tmp_path):
    result = run("workspace", "scan", "--repo", str(tmp_path), "--json")
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["error_code"] == "INVALID_INPUT"
    assert payload["telemetry"] == "none"


def test_workspace_missing_repository_is_an_operational_exit_two(tmp_path):
    result = run(
        "workspace",
        "scan",
        "--repo",
        f"missing={tmp_path / 'does-not-exist'}",
        "--json",
    )
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["error_code"] == "REPOSITORY_NOT_FOUND"
    assert payload["status"] == "blocked"


def test_workspace_check_passes_an_open_registry_then_closes_it(tmp_path, monkeypatch):
    import sqlite3

    import ionic.cli as cli
    from ionic.registry import Registry

    captured = {}

    def workspace_check(repositories, registry, *, fail_on, transitive, workspace_id="local"):
        assert isinstance(registry, Registry)
        captured["registry"] = registry
        captured["transitive"] = transitive
        return {"operation": "check", "status": "checked", "summary": {"blocking": 0}}

    monkeypatch.setattr(cli, "workspace_engine", SimpleNamespace(workspace_check=workspace_check))
    result = run(
        "workspace",
        "check",
        "--repo",
        f"repo={tmp_path}",
        "--registry",
        str(tmp_path / "registry.db"),
        "--transitive",
        "--no-llm",
        "--json",
    )

    assert result.exit_code == 0, result.stdout
    assert captured["transitive"] is True
    with pytest.raises(sqlite3.ProgrammingError):
        captured["registry"].stats()


def test_workspace_check_rejects_semantic_mode_until_supported(tmp_path):
    result = run(
        "workspace", "check", "--repo", f"repo={tmp_path}", "--llm", "--json"
    )
    assert result.exit_code == 2
    assert "not supported" in json.loads(result.stdout)["error"]


def test_workspace_check_exits_one_for_a_blocking_core_check(tmp_path, monkeypatch):
    import ionic.cli as cli

    def workspace_check(repositories, registry, *, fail_on, transitive, workspace_id="local"):
        return {
            "operation": "check",
            "status": "checked",
            "checks": [{"verdict": "REQUEST_CHANGES", "blocking": True}],
            "conflicts": [],
            "errors": [],
        }

    monkeypatch.setattr(cli, "workspace_engine", SimpleNamespace(workspace_check=workspace_check))
    result = run(
        "workspace",
        "check",
        "--repo",
        f"repo={tmp_path}",
        "--registry",
        str(tmp_path / "registry.db"),
        "--json",
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["checks"][0]["verdict"] == "REQUEST_CHANGES"


def test_workspace_sync_plan_is_read_only_and_distinguishes_plan_from_source_scan(
    tmp_path, monkeypatch
):
    import ionic.cli as cli

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
            "scan_id": "reviewed-plan-7",
            "source_scan_id": "source-scan-3",
            "applied": False,
            "actions": [],
        }

    monkeypatch.setattr(
        cli,
        "workspace_engine",
        SimpleNamespace(sync_workspace=sync_workspace),
    )
    result = run("workspace", "sync", "--repo", f"repo={tmp_path}", "--json")

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["operation"] == "sync"
    assert payload["applied"] is False
    assert payload["scan_id"] == "reviewed-plan-7"
    assert payload["source_scan_id"] == "source-scan-3"
    assert payload["scan_id"] != payload["source_scan_id"]
    assert captured == {"apply": False, "expected_scan_id": None}


def test_workspace_sync_help_defines_the_reviewed_plan_token():
    result = run("workspace", "sync", "--help")
    help_text = " ".join(result.stdout.split())

    assert result.exit_code == 0, result.stdout
    assert "Reviewed sync plan token: the exact scan_id" in help_text
    assert "matching read-only workspace" in help_text
    assert "workspace scan source token" in help_text
    assert "is not valid." in help_text


def test_workspace_sync_apply_requires_the_reviewed_plan_token(tmp_path):
    result = run(
        "workspace", "sync", "--repo", f"repo={tmp_path}", "--apply", "--json"
    )
    assert result.exit_code == 3
    payload = json.loads(result.stdout)
    assert payload["error_code"] == "EXPECTED_SCAN_REQUIRED"
    assert "reviewed sync plan token" in payload["error"]
    assert "source_scan_id" in payload["error"]
    assert "cannot authorize apply" in payload["error"]


def test_workspace_sync_passes_selected_agent_refs(tmp_path, monkeypatch):
    import ionic.cli as cli

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
        captured["expected_scan_id"] = expected_scan_id
        captured["selected_refs"] = selected_refs
        captured["prune"] = prune
        captured["apply"] = apply
        return {"operation": "sync", "status": "synced", "scan_id": expected_scan_id}

    monkeypatch.setattr(cli, "workspace_engine", SimpleNamespace(sync_workspace=sync_workspace))
    result = run(
        "workspace",
        "sync",
        "--repo",
        f"repo={tmp_path}",
        "--registry",
        str(tmp_path / "registry.db"),
        "--apply",
        "--expected-scan",
        "reviewed-plan-9",
        "--agent",
        "repo/planner",
        "--prune",
        "--json",
    )

    assert result.exit_code == 0, result.stdout
    assert captured == {
        "expected_scan_id": "reviewed-plan-9",
        "selected_refs": ["repo/planner"],
        "prune": True,
        "apply": True,
    }


@pytest.mark.parametrize(
    ("stale_kind", "expected_code"),
    [("stale_scan", "STALE_SCAN"), ("stale_plan", "STALE_PLAN"), ("stale_registry", "STALE_REGISTRY")],
)
def test_workspace_sync_stale_apply_emits_report_and_exits_three(
    tmp_path, monkeypatch, stale_kind, expected_code
):
    import ionic.cli as cli

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

    monkeypatch.setattr(cli, "workspace_engine", SimpleNamespace(sync_workspace=sync_workspace))
    result = run(
        "workspace",
        "sync",
        "--repo",
        f"repo={tmp_path}",
        "--registry",
        str(tmp_path / "registry.db"),
        "--apply",
        "--expected-scan",
        "old-scan",
        "--json",
    )

    assert result.exit_code == 3
    payload = json.loads(result.stdout)
    assert payload["error_code"] == expected_code
    assert payload["applied"] is False


def test_workspace_sync_text_renders_core_action_dictionary(tmp_path, monkeypatch):
    import ionic.cli as cli

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
            "status": "planned",
            "scan_id": "plan",
            "actions": {
                "add": ["repo/planner"],
                "update": ["repo/researcher"],
                "unchanged": [],
                "prune": [],
            },
        }

    monkeypatch.setattr(cli, "workspace_engine", SimpleNamespace(sync_workspace=sync_workspace))
    result = run("workspace", "sync", "--repo", f"repo={tmp_path}")

    assert result.exit_code == 0, result.stdout
    assert "repo/planner" in result.stdout
    assert "repo/researcher" in result.stdout
    assert "add" in result.stdout
    assert "update" in result.stdout


# ---------------------------------------------------------------------------
# drift, export/import, history, diff
# ---------------------------------------------------------------------------


@pytest.fixture
def scratch_repo(tmp_path):
    """A copy of the demo repos so tests can edit source files freely."""
    import shutil

    repos = tmp_path / "repos"
    shutil.copytree(DEMO_REPOS, repos)
    registry = tmp_path / "registry.db"
    run("register", str(repos), "--registry", str(registry))
    return repos, registry


def test_drift_is_clean_after_registering(scratch_repo):
    _, registry = scratch_repo
    result = run("drift", "--registry", str(registry))
    assert result.exit_code == 0
    assert "every contract matches" in result.stdout


def test_drift_detects_an_edited_source(scratch_repo):
    repos, registry = scratch_repo
    target = repos / "planner-agent" / "AGENTS.md"
    target.write_text(
        target.read_text(encoding="utf-8").replace("- `search_web` — Run a scoping search to confirm a step is answerable before committing to it.\n", ""),
        encoding="utf-8",
    )

    result = run("drift", "--registry", str(registry))
    assert result.exit_code == 1
    assert "drifted" in result.stdout
    assert "planner-agent" in result.stdout


def test_drift_json(scratch_repo):
    _, registry = scratch_repo
    result = run("drift", "--json", "--registry", str(registry))
    payload = json.loads(result.stdout)
    assert {r["contract_id"] for r in payload} == {
        "planner-agent",
        "publisher-agent",
        "researcher-agent",
    }
    assert all(r["status"] == "in_sync" for r in payload)


def test_status_reports_stale_sources(scratch_repo):
    repos, registry = scratch_repo
    (repos / "planner-agent" / "AGENTS.md").unlink()
    result = run("status", "--json", "--registry", str(registry))
    payload = json.loads(result.stdout)
    assert payload["drift"]["stale"] == ["planner-agent"]


def test_check_all_finds_what_changed_on_disk(scratch_repo):
    repos, registry = scratch_repo
    target = repos / "planner-agent" / "AGENTS.md"
    target.write_text(
        target.read_text(encoding="utf-8").replace("- `plan` (json)", "- `plan` (markdown)"), encoding="utf-8"
    )

    result = run("check", "--all", "--no-llm", "--registry", str(registry))
    assert result.exit_code == 1
    assert "REQUEST_CHANGES" in result.stdout
    assert "format changed" in result.stdout


def test_check_all_is_quiet_when_nothing_changed(scratch_repo):
    _, registry = scratch_repo
    result = run("check", "--all", "--no-llm", "--registry", str(registry))
    assert result.exit_code == 0
    assert "nothing to check" in result.stdout


def test_check_rejects_both_an_id_and_all(scratch_repo):
    _, registry = scratch_repo
    result = run("check", "planner-agent", "--all", "--registry", str(registry))
    assert result.exit_code == 2


def test_check_requires_an_id_or_all(scratch_repo):
    _, registry = scratch_repo
    result = run("check", "--registry", str(registry))
    assert result.exit_code == 2


def test_export_import_roundtrip_through_the_cli(scratch_repo, tmp_path):
    _, registry = scratch_repo
    export_path = tmp_path / "export.json"

    assert run("export", "-o", str(export_path), "--registry", str(registry)).exit_code == 0
    payload = json.loads(export_path.read_text(encoding="utf-8"))
    assert len(payload["contracts"]) == 3

    fresh = tmp_path / "fresh.db"
    result = run("import", str(export_path), "--registry", str(fresh))
    assert result.exit_code == 0
    assert "imported 3" in result.stdout

    listed = run("list", "--json", "--registry", str(fresh))
    assert len(json.loads(listed.stdout)) == 3


def test_export_to_stdout_is_valid_json(scratch_repo):
    _, registry = scratch_repo
    result = run("export", "--registry", str(registry))
    assert len(json.loads(result.stdout)["contracts"]) == 3


def test_import_rejects_a_non_json_file(tmp_path):
    junk = tmp_path / "junk.json"
    junk.write_text("not json", encoding="utf-8")
    assert run("import", str(junk), "--registry", str(tmp_path / "r.db")).exit_code == 2


def test_history_shows_every_revision(scratch_repo):
    repos, registry = scratch_repo
    run("register", str(repos / "planner-agent" / "AGENTS.md"), "--force", "--registry", str(registry))

    result = run("history", "planner-agent", "--registry", str(registry))
    assert result.exit_code == 0
    assert "2 revision(s)" in result.stdout
    assert "first" in result.stdout
    assert "registration" in result.stdout


def test_history_json(scratch_repo):
    _, registry = scratch_repo
    result = run("history", "planner-agent", "--json", "--registry", str(registry))
    entries = json.loads(result.stdout)
    assert entries[0]["version"] == "1.4.0"
    assert "fingerprint" in entries[0]


def test_diff_against_a_file(scratch_repo):
    _, registry = scratch_repo
    result = run(
        "diff",
        "planner-agent",
        "--against",
        str(DEMO_REPOS / "planner-agent" / "AGENTS.proposed.md"),
        "--registry",
        str(registry),
    )
    assert result.exit_code == 0
    assert "search_web" in result.stdout
    assert "tool_removed" in result.stdout


def test_diff_with_a_single_revision_says_so(scratch_repo):
    _, registry = scratch_repo
    result = run("diff", "planner-agent", "--registry", str(registry))
    assert result.exit_code == 0
    assert "only ever been registered once" in result.stdout


def test_diff_between_revisions(scratch_repo):
    repos, registry = scratch_repo
    target = repos / "planner-agent" / "AGENTS.md"
    target.write_text(
        target.read_text(encoding="utf-8").replace("- `estimate_effort`", "- `estimate_cost`"),
        encoding="utf-8",
    )
    run("register", str(target), "--force", "--registry", str(registry))

    result = run("diff", "planner-agent", "--registry", str(registry))
    assert result.exit_code == 0
    assert "estimate_effort" in result.stdout


def test_graph_reports_cycles(tmp_path):
    registry = tmp_path / "cycles.db"
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(json.dumps({"id": "a", "depends_on": ["b"]}), encoding="utf-8")
    b.write_text(json.dumps({"id": "b", "depends_on": ["a"]}), encoding="utf-8")
    run("register", str(a), "--registry", str(registry))
    run("register", str(b), "--registry", str(registry))

    result = run("graph", "--registry", str(registry))
    assert "cycle" in result.stdout
