"""Extract Ionic contracts from agent instruction files.

Three layers, most authoritative first:

1. An explicit ```ionic fenced block (YAML or JSON) -- treated as ground truth.
2. YAML frontmatter -- fills in identity fields (name, version, tags...).
3. Heading/bullet heuristics over the prose -- fills in whatever is left.

All of it is deterministic and offline. The LLM never touches extraction;
it is only used later, to judge changes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .models import (
    Constraint,
    Contract,
    Dependency,
    Format,
    IOSpec,
    ToolSpec,
    slugify,
)

AGENT_FILENAMES = ("AGENTS.md", "CLAUDE.md", "AGENT.md", ".ionic.yaml", "ionic.yaml")

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
_FENCE_RE = re.compile(
    r"^```+[ \t]*([^\n`]*)\n(.*?)^```+[ \t]*$",
    re.DOTALL | re.MULTILINE,
)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_BULLET_RE = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+(.*)$")
_IDENT_RE = re.compile(r"^`([^`]+)`|^\*\*([^*]+)\*\*|^([A-Za-z_][\w.\-]*)(?=\s*[(:—–-])")
_PAREN_RE = re.compile(r"\(([^)]*)\)")
_SPLIT_DESC_RE = re.compile(r"\s*(?:[—–:]|--|\s-\s)\s*")

# Section keyword -> contract field. Checked longest-first so that
# "output schema" beats "output".
_SECTION_KEYWORDS: list[tuple[str, str]] = [
    ("depends on", "depends_on"),
    ("dependencies", "depends_on"),
    ("dependency", "depends_on"),
    ("upstream", "depends_on"),
    ("consumes from", "depends_on"),
    ("integrates with", "depends_on"),
    ("persona rules", "persona_rules"),
    ("persona", "persona_rules"),
    ("tone", "persona_rules"),
    ("voice", "persona_rules"),
    ("style", "persona_rules"),
    ("constraints", "constraints"),
    ("guardrails", "constraints"),
    ("hard rules", "constraints"),
    ("rules", "constraints"),
    ("must not", "constraints"),
    ("never", "constraints"),
    ("limits", "constraints"),
    ("policies", "constraints"),
    ("policy", "constraints"),
    ("tools", "tools"),
    ("tool", "tools"),
    ("functions", "tools"),
    ("actions", "tools"),
    ("capabilities", "capabilities"),
    ("responsibilities", "capabilities"),
    ("skills", "capabilities"),
    ("what i do", "capabilities"),
    ("outputs", "outputs"),
    ("output", "outputs"),
    ("returns", "outputs"),
    ("produces", "outputs"),
    ("emits", "outputs"),
    ("response format", "outputs"),
    ("inputs", "inputs"),
    ("input", "inputs"),
    ("accepts", "inputs"),
    ("receives", "inputs"),
    ("request format", "inputs"),
    ("parameters", "inputs"),
    ("identity", "identity"),
    ("role", "identity"),
    ("who i am", "identity"),
    ("purpose", "description"),
    ("overview", "description"),
    ("description", "description"),
    ("summary", "description"),
]

_FORMAT_HINTS: list[tuple[str, Format]] = [
    ("json schema", Format.JSON),
    ("jsonl", Format.JSON),
    ("json", Format.JSON),
    ("yaml", Format.YAML),
    ("yml", Format.YAML),
    ("markdown", Format.MARKDOWN),
    ("md", Format.MARKDOWN),
    ("xml", Format.XML),
    ("csv", Format.CSV),
    ("plain text", Format.TEXT),
    ("plaintext", Format.TEXT),
    ("text", Format.TEXT),
]


class ExtractionError(ValueError):
    """Raised when a file cannot be turned into a contract."""


@dataclass
class Section:
    level: int
    title: str
    lines: list[str] = field(default_factory=list)

    @property
    def body(self) -> str:
        return "\n".join(self.lines).strip()


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def extract_contract(
    text: str,
    *,
    source: str | None = None,
    contract_id: str | None = None,
) -> Contract:
    """Turn an agent instruction file into a Contract."""
    body, frontmatter = _split_frontmatter(text)
    explicit, body = _pull_ionic_block(body)

    payload: dict[str, Any] = {}
    payload.update(_normalize_payload(frontmatter))
    heuristic = _from_prose(body)
    payload = _merge(heuristic, payload)  # frontmatter wins over prose
    payload = _merge(payload, _normalize_payload(explicit))  # ionic block wins over all

    if contract_id:
        payload["id"] = contract_id
    if not payload.get("id"):
        payload["id"] = slugify(payload.get("name") or _first_heading(body) or (source or "agent"))
    if not payload.get("name"):
        payload["name"] = _first_heading(body) or payload["id"]
    if source:
        payload.setdefault("source", source)

    return Contract.model_validate(payload)


def extract_from_file(path: Path | str, *, contract_id: str | None = None) -> Contract:
    path = Path(path)
    if not path.is_file():
        raise ExtractionError(f"{path} is not a file")
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".json"}:
        contract = Contract.model_validate(json.loads(text))
        changes: dict[str, Any] = {"source": path.as_posix()}
        if contract_id:
            changes["id"] = contract_id
        return contract.revise(**changes)
    if path.suffix.lower() in {".yaml", ".yml"}:
        data = _load_structured(text, "yaml")
        if not isinstance(data, dict):
            raise ExtractionError(f"{path} does not contain a contract mapping")
        payload = _normalize_payload(data)
        payload["source"] = path.as_posix()
        if contract_id:
            payload["id"] = contract_id
        return Contract.model_validate(payload)
    # Store source paths with portable separators. Source is deliberately not
    # part of a contract fingerprint, and Path accepts this form on Windows.
    return extract_contract(text, source=path.as_posix(), contract_id=contract_id)


def discover_agent_files(root: Path | str, *, filenames: Iterable[str] = AGENT_FILENAMES) -> list[Path]:
    """Find agent instruction files under a directory tree."""
    root = Path(root)
    if root.is_file():
        return [root]
    wanted = {name.lower() for name in filenames}
    skip_dirs = {".git", "node_modules", ".venv", "venv", "__pycache__", ".ionic", "dist", "build"}
    found: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in skip_dirs for part in path.parts):
            continue
        if path.name.lower() in wanted:
            found.append(path)
    return found


def render_markdown(contract: Contract) -> str:
    """Render a contract back out as an AGENTS.md-style document."""
    out: list[str] = []
    out.append(f"# {contract.name}")
    out.append("")
    out.append("<!-- Generated by Ionic. Edit freely; re-extract to update. -->")
    out.append("")
    out.append("```ionic")
    out.append(
        json.dumps(
            {"id": contract.id, "version": contract.version, "tags": contract.tags},
            indent=2,
        )
    )
    out.append("```")
    out.append("")
    if contract.description:
        out.append("## Purpose")
        out.append("")
        out.append(contract.description)
        out.append("")
    if contract.identity:
        out.append("## Identity")
        out.append("")
        out.append(contract.identity)
        out.append("")
    if contract.inputs:
        out.append("## Inputs")
        out.append("")
        for spec in contract.inputs:
            suffix = "" if spec.required else " (optional)"
            out.append(f"- `{spec.name}` ({spec.format.value}){suffix} — {spec.description}".rstrip(" —"))
        out.append("")
    if contract.outputs:
        out.append("## Outputs")
        out.append("")
        for spec in contract.outputs:
            out.append(f"- `{spec.name}` ({spec.format.value}) — {spec.description}".rstrip(" —"))
        out.append("")
    if contract.tools:
        out.append("## Tools")
        out.append("")
        for tool in contract.tools:
            optional = "" if tool.required else " (optional)"
            out.append(f"- `{tool.name}`{optional} — {tool.description}".rstrip(" —"))
        out.append("")
    if contract.capabilities:
        out.append("## Capabilities")
        out.append("")
        out.extend(f"- {c}" for c in contract.capabilities)
        out.append("")
    if contract.constraints:
        out.append("## Constraints")
        out.append("")
        for constraint in contract.constraints:
            # Only surface the id when it is an authored one. Derived ids are
            # noise in a document meant for humans, and re-extracting the
            # rendered file regenerates them identically anyway.
            if constraint.id == _auto_id(constraint.statement):
                out.append(f"- {constraint.statement}")
            else:
                out.append(f"- [{constraint.id}] {constraint.statement}")
        out.append("")
    if contract.persona_rules:
        out.append("## Persona")
        out.append("")
        out.extend(f"- {r}" for r in contract.persona_rules)
        out.append("")
    if contract.depends_on:
        out.append("## Depends On")
        out.append("")
        for dep in contract.depends_on:
            parts: list[str] = []
            if dep.requires_tools:
                parts.append(f"tools: {', '.join(dep.requires_tools)}")
            if dep.requires_capabilities:
                parts.append(f"capabilities: {', '.join(dep.requires_capabilities)}")
            if dep.expects_outputs:
                parts.append(f"outputs: {', '.join(dep.expects_outputs)}")
            if dep.expects_format:
                parts.append(f"format: {dep.expects_format.value}")
            detail = f" — {'; '.join(parts)}" if parts else ""
            out.append(f"- `{dep.contract_id}`{detail}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------------------
# frontmatter / fenced blocks
# ---------------------------------------------------------------------------


def _split_frontmatter(text: str) -> tuple[str, dict[str, Any]]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return text, {}
    data = _load_structured(match.group(1), "yaml")
    body = text[match.end() :]
    return body, data if isinstance(data, dict) else {}


def _pull_ionic_block(text: str) -> tuple[dict[str, Any], str]:
    """Find and remove an explicit ```ionic block, returning its payload."""
    for match in _FENCE_RE.finditer(text):
        info = (match.group(1) or "").strip().lower()
        if "ionic" not in info.split():
            continue
        lang = "json" if "json" in info else "yaml"
        data = _load_structured(match.group(2), lang)
        if isinstance(data, dict):
            stripped = text[: match.start()] + text[match.end() :]
            return data, stripped
    return {}, text


def _load_structured(raw: str, lang: str) -> Any:
    raw = raw.strip()
    if not raw:
        return {}
    if lang == "json":
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    try:
        import yaml  # imported lazily so JSON-only users need no yaml
    except ModuleNotFoundError:  # pragma: no cover - yaml is a hard dependency
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    try:
        return yaml.safe_load(raw)
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# prose heuristics
# ---------------------------------------------------------------------------


def _first_heading(text: str) -> str | None:
    for line in text.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            return match.group(2).strip()
    return None


def _sections(text: str) -> list[Section]:
    sections: list[Section] = []
    current = Section(level=0, title="", lines=[])
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            current.lines.append(line)
            continue
        heading = None if in_fence else _HEADING_RE.match(line)
        if heading:
            sections.append(current)
            current = Section(level=len(heading.group(1)), title=heading.group(2).strip())
        else:
            current.lines.append(line)
    sections.append(current)
    return [s for s in sections if s.title or s.body]


def _classify(title: str) -> str | None:
    normalized = re.sub(r"[^a-z0-9 ]+", " ", title.lower()).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    if not normalized:
        return None
    for keyword, target in _SECTION_KEYWORDS:
        if keyword in normalized:
            return target
    return None


def _bullets(body: str) -> list[str]:
    """Collect top-level bullets; nested bullets are folded into their parent."""
    items: list[str] = []
    in_fence = False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _BULLET_RE.match(line)
        if not match:
            if items and line.strip() and line.startswith((" ", "\t")):
                items[-1] = f"{items[-1]} {line.strip()}"
            continue
        indent, content = match.group(1), match.group(2).strip()
        if not content:
            continue
        if len(indent.replace("\t", "  ")) >= 2 and items:
            items[-1] = f"{items[-1]} {content}"
        else:
            items.append(content)
    return items


def _prose(body: str) -> str:
    """Non-bullet, non-fence prose from a section."""
    lines: list[str] = []
    in_fence = False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or _BULLET_RE.match(line):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _split_identifier(item: str) -> tuple[str | None, str]:
    """Pull a leading identifier off a bullet: `name` — description."""
    match = _IDENT_RE.match(item.strip())
    if match:
        name = next(g for g in match.groups() if g)
        rest = item.strip()[match.end() :]
        rest = _SPLIT_DESC_RE.sub("", rest, count=1) if _SPLIT_DESC_RE.match(rest) else rest.lstrip()
        return name.strip(), rest.strip()
    return None, item.strip()


def _detect_format(text: str) -> Format | None:
    lowered = text.lower()
    for hint, fmt in _FORMAT_HINTS:
        if re.search(rf"\b{re.escape(hint)}\b", lowered):
            return fmt
    return None


def _io_specs(section: Section) -> list[IOSpec]:
    specs: list[IOSpec] = []
    section_format = _detect_format(section.title) or _detect_format(_prose(section.body))
    schema = _first_json_schema(section.body)
    for item in _bullets(section.body):
        name, description = _split_identifier(item)
        optional = bool(re.search(r"\b(optional|nullable|if provided)\b", item, re.I))
        parenthetical = _PAREN_RE.search(item)
        fmt = None
        if parenthetical:
            fmt = _detect_format(parenthetical.group(1))
            description = _PAREN_RE.sub("", description, count=1).strip(" —–-:")
        fmt = fmt or _detect_format(item) or section_format or Format.TEXT
        if name is None:
            name = slugify(" ".join(item.split()[:3])) or "payload"
        specs.append(
            IOSpec(
                name=name,
                format=fmt,
                description=description,
                required=not optional,
            )
        )
    if not specs and (schema or section_format):
        specs.append(
            IOSpec(
                name="payload",
                format=section_format or Format.JSON,
                description=_prose(section.body)[:400],
                json_schema=schema,
            )
        )
    elif schema and specs:
        specs[0] = specs[0].model_copy(update={"json_schema": schema})
    return specs


def _first_json_schema(body: str) -> dict[str, Any] | None:
    for match in _FENCE_RE.finditer(body):
        info = (match.group(1) or "").strip().lower()
        if info not in {"json", "jsonschema", "json schema", "yaml"}:
            continue
        data = _load_structured(match.group(2), "json" if info.startswith("json") else "yaml")
        if isinstance(data, dict) and ("type" in data or "properties" in data or "$schema" in data):
            return data
    return None


def _tool_specs(section: Section) -> list[ToolSpec]:
    tools: list[ToolSpec] = []
    for item in _bullets(section.body):
        name, description = _split_identifier(item)
        if name is None:
            words = item.split()
            name = slugify(" ".join(words[:3]))
            description = item
        signature = None
        if "(" in name and name.endswith(")"):
            signature = name
            name = name.split("(", 1)[0]
        optional = bool(re.search(r"\b(optional|deprecated|experimental)\b", item, re.I))
        tools.append(
            ToolSpec(
                name=name.strip(),
                description=description,
                signature=signature,
                required=not optional,
            )
        )
    return tools


_DEP_LABELS = {
    "tools": "requires_tools",
    "tool": "requires_tools",
    "requires": "requires_tools",
    "capabilities": "requires_capabilities",
    "capability": "requires_capabilities",
    "outputs": "expects_outputs",
    "output": "expects_outputs",
    "expects": "expects_outputs",
    "fields": "expects_outputs",
    "constraints": "requires_constraints",
    "constraint": "requires_constraints",
}


def _dependencies(section: Section) -> list[Dependency]:
    deps: list[Dependency] = []
    for item in _bullets(section.body):
        name, rest = _split_identifier(item)
        if name is None:
            token = item.split()[0] if item.split() else ""
            name = slugify(token.strip("`*_:,"))
            rest = item
        payload: dict[str, Any] = {"contract_id": slugify(name)}
        for chunk in re.split(r"[;|]", rest):
            label_match = re.match(r"\s*([A-Za-z ]+?)\s*[:=]\s*(.+)$", chunk)
            if not label_match:
                continue
            label = label_match.group(1).strip().lower()
            values = [v.strip(" `.,") for v in re.split(r"[,/]", label_match.group(2)) if v.strip(" `.,")]
            if label in {"format", "formats"}:
                fmt = _detect_format(label_match.group(2))
                if fmt:
                    payload["expects_format"] = fmt
                continue
            field_name = _DEP_LABELS.get(label)
            if field_name:
                payload.setdefault(field_name, []).extend(values)
        if not any(k.startswith(("requires", "expects")) for k in payload):
            payload["notes"] = rest.strip()
        deps.append(Dependency.model_validate(payload))
    return deps


