"""Detect registered contracts that no longer match their source files.

This closes the quietest failure mode in the whole product. Ionic checks a
proposed change against *the registry*, so if someone edits `AGENTS.md` and
never re-registers, every later check is measured against a contract that no
longer exists. Nothing errors. The tool just stops protecting you.

Comparison is by fingerprint, which covers behaviour and deliberately ignores
bookkeeping: moving a file, retagging it, or bumping only the version is not
drift.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Iterable

from .extract import extract_from_file
from .models import IonicModel
from .registry import Registry


class DriftStatus(str, Enum):
    IN_SYNC = "in_sync"
    DRIFTED = "drifted"
    VERSION_ONLY = "version_only"
    SOURCE_MISSING = "source_missing"
    SOURCE_UNREADABLE = "source_unreadable"
    NO_SOURCE = "no_source"

    @property
    def is_problem(self) -> bool:
        return self in {
            DriftStatus.DRIFTED,
            DriftStatus.SOURCE_MISSING,
            DriftStatus.SOURCE_UNREADABLE,
        }


class DriftReport(IonicModel):
    """How one registered contract compares to the file it came from."""

    contract_id: str
    status: DriftStatus
    source: str | None = None
    resolved_source: str | None = None
    registered_version: str = ""
    source_version: str | None = None
    registered_fingerprint: str = ""
    source_fingerprint: str | None = None
    detail: str = ""

    @property
    def is_problem(self) -> bool:
        return self.status.is_problem

    def headline(self) -> str:
        if self.status is DriftStatus.DRIFTED:
            return f"`{self.contract_id}` has changed on disk since it was registered"
        if self.status is DriftStatus.VERSION_ONLY:
            return (
                f"`{self.contract_id}` source is at v{self.source_version}, "
                f"registry at v{self.registered_version}"
            )
        if self.status is DriftStatus.SOURCE_MISSING:
            return f"`{self.contract_id}` source file is gone"
        if self.status is DriftStatus.SOURCE_UNREADABLE:
            return f"`{self.contract_id}` source file could not be read"
        if self.status is DriftStatus.NO_SOURCE:
            return f"`{self.contract_id}` was registered without a source file"
        return f"`{self.contract_id}` matches its source"


def candidate_roots(registry: Registry, extra: Iterable[Path | str] = ()) -> list[Path]:
    """Directories a relative `source` path might be relative to.

    Contracts store whatever path they were registered with, which is usually
    relative to wherever the user was standing at the time. The registry lives
    at `<project>/.ionic/registry.db`, so its grandparent is a good guess at the
    project root.
    """
    roots: list[Path] = [Path(root) for root in extra]
    roots.append(Path.cwd())
    parent = registry.path.parent
    roots.append(parent)
    roots.append(parent.parent)

    seen: set[Path] = set()
    unique: list[Path] = []
    for root in roots:
        resolved = root.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def resolve_source(source: str, roots: Iterable[Path]) -> Path | None:
    """Find a contract's source file, trying each candidate root."""
    candidate = Path(source).expanduser()
    if candidate.is_absolute():
        return candidate if candidate.is_file() else None
    for root in roots:
        resolved = (root / candidate).resolve()
        if resolved.is_file():
            return resolved
    return None


def detect_drift(
    registry: Registry, *, roots: Iterable[Path | str] = (), contract_id: str | None = None
) -> list[DriftReport]:
    """Compare every registered contract against the file it was extracted from."""
    search_roots = candidate_roots(registry, roots)
    contracts = [registry.get(contract_id)] if contract_id else registry.list()

    reports: list[DriftReport] = []
    for contract in contracts:
        report = DriftReport(
            contract_id=contract.id,
            status=DriftStatus.NO_SOURCE,
            source=contract.source,
            registered_version=contract.version,
            registered_fingerprint=contract.fingerprint(),
        )

        if not contract.source:
            report.detail = (
                "Registered directly rather than extracted, so there is nothing to "
                "compare against."
            )
            reports.append(report)
            continue

        resolved = resolve_source(contract.source, search_roots)
        if resolved is None:
            report.status = DriftStatus.SOURCE_MISSING
            report.detail = (
                f"`{contract.source}` was not found. Re-register from its new "
                "location, or remove the contract."
            )
            reports.append(report)
            continue

        report.resolved_source = str(resolved)
        try:
            current = extract_from_file(resolved, contract_id=contract.id)
        except Exception as exc:
            report.status = DriftStatus.SOURCE_UNREADABLE
            report.detail = f"{type(exc).__name__}: {exc}"
            reports.append(report)
            continue

        # Workspace contracts store graph-safe, repository-qualified dependency
        # ids while their source files intentionally keep ergonomic bare ids.
        # Reapply the exact resolution captured by the reviewed scan before
        # comparing fingerprints; never guess from the current filesystem.
        workspace = contract.metadata.get("workspace")
        if isinstance(workspace, dict):
            dependency_map = workspace.get("dependency_map")
            if isinstance(dependency_map, dict):
                current = current.revise(
                    depends_on=[
                        dependency.revise(
                            contract_id=str(
                                dependency_map.get(
                                    dependency.contract_id, dependency.contract_id
                                )
                            )
                        ).model_dump(mode="json")
                        for dependency in current.depends_on
                    ]
                )

        report.source_version = current.version
        report.source_fingerprint = current.fingerprint()

        if current.fingerprint() != contract.fingerprint():
            report.status = DriftStatus.DRIFTED
            report.detail = (
                "Checks are being measured against the registered contract, not "
                "the file. Run a check against the file, then re-register."
            )
        elif current.version != contract.version:
            report.status = DriftStatus.VERSION_ONLY
            report.detail = "Behaviour is identical; only the version differs."
        else:
            report.status = DriftStatus.IN_SYNC

        reports.append(report)

    return reports


def problems(reports: Iterable[DriftReport]) -> list[DriftReport]:
    return [report for report in reports if report.is_problem]


def summarize(reports: Iterable[DriftReport]) -> dict[str, int]:
    counts = {status.value: 0 for status in DriftStatus}
    for report in reports:
        counts[report.status.value] += 1
    return counts
