from __future__ import annotations

import json
from pathlib import Path

import pytest

from ionic.drift import DriftStatus, detect_drift
from ionic.models import Contract
from ionic.registry import Registry, RegistryStateChanged
from ionic.workspace import (
    discover_instruction_files,
    scan_workspace,
    sync_workspace,
    workspace_check,
)


def write_agent(
    root: Path,
    relative: str,
    agent_id: str,
    *,
    name: str | None = None,
    identity: str = "",
    tools: tuple[str, ...] = (),
    outputs: tuple[tuple[str, str], ...] = (),
    constraints: tuple[tuple[str, str], ...] = (),
    depends_on: tuple[dict | str, ...] = (),
) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": agent_id,
        "version": "1.0.0",
        "identity": identity,
        "tools": [{"name": tool} for tool in tools],
        "outputs": [{"name": output, "format": fmt} for output, fmt in outputs],
        "constraints": [
            {"id": constraint_id, "statement": statement}
            for constraint_id, statement in constraints
        ],
        "depends_on": list(depends_on),
    }
    path.write_text(
        f"# {name or agent_id}\n\n```ionic json\n{json.dumps(payload, indent=2)}\n```\n",
        encoding="utf-8",
    )
    return path


def repo(repo_id: str, path: Path) -> dict[str, str]:
    return {"id": repo_id, "path": str(path)}


def test_two_repositories_can_keep_the_same_local_agent_id(tmp_path: Path):
    one, two = tmp_path / "one", tmp_path / "two"
    write_agent(one, "AGENTS.md", "planner", identity="Plans alpha.")
    write_agent(two, "AGENTS.md", "planner", identity="Plans beta.")

    report = scan_workspace([repo("alpha", one), repo("beta", two)])

    assert report.status == "ready"
    assert [agent.ref for agent in report.agents] == ["alpha/planner", "beta/planner"]
    assert len({agent.instance_id for agent in report.agents}) == 2


def test_same_repo_bare_dependency_wins_then_global_unique_resolves(tmp_path: Path):
    alpha, beta = tmp_path / "alpha", tmp_path / "beta"
    write_agent(alpha, "planner/AGENTS.md", "planner", tools=("plan",))
    write_agent(
        alpha,
        "consumer/AGENTS.md",
        "local-consumer",
        depends_on=({"contract_id": "planner", "requires_tools": ["plan"]},),
    )
    write_agent(
        beta,
        "consumer/AGENTS.md",
        "remote-consumer",
        depends_on=({"contract_id": "planner", "requires_tools": ["plan"]},),
    )

    report = scan_workspace([repo("alpha", alpha), repo("beta", beta)])
    by_ref = {agent.ref: agent.contract for agent in report.agents}

    assert by_ref["alpha/local-consumer"].dependency_ids() == ["alpha/planner"]
    assert by_ref["beta/remote-consumer"].dependency_ids() == ["alpha/planner"]
    assert report.status == "ready"


def test_ambiguous_bare_dependency_blocks_but_explicit_qualified_id_works(tmp_path: Path):
    alpha, beta, gamma = tmp_path / "alpha", tmp_path / "beta", tmp_path / "gamma"
    write_agent(alpha, "AGENTS.md", "planner")
    write_agent(beta, "AGENTS.md", "planner")
    write_agent(gamma, "ambiguous/AGENTS.md", "ambiguous", depends_on=("planner",))
    write_agent(gamma, "explicit/AGENTS.md", "explicit", depends_on=("alpha/planner",))

    report = scan_workspace([repo("alpha", alpha), repo("beta", beta), repo("gamma", gamma)])

    assert report.status == "blocked"
    conflict = next(item for item in report.conflicts if item.kind == "ambiguous_dependency")
    assert conflict.blocking is True
    assert {"alpha/planner", "beta/planner"} <= set(conflict.evidence)
    explicit = next(agent for agent in report.agents if agent.ref == "gamma/explicit")
    assert explicit.contract.dependency_ids() == ["alpha/planner"]


