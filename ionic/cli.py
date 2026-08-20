"""Ionic command line interface."""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
import sys
from typing import Any, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from . import __version__
from . import workspace as workspace_engine
from .compat import check_against_registry, render_markdown
from .config import CONFIG_FILENAME, DEFAULT_CONFIG_TOML, Config
from .diff import structural_findings
from .drift import (
    DriftStatus,
    candidate_roots,
    detect_drift,
    problems,
    resolve_source,
    summarize,
)
from .extract import discover_agent_files, extract_from_file
from .extract import render_markdown as render_contract_md
from .judge import JudgeUnavailable, build_judge
from .models import CompatibilityReport, Contract, Finding, Severity, Verdict
from .registry import REGISTRY_DIRNAME, ContractExists, ContractNotFound, Registry

app = typer.Typer(
    name="ionic",
    help="Register agent contracts. Detect semantic breakages before they ship.",
    no_args_is_help=True,
    add_completion=False,
)

workspace_app = typer.Typer(
    name="workspace",
    help="Scan, check, and locally sync agent contracts across repositories.",
    no_args_is_help=True,
    add_completion=False,
)
app.add_typer(workspace_app, name="workspace")

runtime_app = typer.Typer(
    name="runtime",
    help="Discover official subscription-backed agent runtimes.",
    no_args_is_help=True,
    add_completion=False,
)
app.add_typer(runtime_app, name="runtime")

console = Console()
err_console = Console(stderr=True)

# Versioned contract between the native CLI sidecar and Ionic Desktop. Bump
# this only when the JSON/exit-code behavior the desktop relies on changes.
DESKTOP_PROTOCOL = 4

SEVERITY_STYLE = {
    Severity.CRITICAL: "bold red",
    Severity.HIGH: "red",
    Severity.MEDIUM: "yellow",
    Severity.LOW: "cyan",
    Severity.INFO: "dim",
}