_CONSTRAINT_ID_RE = re.compile(r"^\[([a-z0-9][a-z0-9._-]*)\]\s*(.+)$", re.I)
_SEVERITY_TAG_RE = re.compile(r"\((critical|high|medium|low|info)\)\s*$", re.I)


def _parse_constraint(item: str) -> Constraint:
    """Parse a constraint bullet.

    Supports an explicit stable id, which is what dependents reference in
    `requires_constraints`:

        - [source-required] Every plan step carries a source_required flag.
        - [tone] Never speculate beyond the evidence. (medium)

    Without an explicit id, one is derived from the statement text. That is
    stable as long as the wording is, which is fine for constraints nobody
    depends on by name.
    """
    text = item.strip()
    severity = None
    severity_match = _SEVERITY_TAG_RE.search(text)
    if severity_match:
        from .models import Severity  # local import: avoids a cycle at module load

        severity = Severity(severity_match.group(1).lower())
        text = text[: severity_match.start()].strip()

    id_match = _CONSTRAINT_ID_RE.match(text)
    payload: dict[str, Any] = {}
    if id_match:
        payload["id"] = id_match.group(1).lower()
        payload["statement"] = id_match.group(2).strip()
    else:
        payload["statement"] = text
        payload["id"] = _auto_id(text)
    if severity is not None:
        payload["severity"] = severity
    return Constraint.model_validate(payload)


