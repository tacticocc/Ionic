"""Ionic — the compatibility layer for multi-agent systems.

Register agent contracts. Detect semantic breakages before they ship.
Zero telemetry. Free forever.
"""

from __future__ import annotations

__version__ = "0.7.2"

from .compat import check_against_registry, check_compatibility, render_markdown
from .config import Config
from .diff import structural_findings
from .extract import extract_contract, extract_from_file
from .models import (
    CompatibilityReport,
    Constraint,
    Contract,
    Dependency,
    DependencyGraph,
    Finding,
    Format,
    IOSpec,
    Severity,
    ToolSpec,
    Verdict,
)
from .registry import Registry, RegistryStateChanged, open_registry
from .workspace import (
    WorkspaceAgent,
    WorkspaceConflict,
    WorkspaceDocument,
    WorkspaceError,
    WorkspaceNetwork,
    WorkspaceReport,
    WorkspaceRepository,
    discover_instruction_files,
    scan_workspace,
    sync_workspace,
    workspace_check,
)

__all__ = [
    "__version__",
    "CompatibilityReport",
    "Config",
    "Constraint",
    "Contract",
    "Dependency",
    "DependencyGraph",
    "Finding",
    "Format",
    "IOSpec",
    "Registry",
    "RegistryStateChanged",
    "Severity",
    "ToolSpec",
    "Verdict",
    "WorkspaceAgent",
    "WorkspaceConflict",
    "WorkspaceDocument",
    "WorkspaceError",
    "WorkspaceNetwork",
    "WorkspaceReport",
    "WorkspaceRepository",
    "check_against_registry",
    "check_compatibility",
    "discover_instruction_files",
    "extract_contract",
    "extract_from_file",
    "open_registry",
    "render_markdown",
    "scan_workspace",
    "structural_findings",
    "sync_workspace",
    "workspace_check",
]
