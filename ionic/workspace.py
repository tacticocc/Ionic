"""Offline multi-repository discovery, checking, and registry synchronization.

This module deliberately sits above the original flat registry.  A contract is
namespaced as ``<repository>/<local-id>`` only while building a workspace
snapshot, which preserves every existing single-repository API and data file.
Repository roots are local facts; scans never use the network or invoke git.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal, Sequence

from pydantic import Field, field_validator

from .compat import check_compatibility
from .extract import extract_from_file
from .models import (
    CompatibilityReport,
    Contract,
    Dependency,
    IonicModel,
    Severity,
    slugify,
)
from .registry import Registry, RegistryStateChanged

WORKSPACE_SCHEMA_VERSION = "1.0"
_REPOSITORY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_STABLE_TIME = datetime(1970, 1, 1, tzinfo=timezone.utc)
_SUPPORTED_EXACT = {
    "agents.md",
    "agent.md",
    "claude.md",
    "gemini.md",
    ".ionic.yaml",
    "ionic.yaml",
    ".ionic.yml",
    "ionic.yml",
}
_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".ionic",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "dist",
    "build",
    ".next",
    ".cache",
}


class WorkspaceRepository(IonicModel):
    id: str
    path: str
    exists: bool = True
    document_count: int = 0
    agent_count: int = 0

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _REPOSITORY_ID_RE.fullmatch(normalized):
            raise ValueError("repository id must match [a-z0-9][a-z0-9._-]{0,63}")
        return normalized


class WorkspaceDocument(IonicModel):
    repository_id: str
    path: str
    kind: str
    sha256: str
    agent_refs: list[str] = Field(default_factory=list)
    lines: int = 0


class WorkspaceAgent(IonicModel):
    ref: str
    instance_id: str
    fingerprint: str
    contract: Contract
    source: str


class WorkspaceConflict(IonicModel):
    kind: str
    severity: Severity
    blocking: bool
    message: str
    evidence: list[str] = Field(default_factory=list)
    recommendation: str = ""
    # Empty means the conflict is workspace/repository scoped.  Agent-scoped
    # conflicts name the agents whose declarations must be healthy for an
    # operation to proceed.
    agent_refs: list[str] = Field(default_factory=list)


class WorkspaceError(IonicModel):
    repository_id: str | None = None
    path: str | None = None
    message: str


class WorkspaceNetwork(IonicModel):
    used: bool = False


class WorkspaceReport(IonicModel):
    schema_version: str = WORKSPACE_SCHEMA_VERSION
    workspace_id: str = "local"
    operation: Literal["scan", "check", "sync"] = "scan"
    status: Literal["ready", "checked", "planned", "synced", "blocked"] = "ready"
    scan_id: str
    source_scan_id: str | None = None
    registry_path: str | None = None
    registry_state_id: str | None = None
    telemetry: Literal["none"] = "none"
    network: WorkspaceNetwork = Field(default_factory=WorkspaceNetwork)
    repositories: list[WorkspaceRepository] = Field(default_factory=list)
    documents: list[WorkspaceDocument] = Field(default_factory=list)
    agents: list[WorkspaceAgent] = Field(default_factory=list)
    conflicts: list[WorkspaceConflict] = Field(default_factory=list)
    errors: list[WorkspaceError] = Field(default_factory=list)
    checks: list[CompatibilityReport] = Field(default_factory=list)
    actions: dict[str, list[str]] = Field(default_factory=dict)
    applied: bool = False
    summary: dict[str, Any] = Field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        return bool(
            self.errors
            or any(conflict.blocking for conflict in self.conflicts)
            or any(check.verdict.value == "REQUEST_CHANGES" for check in self.checks)
        )


def discover_instruction_files(root: Path | str) -> list[Path]:
    """Discover supported instruction files without following repository escapes."""
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        return []
    found: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            relative = path.resolve().relative_to(root)
        except ValueError:
            continue
        if any(part.lower() in _SKIP_DIRS for part in relative.parts[:-1]):
            continue
        if _inside_nested_repository(root, relative):
            continue
        lowered = path.name.lower()
        is_copilot = relative.as_posix().lower() == ".github/copilot-instructions.md"
        if lowered in _SUPPORTED_EXACT or lowered.endswith(".instructions.md") or is_copilot:
            found.append(path.resolve())
    return sorted(found, key=lambda value: value.relative_to(root).as_posix().lower())


def _inside_nested_repository(root: Path, relative: Path) -> bool:
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        if (current / ".git").exists() or (current / ".hg").exists():
            return True
    return False


def scan_workspace(
    repositories: Sequence[dict[str, Any] | WorkspaceRepository],
    workspace_id: str = "local",
) -> WorkspaceReport:
    """Build a deterministic, offline snapshot across repository roots."""
    repo_inputs, input_conflicts, errors = _normalize_repositories(repositories)
    conflicts = [*input_conflicts]
    documents: list[WorkspaceDocument] = []
    candidates: list[tuple[str, str, Path, Contract]] = []

    for repo in repo_inputs:
        root = Path(repo.path)
        if not root.is_dir():
            errors.append(
                WorkspaceError(repository_id=repo.id, path=repo.path, message="repository path is not a directory")
            )
            repo.exists = False
            continue
        for path in discover_instruction_files(root):
            relative = path.relative_to(root).as_posix()
            try:
                raw = path.read_bytes()
            except OSError as exc:
                errors.append(
                    WorkspaceError(
                        repository_id=repo.id,
                        path=relative,
                        message=f"{type(exc).__name__}: {exc}",
                    )
                )
                continue
            text = raw.decode("utf-8", errors="replace")
            document = WorkspaceDocument(
                repository_id=repo.id,
                path=relative,
                kind=_document_kind(relative),
                sha256=hashlib.sha256(raw).hexdigest(),
                lines=len(text.splitlines()),
            )
            documents.append(document)
            try:
                contract = extract_from_file(path)
            except Exception as exc:
                errors.append(
                    WorkspaceError(repository_id=repo.id, path=relative, message=f"{type(exc).__name__}: {exc}")
                )
                continue
            if _used_path_fallback_id(contract, path):
                fallback_id = _fallback_local_id(relative)
                contract = contract.revise(id=fallback_id, name=fallback_id)
            local_id = contract.id
            if "/" in local_id:
                conflicts.append(
                    WorkspaceConflict(
                        kind="invalid_agent_id",
                        severity=Severity.CRITICAL,
                        blocking=True,
                        message=(
                            f"Agent id `{local_id}` contains `/`; workspace-local ids must "
                            "not contain the repository separator."
                        ),
                        evidence=[f"{repo.id}:{relative}"],
                        recommendation="Use a local id without `/`; Ionic adds the repository namespace.",
                        agent_refs=[f"{repo.id}/{local_id}"],
                    )
                )
                continue
            ref = f"{repo.id}/{local_id}"
            document.agent_refs.append(ref)
            candidates.append((repo.id, relative, path, contract))

    grouped: dict[str, list[tuple[str, str, Path, Contract]]] = defaultdict(list)
    for candidate in candidates:
        grouped[f"{candidate[0]}/{candidate[3].id}"].append(candidate)

    chosen: dict[str, tuple[str, str, Path, Contract]] = {}
    for ref in sorted(grouped):
        group = grouped[ref]
        if len(group) == 1:
            chosen[ref] = group[0]
            continue
        first = group[0]
        first_fp = first[3].fingerprint()
        behavior_divergent = any(item[3].fingerprint() != first_fp for item in group[1:])
        version_divergent = any(item[3].version != first[3].version for item in group[1:])
        divergent = behavior_divergent or version_divergent
        conflicts.extend(
            _document_conflicts(
                ref,
                group,
                divergent=divergent,
                behavior_divergent=behavior_divergent,
                version_divergent=version_divergent,
            )
        )
        # Identical duplicate declarations can safely collapse to a deterministic
        # representative. Divergent declarations are retained as a conflict but
        # never silently select a winner for synchronization.
        if not divergent:
            chosen[ref] = first

    local_occurrences: dict[str, list[str]] = defaultdict(list)
    for ref in chosen:
        local_occurrences[ref.split("/", 1)[1]].append(ref)

    agents: list[WorkspaceAgent] = []
    for ref in sorted(chosen):
        repo_id, relative, source_path, local = chosen[ref]
        resolved_dependencies: list[Dependency] = []
        dependency_map: dict[str, str] = {}
        for dependency in local.depends_on:
            resolved, conflict = _resolve_dependency(
                dependency.contract_id,
                repo_id,
                set(chosen),
                local_occurrences,
                source_ref=ref,
                source_path=relative,
                source_file=source_path,
            )
            if conflict:
                conflicts.append(conflict)
            target = resolved or dependency.contract_id
            resolved_dependencies.append(dependency.revise(contract_id=target))
            if resolved:
                dependency_map[dependency.contract_id] = resolved

        namespaced = local.revise(
            id=ref,
            depends_on=[dependency.model_dump(mode="json") for dependency in resolved_dependencies],
            source=source_path.resolve().as_posix(),
            metadata={
                **local.metadata,
                "workspace": {
                    "repository_id": repo_id,
                    "workspace_id": workspace_id,
                    "local_id": local.id,
                    "relative_source": relative,
                    "dependency_map": dependency_map,
                },
            },
            created_at=_STABLE_TIME,
            updated_at=_STABLE_TIME,
        )
        agents.append(
            WorkspaceAgent(
                ref=ref,
                instance_id=_instance_id(workspace_id, ref),
                fingerprint=namespaced.fingerprint(),
                contract=namespaced,
                source=f"{repo_id}:{relative}",
            )
        )

    _requirement_conflicts(agents, conflicts)
    for repo in repo_inputs:
        repo.document_count = sum(d.repository_id == repo.id for d in documents)
        repo.agent_count = sum(a.ref.startswith(f"{repo.id}/") for a in agents)

    conflicts.sort(key=lambda conflict: (not conflict.blocking, -conflict.severity.rank, conflict.kind, conflict.message))
    scan_id = _scan_id(workspace_id, repo_inputs, documents, agents, conflicts, errors)
    blocking = bool(errors or any(conflict.blocking for conflict in conflicts))
    return WorkspaceReport(
        workspace_id=workspace_id,
        operation="scan",
        status="blocked" if blocking else "ready",
        scan_id=scan_id,
        source_scan_id=scan_id,
        repositories=repo_inputs,
        documents=documents,
        agents=agents,
        conflicts=conflicts,
        errors=errors,
        summary=_summary(repo_inputs, documents, agents, conflicts, errors),
    )


def workspace_check(
    repositories: Sequence[dict[str, Any] | WorkspaceRepository],
    registry: Registry,
    *,
    fail_on: Severity | str = Severity.HIGH,
    transitive: bool = False,
    workspace_id: str = "local",
    _snapshot: dict[str, Any] | None = None,
) -> WorkspaceReport:
    """Check every scanned agent against a registry with a batch overlay.

    Proposed workspace contracts are used as dependent definitions where
    available, so coordinated changes are judged together instead of solely
    against stale registry dependents.
    """
    severity = fail_on if isinstance(fail_on, Severity) else Severity(str(fail_on).lower())
    report = scan_workspace(repositories, workspace_id=workspace_id)
    report.operation = "check"
    snapshot = _snapshot or registry.snapshot()
    report.registry_path = str(snapshot["path"])
    report.registry_state_id = str(snapshot["state_id"])
    if report.blocking:
        report.status = "blocked"
        return report

    current = {contract.id: contract for contract in snapshot["contracts"]}
    overlay = dict(current)
    overlay.update({agent.ref: agent.contract for agent in report.agents})
    checks = _check_agents(
        report.agents,
        current,
        overlay,
        fail_on=severity,
        transitive=transitive,
    )
    report.checks = checks
    blocked_checks = sum(check.verdict.value == "REQUEST_CHANGES" for check in checks)
    report.status = "blocked" if blocked_checks else "checked"
    report.summary = {
        **report.summary,
        "checks": len(checks),
        "blocked_checks": blocked_checks,
    }
    return report


def sync_workspace(
    repositories: Sequence[dict[str, Any] | WorkspaceRepository],
    registry: Registry,
    expected_scan_id: str | None = None,
    selected_refs: Iterable[str] | None = None,
    *,
    apply: bool = False,
    prune: bool = False,
    workspace_id: str = "local",
) -> WorkspaceReport:
    """Plan or atomically apply a workspace-to-registry reconciliation."""
    snapshot = registry.snapshot()
    report = workspace_check(
        repositories,
        registry,
        fail_on=Severity.HIGH,
        transitive=False,
        workspace_id=workspace_id,
        _snapshot=snapshot,
    )
    report.operation = "sync"
    selected = {value.strip().lower() for value in (selected_refs or ()) if value.strip()}
    known = {agent.ref for agent in report.agents}
    unknown = sorted(selected - known)

    if selected:
        relevant_refs = _selected_dependency_closure(selected & known, report.agents)
        report.conflicts = [
            conflict.revise(blocking=False)
            if conflict.blocking
            and conflict.agent_refs
            and relevant_refs.isdisjoint(conflict.agent_refs)
            else conflict
            for conflict in report.conflicts
        ]

    for ref in unknown:
        report.conflicts.append(
            WorkspaceConflict(
                kind="unknown_selected_agent",
                severity=Severity.HIGH,
                blocking=True,
                message=f"Selected agent `{ref}` is not present in this scan.",
                evidence=[ref],
                recommendation="Refresh the scan and choose an agent from its `agents` list.",
                agent_refs=[ref],
            )
        )

    desired = [agent.contract for agent in report.agents if not selected or agent.ref in selected]
    current = {contract.id: contract for contract in snapshot["contracts"]}
    if selected:
        # A selected sync must not borrow an unselected consumer's proposed
        # migration to make a producer change look safe. Overlay only the
        # contracts this operation will actually write; all other dependents
        # remain at their registered baselines.
        selected_agents = [agent for agent in report.agents if agent.ref in selected]
        selected_overlay = dict(current)
        selected_overlay.update({agent.ref: agent.contract for agent in selected_agents})
        report.conflicts.extend(
            _final_dependency_conflicts(selected_agents, selected_overlay)
        )
        report.checks = _check_agents(
            selected_agents,
            current,
            selected_overlay,
            fail_on=Severity.HIGH,
            transitive=False,
        )
        report.summary = {
            **report.summary,
            "checks": len(report.checks),
            "blocked_checks": sum(
                check.verdict.value == "REQUEST_CHANGES" for check in report.checks
            ),
        }
    desired_by_id = {contract.id: contract for contract in desired}
    actions: dict[str, list[str]] = {"add": [], "update": [], "unchanged": [], "prune": []}
    for contract_id in sorted(desired_by_id):
        existing = current.get(contract_id)
        if existing is None:
            actions["add"].append(contract_id)
        elif _contracts_equivalent_for_sync(existing, desired_by_id[contract_id]):
            actions["unchanged"].append(contract_id)
        else:
            actions["update"].append(contract_id)

    if prune:
        repository_ids = {repo.id for repo in report.repositories}
        actions["prune"] = sorted(
            contract_id
            for contract_id, contract in current.items()
            if _workspace_owns_contract(contract, workspace_id, repository_ids)
            and contract_id not in desired_by_id
            and (not selected or contract_id in selected)
        )
    report.actions = actions
    report.summary = {**report.summary, **{f"actions_{key}": len(value) for key, value in actions.items()}}

    source_scan_id = report.source_scan_id or report.scan_id
    plan_token = _plan_token(
        source_scan_id=source_scan_id,
        workspace_id=workspace_id,
        registry_path=str(snapshot["path"]),
        registry_state_id=str(snapshot["state_id"]),
        selected=selected,
        prune=prune,
        desired=desired,
        actions=actions,
    )
    report.source_scan_id = source_scan_id
    report.scan_id = plan_token
    _refresh_summary(report)

    if report.blocking:
        report.status = "blocked"
        return report
    if not apply:
        report.status = "planned"
        return report
    if not expected_scan_id or expected_scan_id != report.scan_id:
        report.conflicts.append(
            WorkspaceConflict(
                kind="stale_plan",
                severity=Severity.CRITICAL,
                blocking=True,
                message="The sync plan no longer matches the reviewed plan; no contracts were written.",
                evidence=[f"expected={expected_scan_id or '<missing>'}", f"actual={report.scan_id}"],
                recommendation="Review the new plan and apply using its scan_id.",
            )
        )
        _refresh_summary(report)
        report.status = "blocked"
        return report

    try:
        result = registry.sync_batch(
            desired,
            prune_ids=actions["prune"],
            expected_state=str(snapshot["state_id"]),
        )
    except RegistryStateChanged as exc:
        report.conflicts.append(
            WorkspaceConflict(
                kind="stale_registry",
                severity=Severity.CRITICAL,
                blocking=True,
                message="The registry changed after this sync plan was reviewed; no contracts were written.",
                evidence=[f"expected={exc.expected}", f"actual={exc.actual}"],
                recommendation="Generate a new sync plan and review it before applying.",
            )
        )
        _refresh_summary(report)
        report.status = "blocked"
        report.applied = False
        return report
    report.actions = {
        "add": result["added"],
        "update": result["updated"],
        "unchanged": result["unchanged"],
        "prune": result["pruned"],
    }
    report.applied = True
    report.status = "synced"
    report.summary = {
        **report.summary,
        "applied": True,
        **{f"actions_{key}": len(value) for key, value in report.actions.items()},
    }
    return report


def _selected_dependency_closure(
    selected: set[str], agents: Iterable[WorkspaceAgent]
) -> set[str]:
    """Return selected agents plus their known workspace dependencies."""
    by_ref = {agent.ref: agent for agent in agents}
    closure = set(selected)
    queue = sorted(selected)
    while queue:
        ref = queue.pop(0)
        agent = by_ref.get(ref)
        if agent is None:
            continue
        for dependency_ref in sorted(agent.contract.dependency_ids()):
            if dependency_ref in by_ref and dependency_ref not in closure:
                closure.add(dependency_ref)
                queue.append(dependency_ref)
    return closure


def _final_dependency_conflicts(
    agents: Iterable[WorkspaceAgent], overlay: dict[str, Contract]
) -> list[WorkspaceConflict]:
    """Validate selected agents against the contracts that will exist after apply."""
    conflicts: list[WorkspaceConflict] = []
    for agent in agents:
        for dependency in agent.contract.depends_on:
            target = overlay.get(dependency.contract_id)
            if target is None:
                conflicts.append(
                    WorkspaceConflict(
                        kind="selected_dependency_missing",
                        severity=Severity.CRITICAL,
                        blocking=True,
                        message=(
                            f"`{agent.ref}` cannot be synced without upstream "
                            f"`{dependency.contract_id}`."
                        ),
                        evidence=[agent.source, dependency.contract_id],
                        recommendation="Sync the dependency closure together, or register a compatible upstream baseline first.",
                        agent_refs=[agent.ref],
                    )
                )
                continue
            requirements = {
                "tools": sorted(set(dependency.requires_tools) - target.tool_names()),
                "capabilities": sorted(
                    set(dependency.requires_capabilities) - set(target.capabilities)
                ),
                "outputs": sorted(set(dependency.expects_outputs) - target.output_names()),
                "constraints": sorted(
                    set(dependency.requires_constraints) - target.constraint_ids()
                ),
            }
            for kind, missing in requirements.items():
                if missing:
                    conflicts.append(
                        WorkspaceConflict(
                            kind=f"selected_dependency_{kind}_mismatch",
                            severity=Severity.CRITICAL,
                            blocking=True,
                            message=(
                                f"`{agent.ref}` requires {kind} absent from the final "
                                f"`{dependency.contract_id}` contract."
                            ),
                            evidence=[f"missing: {', '.join(missing)}", agent.source],
                            recommendation="Sync a compatible upstream contract in the same selected closure.",
                            agent_refs=[agent.ref],
                        )
                    )
            if dependency.expects_format:
                wrong = [
                    output.name
                    for output in target.outputs
                    if output.name in dependency.expects_outputs
                    and output.format != dependency.expects_format
                ]
                if wrong:
                    conflicts.append(
                        WorkspaceConflict(
                            kind="selected_dependency_format_mismatch",
                            severity=Severity.CRITICAL,
                            blocking=True,
                            message=(
                                f"`{agent.ref}` expects a different output format from "
                                f"the final `{dependency.contract_id}` contract."
                            ),
                            evidence=[
                                f"expected {dependency.expects_format.value}: {', '.join(wrong)}",
                                agent.source,
                            ],
                            recommendation="Sync compatible producer and consumer contracts together.",
                            agent_refs=[agent.ref],
                        )
                    )
    return conflicts


def _workspace_owns_contract(
    contract: Contract, workspace_id: str, repository_ids: set[str]
) -> bool:
    workspace = contract.metadata.get("workspace")
    return bool(
        isinstance(workspace, dict)
        and workspace.get("workspace_id") == workspace_id
        and workspace.get("repository_id") in repository_ids
    )


def _plan_token(
    *,
    source_scan_id: str,
    workspace_id: str,
    registry_path: str,
    registry_state_id: str,
    selected: set[str],
    prune: bool,
    desired: list[Contract],
    actions: dict[str, list[str]],
) -> str:
    desired_hashes: dict[str, str] = {}
    for contract in desired:
        payload = contract.model_dump(
            mode="json", exclude={"created_at", "updated_at"}
        )
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        desired_hashes[contract.id] = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    canonical_path = os.path.normcase(str(Path(registry_path).resolve()))
    payload = {
        "domain": "ionic-workspace-sync-plan-v1",
        "source_scan_id": source_scan_id,
        "workspace_id": workspace_id,
        "registry_path": canonical_path,
        "registry_state_id": registry_state_id,
        "selection": {"mode": "selected", "refs": sorted(selected)}
        if selected
        else {"mode": "all", "refs": []},
        "prune": prune,
        "policy": {"fail_on": "high", "transitive": False},
        "desired": desired_hashes,
        "actions": {key: sorted(value) for key, value in sorted(actions.items())},
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _check_agents(
    agents: Iterable[WorkspaceAgent],
    current_contracts: dict[str, Contract],
    overlay: dict[str, Contract],
    *,
    fail_on: Severity,
    transitive: bool,
) -> list[CompatibilityReport]:
    checks: list[CompatibilityReport] = []
    for agent in agents:
        current = current_contracts.get(agent.ref)
        direct = [
            contract
            for contract in overlay.values()
            if agent.ref in contract.dependency_ids() and contract.id != agent.ref
        ]
        dependents = _transitive_overlay_dependents(overlay, agent.ref) if transitive else direct
        check = check_compatibility(
            current, agent.contract, dependents, fail_on=fail_on
        ).revise(generated_at=_STABLE_TIME)
        if current is None:
            resolved_targets = set(overlay)
            filtered = [
                finding
                for finding in check.findings
                if not (
                    finding.kind == "unresolved_dependency"
                    and all(
                        target in resolved_targets
                        for target in agent.contract.dependency_ids()
                    )
                )
            ]
            if len(filtered) != len(check.findings):
                check = check.revise(findings=filtered)
        checks.append(check)
    return checks


def _normalize_repositories(
    repositories: Sequence[dict[str, Any] | WorkspaceRepository],
) -> tuple[list[WorkspaceRepository], list[WorkspaceConflict], list[WorkspaceError]]:
    normalized: list[WorkspaceRepository] = []
    conflicts: list[WorkspaceConflict] = []
    errors: list[WorkspaceError] = []
    seen_ids: dict[str, str] = {}
    seen_paths: dict[str, str] = {}
    for item in repositories:
        raw = item.model_dump() if isinstance(item, WorkspaceRepository) else dict(item)
        repo_id = str(raw.get("id") or "").strip().lower()
        raw_path = raw.get("path")
        if not raw.get("id") or not raw_path:
            errors.append(WorkspaceError(message="each repository requires non-empty `id` and `path`"))
            continue
        if not _REPOSITORY_ID_RE.fullmatch(repo_id):
            errors.append(
                WorkspaceError(
                    repository_id=repo_id or None,
                    path=str(raw_path),
                    message="repository id must match [a-z0-9][a-z0-9._-]{0,63}",
                )
            )
            continue
        path = str(Path(str(raw_path)).expanduser().resolve())
        if repo_id in seen_ids:
            conflicts.append(
                WorkspaceConflict(
                    kind="duplicate_repository_id",
                    severity=Severity.CRITICAL,
                    blocking=True,
                    message=f"Repository id `{repo_id}` was supplied more than once.",
                    evidence=[seen_ids[repo_id], path],
                    recommendation="Assign every repository a stable unique id.",
                )
            )
            continue
        path_key = path.casefold()
        if path_key in seen_paths:
            conflicts.append(
                WorkspaceConflict(
                    kind="duplicate_repository_path",
                    severity=Severity.HIGH,
                    blocking=True,
                    message=f"The same repository path is assigned to `{seen_paths[path_key]}` and `{repo_id}`.",
                    evidence=[path],
                    recommendation="Keep one repository entry for each checkout.",
                )
            )
            continue
        new_path = Path(path)
        for existing_key, existing_id in seen_paths.items():
            existing_path = Path(seen_ids[existing_id])
            if new_path.is_relative_to(existing_path) or existing_path.is_relative_to(new_path):
                conflicts.append(
                    WorkspaceConflict(
                        kind="overlapping_repository_roots",
                        severity=Severity.CRITICAL,
                        blocking=True,
                        message=(
                            f"Repository roots `{existing_id}` and `{repo_id}` overlap."
                        ),
                        evidence=[str(existing_path), str(new_path)],
                        recommendation="Use disjoint checkout roots; nested repositories must be listed separately, not through their parent.",
                    )
                )
        seen_ids[repo_id] = path
        seen_paths[path_key] = repo_id
        normalized.append(WorkspaceRepository(id=repo_id, path=path))
    normalized.sort(key=lambda repo: repo.id)
    return normalized, conflicts, errors


def _document_kind(relative: str) -> str:
    lowered = relative.lower()
    name = Path(relative).name.lower()
    if lowered == ".github/copilot-instructions.md":
        return "copilot"
    if name.endswith(".instructions.md"):
        return "instructions"
    if name.startswith("agents") or name == "agent.md":
        return "agents"
    if name == "claude.md":
        return "claude"
    if name == "gemini.md":
        return "gemini"
    return "ionic"


def _used_path_fallback_id(contract: Contract, path: Path) -> bool:
    """Detect extract_contract's last-resort source-path-derived identity."""
    if contract.name != contract.id:
        return False
    return contract.id == slugify(path.as_posix())


