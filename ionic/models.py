"""Core data model for Ionic contracts.

An Ionic Contract is a structured description of an agent's behavioral
promises: who it is, what it takes in, what it hands back, what tools and
capabilities it exposes, and which constraints other agents rely on.

Everything in this module is pure data. No I/O, no network, no LLM.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

SCHEMA_VERSION = "1.0"

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]*$")
_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def slugify(value: str) -> str:
    """Turn a human name into a contract id."""
    slug = value.strip().lower()
    slug = re.sub(r"[^a-z0-9._/-]+", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug or "unnamed-agent"


def parse_version(version: str) -> tuple[int, int, int]:
    """Parse a semver string into a comparable tuple. Unparseable -> (0, 0, 0)."""
    match = _SEMVER_RE.match(version.strip())
    if not match:
        return (0, 0, 0)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


class Severity(str, Enum):
    """How badly a finding hurts."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]

    def __ge__(self, other: object) -> bool:  # type: ignore[override]
        if isinstance(other, Severity):
            return self.rank >= other.rank
        return NotImplemented

    def __gt__(self, other: object) -> bool:  # type: ignore[override]
        if isinstance(other, Severity):
            return self.rank > other.rank
        return NotImplemented

    def __le__(self, other: object) -> bool:  # type: ignore[override]
        if isinstance(other, Severity):
            return self.rank <= other.rank
        return NotImplemented

    def __lt__(self, other: object) -> bool:  # type: ignore[override]
        if isinstance(other, Severity):
            return self.rank < other.rank
        return NotImplemented


_SEVERITY_RANK: dict[Severity, int] = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


class Verdict(str, Enum):
    APPROVED = "APPROVED"
    REQUEST_CHANGES = "REQUEST_CHANGES"


class Format(str, Enum):
    """Wire format of an input or output."""

    JSON = "json"
    YAML = "yaml"
    XML = "xml"
    MARKDOWN = "markdown"
    TEXT = "text"
    CSV = "csv"
    BINARY = "binary"
    OTHER = "other"


class IonicModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, use_enum_values=False)

    def revise(self, **changes: Any) -> Any:
        """Return a validated copy with `changes` applied.

        Prefer this over `model_copy(update=...)`, which skips validation and
        will happily leave raw dicts sitting where model objects belong.
        """
        payload = self.model_dump(mode="json")
        payload.update(changes)
        return type(self).model_validate(payload)


class IOSpec(IonicModel):
    """One input or output channel of an agent."""

    name: str
    format: Format = Format.TEXT
    description: str = ""
    json_schema: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("json_schema", "schema"),
        description="Optional JSON Schema describing the payload shape.",
    )
    required: bool = True
    example: str | None = None

    @field_validator("format", mode="before")
    @classmethod
    def _coerce_format(cls, value: Any) -> Any:
        if isinstance(value, str):
            normalized = value.strip().lower()
            aliases = {
                "md": "markdown",
                "plaintext": "text",
                "plain": "text",
                "string": "text",
                "str": "text",
                "jsonl": "json",
                "yml": "yaml",
            }
            normalized = aliases.get(normalized, normalized)
            if normalized not in {f.value for f in Format}:
                return Format.OTHER
            return normalized
        return value


class ToolSpec(IonicModel):
    """A tool or function the agent exposes or is expected to call."""

    name: str
    description: str = ""
    signature: str | None = None
    required: bool = Field(
        default=True,
        description="Whether removing this tool should be treated as a breaking change.",
    )


class Constraint(IonicModel):
    """A behavioral rule other agents may depend on."""

    id: str
    statement: str
    severity: Severity = Severity.HIGH
    rationale: str = ""

    @model_validator(mode="before")
    @classmethod
    def _from_string(cls, value: Any) -> Any:
        if isinstance(value, str):
            return {"id": _auto_constraint_id(value), "statement": value.strip()}
        return value


def _auto_constraint_id(statement: str) -> str:
    digest = hashlib.sha256(statement.strip().encode("utf-8")).hexdigest()[:8]
    words = re.findall(r"[a-z0-9]+", statement.lower())[:4]
    stem = "-".join(words) or "constraint"
    return f"{stem}-{digest}"