SEVERITY_ICON = {
    Severity.CRITICAL: "■",
    Severity.HIGH: "▲",
    Severity.MEDIUM: "◆",
    Severity.LOW: "•",
    Severity.INFO: "·",
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _fail(message: str, code: int = 2) -> None:
    err_console.print(f"[bold red]error:[/] {message}")
    raise typer.Exit(code)


def _open(registry_path: Optional[Path] = None) -> tuple[Config, Registry]:
    config = Config.load(registry_path=registry_path)
    return config, Registry(config.registry_path)


def _load_contract(path: Path, contract_id: str | None = None) -> Contract:
    try:
        return extract_from_file(path, contract_id=contract_id)
    except Exception as exc:
        _fail(f"could not read a contract from {path}: {exc}")
        raise  # unreachable


def _emit_json(payload: str) -> None:
    """Write JSON to stdout.

    When stdout is a pipe (CI, the desktop app, `| jq`) the bytes go out raw so
    nothing -- colour codes, wrapping, a FORCE_COLOR in the environment -- can
    ever corrupt them. Interactive users still get the pretty version.
    """
    if console.is_terminal:
        console.print_json(payload)
    else:
        print(payload)


def _severity_text(severity: Severity) -> Text:
    return Text(
        f"{SEVERITY_ICON[severity]} {severity.value}",
        style=SEVERITY_STYLE[severity],
    )


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


def _parse_repositories(values: list[str]) -> list[dict[str, str]]:
    repositories: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in values:
        repository_id, separator, path = raw.partition("=")
        repository_id = repository_id.strip().lower()
        path = path.strip()
        if not separator or not repository_id or not path:
            raise ValueError(
                f"invalid --repo {raw!r}; expected a repository id and path as ID=PATH"
            )
        if repository_id in seen:
            raise ValueError(f"duplicate repository id {repository_id!r}")
        seen.add(repository_id)
        repositories.append({"id": repository_id, "path": path})
    return repositories


def _workspace_inputs(
    manifest: Path | None, repository_values: list[str]
) -> tuple[list[dict[str, str]], str]:
    """Read a minimal local workspace manifest and merge explicit repositories."""
    repositories: list[dict[str, str]] = []
    workspace_id = "local"
    if manifest is not None:
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
            repositories.append({"id": repository_id, "path": str(path.resolve())})

    repositories.extend(_parse_repositories(repository_values))
    if not repositories:
        raise ValueError("provide at least one --repo ID=PATH or --manifest PATH")
    seen: set[str] = set()
    for item in repositories:
        repository_id = item["id"]
        if repository_id in seen:
            raise ValueError(f"duplicate repository id {repository_id!r}")
        seen.add(repository_id)
    return repositories, workspace_id


def _parse_agent_refs(values: list[str]) -> list[str]:
    references: list[str] = []
    seen: set[tuple[str, str]] = set()
    for raw in values:
        repository_id, separator, contract_id = raw.strip().partition("/")
        repository_id = repository_id.strip().lower()
        contract_id = contract_id.strip().lower()
        if not separator or not repository_id or not contract_id:
            raise ValueError(
                f"invalid --agent {raw!r}; expected a qualified agent as REPOSITORY/CONTRACT"
            )
        key = (repository_id, contract_id)
        if key in seen:
            continue
        seen.add(key)
        references.append(f"{repository_id}/{contract_id}")
    return references


def _workspace_has_blockers(payload: dict[str, Any], fail_on: Severity) -> bool:
    status = str(payload.get("status", "")).strip().lower()
    if status in {"blocked", "conflicts", "request_changes"}:
        return True
    if payload.get("blocking") is True:
        return True
    if str(payload.get("verdict", "")).upper() == "REQUEST_CHANGES":
        return True
    summary = payload.get("summary")
    if isinstance(summary, dict):
        for key in ("blocking", "blocking_conflicts", "blocked"):
            try:
                if int(summary.get(key, 0)) > 0:
                    return True
            except (TypeError, ValueError):
                pass
    for conflict in payload.get("conflicts", []):
        if not isinstance(conflict, dict):
            continue
        if conflict.get("blocking") is True:
            return True
        try:
            if Severity(str(conflict.get("severity", "info")).lower()) >= fail_on:
                return True
        except ValueError:
            continue
    checks = [
        *(payload.get("checks") or []),
        *(payload.get("reports") or []),
    ]
    for report in checks:
        if isinstance(report, dict) and (
            report.get("blocking") is True
            or str(report.get("verdict", "")).upper() == "REQUEST_CHANGES"
        ):
            return True
    return False


def _workspace_refusal(payload: dict[str, Any]) -> tuple[str, str] | None:
    for conflict in payload.get("conflicts", []):
        if not isinstance(conflict, dict):
            continue
        kind = str(conflict.get("kind", "")).strip().lower()
        if kind in {"stale_scan", "stale_plan", "stale_registry"}:
            code = kind.upper()
            return code, str(conflict.get("message") or kind.replace("_", " "))
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


def _workspace_error_code(exc: Exception) -> tuple[str, int]:
    explicit = getattr(exc, "code", None) or getattr(exc, "error_code", None)
    code = str(explicit or type(exc).__name__).strip().upper()
    if "STALE" in code:
        return code if code.startswith("STALE_") else "STALE_SCAN", 3
    if "REFUS" in code or "LOCK" in code or "APPLY" in code:
        return code, 3
    known = {
        "VALUEERROR": "INVALID_INPUT",
        "FILENOTFOUNDERROR": "REPOSITORY_NOT_FOUND",
        "PERMISSIONERROR": "REPOSITORY_UNREADABLE",
    }
    return known.get(code, code), 2


def _workspace_fail(exc: Exception, *, output_format: str) -> None:
    error_code, exit_code = _workspace_error_code(exc)
    if output_format == "json":
        _emit_json(
            json.dumps(
                {
                    "ok": False,
                    "error": str(exc),
                    "error_code": error_code,
                    "telemetry": "none",
                    "network": {"used": False},
                },
                indent=2,
            )
        )
        raise typer.Exit(exit_code)
    _fail(str(exc), code=exit_code)


def _emit_workspace(payload: dict[str, Any], *, output_format: str) -> None:
    if output_format == "json":
        _emit_json(json.dumps(payload, indent=2))
        return
    operation = str(payload.get("operation") or "workspace")
    status = str(payload.get("status") or ("blocked" if payload.get("blocking") else "clean"))
    summary = payload.get("summary", {})
    body = Text()
    body.append(f"{status}\n", style="bold red" if status in {"blocked", "conflicts"} else "bold green")
    if isinstance(summary, dict):
        bits = [f"{key}: {value}" for key, value in summary.items() if isinstance(value, (str, int))]
        if bits:
            body.append("  ".join(bits), style="dim")
    console.print(Panel(body, title=f"ionic workspace {operation}", border_style="cyan"))

    conflicts = payload.get("conflicts", [])
    if conflicts:
        table = Table(title="instruction conflicts", title_justify="left", box=None)
        table.add_column("severity")
        table.add_column("kind", style="dim")
        table.add_column("conflict")
        for conflict in conflicts:
            if not isinstance(conflict, dict):
                continue
            table.add_row(
                str(conflict.get("severity", "")),
                str(conflict.get("kind", "")),
                str(conflict.get("summary") or conflict.get("message") or ""),
            )
        console.print(table)

    actions = payload.get("actions", [])
    if actions:
        table = Table(title="sync plan", title_justify="left", box=None)
        table.add_column("action")
        table.add_column("agent")
        table.add_column("source", style="dim")
        if isinstance(actions, dict):
            for action_name in ("add", "update", "unchanged", "prune"):
                for reference in actions.get(action_name, []):
                    table.add_row(action_name, str(reference), "")
        else:
            for action in actions:
                if not isinstance(action, dict):
                    continue
                reference = (
                    action.get("agent_ref")
                    or action.get("instance_id")
                    or action.get("contract_id")
                )
                if isinstance(reference, dict):
                    reference = (
                        f"{reference.get('repository_id', '')}/"
                        f"{reference.get('contract_id', '')}"
                    )
                table.add_row(
                    str(action.get("action") or action.get("status") or ""),
                    str(reference or ""),
                    str(action.get("source") or ""),
                )
        console.print(table)


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


@app.command()
def init(
    path: Optional[Path] = typer.Argument(None, help="Project root. Defaults to cwd."),
) -> None:
    """Create a local Ionic registry in this project."""
    root = (path or Path.cwd()).resolve()
    ionic_dir = root / REGISTRY_DIRNAME
    ionic_dir.mkdir(parents=True, exist_ok=True)

    config_file = ionic_dir / CONFIG_FILENAME
    created_config = False
    if not config_file.exists():
        config_file.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
        created_config = True

    registry = Registry(ionic_dir / "registry.db")
    stats = registry.stats()
    registry.close()

    body = Text()
    body.append("registry  ", style="dim")
    body.append(f"{stats['path']}\n")
    body.append("config    ", style="dim")
    body.append(f"{config_file}{' (created)' if created_config else ''}\n")
    body.append("contracts ", style="dim")
    body.append(f"{stats['contracts']}\n\n")
    body.append("Next: ", style="dim")
    body.append("ionic register .", style="bold")
    body.append("  to extract contracts from AGENTS.md / CLAUDE.md files.", style="dim")

    console.print(Panel(body, title="Ionic initialised", border_style="green"))


@runtime_app.command("status")
def runtime_status_command(
    output_format: str = typer.Option("text", "--format", help="text|json"),
    as_json: bool = typer.Option(False, "--json", help="Alias for --format json."),
) -> None:
    """Report installed official agent runtimes without inspecting their login stores."""
    from .runtimes import discover_runtimes

    if as_json:
        output_format = "json"
    if output_format not in {"text", "json"}:
        _fail("invalid --format; expected text|json")
    # Opening Settings or running status must never execute arbitrary PATH
    # entries. Authentication and version probing happen only on invocation.
    statuses = discover_runtimes(probe_versions=False)
    payload = {
        "schema_version": 1,
        "operation": "runtime_status",
        "account_required": False,
        "telemetry": "none",
        "network": {"used": False},
        "authentication_inspected": False,
        "runtimes": [
            {
                "id": status.metadata.runtime_id,
                "display_name": status.metadata.display_name,
                "vendor": status.metadata.vendor,
                "kind": status.metadata.kind.value,
                "state": status.state.value,
                "available": status.available,
                "installed": status.executable is not None,
                "authenticated": None,
                "executable": str(status.executable) if status.executable else None,
                "version": status.version,
                "message": status.message,
                "maturity": status.metadata.maturity.value,
                "capabilities": sorted(capability.value for capability in status.metadata.capabilities),
                "direct_api_provider": status.metadata.direct_api_provider,
                "docs_url": status.metadata.docs_url,
                "policy_note": status.metadata.policy_note,
            }
            for status in statuses
        ],
    }
    if output_format == "json":
        _emit_json(json.dumps(payload))
        return

    table = Table(title="Subscription runtimes", box=None)
    table.add_column("Runtime", style="bold")
    table.add_column("Installation")
    table.add_column("Authentication", style="dim")
    table.add_column("Maturity", style="dim")
    for runtime in payload["runtimes"]:
        installation = "installed" if runtime["installed"] else "not found"
        table.add_row(runtime["display_name"], installation, "not inspected", runtime["maturity"])
    console.print(table)
    console.print(
        "[dim]These are official product runtimes, not generic API credentials. "
        "Ionic never reads their cached login files.[/]"
    )


@workspace_app.command("scan")
def workspace_scan_command(
    manifest: Optional[Path] = typer.Option(
        None, "--manifest", help="Workspace manifest. Use --repo for an ad-hoc workspace."
    ),
    repo: Optional[list[str]] = typer.Option(
        None, "--repo", help="Repository to scan as ID=PATH. Repeat for multiple repositories."
    ),
    output_format: str = typer.Option("text", "--format", help="text|json"),
    as_json: bool = typer.Option(False, "--json", help="Alias for --format json."),
) -> None:
    """Discover source files without writing; this scan ID cannot authorize sync apply."""
    try:
        if as_json:
            output_format = "json"
        if output_format not in {"text", "json"}:
            raise ValueError("invalid --format; expected text|json")
        repositories, workspace_id = _workspace_inputs(manifest, repo or [])
        result = workspace_engine.scan_workspace(
            repositories, workspace_id=workspace_id
        )
        payload = _workspace_payload(result)
    except Exception as exc:
        _workspace_fail(exc, output_format=output_format)
        return

    operational_error = _workspace_report_error(payload)
    if operational_error:
        payload.setdefault("error_code", operational_error[0])
        payload.setdefault("error", operational_error[1])
    _emit_workspace(payload, output_format=output_format)
    if operational_error:
        raise typer.Exit(2)
    if _workspace_has_blockers(payload, Severity.HIGH):
        raise typer.Exit(1)


@workspace_app.command("check")
def workspace_check_command(
    manifest: Optional[Path] = typer.Option(None, "--manifest"),
    repo: Optional[list[str]] = typer.Option(
        None, "--repo", help="Repository to check as ID=PATH. Repeat as needed."
    ),
    registry_path: Optional[Path] = typer.Option(None, "--registry"),
    use_llm: bool = typer.Option(
        False,
        "--llm/--no-llm",
        help="Workspace v1 is offline; --llm is reserved for a future semantic pass.",
    ),
    fail_on: str = typer.Option("high", "--fail-on"),
    transitive: bool = typer.Option(False, "--transitive", help="Include indirect dependents."),
    output_format: str = typer.Option("text", "--format", help="text|json"),
    as_json: bool = typer.Option(False, "--json", help="Alias for --format json."),
) -> None:
    """Check scanned contracts and instruction conflicts against the local registry."""
    try:
        if as_json:
            output_format = "json"
        if output_format not in {"text", "json"}:
            raise ValueError("invalid --format; expected text|json")
        if use_llm:
            raise ValueError(
                "--llm is not supported for workspace checks yet; use --no-llm"
            )
        severity = Severity(fail_on.strip().lower())
        repositories, workspace_id = _workspace_inputs(manifest, repo or [])
        _, registry = _open(registry_path)
        try:
            result = workspace_engine.workspace_check(
                repositories,
                registry,
                fail_on=severity,
                transitive=transitive,
                workspace_id=workspace_id,
            )
        finally:
            registry.close()
        payload = _workspace_payload(result)
    except Exception as exc:
        _workspace_fail(exc, output_format=output_format)
        return

    operational_error = _workspace_report_error(payload)
    if operational_error:
        payload.setdefault("error_code", operational_error[0])
        payload.setdefault("error", operational_error[1])
    _emit_workspace(payload, output_format=output_format)
    if operational_error:
        raise typer.Exit(2)
    if _workspace_has_blockers(payload, severity):
        raise typer.Exit(1)


@workspace_app.command("sync")
def workspace_sync_command(
    manifest: Optional[Path] = typer.Option(None, "--manifest"),
    repo: Optional[list[str]] = typer.Option(
        None, "--repo", help="Repository to sync as ID=PATH. Repeat as needed."
    ),
    agent: Optional[list[str]] = typer.Option(
        None,
        "--agent",
        help="Qualified agent to sync as REPOSITORY/CONTRACT. Repeat as needed.",
    ),
    registry_path: Optional[Path] = typer.Option(None, "--registry"),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply the reviewed sync plan to the local registry. The default is read-only.",
    ),
    expected_scan: Optional[str] = typer.Option(
        None,
        "--expected-scan",
        help=(
            "Reviewed sync plan token: the exact scan_id returned by the matching "
            "read-only workspace sync preview. A workspace scan source token is not valid."
        ),
    ),
    prune: bool = typer.Option(
        False,
        "--prune",
        help="Remove missing contracts from repositories in this workspace only.",
    ),
    output_format: str = typer.Option("text", "--format", help="text|json"),
    as_json: bool = typer.Option(False, "--json", help="Alias for --format json."),
) -> None:
    """Preview or apply registry sync; preview scan_id is the reviewed plan token."""
    if as_json:
        output_format = "json"
    if output_format not in {"text", "json"}:
        _workspace_fail(ValueError("invalid --format; expected text|json"), output_format=output_format)
        return
    if apply and not expected_scan:
        message = (
            "--apply requires --expected-scan set to the reviewed sync plan token "
            "from the matching workspace sync preview (its scan_id); a source "
            "workspace scan identity (source_scan_id in the preview) cannot authorize apply"
        )
        if output_format == "json":
            _emit_json(
                json.dumps(
                    {
                        "ok": False,
                        "error": message,
                        "error_code": "EXPECTED_SCAN_REQUIRED",
                        "telemetry": "none",
                        "network": {"used": False},
                    },
                    indent=2,
                )
            )
            raise typer.Exit(3)
        _fail(message, code=3)
        return

    try:
        repositories, workspace_id = _workspace_inputs(manifest, repo or [])
        selected_refs = _parse_agent_refs(agent or [])
        _, registry = _open(registry_path)
        try:
            result = workspace_engine.sync_workspace(
                repositories,
                registry,
                expected_scan_id=expected_scan,
                selected_refs=selected_refs or None,
                apply=apply,
                prune=prune,
                workspace_id=workspace_id,
            )
        finally:
            registry.close()
        payload = _workspace_payload(result)
    except Exception as exc:
        _workspace_fail(exc, output_format=output_format)
        return

    refusal = _workspace_refusal(payload)
    operational_error = _workspace_report_error(payload)
    if refusal:
        payload.setdefault("error_code", refusal[0])
        payload.setdefault("error", refusal[1])
    elif operational_error:
        payload.setdefault("error_code", operational_error[0])
        payload.setdefault("error", operational_error[1])
    _emit_workspace(payload, output_format=output_format)
    if refusal:
        raise typer.Exit(3)
    if operational_error:
        raise typer.Exit(2)
    if _workspace_has_blockers(payload, Severity.HIGH):
        raise typer.Exit(1)