def _fallback_local_id(relative: str) -> str:
    # Include the filename as well as its parent so headingless AGENTS.md and
    # CLAUDE.md files at the same level remain distinct agents. Strip leading
    # dot-directory punctuation after flattening to keep a valid local slug.
    lowered = relative.lower()
    stem = relative[: -len(".md")] if lowered.endswith(".md") else relative
    flattened = stem.replace("\\", "/").replace("/", "-").lstrip("._-")
    return slugify(flattened)


def _document_conflicts(
    ref: str,
    group: list[tuple[str, str, Path, Contract]],
    *,
    divergent: bool,
    behavior_divergent: bool,
    version_divergent: bool,
) -> list[WorkspaceConflict]:
    paths = [f"{repo}:{relative}" for repo, relative, _path, _contract in group]
    if not divergent:
        return [
            WorkspaceConflict(
                kind="duplicate_agent_document",
                severity=Severity.LOW,
                blocking=False,
                message=f"`{ref}` is declared identically in {len(group)} instruction files.",
                evidence=paths,
                recommendation="Keep one canonical declaration to avoid future divergence.",
                agent_refs=[ref],
            )
        ]

    conflicts: list[WorkspaceConflict] = [
        WorkspaceConflict(
            kind="divergent_agent_documents",
            severity=Severity.CRITICAL,
            blocking=True,
            message=f"`{ref}` has conflicting declarations; Ionic will not choose a winner.",
            evidence=paths,
            recommendation="Give distinct agents distinct ids, or consolidate this agent into one canonical contract.",
            agent_refs=[ref],
        )
    ]
    if version_divergent:
        conflicts.append(
            WorkspaceConflict(
                kind="version_conflict",
                severity=Severity.HIGH,
                blocking=True,
                message=f"`{ref}` declares more than one contract version.",
                evidence=[
                    f"{repo}:{relative}: version {contract.version}"
                    for repo, relative, _path, contract in group
                ],
                recommendation="Use one version for every document declaring this agent.",
                agent_refs=[ref],
            )
        )
    if not behavior_divergent:
        return conflicts
    extractors = {
        "identity": lambda contract: contract.identity.strip(),
        "tools": lambda contract: sorted(
            json.dumps(tool.model_dump(mode="json"), sort_keys=True)
            for tool in contract.tools
        ),
        "outputs": lambda contract: sorted(
            json.dumps(output.model_dump(mode="json"), sort_keys=True)
            for output in contract.outputs
        ),
        "constraints": lambda contract: sorted(
            json.dumps(constraint.model_dump(mode="json"), sort_keys=True)
            for constraint in contract.constraints
        ),
    }
    for field, extractor in extractors.items():
        values = [extractor(item[3]) for item in group]
        if any(value != values[0] for value in values[1:]):
            conflicts.append(
                WorkspaceConflict(
                    kind=f"{field}_conflict",
                    severity=Severity.HIGH,
                    blocking=True,
                    message=f"`{ref}` declares incompatible {field} across instruction files.",
                    evidence=_field_evidence(group, field),
                    recommendation=f"Make the {field} declaration consistent or assign separate agent ids.",
                    agent_refs=[ref],
                )
            )
    return conflicts