def _auto_id(statement: str) -> str:
    from .models import _auto_constraint_id

    return _auto_constraint_id(statement)


def _from_prose(text: str) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    sections = _sections(text)

    for section in sections:
        if section.level == 1 and not payload.get("name"):
            payload["name"] = section.title
            lead = _prose(section.body)
            if lead and not payload.get("description"):
                payload["description"] = lead[:1000]
            continue

        target = _classify(section.title)
        if target is None:
            continue

        if target == "tools":
            payload.setdefault("tools", []).extend(_tool_specs(section))
        elif target in {"inputs", "outputs"}:
            payload.setdefault(target, []).extend(_io_specs(section))
        elif target == "constraints":
            bullets = _bullets(section.body)
            payload.setdefault("constraints", []).extend(
                _parse_constraint(item) for item in bullets
            )
            leftover = _prose(section.body)
            if leftover and not bullets:
                payload.setdefault("constraints", []).append(_parse_constraint(leftover))
        elif target == "capabilities":
            payload.setdefault("capabilities", []).extend(_bullets(section.body))
        elif target == "persona_rules":
            items = _bullets(section.body) or [_prose(section.body)]
            payload.setdefault("persona_rules", []).extend(i for i in items if i)
        elif target == "depends_on":
            payload.setdefault("depends_on", []).extend(_dependencies(section))
        elif target == "identity":
            existing = payload.get("identity", "")
            payload["identity"] = (existing + "\n" + _prose(section.body)).strip()
        elif target == "description" and not payload.get("description"):
            payload["description"] = _prose(section.body)[:1000]

    if not payload.get("identity"):
        lead = _lead_paragraph(text)
        if lead:
            payload["identity"] = lead
    return payload


