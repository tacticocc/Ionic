"""Ionic MCP server.

The primary interface. Agents and orchestrators register their contracts and
check changes natively, without shelling out to a CLI.

Run it with `ionic serve` (stdio) or `ionic serve --http`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from . import __version__, workspace as workspace_engine
from .compat import check_against_registry, render_markdown
from .config import Config
from .drift import detect_drift, problems, summarize
from .extract import extract_contract, extract_from_file, render_markdown as render_contract_md
from .judge import JudgeUnavailable, build_judge
from .models import Contract, Severity
from .registry import ContractExists, ContractNotFound, Registry

INSTRUCTIONS = """\
Ionic keeps multi-agent systems from falling apart.

Register each agent's behavioral contract once, then check proposed changes
against the contracts that depend on them before shipping. A check returns
APPROVED or REQUEST_CHANGES with a per-finding impact report.

Typical flow:
  1. `scan_workspace` to discover local repositories and instruction conflicts.
  2. `check_workspace_compatibility` before changing contracts other agents use.
  3. `sync_workspace` to preview a local registry plan, then apply that exact
     `report.scan_id` reviewed plan token when the user chooses to update the
     registry. The source scan identity in `report.source_scan_id` cannot
     authorize an apply.

Single-contract tools remain available for targeted workflows. Workspace sync
never fetches, pulls, pushes, or edits agent instruction files.