def _field_evidence(group: list[tuple[str, str, Path, Contract]], field: str) -> list[str]:
    evidence: list[str] = []
    heading = re.compile(rf"^\s*#+\s+.*{re.escape(field.rstrip('s'))}", re.I)
    for repo, relative, path, _contract in group:
        line_no = 1
        try:
            for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if heading.search(line):
                    line_no = index
                    break
        except OSError:
            pass
        evidence.append(f"{repo}:{relative}:{line_no}")
    return evidence


def _resolve_dependency(
    target: str,
    source_repo: str,
    refs: set[str],
    local_occurrences: dict[str, list[str]],
    *,
    source_ref: str,
    source_path: str,
    source_file: Path,
) -> tuple[str | None, WorkspaceConflict | None]:
    normalized = target.strip().lower()
    source_evidence = _dependency_evidence(
        source_repo, source_path, source_file, normalized
    )
    if "/" in normalized:
        if normalized in refs:
            return normalized, None
        return None, WorkspaceConflict(
            kind="unresolved_dependency",
            severity=Severity.HIGH,
            blocking=True,
            message=f"`{source_ref}` depends on unregistered `{normalized}`.",
            evidence=[source_evidence, normalized],
            recommendation="Add the target repository or correct the qualified contract id.",
            agent_refs=[source_ref],
        )
    same_repo = f"{source_repo}/{normalized}"
    if same_repo in refs:
        return same_repo, None
    matches = local_occurrences.get(normalized, [])
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        return None, WorkspaceConflict(
            kind="ambiguous_dependency",
            severity=Severity.CRITICAL,
            blocking=True,
            message=f"`{source_ref}` uses ambiguous bare dependency `{normalized}`.",
            evidence=[source_evidence, *sorted(matches)],
            recommendation="Qualify it as `<repository>/<contract>`.",
            agent_refs=[source_ref],
        )
    return None, WorkspaceConflict(
        kind="unresolved_dependency",
        severity=Severity.HIGH,
        blocking=True,
        message=f"`{source_ref}` depends on missing `{normalized}`.",
        evidence=[source_evidence],
        recommendation="Add the repository containing this contract or fix the dependency id.",
        agent_refs=[source_ref],
    )