@app.command()
def register(
    path: Path = typer.Argument(..., help="A contract file, an AGENTS.md, or a directory to scan."),
    contract_id: Optional[str] = typer.Option(None, "--id", help="Override the contract id."),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite if already registered."),
    registry_path: Optional[Path] = typer.Option(None, "--registry", help="Registry file to use."),
) -> None:
    """Register one or more contracts."""
    config, registry = _open(registry_path)
    paths = discover_agent_files(path) if path.is_dir() else [path]
    if not paths:
        _fail(f"no agent instruction files found under {path}")

    table = Table(box=None, pad_edge=False)
    table.add_column("", width=2)
    table.add_column("contract", style="bold")
    table.add_column("version")
    table.add_column("source", style="dim")

    registered = 0
    for target in paths:
        contract = _load_contract(target, contract_id if len(paths) == 1 else None)
        try:
            stored = registry.register(contract, force=force)
        except ContractExists as exc:
            table.add_row(Text("!", style="yellow"), contract.id, contract.version, str(exc))
            continue
        registered += 1
        table.add_row(
            Text("+", style="green"), stored.id, f"v{stored.version}", str(target)
        )

    console.print(table)
    console.print(
        f"[dim]{registered} registered · registry {registry.path}[/]"
    )
    registry.close()
    if registered == 0:
        raise typer.Exit(1)