def test_requirement_mismatch_is_a_blocking_structural_conflict(tmp_path: Path):
    root = tmp_path / "repo"
    write_agent(root, "planner/AGENTS.md", "planner", tools=("plan",))
    write_agent(
        root,
        "consumer/AGENTS.md",
        "consumer",
        depends_on=({"contract_id": "planner", "requires_tools": ["missing"]},),
    )

    report = scan_workspace([repo("app", root)])

    assert report.status == "blocked"
    conflict = next(item for item in report.conflicts if item.kind == "unresolved_tools_requirement")
    assert conflict.severity.value == "critical"
    assert "missing" in conflict.evidence[0]


def test_divergent_same_agent_documents_do_not_silently_choose_a_winner(tmp_path: Path):
    root = tmp_path / "repo"
    write_agent(root, "AGENTS.md", "planner", identity="Plans.", tools=("one",))
    write_agent(root, ".github/copilot-instructions.md", "planner", identity="Executes.", tools=("two",))

    report = scan_workspace([repo("app", root)])

    kinds = {conflict.kind for conflict in report.conflicts}
    assert {"divergent_agent_documents", "identity_conflict", "tools_conflict"} <= kinds
    assert "app/planner" not in {agent.ref for agent in report.agents}
    assert all(conflict.evidence for conflict in report.conflicts)


def test_structured_tool_signature_conflicts_are_classified(tmp_path: Path):
    root = tmp_path / "repo"
    first = write_agent(root, "AGENTS.md", "runner", tools=("run",))
    second = write_agent(root, "CLAUDE.md", "runner", tools=("run",))
    first.write_text(
        first.read_text(encoding="utf-8").replace(
            '"name": "run"', '"name": "run", "signature": "(value: str)"'
        ),
        encoding="utf-8",
    )
    second.write_text(
        second.read_text(encoding="utf-8").replace(
            '"name": "run"', '"name": "run", "signature": "(value: int)"'
        ),
        encoding="utf-8",
    )

    report = scan_workspace([repo("app", root)])

    assert report.status == "blocked"
    assert any(conflict.kind == "tools_conflict" for conflict in report.conflicts)


def test_identical_duplicate_documents_collapse_without_losing_document_provenance(tmp_path: Path):
    root = tmp_path / "repo"
    first = write_agent(root, "AGENTS.md", "planner", tools=("one",))
    second = root / "nested" / "CLAUDE.md"
    second.parent.mkdir(parents=True)
    second.write_text(first.read_text(encoding="utf-8"), encoding="utf-8")

    report = scan_workspace([repo("app", root)])

    assert report.status == "ready"
    assert len(report.documents) == 2
    assert [agent.ref for agent in report.agents] == ["app/planner"]
    duplicate = next(item for item in report.conflicts if item.kind == "duplicate_agent_document")
    assert duplicate.blocking is False


def test_discovery_supports_vendor_instruction_files_and_excludes_noise(tmp_path: Path):
    root = tmp_path / "repo"
    write_agent(root, "GEMINI.md", "gemini")
    write_agent(root, ".github/copilot-instructions.md", "copilot")
    write_agent(root, "policies/security.instructions.md", "security")
    write_agent(root, "node_modules/AGENTS.md", "noise")
    write_agent(root, ".git/CLAUDE.md", "noise-two")

    found = [path.relative_to(root).as_posix() for path in discover_instruction_files(root)]

    assert found == [
        ".github/copilot-instructions.md",
        "GEMINI.md",
        "policies/security.instructions.md",
    ]