def _dependency_evidence(
    repository_id: str, relative: str, source_file: Path, target: str
) -> str:
    try:
        for line_number, line in enumerate(
            source_file.read_text(encoding="utf-8").splitlines(), 1
        ):
            if target.casefold() in line.casefold():
                return f"{repository_id}:{relative}:{line_number}"
    except OSError:
        pass
    return f"{repository_id}:{relative}"


def _requirement_conflicts(
    agents: list[WorkspaceAgent], conflicts: list[WorkspaceConflict]
) -> None:
    by_ref = {agent.ref: agent for agent in agents}
    for agent in agents:
        for dependency in agent.contract.depends_on:
            target = by_ref.get(dependency.contract_id)
            if target is None:
                continue
            missing_tools = sorted(set(dependency.requires_tools) - target.contract.tool_names())
            missing_capabilities = sorted(
                set(dependency.requires_capabilities) - set(target.contract.capabilities)
            )
            missing_outputs = sorted(set(dependency.expects_outputs) - target.contract.output_names())
            missing_constraints = sorted(
                set(dependency.requires_constraints) - target.contract.constraint_ids()
            )
            mismatches = {
                "tools": missing_tools,
                "capabilities": missing_capabilities,
                "outputs": missing_outputs,
                "constraints": missing_constraints,
            }
            for kind, missing in mismatches.items():
                if not missing:
                    continue
                conflicts.append(
                    WorkspaceConflict(
                        kind=f"unresolved_{kind}_requirement",
                        severity=Severity.CRITICAL,
                        blocking=True,
                        message=f"`{agent.ref}` requires {kind} absent from `{target.ref}`.",
                        evidence=[f"required: {', '.join(missing)}", agent.source, target.source],
                        recommendation="Restore the promised surface or update the dependent declaration.",
                        agent_refs=[agent.ref],
                    )
                )
            if dependency.expects_format and dependency.expects_outputs:
                wrong = [
                    output.name
                    for output in target.contract.outputs
                    if output.name in dependency.expects_outputs
                    and output.format != dependency.expects_format
                ]
                if wrong:
                    conflicts.append(
                        WorkspaceConflict(
                            kind="output_format_requirement_mismatch",
                            severity=Severity.CRITICAL,
                            blocking=True,
                            message=f"`{agent.ref}` expects a different output format from `{target.ref}`.",
                            evidence=[
                                f"expected {dependency.expects_format.value}: {', '.join(wrong)}",
                                agent.source,
                                target.source,
                            ],
                            recommendation="Align the producer format and consumer expectation.",
                            agent_refs=[agent.ref],
                        )
                    )