@app.command("list")
def list_contracts(
    tag: Optional[str] = typer.Option(None, "--tag", help="Only show contracts with this tag."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of a table."),
    registry_path: Optional[Path] = typer.Option(None, "--registry"),
) -> None:
    """List registered contracts."""
    _, registry = _open(registry_path)
    contracts = registry.list(tag=tag)

    if as_json:
        _emit_json(json.dumps([c.model_dump(mode="json") for c in contracts], indent=2))
        registry.close()
        return

    if not contracts:
        console.print("[dim]No contracts registered. Try [bold]ionic register .[/bold][/]")
        registry.close()
        return

    table = Table(title=f"{len(contracts)} contract(s)", title_justify="left", header_style="dim")
    table.add_column("id", style="bold")
    table.add_column("version")
    table.add_column("tools", justify="right")
    table.add_column("outputs", justify="right")
    table.add_column("constraints", justify="right")
    table.add_column("depends on", style="dim")

    for contract in contracts:
        deps = ", ".join(contract.dependency_ids()) or "—"
        table.add_row(
            contract.id,
            contract.version,
            str(len(contract.tools)),
            str(len(contract.outputs)),
            str(len(contract.constraints)),
            deps,
        )
    console.print(table)
    registry.close()


@app.command()
def show(
    contract_id: str = typer.Argument(..., help="Contract id."),
    as_json: bool = typer.Option(False, "--json", help="Emit the raw contract JSON."),
    as_markdown: bool = typer.Option(False, "--markdown", help="Render as an AGENTS.md document."),
    registry_path: Optional[Path] = typer.Option(None, "--registry"),
) -> None:
    """Show one contract in full."""
    _, registry = _open(registry_path)
    try:
        contract = registry.get(contract_id)
    except ContractNotFound as exc:
        registry.close()
        _fail(str(exc), code=1)
        return

    if as_json:
        _emit_json(contract.to_json())
        registry.close()
        return
    if as_markdown:
        console.print(render_contract_md(contract))
        registry.close()
        return

    header = Text()
    header.append(f"{contract.name}\n", style="bold")
    header.append(f"{contract.id} · v{contract.version} · {contract.fingerprint()}", style="dim")
    if contract.description:
        header.append(f"\n\n{contract.description}")
    console.print(Panel(header, border_style="cyan"))

    if contract.identity:
        console.print(Panel(contract.identity, title="identity", border_style="dim"))

    def _bullets(title: str, items: list[str]) -> None:
        if not items:
            return
        console.print(Rule(title, style="dim", align="left"))
        for item in items:
            console.print(f"  [dim]·[/] {item}")

    if contract.tools:
        console.print(Rule("tools", style="dim", align="left"))
        for tool in contract.tools:
            flag = "" if tool.required else " [dim](optional)[/]"
            console.print(f"  [bold]{tool.name}[/]{flag} [dim]{tool.description}[/]")

    if contract.inputs:
        console.print(Rule("inputs", style="dim", align="left"))
        for spec in contract.inputs:
            req = "" if spec.required else " [dim](optional)[/]"
            console.print(f"  [bold]{spec.name}[/] [cyan]{spec.format.value}[/]{req} [dim]{spec.description}[/]")

    if contract.outputs:
        console.print(Rule("outputs", style="dim", align="left"))
        for spec in contract.outputs:
            console.print(f"  [bold]{spec.name}[/] [cyan]{spec.format.value}[/] [dim]{spec.description}[/]")

    _bullets("capabilities", list(contract.capabilities))
    _bullets("constraints", [c.statement for c in contract.constraints])
    _bullets("persona rules", list(contract.persona_rules))

    if contract.depends_on:
        console.print(Rule("depends on", style="dim", align="left"))
        for dep in contract.depends_on:
            needs = []
            if dep.requires_tools:
                needs.append(f"tools: {', '.join(dep.requires_tools)}")
            if dep.requires_capabilities:
                needs.append(f"capabilities: {', '.join(dep.requires_capabilities)}")
            if dep.expects_outputs:
                needs.append(f"outputs: {', '.join(dep.expects_outputs)}")
            if dep.expects_format:
                needs.append(f"format: {dep.expects_format.value}")
            console.print(f"  [bold]{dep.contract_id}[/] [dim]{' · '.join(needs)}[/]")

    registry.close()


@app.command()
def rm(
    contract_id: str = typer.Argument(...),
    registry_path: Optional[Path] = typer.Option(None, "--registry"),
) -> None:
    """Remove a contract from the registry."""
    _, registry = _open(registry_path)
    try:
        registry.delete(contract_id)
    except ContractNotFound as exc:
        registry.close()
        _fail(str(exc), code=1)
        return
    console.print(f"[green]removed[/] {contract_id}")
    registry.close()


@app.command()
def extract(
    path: Path = typer.Argument(..., help="An AGENTS.md / CLAUDE.md file."),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Write JSON here."),
    contract_id: Optional[str] = typer.Option(None, "--id"),
) -> None:
    """Extract a contract from an instruction file without registering it."""
    contract = _load_contract(path, contract_id)
    payload = contract.to_json()
    if output:
        output.write_text(payload + "\n", encoding="utf-8")
        console.print(f"[green]wrote[/] {output}")
    else:
        _emit_json(payload)


@app.command()
def check(
    contract_id: Optional[str] = typer.Argument(
        None, help="The contract being changed. Omit with --all."
    ),
    against: Optional[Path] = typer.Option(
        None,
        "--against",
        "-a",
        help="Proposed contract: a JSON contract or a changed AGENTS.md. "
        "Defaults to the contract's recorded source file.",
    ),
    check_all: bool = typer.Option(
        False,
        "--all",
        help="Check every contract whose source file has changed since it was registered.",
    ),
    use_llm: bool = typer.Option(
        False,
        "--llm/--no-llm",
        help="Opt in to the semantic judge. Structural analysis is the default.",
    ),
    fail_on: str = typer.Option("high", "--fail-on", help="critical|high|medium|low|info"),
    transitive: bool = typer.Option(
        False, "--transitive", help="Include indirect dependents."
    ),
    output_format: str = typer.Option("text", "--format", help="text|markdown|json"),
    registry_path: Optional[Path] = typer.Option(None, "--registry"),
) -> None:
    """Check whether a proposed change breaks anything that depends on it.

    Exits 1 on REQUEST_CHANGES, so it drops straight into CI.
    """
    config, registry = _open(registry_path)

    try:
        severity = Severity(fail_on.strip().lower())
    except ValueError:
        registry.close()
        _fail(f"invalid --fail-on {fail_on!r}; expected critical|high|medium|low|info")
        return

    if check_all and contract_id:
        registry.close()
        _fail("pass a contract id or --all, not both")
        return
    if not check_all and not contract_id:
        registry.close()
        _fail("pass a contract id, or --all to check everything that changed on disk")
        return

    try:
        judge = build_judge(config, enabled=use_llm)
    except JudgeUnavailable as exc:
        registry.close()
        _fail(str(exc))
        return

    credential_names = {
        "anthropic": "ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN",
        "openai": "OPENAI_API_KEY",
        "google": "GEMINI_API_KEY or GOOGLE_API_KEY",
        "xai": "XAI_API_KEY",
    }
    if (
        use_llm
        and config.model_access == "api"
        and config.judge_provider in credential_names
        and not config.judge_credentials_present
    ):
        err_console.print(
            f"[yellow]note:[/] no {credential_names[config.judge_provider]} found; "
            "the semantic review may be skipped. Use --no-llm to silence this."
        )

    targets: list[tuple[str, Path]] = []
    if check_all:
        changed = [
            report
            for report in detect_drift(registry)
            if report.status is DriftStatus.DRIFTED and report.resolved_source
        ]
        if not changed:
            registry.close()
            console.print(
                "[green]nothing to check[/] [dim]— every registered contract matches "
                "its source file.[/]"
            )
            raise typer.Exit(0)
        targets = [(r.contract_id, Path(r.resolved_source)) for r in changed]
    else:
        source = against
        if source is None:
            try:
                current = registry.get(contract_id)
            except ContractNotFound as exc:
                registry.close()
                _fail(str(exc), code=1)
                return
            if not current.source:
                registry.close()
                _fail(
                    f"`{contract_id}` has no recorded source file; pass --against explicitly"
                )
                return
            resolved = resolve_source(current.source, candidate_roots(registry))
            if resolved is None:
                registry.close()
                _fail(f"{current.source} does not exist; pass --against explicitly")
                return
            source = resolved
        if not source.exists():
            registry.close()
            _fail(f"{source} does not exist")
            return
        targets = [(contract_id, source)]

    reports = []
    for target_id, source_path in targets:
        proposed = _load_contract(source_path, target_id)
        with console.status(f"analysing {target_id}…", spinner="dots"):
            reports.append(
                check_against_registry(
                    registry, proposed, judge=judge, fail_on=severity, transitive=transitive
                )
            )
    registry.close()

    if output_format == "json":
        payload = [r.model_dump(mode="json") for r in reports]
        _emit_json(json.dumps(payload if check_all else payload[0], indent=2))
    elif output_format == "markdown":
        console.print("\n\n---\n\n".join(render_markdown(r) for r in reports))
    else:
        for index, report in enumerate(reports):
            if index:
                console.print()
            _print_report(report)
        if len(reports) > 1:
            blocked = [r for r in reports if r.verdict is Verdict.REQUEST_CHANGES]
            console.print()
            console.print(
                f"[bold]{len(reports)} contract(s) checked · "
                f"{len(blocked)} blocked[/]"
            )

    blocked_any = any(r.verdict is Verdict.REQUEST_CHANGES for r in reports)
    raise typer.Exit(1 if blocked_any else 0)


@app.command()
def drift(
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of a table."),
    registry_path: Optional[Path] = typer.Option(None, "--registry"),
) -> None:
    """Find registered contracts that no longer match their source files.

    A stale registry is the quietest way Ionic can stop protecting you: checks
    keep passing, but they are measured against a contract nobody is running
    any more. Exits 1 if anything has drifted.
    """
    _, registry = _open(registry_path)
    reports = detect_drift(registry)
    registry.close()

    if as_json:
        _emit_json(json.dumps([r.model_dump(mode="json") for r in reports], indent=2))
        raise typer.Exit(1 if any(r.is_problem for r in reports) else 0)

    if not reports:
        console.print("[dim]No contracts registered.[/]")
        raise typer.Exit(0)

    table = Table(header_style="dim", box=None, padding=(0, 2))
    table.add_column("contract", style="bold")
    table.add_column("status")
    table.add_column("registry")
    table.add_column("source")
    table.add_column("file", style="dim")

    styles = {
        DriftStatus.IN_SYNC: "green",
        DriftStatus.VERSION_ONLY: "cyan",
        DriftStatus.DRIFTED: "bold red",
        DriftStatus.SOURCE_MISSING: "red",
        DriftStatus.SOURCE_UNREADABLE: "red",
        DriftStatus.NO_SOURCE: "dim",
    }
    for report in reports:
        table.add_row(
            report.contract_id,
            Text(report.status.value, style=styles[report.status]),
            f"v{report.registered_version}",
            f"v{report.source_version}" if report.source_version else "—",
            report.source or "—",
        )
    console.print(table)

    problematic = problems(reports)
    if problematic:
        console.print()
        for report in problematic:
            console.print(f"  [red]•[/] {report.headline()}")
            if report.detail:
                console.print(f"    [dim]{report.detail}[/]")
        console.print()
        console.print(
            "[dim]Run [bold]ionic check --all[/bold] to see what the changes would "
            "break, then [bold]ionic register <path> --force[/bold] to update.[/]"
        )
        raise typer.Exit(1)

    console.print()
    console.print("[green]every contract matches its source file.[/]")


@app.command("export")
def export_registry(
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Write here."),
    registry_path: Optional[Path] = typer.Option(None, "--registry"),
) -> None:
    """Export every contract as JSON, for review, backup, or sharing via git."""
    _, registry = _open(registry_path)
    payload = json.dumps(registry.export(), indent=2, sort_keys=False)
    count = len(registry.list())
    registry.close()

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload + "\n", encoding="utf-8")
        console.print(f"[green]exported[/] {count} contract(s) → {output}")
    else:
        _emit_json(payload)