def test_repository_and_agent_namespace_segments_are_strict(tmp_path: Path):
    root = tmp_path / "repo"
    write_agent(root, "AGENTS.md", "nested/planner")

    invalid_repo = scan_workspace([repo("team/app", root)])
    invalid_agent = scan_workspace([repo("app", root)])

    assert invalid_repo.status == "blocked"
    assert "must match" in invalid_repo.errors[0].message
    assert invalid_agent.status == "blocked"
    assert any(conflict.kind == "invalid_agent_id" for conflict in invalid_agent.conflicts)


def test_repeated_scans_are_byte_for_byte_deterministic_as_models(tmp_path: Path):
    root = tmp_path / "repo"
    write_agent(root, "AGENTS.md", "planner", tools=("plan",))

    first = scan_workspace([repo("app", root)])
    second = scan_workspace([repo("app", root)])

    assert first.scan_id == second.scan_id
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_one_unreadable_document_is_a_scoped_error_not_an_aborted_scan(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "repo"
    blocked = write_agent(root, "AGENTS.md", "blocked")
    write_agent(root, "nested/CLAUDE.md", "healthy")
    original = Path.read_bytes

    def selective_read(path: Path):
        if path == blocked.resolve():
            raise PermissionError("denied")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", selective_read)

    report = scan_workspace([repo("app", root)])

    assert report.status == "blocked"
    assert [(error.repository_id, error.path) for error in report.errors] == [
        ("app", "AGENTS.md")
    ]
    assert [agent.ref for agent in report.agents] == ["app/healthy"]


def test_sync_plans_then_requires_the_reviewed_scan_and_is_noop_aware(tmp_path: Path):
    root = tmp_path / "repo"
    source = write_agent(root, "AGENTS.md", "planner", tools=("plan",))
    registry = Registry(tmp_path / "registry.db")
    repositories = [repo("app", root)]

    plan = sync_workspace(repositories, registry)
    assert plan.status == "planned"
    assert plan.applied is False
    assert plan.actions["add"] == ["app/planner"]
    assert registry.list() == []

    stale = sync_workspace(repositories, registry, "wrong", apply=True)
    assert stale.status == "blocked"
    assert any(conflict.kind == "stale_plan" for conflict in stale.conflicts)
    assert registry.list() == []

    applied = sync_workspace(repositories, registry, plan.scan_id, apply=True)
    assert applied.status == "synced"
    assert applied.applied is True
    assert registry.get("app/planner").source == source.resolve().as_posix()
    assert len(registry.history("app/planner")) == 1
    assert detect_drift(registry)[0].status is DriftStatus.IN_SYNC

    second_plan = sync_workspace(repositories, registry)
    assert second_plan.actions["unchanged"] == ["app/planner"]
    second = sync_workspace(repositories, registry, second_plan.scan_id, apply=True)
    assert second.actions["unchanged"] == ["app/planner"]
    assert len(registry.history("app/planner")) == 1
    registry.close()


def test_selected_agent_sync_preserves_other_agents_and_prune_is_repo_scoped(tmp_path: Path):
    root = tmp_path / "repo"
    write_agent(root, "one/AGENTS.md", "one")
    write_agent(root, "two/AGENTS.md", "two")
    registry = Registry(tmp_path / "registry.db")
    registry.register(Contract(id="unrelated"))
    repositories = [repo("app", root)]

    plan = sync_workspace(repositories, registry, selected_refs=["app/one"])
    applied = sync_workspace(
        repositories,
        registry,
        plan.scan_id,
        selected_refs=["app/one"],
        apply=True,
    )
    assert applied.actions["add"] == ["app/one"]
    assert registry.exists("app/two") is False
    assert registry.exists("unrelated") is True

    write_agent(root, "one/AGENTS.md", "renamed")
    prune_plan = sync_workspace(repositories, registry, prune=True)
    assert prune_plan.actions["prune"] == ["app/one"]
    sync_workspace(repositories, registry, prune_plan.scan_id, apply=True, prune=True)
    assert registry.exists("app/one") is False
    assert registry.exists("unrelated") is True
    registry.close()


def test_workspace_check_uses_batch_overlay_and_avoids_false_unresolved_findings(tmp_path: Path):
    root = tmp_path / "repo"
    write_agent(root, "planner/AGENTS.md", "planner", tools=("plan",))
    write_agent(root, "consumer/AGENTS.md", "consumer", depends_on=("planner",))
    registry = Registry(tmp_path / "registry.db")

    report = workspace_check([repo("app", root)], registry)

    assert report.status == "checked"
    assert len(report.checks) == 2
    consumer = next(check for check in report.checks if check.contract_id == "app/consumer")
    assert not any(finding.kind == "unresolved_dependency" for finding in consumer.findings)
    registry.close()


def test_sync_blocks_an_incompatible_baseline_change_before_mutation(tmp_path: Path):
    root = tmp_path / "repo"
    producer = write_agent(root, "producer/AGENTS.md", "producer", tools=("search",))
    producer.write_text(
        producer.read_text(encoding="utf-8").replace(
            '"name": "search"', '"name": "search", "signature": "(query: str)"'
        ),
        encoding="utf-8",
    )
    write_agent(
        root,
        "consumer/AGENTS.md",
        "consumer",
        depends_on=({"contract_id": "producer", "requires_tools": ["search"]},),
    )
    registry = Registry(tmp_path / "registry.db")
    repositories = [repo("app", root)]
    initial = sync_workspace(repositories, registry)
    assert sync_workspace(repositories, registry, initial.scan_id, apply=True).status == "synced"

    producer.write_text(
        producer.read_text(encoding="utf-8").replace("(query: str)", "(query: int)"),
        encoding="utf-8",
    )
    blocked = sync_workspace(repositories, registry)

    assert blocked.status == "blocked"
    assert any(check.verdict.value == "REQUEST_CHANGES" for check in blocked.checks)
    # A blocked plan may still describe the prospective update, but apply must
    # refuse even when handed the exact scan id.
    refused = sync_workspace(repositories, registry, blocked.scan_id, apply=True)
    assert refused.status == "blocked"
    assert registry.get("app/producer").tools[0].signature == "(query: str)"
    registry.close()


def test_sync_allows_a_coordinated_producer_consumer_migration(tmp_path: Path):
    root = tmp_path / "repo"
    write_agent(root, "producer/AGENTS.md", "producer", tools=("search",))
    write_agent(
        root,
        "consumer/AGENTS.md",
        "consumer",
        depends_on=({"contract_id": "producer", "requires_tools": ["search"]},),
    )
    registry = Registry(tmp_path / "registry.db")
    repositories = [repo("app", root)]
    initial = sync_workspace(repositories, registry)
    sync_workspace(repositories, registry, initial.scan_id, apply=True)

    write_agent(root, "producer/AGENTS.md", "producer", tools=("lookup",))
    write_agent(
        root,
        "consumer/AGENTS.md",
        "consumer",
        depends_on=({"contract_id": "producer", "requires_tools": ["lookup"]},),
    )
    coordinated = sync_workspace(repositories, registry)

    assert coordinated.status == "planned"
    assert all(check.verdict.value == "APPROVED" for check in coordinated.checks)
    applied = sync_workspace(repositories, registry, coordinated.scan_id, apply=True)
    assert applied.status == "synced"
    assert registry.get("app/producer").tool_names() == {"lookup"}
    registry.close()


def test_registry_batch_rolls_back_every_write_if_one_write_fails(tmp_path: Path, monkeypatch):
    registry = Registry(tmp_path / "registry.db")
    original = registry._write_in_transaction
    calls = 0

    def exploding(contract):
        nonlocal calls
        calls += 1
        original(contract)
        if calls == 2:
            raise RuntimeError("boom")

    monkeypatch.setattr(registry, "_write_in_transaction", exploding)

    try:
        registry.sync_batch([Contract(id="one"), Contract(id="two")])
    except RuntimeError:
        pass
    else:  # pragma: no cover - protects the rollback assertion
        raise AssertionError("the injected write failure did not fire")

    assert registry.list() == []
    assert registry.stats()["revisions"] == 0
    registry.close()


def test_sync_plan_token_binds_source_registry_selection_and_prune(tmp_path: Path):
    root = tmp_path / "repo"
    write_agent(root, "AGENTS.md", "planner")
    repositories = [repo("app", root)]
    registry = Registry(tmp_path / "one.db")
    other_registry = Registry(tmp_path / "two.db")

    source_scan = scan_workspace(repositories)
    all_plan = sync_workspace(repositories, registry)
    selected_plan = sync_workspace(
        repositories, registry, selected_refs=["app/planner"]
    )
    prune_plan = sync_workspace(repositories, registry, prune=True)
    other_path_plan = sync_workspace(repositories, other_registry)

    assert all_plan.source_scan_id == source_scan.scan_id
    assert all_plan.model_dump(mode="json") == sync_workspace(
        repositories, registry
    ).model_dump(mode="json")
    assert all_plan.scan_id != source_scan.scan_id
    assert len(all_plan.scan_id) == 64
    assert selected_plan.source_scan_id == all_plan.source_scan_id
    assert selected_plan.scan_id != all_plan.scan_id
    assert prune_plan.scan_id != all_plan.scan_id
    assert other_path_plan.registry_state_id == all_plan.registry_state_id
    assert other_path_plan.scan_id != all_plan.scan_id

    wrong_token = sync_workspace(
        repositories, registry, source_scan.scan_id, apply=True
    )
    assert wrong_token.status == "blocked"
    assert any(conflict.kind == "stale_plan" for conflict in wrong_token.conflicts)
    assert registry.list() == []

    applied = sync_workspace(
        repositories, registry, all_plan.scan_id, apply=True
    )
    assert applied.status == "synced"
    assert applied.scan_id == all_plan.scan_id
    assert applied.source_scan_id == source_scan.scan_id

    registry.close()
    other_registry.close()


def test_registry_mutation_invalidates_an_old_plan_without_partial_writes(tmp_path: Path):
    root = tmp_path / "repo"
    write_agent(root, "AGENTS.md", "planner")
    repositories = [repo("app", root)]
    registry = Registry(tmp_path / "registry.db")
    plan = sync_workspace(repositories, registry)

    registry.register(Contract(id="unrelated"))
    refused = sync_workspace(
        repositories, registry, plan.scan_id, apply=True
    )

    assert refused.status == "blocked"
    assert any(conflict.kind == "stale_plan" for conflict in refused.conflicts)
    assert registry.exists("app/planner") is False
    assert registry.exists("unrelated") is True
    registry.close()


def test_registry_compare_and_swap_race_becomes_a_scoped_conflict(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "repo"
    write_agent(root, "AGENTS.md", "planner")
    repositories = [repo("app", root)]
    registry_path = tmp_path / "registry.db"
    registry = Registry(registry_path)
    competitor = Registry(registry_path)
    plan = sync_workspace(repositories, registry)
    original = registry.sync_batch

    def racing_sync(contracts, **kwargs):
        competitor.register(Contract(id="concurrent-writer"))
        return original(contracts, **kwargs)

    monkeypatch.setattr(registry, "sync_batch", racing_sync)
    refused = sync_workspace(
        repositories, registry, plan.scan_id, apply=True
    )

    assert refused.status == "blocked"
    conflict = next(
        conflict for conflict in refused.conflicts if conflict.kind == "stale_registry"
    )
    assert conflict.blocking is True
    assert len(conflict.evidence) == 2
    assert registry.exists("app/planner") is False
    assert registry.exists("concurrent-writer") is True
    competitor.close()
    registry.close()


def test_prune_requires_explicit_workspace_and_repository_ownership(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    registry = Registry(tmp_path / "registry.db")
    registry.register(
        Contract(
            id="app/owned-stale",
            metadata={
                "workspace": {"workspace_id": "local", "repository_id": "app"}
            },
        )
    )
    registry.register(Contract(id="app/prefix-only"))
    registry.register(
        Contract(
            id="app/other-workspace",
            metadata={
                "workspace": {"workspace_id": "other", "repository_id": "app"}
            },
        )
    )
    registry.register(
        Contract(
            id="app/other-repository",
            metadata={
                "workspace": {"workspace_id": "local", "repository_id": "other"}
            },
        )
    )
    repositories = [repo("app", root)]

    plan = sync_workspace(repositories, registry, prune=True)
    assert plan.actions["prune"] == ["app/owned-stale"]
    applied = sync_workspace(
        repositories, registry, plan.scan_id, apply=True, prune=True
    )

    assert applied.status == "synced"
    assert registry.exists("app/owned-stale") is False
    assert registry.exists("app/prefix-only") is True
    assert registry.exists("app/other-workspace") is True
    assert registry.exists("app/other-repository") is True
    registry.close()


def test_selected_sync_requires_a_compatible_final_dependency_closure(tmp_path: Path):
    root = tmp_path / "repo"
    write_agent(root, "producer/AGENTS.md", "producer", tools=("search",))
    write_agent(
        root,
        "consumer/AGENTS.md",
        "consumer",
        depends_on=(
            {"contract_id": "producer", "requires_tools": ["search"]},
        ),
    )
    repositories = [repo("app", root)]
    registry = Registry(tmp_path / "registry.db")

    missing = sync_workspace(
        repositories, registry, selected_refs=["app/consumer"]
    )
    assert missing.status == "blocked"
    assert any(
        conflict.kind == "selected_dependency_missing"
        for conflict in missing.conflicts
    )

    registry.register(Contract(id="app/producer"))
    incompatible = sync_workspace(
        repositories, registry, selected_refs=["app/consumer"]
    )
    assert incompatible.status == "blocked"
    assert any(
        conflict.kind == "selected_dependency_tools_mismatch"
        for conflict in incompatible.conflicts
    )

    closure_one = sync_workspace(
        repositories,
        registry,
        selected_refs=["app/consumer", "app/producer"],
    )
    closure_two = sync_workspace(
        repositories,
        registry,
        selected_refs=["app/producer", "app/consumer"],
    )
    assert closure_one.status == "planned"
    assert closure_one.scan_id == closure_two.scan_id
    applied = sync_workspace(
        repositories,
        registry,
        closure_one.scan_id,
        selected_refs=["app/producer", "app/consumer"],
        apply=True,
    )
    assert applied.status == "synced"
    assert registry.get("app/producer").tool_names() == {"search"}
    registry.close()


def test_selected_sync_ignores_unrelated_scoped_conflicts_and_keeps_plan_token(
    tmp_path: Path,
):
    root = tmp_path / "repo"
    write_agent(root, "healthy/AGENTS.md", "healthy")
    write_agent(root, "broken/AGENTS.md", "broken", depends_on=("missing",))
    repositories = [repo("app", root)]
    registry = Registry(tmp_path / "registry.db")

    whole_workspace = sync_workspace(repositories, registry)
    assert whole_workspace.status == "blocked"

    plan = sync_workspace(
        repositories, registry, selected_refs=["app/healthy"]
    )
    assert plan.status == "planned"
    conflict = next(
        item for item in plan.conflicts if item.kind == "unresolved_dependency"
    )
    assert conflict.agent_refs == ["app/broken"]
    assert conflict.blocking is False

    applied = sync_workspace(
        repositories,
        registry,
        plan.scan_id,
        selected_refs=["app/healthy"],
        apply=True,
    )
    assert applied.status == "synced"
    assert applied.scan_id == plan.scan_id
    assert registry.exists("app/healthy") is True
    assert registry.exists("app/broken") is False
    registry.close()


def test_selected_sync_blocks_conflicts_in_required_dependency_closure(
    tmp_path: Path,
):
    root = tmp_path / "repo"
    write_agent(root, "consumer/AGENTS.md", "consumer", depends_on=("producer",))
    write_agent(root, "producer/AGENTS.md", "producer", depends_on=("missing",))
    registry = Registry(tmp_path / "registry.db")

    report = sync_workspace(
        [repo("app", root)], registry, selected_refs=["app/consumer"]
    )

    assert report.status == "blocked"
    conflict = next(
        item
        for item in report.conflicts
        if item.kind == "unresolved_dependency"
        and item.agent_refs == ["app/producer"]
    )
    assert conflict.blocking is True
    registry.close()


def test_headingless_instruction_files_get_clean_deterministic_local_ids(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "AGENTS.md").write_text("Always verify the result.\n", encoding="utf-8")
    copilot = root / ".github" / "copilot-instructions.md"
    copilot.parent.mkdir()
    copilot.write_text("Prefer small changes.\n", encoding="utf-8")

    report = scan_workspace([repo("app", root)])

    assert report.status == "ready"
    assert [agent.ref for agent in report.agents] == [
        "app/agents",
        "app/github-copilot-instructions",
    ]
    assert [agent.contract.name for agent in report.agents] == [
        "agents",
        "github-copilot-instructions",
    ]
    assert not any(conflict.kind == "invalid_agent_id" for conflict in report.conflicts)
    assert report.model_dump(mode="json") == scan_workspace(
        [repo("app", root)]
    ).model_dump(mode="json")


def test_nested_repository_boundaries_are_not_scanned_and_roots_cannot_overlap(
    tmp_path: Path,
):
    parent = tmp_path / "parent"
    child = parent / "child"
    write_agent(parent, "AGENTS.md", "parent-agent")
    write_agent(child, "AGENTS.md", "child-agent")
    (child / ".git").mkdir()

    found = [
        path.relative_to(parent).as_posix()
        for path in discover_instruction_files(parent)
    ]
    assert found == ["AGENTS.md"]
    parent_only = scan_workspace([repo("parent", parent)])
    assert [agent.ref for agent in parent_only.agents] == ["parent/parent-agent"]

    overlapping = scan_workspace(
        [repo("parent", parent), repo("child", child)]
    )
    conflict = next(
        conflict
        for conflict in overlapping.conflicts
        if conflict.kind == "overlapping_repository_roots"
    )
    assert conflict.blocking is True
    assert overlapping.status == "blocked"


def test_same_agent_behavior_with_different_versions_has_no_winner(tmp_path: Path):
    root = tmp_path / "repo"
    first = write_agent(root, "AGENTS.md", "planner", tools=("plan",))
    second = root / "CLAUDE.md"
    second.write_text(
        first.read_text(encoding="utf-8").replace(
            '"version": "1.0.0"', '"version": "2.0.0"'
        ),
        encoding="utf-8",
    )

    report = scan_workspace([repo("app", root)])

    assert report.status == "blocked"
    assert any(conflict.kind == "version_conflict" for conflict in report.conflicts)
    assert "app/planner" not in {agent.ref for agent in report.agents}


def test_sync_batch_expected_state_mismatch_rolls_back_the_entire_batch(tmp_path: Path):
    registry_path = tmp_path / "registry.db"
    first = Registry(registry_path)
    second = Registry(registry_path)
    expected = first.state_fingerprint()
    second.register(Contract(id="concurrent"))

    with pytest.raises(RegistryStateChanged) as exc_info:
        first.sync_batch(
            [Contract(id="desired")],
            prune_ids=["concurrent"],
            expected_state=expected,
        )

    assert exc_info.value.expected == expected
    assert exc_info.value.actual != expected
    assert first.exists("desired") is False
    assert first.exists("concurrent") is True
    second.close()
    first.close()