def _transitive_overlay_dependents(
    overlay: dict[str, Contract], target: str
) -> list[Contract]:
    seen: set[str] = set()
    queue = [target]
    output: list[Contract] = []
    while queue:
        current = queue.pop(0)
        for contract in overlay.values():
            if current not in contract.dependency_ids() or contract.id in seen or contract.id == target:
                continue
            seen.add(contract.id)
            output.append(contract)
            queue.append(contract.id)
    return output


def _contracts_equivalent_for_sync(existing: Contract, proposed: Contract) -> bool:
    left = existing.model_dump(mode="json", exclude={"created_at", "updated_at"})
    right = proposed.model_dump(mode="json", exclude={"created_at", "updated_at"})
    return left == right


def _instance_id(workspace_id: str, ref: str) -> str:
    return hashlib.sha256(f"{workspace_id}\0{ref}".encode()).hexdigest()[:16]


def _scan_id(
    workspace_id: str,
    repositories: list[WorkspaceRepository],
    documents: list[WorkspaceDocument],
    agents: list[WorkspaceAgent],
    conflicts: list[WorkspaceConflict],
    errors: list[WorkspaceError],
) -> str:
    payload = {
        "schema": WORKSPACE_SCHEMA_VERSION,
        "workspace": workspace_id,
        "repositories": [(repo.id, repo.path) for repo in repositories],
        "documents": [(doc.repository_id, doc.path, doc.sha256) for doc in documents],
        "agents": [(agent.ref, agent.fingerprint) for agent in agents],
        "conflicts": [
            (conflict.kind, conflict.severity.value, conflict.message, conflict.evidence)
            for conflict in conflicts
        ],
        "errors": [error.model_dump(mode="json") for error in errors],
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:20]