@app.command("import")
def import_registry(
    path: Path = typer.Argument(..., help="A file written by `ionic export`."),
    force: bool = typer.Option(
        True, "--force/--no-force", help="Overwrite contracts that already exist."
    ),
    registry_path: Optional[Path] = typer.Option(None, "--registry"),
) -> None:
    """Import contracts from an export file."""
    if not path.is_file():
        _fail(f"{path} is not a file")
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _fail(f"{path} is not valid JSON: {exc}")
        return

    _, registry = _open(registry_path)
    try:
        imported = registry.import_contracts(payload, force=force)
    except Exception as exc:
        registry.close()
        _fail(f"import failed: {exc}")
        return
    registry.close()

    console.print(f"[green]imported[/] {len(imported)} contract(s)")
    for contract in imported:
        console.print(f"  [dim]+[/] {contract.id} [dim]v{contract.version}[/]")


@app.command()
def history(
    contract_id: str = typer.Argument(...),
    limit: int = typer.Option(20, "--limit", "-n"),
    as_json: bool = typer.Option(False, "--json"),
    registry_path: Optional[Path] = typer.Option(None, "--registry"),
) -> None:
    """Show how a contract has changed over time."""
    _, registry = _open(registry_path)
    if not registry.exists(contract_id):
        registry.close()
        _fail(f"No contract registered with id {contract_id!r}", code=1)
        return
    entries = registry.history(contract_id, limit=limit)
    registry.close()

    if as_json:
        _emit_json(
            json.dumps(
                [{k: v for k, v in e.items() if k != "contract"} for e in entries], indent=2
            )
        )
        return

    table = Table(
        title=f"{contract_id} — {len(entries)} revision(s), newest first",
        title_justify="left",
        header_style="dim",
        box=None,
        padding=(0, 2),
    )
    table.add_column("#", justify="right", style="dim")
    table.add_column("version", style="bold")
    table.add_column("fingerprint", style="dim")
    table.add_column("recorded")
    table.add_column("", style="dim")

    for index, entry in enumerate(entries):
        # Entries are newest-first, so "changed" means it differs from the
        # revision recorded before it, which is the *next* row down.
        older = entries[index + 1]["fingerprint"] if index + 1 < len(entries) else None
        note = ""
        if older is None:
            note = "first registration"
        elif older != entry["fingerprint"]:
            note = "behaviour changed"
        else:
            note = "version only"
        table.add_row(
            str(len(entries) - index),
            f"v{entry['version']}",
            entry["fingerprint"],
            entry["recorded_at"].split(".")[0].replace("T", " "),
            note,
        )
    console.print(table)