All state is local. Nothing is sent anywhere except the semantic review call
to the LLM provider the user configured, and that is skippable with
use_llm=false."""

server: MCPServer = MCPServer(
    name="ionic",
    title="Ionic — agent contract compatibility",
    version=__version__,
    instructions=INSTRUCTIONS,
)

_config: Config | None = None
_registry: Registry | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config.load()
    return _config


def get_registry() -> Registry:
    global _registry
    if _registry is None:
        _registry = Registry(get_config().registry_path)
    return _registry


def _ok(**payload: Any) -> dict[str, Any]:
    return {"ok": True, **payload}


def _err(message: str, **payload: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, **payload}


def _workspace_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _workspace_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_workspace_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "value") and not isinstance(value, (str, bytes)):
        return _workspace_jsonable(value.value)
    return value


def _workspace_payload(result: Any) -> dict[str, Any]:
    payload = _workspace_jsonable(result)
    if not isinstance(payload, dict):
        raise TypeError("workspace engine returned a non-object result")
    payload.setdefault("telemetry", "none")
    payload.setdefault("network", {"used": False})
    return payload


def _workspace_inputs(
    manifest_path: str | None, repositories: list[dict[str, str]] | None
) -> tuple[list[dict[str, str]], str]:
    combined: list[dict[str, str]] = []
    workspace_id = "local"
    if manifest_path is not None:
        manifest = Path(manifest_path)
        if not manifest.is_file():
            raise FileNotFoundError(f"workspace manifest {manifest} does not exist")
        try:
            document = json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"workspace manifest {manifest} is not valid JSON: {exc}") from exc
        if not isinstance(document, dict):
            raise ValueError("workspace manifest must be a JSON object")
        workspace_id = str(document.get("workspace_id") or "local").strip() or "local"
        raw_repositories = document.get("repositories")
        if not isinstance(raw_repositories, list):
            raise ValueError("workspace manifest must contain a `repositories` array")
        base = manifest.resolve().parent
        for index, item in enumerate(raw_repositories):
            if not isinstance(item, dict):
                raise ValueError(f"repositories[{index}] must be an object")
            repository_id = str(item.get("id") or "").strip().lower()
            raw_path = str(item.get("path") or "").strip()
            if not repository_id or not raw_path:
                raise ValueError(f"repositories[{index}] requires non-empty `id` and `path`")
            path = Path(raw_path).expanduser()
            if not path.is_absolute():
                path = base / path
            combined.append({"id": repository_id, "path": str(path.resolve())})

    for index, item in enumerate(repositories or []):
        if not isinstance(item, dict):
            raise ValueError(f"repositories[{index}] must be an object")
        repository_id = str(item.get("id") or "").strip().lower()
        raw_path = str(item.get("path") or "").strip()
        if not repository_id or not raw_path:
            raise ValueError(f"repositories[{index}] requires non-empty `id` and `path`")
        combined.append({"id": repository_id, "path": raw_path})

    if not combined:
        raise ValueError("provide `repositories` or `manifest_path`")
    seen: set[str] = set()
    for item in combined:
        if item["id"] in seen:
            raise ValueError(f"duplicate repository id {item['id']!r}")
        seen.add(item["id"])
    return combined, workspace_id


def _workspace_error(exc: Exception) -> dict[str, Any]:
    explicit = getattr(exc, "code", None) or getattr(exc, "error_code", None)
    code = str(explicit or type(exc).__name__).strip().upper()
    if "STALE" in code:
        code = code if code.startswith("STALE_") else "STALE_SCAN"
    else:
        code = {
            "VALUEERROR": "INVALID_INPUT",
            "FILENOTFOUNDERROR": "REPOSITORY_NOT_FOUND",
            "PERMISSIONERROR": "REPOSITORY_UNREADABLE",
        }.get(code, code)
    return _err(
        str(exc),
        error_code=code,
        retryable=code in {"STALE_SCAN", "STALE_PLAN", "STALE_REGISTRY", "LOCK_BUSY"},
        telemetry="none",
        network={"used": False},
    )


def _workspace_refusal(payload: dict[str, Any]) -> tuple[str, str] | None:
    for conflict in payload.get("conflicts", []):
        if not isinstance(conflict, dict):
            continue
        kind = str(conflict.get("kind", "")).strip().lower()
        if kind in {"stale_scan", "stale_plan", "stale_registry"}:
            return kind.upper(), str(conflict.get("message") or kind.replace("_", " "))
    for error in payload.get("errors", []):
        if not isinstance(error, dict):
            continue
        code = str(error.get("code") or error.get("error_code") or "").strip().upper()
        if code in {
            "STALE_SCAN",
            "STALE_PLAN",
            "STALE_REGISTRY",
            "LOCK_BUSY",
            "APPLY_REFUSED",
        }:
            return code, str(error.get("message") or code.replace("_", " ").lower())
    return None


def _workspace_report_error(payload: dict[str, Any]) -> tuple[str, str] | None:
    errors = [error for error in payload.get("errors", []) if isinstance(error, dict)]
    if not errors:
        return None
    message = str(errors[0].get("message") or "workspace scan failed")
    lowered = message.lower()
    if "not a directory" in lowered or "does not exist" in lowered:
        code = "REPOSITORY_NOT_FOUND"
    elif "permission" in lowered or "unreadable" in lowered:
        code = "REPOSITORY_UNREADABLE"
    elif "requires non-empty" in lowered:
        code = "INVALID_INPUT"
    else:
        code = "INVALID_INSTRUCTION"
    if len(errors) > 1:
        message = f"{message} ({len(errors)} workspace errors total)"
    return code, message


def _summarize(contract: Contract) -> dict[str, Any]:
    return {
        "id": contract.id,
        "name": contract.name,
        "version": contract.version,
        "description": contract.description,
        "tools": [t.name for t in contract.tools],
        "capabilities": list(contract.capabilities),
        "outputs": [o.name for o in contract.outputs],
        "depends_on": contract.dependency_ids(),
        "tags": list(contract.tags),
        "fingerprint": contract.fingerprint(),
        "updated_at": contract.updated_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------


@server.tool(
    name="scan_workspace",
    title="Scan local agent repositories",
    description=(
        "Read one or more local repositories, discover agent instruction files, "
        "and report every agent instance plus deterministic instruction conflicts. "
        "Pass either `manifest_path` or repeatable repository objects with `id` and "
        "`path`. The report's `scan_id` identifies this source snapshot only; it "
        "cannot authorize `sync_workspace` apply. In a sync preview, the same source "
        "identity is exposed as `source_scan_id`. This is read-only and offline by "
        "default; it neither changes the registry nor performs git or network "
        "synchronization."
    ),
)
def scan_workspace_tool(
    manifest_path: str | None = None,
    repositories: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    try:
        resolved_repositories, workspace_id = _workspace_inputs(manifest_path, repositories)
        result = workspace_engine.scan_workspace(
            resolved_repositories,
            workspace_id=workspace_id,
        )
        payload = _workspace_payload(result)
        operational_error = _workspace_report_error(payload)
        if operational_error:
            return _err(
                operational_error[1],
                error_code=operational_error[0],
                retryable=False,
                report=payload,
                telemetry="none",
                network={"used": False},
            )
        return _ok(report=payload, telemetry="none")
    except Exception as exc:
        return _workspace_error(exc)


@server.tool(
    name="check_workspace_compatibility",
    title="Check a local multi-repository workspace",
    description=(
        "Scan local repositories, report instruction conflicts, and check their "
        "agent contracts against the selected local registry. Blocking findings "
        "are a successful analysis result (`ok=true` with a blocked report), not "
        "a transport error. Structural checks are deterministic and offline; "
        "all workspace checks are deterministic, local, and offline."
    ),
)
def check_workspace_compatibility_tool(
    manifest_path: str | None = None,
    repositories: list[dict[str, str]] | None = None,
    registry_path: str | None = None,
    fail_on: Literal["critical", "high", "medium", "low", "info"] = "high",
    transitive: bool = False,
) -> dict[str, Any]:
    try:
        resolved_repositories, workspace_id = _workspace_inputs(manifest_path, repositories)
        registry = Registry(registry_path or get_config().registry_path)
        try:
            result = workspace_engine.workspace_check(
                resolved_repositories,
                registry,
                fail_on=Severity(fail_on),
                transitive=transitive,
                workspace_id=workspace_id,
            )
        finally:
            registry.close()
        payload = _workspace_payload(result)
        operational_error = _workspace_report_error(payload)
        if operational_error:
            return _err(
                operational_error[1],
                error_code=operational_error[0],
                retryable=False,
                report=payload,
                telemetry="none",
                network={"used": False},
            )
        return _ok(report=payload, telemetry="none")
    except Exception as exc:
        return _workspace_error(exc)


@server.tool(
    name="sync_workspace",
    title="Plan or apply a local workspace registry sync",
    description=(
        "Create a read-only workspace-to-registry sync plan by default. To apply "
        "it, pass `apply=true` and set `expected_scan` to the exact reviewed sync "
        "plan token in that matching preview's `report.scan_id`. The preview's "
        "`report.source_scan_id`, and `scan_workspace`'s `report.scan_id`, identify "
        "only the source snapshot and cannot authorize apply. Stale or mismatched "
        "plans are refused. `prune` is opt-in and is limited to repositories in this "
        "workspace. This never fetches, pulls, pushes, or rewrites instruction Markdown."
    ),
)
def sync_workspace_tool(
    manifest_path: str | None = None,
    repositories: list[dict[str, str]] | None = None,
    registry_path: str | None = None,
    apply: bool = False,
    expected_scan: Annotated[
        str | None,
        Field(
            title="Reviewed sync plan token",
            description=(
                "For apply=true, the exact report.scan_id from the matching read-only "
                "sync_workspace preview. Never use report.source_scan_id or the "
                "scan_workspace report.scan_id."
            ),
        ),
    ] = None,
    prune: bool = False,
    selected_refs: list[str] | None = None,
) -> dict[str, Any]:
    if apply and not expected_scan:
        return _err(
            "apply requires `expected_scan` set to the reviewed sync plan token from "
            "the matching sync_workspace preview (`report.scan_id`); a source scan "
            "identity (`report.source_scan_id` or scan_workspace `report.scan_id`) "
            "cannot authorize apply",
            error_code="EXPECTED_SCAN_REQUIRED",
            retryable=False,
            telemetry="none",
            network={"used": False},
        )
    try:
        resolved_repositories, workspace_id = _workspace_inputs(manifest_path, repositories)
        registry = Registry(registry_path or get_config().registry_path)
        try:
            result = workspace_engine.sync_workspace(
                resolved_repositories,
                registry,
                expected_scan_id=expected_scan,
                selected_refs=selected_refs,
                apply=apply,
                prune=prune,
                workspace_id=workspace_id,
            )
        finally:
            registry.close()
        payload = _workspace_payload(result)
        refusal = _workspace_refusal(payload)
        operational_error = _workspace_report_error(payload)
        if refusal:
            return _err(
                refusal[1],
                error_code=refusal[0],
                retryable=refusal[0]
                in {"STALE_SCAN", "STALE_PLAN", "STALE_REGISTRY", "LOCK_BUSY"},
                report=payload,
                telemetry="none",
                network={"used": False},
            )
        if operational_error:
            return _err(
                operational_error[1],
                error_code=operational_error[0],
                retryable=False,
                report=payload,
                telemetry="none",
                network={"used": False},
            )
        return _ok(report=payload, telemetry="none")
    except Exception as exc:
        return _workspace_error(exc)


@server.tool(
    name="register_contract",
    title="Register an agent contract",
    description=(
        "Register an agent's behavioral contract in the local registry. Pass the "
        "contract as an object with at least an `id`; `name`, `version`, "
        "`description`, `identity`, `inputs`, `outputs`, `tools`, `capabilities`, "
        "`constraints`, `persona_rules`, and `depends_on` are all optional.\n\n"
        "In `depends_on`, name exactly what you need from each upstream agent "
        "(`requires_tools`, `requires_capabilities`, `expects_outputs`, "
        "`expects_format`). Those declarations are what let Ionic prove a change "
        "is breaking rather than merely guess."
    ),
)
def register_contract(
    contract: dict[str, Any],
    force: bool = False,
) -> dict[str, Any]:
    try:
        parsed = Contract.model_validate(contract)
    except Exception as exc:
        return _err(f"invalid contract: {exc}")
    try:
        stored = get_registry().register(parsed, force=force)
    except ContractExists as exc:
        return _err(str(exc), hint="Call update_contract, or pass force=true.")
    return _ok(contract=_summarize(stored))


@server.tool(
    name="update_contract",
    title="Update a registered contract",
    description=(
        "Apply a partial update to a registered contract. `changes` is merged "
        "over the stored contract at the top level, so pass whole fields "
        "(e.g. the full `tools` list, not a single tool).\n\n"
        "This writes immediately. To find out whether the change is safe first, "
        "call check_compatibility with the same payload."
    ),
)
def update_contract(
    contract_id: str,
    changes: dict[str, Any],
) -> dict[str, Any]:
    try:
        updated = get_registry().patch(contract_id, changes)
    except ContractNotFound as exc:
        return _err(str(exc))
    except Exception as exc:
        return _err(f"invalid update: {exc}")
    return _ok(contract=_summarize(updated))


@server.tool(
    name="list_contracts",
    title="List registered contracts",
    description=(
        "List every contract in the local registry, optionally filtered by tag. "
        "Returns summaries; call get_contract for the full document."
    ),
)
def list_contracts(tag: str | None = None) -> dict[str, Any]:
    contracts = get_registry().list(tag=tag)
    return _ok(
        count=len(contracts),
        contracts=[_summarize(c) for c in contracts],
    )


@server.tool(
    name="get_contract",
    title="Get one contract in full",
    description="Fetch a single registered contract, including its full history if asked.",
)
def get_contract(contract_id: str, include_history: bool = False) -> dict[str, Any]:
    registry = get_registry()
    try:
        contract = registry.get(contract_id)
    except ContractNotFound as exc:
        return _err(str(exc))
    payload: dict[str, Any] = {"contract": contract.model_dump(mode="json")}
    if include_history:
        payload["history"] = [
            {k: v for k, v in entry.items() if k != "contract"}
            for entry in registry.history(contract_id)
        ]
    return _ok(**payload)


@server.tool(
    name="check_compatibility",
    title="Check whether a change breaks dependents",
    description=(
        "The core operation. Given a proposed version of a contract, compare it "
        "against the registered version and against every contract that depends "
        "on it.\n\n"
        "Supply the proposal either as `proposed` (a full contract object) or as "
        "`proposed_markdown` (the text of a changed AGENTS.md / CLAUDE.md).\n\n"
        "Returns APPROVED or REQUEST_CHANGES plus findings, each naming the "
        "affected dependent, the evidence, and a concrete fix. Structural "
        "findings are deterministic; semantic findings come from the configured "
        "LLM judge only when use_llm=true. Checks default to deterministic, "
        "fully offline structural analysis."
    ),
)
def check_compatibility(
    contract_id: str,
    proposed: dict[str, Any] | None = None,
    proposed_markdown: str | None = None,
    use_llm: bool = False,
    fail_on: Literal["critical", "high", "medium", "low", "info"] = "high",
    transitive: bool = False,
) -> dict[str, Any]:
    if proposed is None and proposed_markdown is None:
        return _err("provide either `proposed` or `proposed_markdown`")

    try:
        if proposed_markdown is not None:
            candidate = extract_contract(proposed_markdown, contract_id=contract_id)
        else:
            payload = dict(proposed or {})
            payload.setdefault("id", contract_id)
            candidate = Contract.model_validate(payload)
    except Exception as exc:
        return _err(f"could not read the proposed contract: {exc}")

    if candidate.id != contract_id.strip().lower():
        candidate = candidate.model_copy(update={"id": contract_id.strip().lower()})

    config = get_config()
    try:
        judge = build_judge(config, enabled=use_llm)
    except JudgeUnavailable as exc:
        return _err(str(exc))

    report = check_against_registry(
        get_registry(),
        candidate,
        judge=judge,
        fail_on=Severity(fail_on),
        transitive=transitive,
    )
    payload = report.model_dump(mode="json")
    payload["markdown"] = render_markdown(report)
    return _ok(report=payload)


@server.tool(
    name="get_dependency_graph",
    title="Get the contract dependency graph",
    description=(
        "Return the dependency graph as nodes and edges. Each edge records what "
        "the dependent actually needs from its upstream contract, and whether "
        "that upstream contract is registered at all.\n\n"
        "Pass `contract_id` to restrict the graph to one contract's "
        "neighbourhood: everything that would be affected if it changed, plus "
        "what it leans on."
    ),
)
def get_dependency_graph(contract_id: str | None = None) -> dict[str, Any]:
    registry = get_registry()
    try:
        graph = registry.graph(root=contract_id)
    except ContractNotFound as exc:
        return _err(str(exc))
    payload = graph.model_dump(mode="json")
    if contract_id:
        payload["dependents_of_root"] = [c.id for c in registry.dependents(contract_id)]
        payload["transitive_dependents_of_root"] = [
            c.id for c in registry.transitive_dependents(contract_id)
        ]
    payload["unresolved_edges"] = [e.model_dump(mode="json") for e in graph.unresolved()]
    payload["cycles"] = graph.cycles()
    return _ok(graph=payload)


@server.tool(
    name="extract_contract",
    title="Extract a contract from an agent instruction file",
    description=(
        "Parse an AGENTS.md / CLAUDE.md file (by path, or by passing its text) "
        "into an Ionic contract without registering it. Useful for reviewing "
        "what Ionic sees before committing to it."
    ),
)
def extract_contract_tool(
    path: str | None = None,
    text: str | None = None,
    contract_id: str | None = None,
    register: bool = False,
) -> dict[str, Any]:
    if path is None and text is None:
        return _err("provide either `path` or `text`")
    try:
        if path is not None:
            contract = extract_from_file(path, contract_id=contract_id)
        else:
            contract = extract_contract(text or "", contract_id=contract_id)
    except Exception as exc:
        return _err(f"extraction failed: {exc}")

    payload: dict[str, Any] = {"contract": contract.model_dump(mode="json")}
    if register:
        payload["contract"] = get_registry().upsert(contract).model_dump(mode="json")
        payload["registered"] = True
    return _ok(**payload)


@server.tool(
    name="render_contract",
    title="Render a contract as markdown",
    description=(
        "Render a registered contract back out as an AGENTS.md-style document, "
        "so the contract and the instruction file can be kept in sync."
    ),
)
def render_contract(contract_id: str) -> dict[str, Any]:
    try:
        contract = get_registry().get(contract_id)
    except ContractNotFound as exc:
        return _err(str(exc))
    return _ok(markdown=render_contract_md(contract))


@server.tool(
    name="detect_drift",
    title="Find contracts that no longer match their source files",
    description=(
        "Compare every registered contract against the file it was extracted "
        "from. A stale registry is the quietest way Ionic stops working: checks "
        "keep passing, but they are measured against a contract nobody is "
        "running any more.\n\n"
        "Statuses: `in_sync`, `version_only` (behaviour identical, version "
        "differs), `drifted` (behaviour changed on disk), `source_missing`, "
        "`source_unreadable`, `no_source` (registered directly).\n\n"
        "Call this before trusting a check, and after any run of edits to "
        "agent instruction files."
    ),
)
def detect_drift_tool(contract_id: str | None = None) -> dict[str, Any]:
    registry = get_registry()
    try:
        reports = detect_drift(registry, contract_id=contract_id)
    except ContractNotFound as exc:
        return _err(str(exc))
    stale = problems(reports)
    return _ok(
        counts=summarize(reports),
        stale=[r.contract_id for r in stale],
        reports=[r.model_dump(mode="json") for r in reports],
        advice=(
            "Re-register the drifted contracts, or run check_compatibility "
            "against their source files first to see what the edits would break."
        )
        if stale
        else "Every registered contract matches its source file.",
    )


@server.tool(
    name="registry_status",
    title="Describe the local registry",
    description=(
        "Where the registry lives, how much is in it, and which semantic judge "
        "is configured. Useful for confirming Ionic is pointed at the right "
        "project before registering anything."
    ),
)
def registry_status() -> dict[str, Any]:
    config = get_config()
    registry = get_registry()
    stats = registry.stats()
    stale = problems(detect_drift(registry))
    return _ok(
        registry=stats,
        stale_contracts=[r.contract_id for r in stale],
        judge=config.describe_judge(),
        project_root=str(config.project_root),
        telemetry="none",
    )


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def main(transport: str = "stdio", **kwargs: Any) -> None:
    """Run the MCP server."""
    if os.environ.get("IONIC_REGISTRY_OVERRIDE"):
        global _config
        _config = Config.load(registry_path=os.environ["IONIC_REGISTRY_OVERRIDE"])
    server.run(transport=transport, **kwargs)  # type: ignore[arg-type]


if __name__ == "__main__":  # pragma: no cover
    main()