class Dependency(IonicModel):
    """A declared reliance on another agent's contract.

    The optional `requires_*` fields are what make compatibility checking
    precise: they say exactly which parts of the upstream contract this agent
    is leaning on, so removing one of them is provably breaking rather than
    merely suspicious.
    """

    contract_id: str
    requires_tools: list[str] = Field(default_factory=list)
    requires_capabilities: list[str] = Field(default_factory=list)
    expects_outputs: list[str] = Field(default_factory=list)
    expects_format: Format | None = None
    requires_constraints: list[str] = Field(default_factory=list)
    notes: str = ""

    @model_validator(mode="before")
    @classmethod
    def _from_string(cls, value: Any) -> Any:
        if isinstance(value, str):
            return {"contract_id": value.strip()}
        return value


class Contract(IonicModel):
    """An agent's behavioral contract."""

    id: str
    name: str = ""
    version: str = "0.1.0"
    description: str = ""
    identity: str = Field(
        default="",
        description="Role / persona summary that downstream agents rely on.",
    )
    inputs: list[IOSpec] = Field(default_factory=list)
    outputs: list[IOSpec] = Field(default_factory=list)
    tools: list[ToolSpec] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)
    persona_rules: list[str] = Field(default_factory=list)
    depends_on: list[Dependency] = Field(default_factory=list)
    source: str | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        candidate = value.strip().lower()
        if not _SLUG_RE.match(candidate):
            candidate = slugify(candidate)
        return candidate

    @field_validator("version")
    @classmethod
    def _validate_version(cls, value: str) -> str:
        value = value.strip()
        if not _SEMVER_RE.match(value):
            raise ValueError(
                f"version must be semver (e.g. 1.2.3), got {value!r}"
            )
        return value

    @model_validator(mode="after")
    def _default_name(self) -> "Contract":
        if not self.name:
            object.__setattr__(self, "name", self.id)
        return self

    # -- derived views -------------------------------------------------

    def tool_names(self) -> set[str]:
        return {t.name for t in self.tools}

    def output_names(self) -> set[str]:
        return {o.name for o in self.outputs}

    def input_names(self) -> set[str]:
        return {i.name for i in self.inputs}

    def constraint_ids(self) -> set[str]:
        return {c.id for c in self.constraints}

    def dependency_ids(self) -> list[str]:
        return [d.contract_id for d in self.depends_on]

    def dependency_on(self, contract_id: str) -> Dependency | None:
        for dep in self.depends_on:
            if dep.contract_id == contract_id:
                return dep
        return None

    def tool(self, name: str) -> ToolSpec | None:
        return next((t for t in self.tools if t.name == name), None)

    def output(self, name: str) -> IOSpec | None:
        return next((o for o in self.outputs if o.name == name), None)

    def input(self, name: str) -> IOSpec | None:
        return next((i for i in self.inputs if i.name == name), None)

    def constraint(self, constraint_id: str) -> Constraint | None:
        return next((c for c in self.constraints if c.id == constraint_id), None)

    # -- identity ------------------------------------------------------

    def semantic_core(self) -> dict[str, Any]:
        """The parts of the contract that other agents actually bind to.

        Deliberately excludes timestamps, source paths, tags, and metadata:
        moving a file or re-tagging a contract is not a behavioral change.
        """
        payload = self.model_dump(
            mode="json",
            exclude={
                "created_at",
                "updated_at",
                "source",
                "tags",
                "metadata",
                "schema_version",
                "version",
            },
        )
        return payload

    def fingerprint(self) -> str:
        """Stable hash of the semantic core, for change detection."""
        blob = json.dumps(self.semantic_core(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def bumped(self, level: str = "minor") -> str:
        """Return this contract's version bumped at the given level."""
        major, minor, patch = parse_version(self.version)
        if level == "major":
            return f"{major + 1}.0.0"
        if level == "patch":
            return f"{major}.{minor}.{patch + 1}"
        return f"{major}.{minor + 1}.0"

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.model_dump(mode="json"), indent=indent, sort_keys=False)


class Finding(IonicModel):
    """One way a proposed change could break the system."""

    kind: str
    severity: Severity
    summary: str
    detail: str = ""
    changed_contract: str = ""
    affected_contract: str | None = None
    evidence: list[str] = Field(default_factory=list)
    recommendation: str = ""
    origin: str = Field(
        default="structural",
        description="'structural' for deterministic rules, 'semantic' for the LLM judge.",
    )

    def sort_key(self) -> tuple[int, str, str]:
        return (-self.severity.rank, self.affected_contract or "", self.kind)


class JudgeInfo(IonicModel):
    """Provenance for the semantic half of a report."""

    enabled: bool = False
    provider: str = "none"
    model: str = ""
    error: str | None = None


class CompatibilityReport(IonicModel):
    """The answer to 'will this change break anything?'"""

    verdict: Verdict
    contract_id: str
    from_version: str
    to_version: str
    fingerprint_before: str = ""
    fingerprint_after: str = ""
    findings: list[Finding] = Field(default_factory=list)
    dependents_checked: list[str] = Field(default_factory=list)
    fail_on: Severity = Severity.HIGH
    judge: JudgeInfo = Field(default_factory=JudgeInfo)
    assessment: str = Field(
        default="",
        description=(
            "Advisory prose from the semantic judge. Never affects the verdict; "
            "only findings do."
        ),
    )
    generated_at: datetime = Field(default_factory=utcnow)

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.severity >= self.fail_on]

    @property
    def highest_severity(self) -> Severity | None:
        if not self.findings:
            return None
        return max((f.severity for f in self.findings), key=lambda s: s.rank)

    def counts(self) -> dict[str, int]:
        counts = {s.value: 0 for s in Severity}
        for finding in self.findings:
            counts[finding.severity.value] += 1
        return counts

    def sorted_findings(self) -> list[Finding]:
        return sorted(self.findings, key=lambda f: f.sort_key())

    def headline(self) -> str:
        if self.verdict is Verdict.APPROVED:
            if not self.findings:
                return "No impact detected on dependent contracts."
            return (
                f"{len(self.findings)} non-blocking observation(s); "
                f"nothing at or above {self.fail_on.value}."
            )
        blocking = self.blocking
        return (
            f"{len(blocking)} blocking issue(s) across "
            f"{len({f.affected_contract for f in blocking if f.affected_contract})} "
            "dependent contract(s)."
        )