@app.command()
def diff(
    contract_id: str = typer.Argument(...),
    against: Optional[Path] = typer.Option(
        None,
        "--against",
        "-a",
        help="Compare the registered contract to this file instead of to its previous revision.",
    ),
    registry_path: Optional[Path] = typer.Option(None, "--registry"),
) -> None:
    """Show what changed, with no verdict and no dependents consulted.

    `ionic check` answers "does this break anyone". `ionic diff` just answers
    "what moved".
    """
    _, registry = _open(registry_path)
    try:
        current = registry.get(contract_id)
    except ContractNotFound as exc:
        registry.close()
        _fail(str(exc), code=1)
        return

    if against is not None:
        if not against.exists():
            registry.close()
            _fail(f"{against} does not exist")
            return
        before, after = current, _load_contract(against, contract_id)
        label = f"registry v{before.version} → {against}"
    else:
        previous = registry.previous(contract_id)
        if previous is None:
            registry.close()
            console.print(
                f"[dim]`{contract_id}` has only ever been registered once; "
                "nothing to compare. Try --against <file>.[/]"
            )
            raise typer.Exit(0)
        before, after = previous, current
        label = f"v{before.version} → v{after.version}"
    registry.close()

    findings = structural_findings(before, after, [])
    console.print(Panel(Text(label, style="bold"), border_style="cyan"))

    if not findings:
        console.print("[dim]No behavioural differences.[/]")
        return

    table = Table(header_style="dim", box=None, padding=(0, 2))
    table.add_column("severity")
    table.add_column("change")
    table.add_column("kind", style="dim")
    for finding in sorted(findings, key=lambda f: f.sort_key()):
        table.add_row(_severity_text(finding.severity), finding.summary, finding.kind)
    console.print(table)
    console.print()
    console.print(
        "[dim]Severities here ignore dependents. Run "
        f"[bold]ionic check {contract_id}[/bold] to see who actually breaks.[/]"
    )