def _summary(
    repositories: list[WorkspaceRepository],
    documents: list[WorkspaceDocument],
    agents: list[WorkspaceAgent],
    conflicts: list[WorkspaceConflict],
    errors: list[WorkspaceError],
) -> dict[str, Any]:
    severities = Counter(conflict.severity.value for conflict in conflicts)
    return {
        "repositories": len(repositories),
        "documents": len(documents),
        "agents": len(agents),
        "conflicts": len(conflicts),
        "blocking_conflicts": sum(conflict.blocking for conflict in conflicts),
        "errors": len(errors),
        "conflicts_by_severity": {severity.value: severities[severity.value] for severity in Severity},
    }


def _refresh_summary(report: WorkspaceReport) -> None:
    """Keep aggregate counts accurate after sync-specific conflicts are added."""
    report.summary = {
        **report.summary,
        **_summary(
            report.repositories,
            report.documents,
            report.agents,
            report.conflicts,
            report.errors,
        ),
    }


__all__ = [
    "WORKSPACE_SCHEMA_VERSION",
    "WorkspaceAgent",
    "WorkspaceConflict",
    "WorkspaceDocument",
    "WorkspaceError",
    "WorkspaceNetwork",
    "WorkspaceReport",
    "WorkspaceRepository",
    "discover_instruction_files",
    "scan_workspace",
    "sync_workspace",
    "workspace_check",
]