def _lead_paragraph(text: str) -> str:
    """The opening prose of the document, used as a fallback identity.

    Only the preamble and the title section describe the agent itself. Prose
    further down belongs to whatever section it sits under, and treating it as
    identity meant that appending a sentence anywhere in the file silently
    changed the contract's fingerprint.
    """
    body = _pull_ionic_block(text)[1]
    for section in _sections(body):
        if section.level not in (0, 1):
            break
        prose = _prose(section.body)
        if prose:
            return prose.split("\n\n")[0].strip()[:600]
    return ""


# ---------------------------------------------------------------------------
# payload normalisation / merging
# ---------------------------------------------------------------------------

_LIST_FIELDS = {
    "inputs",
    "outputs",
    "tools",
    "capabilities",
    "constraints",
    "persona_rules",
    "depends_on",
    "tags",
}

_ALIASES = {
    "agent": "name",
    "agent_id": "id",
    "contract_id": "id",
    "role": "identity",
    "persona": "identity",
    "rules": "persona_rules",
    "requires": "depends_on",
    "dependencies": "depends_on",
}


def _normalize_payload(raw: dict[str, Any]) -> dict[str, Any]:
    """Accept loose author-written keys and coerce them toward the model."""
    if not raw:
        return {}
    payload: dict[str, Any] = {}
    known = set(Contract.model_fields.keys())
    for key, value in raw.items():
        if value is None:
            continue
        field_name = _ALIASES.get(str(key).strip().lower(), str(key).strip().lower())
        if field_name not in known:
            payload.setdefault("metadata", {})[str(key)] = value
            continue
        if field_name in _LIST_FIELDS and isinstance(value, (str, dict)):
            value = [value]
        payload[field_name] = value
    return payload


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Override wins on scalars; lists from override replace base lists."""
    merged = dict(base)
    for key, value in override.items():
        if value in (None, "", [], {}):
            continue
        merged[key] = value
    return merged