@app.command()
def graph(
    contract_id: Optional[str] = typer.Option(None, "--id", help="Focus on one contract."),
    output_format: str = typer.Option("tree", "--format", help="tree|json|dot"),
    registry_path: Optional[Path] = typer.Option(None, "--registry"),
) -> None:
    """Show the contract dependency graph."""
    _, registry = _open(registry_path)
    try:
        dependency_graph = registry.graph(root=contract_id)
    except ContractNotFound as exc:
        registry.close()
        _fail(str(exc), code=1)
        return

    if output_format == "json":
        _emit_json(json.dumps(dependency_graph.model_dump(mode="json"), indent=2))
        registry.close()
        return

    if output_format == "dot":
        lines = ["digraph ionic {", '  rankdir="LR";', '  node [shape=box, style=rounded];']
        for node in dependency_graph.nodes:
            lines.append(f'  "{node.id}" [label="{node.name}\\nv{node.version}"];')
        for edge in dependency_graph.edges:
            style = "" if edge.resolved else ' [style=dashed, color=red]'
            lines.append(f'  "{edge.source}" -> "{edge.target}"{style};')
        lines.append("}")
        console.print("\n".join(lines))
        registry.close()
        return

    if not dependency_graph.nodes:
        console.print("[dim]No contracts registered.[/]")
        registry.close()
        return

    tree = Tree("[bold]contracts[/]")
    for node in sorted(dependency_graph.nodes, key=lambda n: n.id):
        branch = tree.add(f"[bold]{node.id}[/] [dim]v{node.version}[/]")
        outgoing = [e for e in dependency_graph.edges if e.source == node.id]
        incoming = [e for e in dependency_graph.edges if e.target == node.id]
        for edge in outgoing:
            needs = []
            if edge.requires_tools:
                needs.append(f"tools: {', '.join(edge.requires_tools)}")
            if edge.requires_capabilities:
                needs.append(f"capabilities: {', '.join(edge.requires_capabilities)}")
            if edge.expects_outputs:
                needs.append(f"outputs: {', '.join(edge.expects_outputs)}")
            suffix = f" [dim]({'; '.join(needs)})[/]" if needs else ""
            marker = "→" if edge.resolved else "[red]→ (unregistered)[/]"
            branch.add(f"{marker} depends on [bold]{edge.target}[/]{suffix}")
        if incoming:
            names = ", ".join(sorted(e.source for e in incoming))
            branch.add(f"[dim]← used by {names}[/]")
    console.print(tree)

    unresolved = dependency_graph.unresolved()
    if unresolved:
        console.print()
        console.print(
            f"[yellow]{len(unresolved)} dependency(ies) point at unregistered contracts; "
            "changes to those will not be checked.[/]"
        )

    cycles = dependency_graph.cycles()
    if cycles:
        console.print()
        console.print(f"[yellow]{len(cycles)} dependency cycle(s):[/]")
        for cycle in cycles:
            console.print("  [yellow]↻[/] " + " → ".join(cycle))
        console.print(
            "  [dim]Neither side can be rolled out without transiently breaking "
            "the other.[/]"
        )
    registry.close()