class GraphNode(IonicModel):
    id: str
    name: str
    version: str
    tags: list[str] = Field(default_factory=list)


class GraphEdge(IonicModel):
    source: str
    target: str
    requires_tools: list[str] = Field(default_factory=list)
    requires_capabilities: list[str] = Field(default_factory=list)
    expects_outputs: list[str] = Field(default_factory=list)
    resolved: bool = True


class DependencyGraph(IonicModel):
    """Who depends on whom, and on exactly what."""

    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)

    def dependents_of(self, contract_id: str) -> list[str]:
        return [e.source for e in self.edges if e.target == contract_id]

    def dependencies_of(self, contract_id: str) -> list[str]:
        return [e.target for e in self.edges if e.source == contract_id]

    def unresolved(self) -> list[GraphEdge]:
        return [e for e in self.edges if not e.resolved]

    def cycles(self) -> list[list[str]]:
        """Find dependency cycles.

        A cycle between agent contracts means no version of either can be
        rolled out without transiently breaking the other, so it is worth
        surfacing even though Ionic tolerates it everywhere else.
        """
        adjacency: dict[str, list[str]] = {node.id: [] for node in self.nodes}
        for edge in self.edges:
            if edge.source in adjacency and edge.target in adjacency:
                adjacency[edge.source].append(edge.target)

        found: list[list[str]] = []
        seen_signatures: set[frozenset[str]] = set()
        visited: set[str] = set()
        stack: list[str] = []
        on_stack: set[str] = set()

        def walk(node: str) -> None:
            visited.add(node)
            stack.append(node)
            on_stack.add(node)
            for neighbour in adjacency.get(node, []):
                if neighbour in on_stack:
                    cycle = stack[stack.index(neighbour) :] + [neighbour]
                    signature = frozenset(cycle)
                    if signature not in seen_signatures:
                        seen_signatures.add(signature)
                        found.append(cycle)
                elif neighbour not in visited:
                    walk(neighbour)
            stack.pop()
            on_stack.discard(node)

        for node in adjacency:
            if node not in visited:
                walk(node)
        return found