@app.command()
def status(
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of a panel."),
    registry_path: Optional[Path] = typer.Option(None, "--registry"),
) -> None:
    """Show where the registry lives and how Ionic is configured."""
    config, registry = _open(registry_path)
    stats = registry.stats()
    drift_reports = detect_drift(registry)
    registry.close()

    drift_counts = summarize(drift_reports)
    stale = problems(drift_reports)

    if as_json:
        judge_provider = (
            config.subscription_runtime
            if config.model_access == "subscription"
            else config.judge_provider
        )
        judge_model = (
            str(config.judge_model or "").strip()
            if config.model_access == "subscription"
            else config.effective_judge_model
        )
        _emit_json(
            json.dumps(
                {
                    "version": __version__,
                    "desktop_protocol": DESKTOP_PROTOCOL,
                    "project_root": str(config.project_root),
                    "registry": stats,
                    "judge": {
                        "access": config.model_access,
                        "provider": judge_provider,
                        "model": judge_model,
                        "description": config.describe_judge(),
                        "credentials_present": config.judge_credentials_present,
                    },
                    "fail_on": config.fail_on.value,
                    "drift": {
                        "counts": drift_counts,
                        "stale": [r.contract_id for r in stale],
                    },
                    "telemetry": "none",
                },
                indent=2,
            )
        )
        return

    table = Table(box=None, show_header=False, pad_edge=False)
    table.add_column(style="dim", width=14)
    table.add_column()
    table.add_row("version", __version__)
    table.add_row("project root", str(config.project_root))
    table.add_row("registry", stats["path"])
    table.add_row("contracts", str(stats["contracts"]))
    table.add_row("dependencies", str(stats["dependencies"]))
    table.add_row("revisions", str(stats["revisions"]))
    table.add_row("judge", config.describe_judge())
    table.add_row("fail on", config.fail_on.value)
    if stale:
        table.add_row(
            "sources",
            f"[bold red]{len(stale)} stale[/] [dim]of {len(drift_reports)} — run "
            "[bold]ionic drift[/bold][/]",
        )
    elif drift_reports:
        table.add_row("sources", f"[green]{len(drift_reports)} in sync[/]")
    table.add_row("telemetry", "[green]none[/]")
    console.print(Panel(table, title="ionic status", border_style="cyan"))


@app.command()
def serve(
    http: bool = typer.Option(False, "--http", help="Serve over streamable HTTP instead of stdio."),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
) -> None:
    """Run the Ionic MCP server."""
    from .mcp_server import main as serve_main

    if http:
        err_console.print(
            f"[dim]ionic MCP server on http://{host}:{port} (streamable-http)[/]"
        )
        serve_main(transport="streamable-http", host=host, port=port)
    else:
        err_console.print("[dim]ionic MCP server on stdio[/]")
        serve_main(transport="stdio")


@app.command()
def version() -> None:
    """Print the Ionic version."""
    console.print(__version__)


# ---------------------------------------------------------------------------
# report rendering
# ---------------------------------------------------------------------------


def _print_report(report: CompatibilityReport) -> None:
    approved = report.verdict is Verdict.APPROVED
    style = "green" if approved else "red"

    header = Text()
    header.append(report.verdict.value, style=f"bold {style}")
    header.append(f"   {report.contract_id}  ", style="bold")
    header.append(f"v{report.from_version} → v{report.to_version}\n", style="dim")
    header.append(report.headline(), style="dim")
    console.print(Panel(header, border_style=style))

    if report.assessment:
        console.print(Panel(report.assessment, title="semantic review", border_style="dim"))

    counts = report.counts()
    chips = [
        f"[{SEVERITY_STYLE[sev]}]{counts[sev.value]} {sev.value}[/]"
        for sev in Severity
        if counts[sev.value]
    ]
    deps = ", ".join(report.dependents_checked) or "none registered"
    console.print(f"  {'  '.join(chips) if chips else '[dim]no findings[/]'}")
    console.print(f"  [dim]dependents checked: {deps}[/]")
    console.print()

    blocking = [f for f in report.sorted_findings() if f.severity >= report.fail_on]
    other = [f for f in report.sorted_findings() if f.severity < report.fail_on]

    for finding in blocking:
        console.print(_finding_panel(finding))

    if other:
        table = Table(
            title="other observations",
            title_justify="left",
            header_style="dim",
            box=None,
            padding=(0, 1),
        )
        table.add_column("severity")
        table.add_column("affects", style="dim")
        table.add_column("finding")
        for finding in other:
            table.add_row(
                _severity_text(finding.severity),
                finding.affected_contract or "—",
                finding.summary,
            )
        console.print(table)
        console.print()

    judge = report.judge
    if judge.enabled:
        console.print(f"[dim]semantic review: {judge.provider} {judge.model}[/]")
    elif judge.error:
        console.print(f"[yellow]semantic review skipped:[/] [dim]{judge.error}[/]")
    else:
        console.print("[dim]structural analysis only[/]")


def _finding_panel(finding: Finding) -> Panel:
    body = Text()
    if finding.detail:
        body.append(finding.detail + "\n\n")
    if finding.evidence:
        body.append("evidence\n", style="dim")
        for item in finding.evidence:
            body.append(f"  · {item}\n", style="dim")
        body.append("\n")
    if finding.recommendation:
        body.append("fix  ", style="dim")
        body.append(finding.recommendation)

    target = f" → {finding.affected_contract}" if finding.affected_contract else ""
    title = Text()
    title.append(f"{SEVERITY_ICON[finding.severity]} {finding.severity.value.upper()}  ", style=SEVERITY_STYLE[finding.severity])
    title.append(f"{finding.summary}{target}", style="bold")

    subtitle = f"{finding.kind} · {finding.origin}"
    return Panel(
        body if str(body) else Text(finding.summary),
        title=title,
        title_align="left",
        subtitle=subtitle,
        subtitle_align="right",
        border_style=SEVERITY_STYLE[finding.severity],
    )


def _stream_is_tty(stream: Any) -> bool:
    """Return whether *stream* is interactive without trusting its shape."""
    try:
        return bool(stream.isatty())
    except (AttributeError, OSError):
        return False


def _should_launch_tui(
    *,
    argv: list[str] | None = None,
    input_stream: Any | None = None,
    output_stream: Any | None = None,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Keep the interactive shell out of scripts, CI, and Desktop IPC."""
    arguments = sys.argv[1:] if argv is None else argv
    stdin = sys.stdin if input_stream is None else input_stream
    stdout = sys.stdout if output_stream is None else output_stream
    env = os.environ if environ is None else environ
    return (
        not arguments
        and _stream_is_tty(stdin)
        and _stream_is_tty(stdout)
        and not env.get("CI")
        and not env.get("IONIC_NO_TUI")
    )


def main() -> None:  # pragma: no cover
    if _should_launch_tui():
        from .tui import run_tui

        try:
            raise SystemExit(run_tui())
        except KeyboardInterrupt:
            raise SystemExit(130) from None
        except EOFError:
            raise SystemExit(0) from None
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
