"""Deterministic breakage analysis.

This is the offline half of Ionic and it is deliberately the load-bearing
half: every finding here is provable from the two contracts and the
dependents' declared expectations. No model is consulted, no network call is
made, and the same inputs always produce the same findings.

Severity is decided by evidence. If a dependent explicitly declares that it
requires the thing you removed, that is CRITICAL. If nothing declares it, the
same removal is a MEDIUM: still worth surfacing, not worth blocking a merge
over on its own.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from .models import (
    Constraint,
    Contract,
    Dependency,
    Finding,
    Severity,
    parse_version,
)


def structural_findings(
    current: Contract,
    proposed: Contract,
    dependents: Iterable[Contract] = (),
) -> list[Finding]:
    """Compare two revisions of a contract against everything that depends on it."""
    dependents = list(dependents)
    findings: list[Finding] = []
    findings += _tool_findings(current, proposed, dependents)
    findings += _capability_findings(current, proposed, dependents)
    findings += _output_findings(current, proposed, dependents)
    findings += _input_findings(current, proposed, dependents)
    findings += _constraint_findings(current, proposed, dependents)
    findings += _persona_findings(current, proposed)
    findings += _identity_findings(current, proposed)
    findings += _dependency_findings(current, proposed)
    findings += _version_findings(current, proposed, findings)
    for finding in findings:
        finding.changed_contract = proposed.id
    return findings


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _dep_of(dependent: Contract, target_id: str) -> Dependency:
    return dependent.dependency_on(target_id) or Dependency(contract_id=target_id)


def _consumers(
    dependents: list[Contract], target_id: str, attr: str, value: str
) -> list[Contract]:
    """Dependents that explicitly declare they rely on `value`."""
    out = []
    for dependent in dependents:
        dep = _dep_of(dependent, target_id)
        if value in getattr(dep, attr):
            out.append(dependent)
    return out


def _flatten_schema(
    schema: dict[str, Any] | None, prefix: str = "", depth: int = 0
) -> dict[str, dict[str, Any]]:
    """Flatten a JSON Schema into `{dotted.path: {type, required}}`."""
    if not isinstance(schema, dict) or depth > 6:
        return {}
    flat: dict[str, dict[str, Any]] = {}
    properties = schema.get("properties")
    required = set(schema.get("required") or [])
    if isinstance(properties, dict):
        for key, subschema in properties.items():
            path = f"{prefix}{key}"
            if not isinstance(subschema, dict):
                continue
            flat[path] = {
                "type": subschema.get("type"),
                "required": key in required,
                "enum": tuple(subschema.get("enum")) if subschema.get("enum") else None,
            }
            flat.update(_flatten_schema(subschema, prefix=f"{path}.", depth=depth + 1))
    items = schema.get("items")
    if isinstance(items, dict):
        flat.update(_flatten_schema(items, prefix=f"{prefix}[].", depth=depth + 1))
    return flat


def _schema_breakages(old: dict[str, Any] | None, new: dict[str, Any] | None) -> list[str]:
    """Human-readable list of ways `new` is stricter or narrower than `old`."""
    if not old or not new:
        return []
    before = _flatten_schema(old)
    after = _flatten_schema(new)
    notes: list[str] = []
    for path, spec in before.items():
        if path not in after:
            notes.append(f"field `{path}` removed")
            continue
        now = after[path]
        if spec["type"] and now["type"] and spec["type"] != now["type"]:
            notes.append(f"field `{path}` changed type {spec['type']} -> {now['type']}")
        if not spec["required"] and now["required"]:
            notes.append(f"field `{path}` became required")
        if spec["enum"] and now["enum"]:
            dropped = set(spec["enum"]) - set(now["enum"])
            if dropped:
                notes.append(
                    f"field `{path}` dropped enum value(s): {', '.join(map(str, sorted(dropped)))}"
                )
    for path, spec in after.items():
        if path not in before and spec["required"]:
            notes.append(f"new required field `{path}` added")
    return notes


# ---------------------------------------------------------------------------
# rules
# ---------------------------------------------------------------------------


def _tool_findings(
    current: Contract, proposed: Contract, dependents: list[Contract]
) -> list[Finding]:
    findings: list[Finding] = []
    before = {t.name: t for t in current.tools}
    after = {t.name: t for t in proposed.tools}

    for name, tool in before.items():
        if name in after:
            continue
        consumers = _consumers(dependents, current.id, "requires_tools", name)
        if consumers:
            for consumer in consumers:
                findings.append(
                    Finding(
                        kind="tool_removed",
                        severity=Severity.CRITICAL,
                        summary=f"Required tool `{name}` removed",
                        detail=(
                            f"`{consumer.name}` declares a dependency on `{current.id}` "
                            f"that names `{name}` as a required tool. Removing it leaves "
                            "that agent with no way to perform the step it was relying on."
                        ),
                        affected_contract=consumer.id,
                        evidence=[
                            f"{current.id} v{current.version} exposes tool `{name}`",
                            f"{proposed.id} v{proposed.version} does not",
                            f"{consumer.id}.depends_on[{current.id}].requires_tools includes `{name}`",
                        ],
                        recommendation=(
                            f"Keep `{name}` (optionally deprecate it first), or update "
                            f"`{consumer.id}` to use a replacement before merging."
                        ),
                    )
                )
        else:
            findings.append(
                Finding(
                    kind="tool_removed",
                    severity=Severity.MEDIUM if tool.required else Severity.LOW,
                    summary=f"Tool `{name}` removed",
                    detail=(
                        "No registered contract declares a hard requirement on this tool, "
                        "but undeclared callers may still exist."
                    ),
                    evidence=[f"`{name}` present in v{current.version}, absent in v{proposed.version}"],
                    recommendation="Confirm no unregistered agent calls this tool.",
                )
            )

    for name, tool in after.items():
        if name not in before:
            findings.append(
                Finding(
                    kind="tool_added",
                    severity=Severity.INFO,
                    summary=f"Tool `{name}` added",
                    detail=tool.description or "New capability surface.",
                    evidence=[f"`{name}` is new in v{proposed.version}"],
                    recommendation="Additive; no downstream action required.",
                )
            )
            continue
        old_tool = before[name]
        if old_tool.signature and tool.signature and old_tool.signature != tool.signature:
            consumers = _consumers(dependents, current.id, "requires_tools", name)
            findings.append(
                Finding(
                    kind="tool_signature_changed",
                    severity=Severity.HIGH if consumers else Severity.MEDIUM,
                    summary=f"Signature of `{name}` changed",
                    detail=f"`{old_tool.signature}` -> `{tool.signature}`",
                    affected_contract=consumers[0].id if consumers else None,
                    evidence=[f"{old_tool.signature} -> {tool.signature}"],
                    recommendation="Callers passing the old arguments will fail; update them together.",
                )
            )
        if old_tool.required and not tool.required:
            findings.append(
                Finding(
                    kind="tool_downgraded",
                    severity=Severity.LOW,
                    summary=f"Tool `{name}` is no longer marked required",
                    detail="Downstream agents can no longer assume this tool is guaranteed.",
                    evidence=[f"`{name}`.required: true -> false"],
                    recommendation="Confirm dependents treat this tool as best-effort.",
                )
            )
    return findings


def _capability_findings(
    current: Contract, proposed: Contract, dependents: list[Contract]
) -> list[Finding]:
    findings: list[Finding] = []
    before = set(current.capabilities)
    after = set(proposed.capabilities)

    for capability in sorted(before - after):
        consumers = _consumers(dependents, current.id, "requires_capabilities", capability)
        for consumer in consumers:
            findings.append(
                Finding(
                    kind="capability_removed",
                    severity=Severity.CRITICAL,
                    summary=f"Required capability '{capability}' removed",
                    detail=(
                        f"`{consumer.name}` declares this capability as a requirement of "
                        f"`{current.id}`."
                    ),
                    affected_contract=consumer.id,
                    evidence=[
                        f"capability '{capability}' dropped in v{proposed.version}",
                        f"{consumer.id} requires it",
                    ],
                    recommendation=f"Restore the capability or re-scope `{consumer.id}`.",
                )
            )
        if not consumers:
            findings.append(
                Finding(
                    kind="capability_removed",
                    severity=Severity.MEDIUM,
                    summary=f"Capability '{capability}' removed",
                    detail="No declared dependent requires it, but the agent's advertised scope shrank.",
                    evidence=[f"'{capability}' present in v{current.version}, absent in v{proposed.version}"],
                    recommendation="Confirm nothing relies on this behaviour informally.",
                )
            )

    for capability in sorted(after - before):
        findings.append(
            Finding(
                kind="capability_added",
                severity=Severity.INFO,
                summary=f"Capability '{capability}' added",
                evidence=[f"'{capability}' is new in v{proposed.version}"],
                recommendation="Additive; no downstream action required.",
            )
        )
    return findings


def _output_findings(
    current: Contract, proposed: Contract, dependents: list[Contract]
) -> list[Finding]:
    findings: list[Finding] = []
    before = {o.name: o for o in current.outputs}
    after = {o.name: o for o in proposed.outputs}

    for name, spec in before.items():
        if name in after:
            continue
        consumers = _consumers(dependents, current.id, "expects_outputs", name)
        for consumer in consumers:
            findings.append(
                Finding(
                    kind="output_removed",
                    severity=Severity.CRITICAL,
                    summary=f"Consumed output `{name}` removed",
                    detail=(
                        f"`{consumer.name}` reads `{name}` from `{current.id}`. "
                        "After this change there is nothing to read."
                    ),
                    affected_contract=consumer.id,
                    evidence=[
                        f"output `{name}` ({spec.format.value}) dropped in v{proposed.version}",
                        f"{consumer.id}.depends_on[{current.id}].expects_outputs includes `{name}`",
                    ],
                    recommendation=f"Keep emitting `{name}`, or migrate `{consumer.id}` first.",
                )
            )
        if not consumers:
            findings.append(
                Finding(
                    kind="output_removed",
                    severity=Severity.MEDIUM,
                    summary=f"Output `{name}` removed",
                    detail="No declared consumer, but any unregistered reader will break.",
                    evidence=[f"`{name}` present in v{current.version}, absent in v{proposed.version}"],
                    recommendation="Confirm no unregistered consumer reads this field.",
                )
            )

    for name, spec in after.items():
        old = before.get(name)
        if old is None:
            findings.append(
                Finding(
                    kind="output_added",
                    severity=Severity.INFO,
                    summary=f"Output `{name}` added",
                    evidence=[f"`{name}` ({spec.format.value}) is new in v{proposed.version}"],
                    recommendation="Additive; no downstream action required.",
                )
            )
            continue

        if old.format != spec.format:
            consumers = _consumers(dependents, current.id, "expects_outputs", name)
            format_consumers = [
                d
                for d in dependents
                if _dep_of(d, current.id).expects_format == old.format
            ]
            affected = consumers or format_consumers
            for consumer in affected:
                findings.append(
                    Finding(
                        kind="output_format_changed",
                        severity=Severity.CRITICAL,
                        summary=(
                            f"Output `{name}` format changed: "
                            f"{old.format.value} → {spec.format.value}"
                        ),
                        detail=(
                            f"`{consumer.name}` expects {old.format.value} from `{current.id}`. "
                            f"A {spec.format.value} payload will not parse, and the failure "
                            "surfaces at runtime rather than at merge time."
                        ),
                        affected_contract=consumer.id,
                        evidence=[
                            f"`{name}`.format: {old.format.value} -> {spec.format.value}",
                            f"{consumer.id} expects {old.format.value}",
                        ],
                        recommendation=(
                            f"Emit both formats during a migration window, or update "
                            f"`{consumer.id}`'s parser in the same change."
                        ),
                    )
                )
            if not affected:
                findings.append(
                    Finding(
                        kind="output_format_changed",
                        severity=Severity.HIGH,
                        summary=f"Output `{name}` changed from {old.format.value} to {spec.format.value}",
                        detail="Format changes break every consumer that parses the payload.",
                        evidence=[f"`{name}`.format: {old.format.value} -> {spec.format.value}"],
                        recommendation="Version the output or provide a compatibility shim.",
                    )
                )

        for note in _schema_breakages(old.json_schema, spec.json_schema):
            consumers = _consumers(dependents, current.id, "expects_outputs", name)
            findings.append(
                Finding(
                    kind="output_schema_narrowed",
                    severity=Severity.CRITICAL if consumers else Severity.HIGH,
                    summary=f"Output `{name}` schema change: {note}",
                    detail=(
                        "Consumers bind to the shape of this payload; narrowing it "
                        "breaks them at parse time."
                    ),
                    affected_contract=consumers[0].id if consumers else None,
                    evidence=[f"`{name}` schema: {note}"],
                    recommendation="Keep the old field alongside the new one until consumers migrate.",
                )
            )

        if old.required and not spec.required:
            findings.append(
                Finding(
                    kind="output_optional",
                    severity=Severity.HIGH,
                    summary=f"Output `{name}` is no longer always emitted",
                    detail="Consumers that assume the field is present will see missing data.",
                    evidence=[f"`{name}`.required: true -> false"],
                    recommendation="Document the conditions under which it is omitted.",
                )
            )
    return findings


def _input_findings(
    current: Contract, proposed: Contract, dependents: list[Contract]
) -> list[Finding]:
    findings: list[Finding] = []
    before = {i.name: i for i in current.inputs}
    after = {i.name: i for i in proposed.inputs}
    callers = [d for d in dependents if d.dependency_on(current.id)]

    for name, spec in after.items():
        old = before.get(name)
        if old is None and spec.required:
            findings.append(
                Finding(
                    kind="required_input_added",
                    severity=Severity.HIGH if callers else Severity.MEDIUM,
                    summary=f"New required input `{name}`",
                    detail=(
                        "Existing callers do not send this field, so every current call "
                        "site becomes invalid the moment this ships."
                    ),
                    affected_contract=callers[0].id if len(callers) == 1 else None,
                    evidence=[f"`{name}` ({spec.format.value}) is new and required"],
                    recommendation="Ship it optional with a default, then tighten once callers migrate.",
                )
            )
        elif old is not None:
            if not old.required and spec.required:
                findings.append(
                    Finding(
                        kind="input_now_required",
                        severity=Severity.HIGH if callers else Severity.MEDIUM,
                        summary=f"Input `{name}` is now required",
                        detail="Callers that omitted it will start failing validation.",
                        evidence=[f"`{name}`.required: false -> true"],
                        recommendation="Keep it optional until callers are updated.",
                    )
                )
            if old.format != spec.format:
                findings.append(
                    Finding(
                        kind="input_format_changed",
                        severity=Severity.HIGH,
                        summary=(
                            f"Input `{name}` changed from {old.format.value} to {spec.format.value}"
                        ),
                        detail="Callers serialise this payload; a format change invalidates them.",
                        evidence=[f"`{name}`.format: {old.format.value} -> {spec.format.value}"],
                        recommendation="Accept both formats during migration.",
                    )
                )
            for note in _schema_breakages(old.json_schema, spec.json_schema):
                findings.append(
                    Finding(
                        kind="input_schema_narrowed",
                        severity=Severity.HIGH,
                        summary=f"Input `{name}` schema change: {note}",
                        detail="Callers constructing the old payload shape will be rejected.",
                        evidence=[f"`{name}` schema: {note}"],
                        recommendation="Widen the schema or accept both shapes during migration.",
                    )
                )

    for name in before.keys() - after.keys():
        findings.append(
            Finding(
                kind="input_removed",
                severity=Severity.LOW,
                summary=f"Input `{name}` removed",
                detail="Callers still sending it will have it ignored (or rejected on strict schemas).",
                evidence=[f"`{name}` present in v{current.version}, absent in v{proposed.version}"],
                recommendation="Ignore the field rather than rejecting it, for one release.",
            )
        )
    return findings


def _statement_tokens(statement: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9_]+", statement.lower()) if len(t) > 2}


def _find_rename(
    constraint: Constraint, removed_ids: set[str], added: dict[str, Constraint]
) -> Constraint | None:
    """Match a removed constraint to a newly-added one that says the same thing.

    Constraint ids derived from statement text change whenever the wording
    does, so a reworded constraint looks like a removal plus an addition. That
    reads as a broken guarantee when it is really a rename -- and the fix
    ("keep the id stable") is completely different from the fix for an actual
    removal.
    """
    if not added:
        return None
    left = _statement_tokens(constraint.statement)
    if not left:
        return None

    best: tuple[float, Constraint | None] = (0.0, None)
    for candidate in added.values():
        right = _statement_tokens(candidate.statement)
        if not right:
            continue
        overlap = len(left & right) / min(len(left), len(right))
        if overlap > best[0]:
            best = (overlap, candidate)
    return best[1] if best[0] >= 0.6 else None


def _constraint_findings(
    current: Contract, proposed: Contract, dependents: list[Contract]
) -> list[Finding]:
    findings: list[Finding] = []
    before = {c.id: c for c in current.constraints}
    after = {c.id: c for c in proposed.constraints}

    removed_ids = before.keys() - after.keys()
    added_only = {cid: c for cid, c in after.items() if cid not in before}
    renames: dict[str, Constraint] = {}
    for cid in removed_ids:
        match = _find_rename(before[cid], removed_ids, added_only)
        if match is not None:
            renames[cid] = match
            added_only.pop(match.id, None)

    for cid, constraint in before.items():
        if cid in renames:
            replacement = renames[cid]
            consumers = _consumers(dependents, current.id, "requires_constraints", cid)
            for consumer in consumers:
                findings.append(
                    Finding(
                        kind="constraint_id_changed",
                        severity=Severity.CRITICAL,
                        summary=f"Constraint `{cid}` was reworded, changing its id",
                        detail=(
                            f"The guarantee survives as `{replacement.id}`, but "
                            f"`{consumer.name}` references it by the id `{cid}`, which "
                            "no longer exists. This is a rename, not a removal -- the "
                            "fix is to keep the id, not to restore the wording."
                        ),
                        affected_contract=consumer.id,
                        evidence=[
                            f"before: [{cid}] {_clip(constraint.statement, 100)}",
                            f"after:  [{replacement.id}] {_clip(replacement.statement, 100)}",
                            f"{consumer.id} requires `{cid}`",
                        ],
                        recommendation=(
                            f"Pin the id explicitly in the source file — write "
                            f"`- [{cid}] {_clip(replacement.statement, 60)}` — so the "
                            "wording can change without breaking the reference."
                        ),
                    )
                )
            if not consumers:
                findings.append(
                    Finding(
                        kind="constraint_reworded",
                        severity=Severity.LOW,
                        summary=f"Constraint `{cid}` reworded (now `{replacement.id}`)",
                        detail=(
                            "The guarantee looks equivalent. Its id changed because it "
                            "is derived from the statement text; nothing references it "
                            "by id today."
                        ),
                        evidence=[
                            f"before: {_clip(constraint.statement, 100)}",
                            f"after:  {_clip(replacement.statement, 100)}",
                        ],
                        recommendation=(
                            "Give it a stable id (`- [some-id] …`) if you want "
                            "dependents to be able to rely on it by name."
                        ),
                    )
                )
            continue

        if cid in after:
            now = after[cid]
            if now.severity < constraint.severity:
                findings.append(
                    Finding(
                        kind="constraint_relaxed",
                        severity=Severity.MEDIUM,
                        summary=f"Constraint '{constraint.statement[:60]}' relaxed",
                        detail=(
                            f"severity {constraint.severity.value} -> {now.severity.value}. "
                            "Downstream agents that treated this as a guarantee now have a weaker one."
                        ),
                        evidence=[f"{cid}: {constraint.severity.value} -> {now.severity.value}"],
                        recommendation="Confirm dependents do not treat this as a hard guarantee.",
                    )
                )
            continue

        consumers = _consumers(dependents, current.id, "requires_constraints", cid)
        for consumer in consumers:
            findings.append(
                Finding(
                    kind="constraint_removed",
                    severity=Severity.CRITICAL,
                    summary=f"Required constraint `{cid}` removed",
                    detail=(
                        f"`{consumer.name}` declares a dependency on constraint `{cid}`: "
                        f"\"{constraint.statement}\". Dropping it removes a guarantee that "
                        "another agent's behaviour is built on."
                    ),
                    affected_contract=consumer.id,
                    evidence=[f"constraint `{cid}` removed", f"{consumer.id} requires `{cid}`"],
                    recommendation="Restore the constraint or renegotiate it with the dependent.",
                )
            )
        if not consumers:
            findings.append(
                Finding(
                    kind="constraint_removed",
                    severity=Severity.HIGH if constraint.severity >= Severity.HIGH else Severity.MEDIUM,
                    summary=f"Constraint removed: \"{_clip(constraint.statement)}\"",
                    detail=(
                        "Constraints are promises other agents plan around. Removing one is "
                        "invisible at runtime until behaviour silently changes."
                    ),
                    evidence=[f"`{cid}` present in v{current.version}, absent in v{proposed.version}"],
                    recommendation="If this is intentional, note it in the change description.",
                )
            )

    replacement_ids = {c.id for c in renames.values()}
    for cid, constraint in after.items():
        if cid not in before and cid not in replacement_ids:
            findings.append(
                Finding(
                    kind="constraint_added",
                    severity=Severity.LOW,
                    summary=f"New constraint: \"{_clip(constraint.statement)}\"",
                    detail=(
                        "New constraints can reject work that used to be accepted. "
                        "Usually safe, occasionally not."
                    ),
                    evidence=[f"`{cid}` is new in v{proposed.version}"],
                    recommendation="Check that dependents' existing requests still satisfy it.",
                )
            )
    return findings


def _persona_findings(current: Contract, proposed: Contract) -> list[Finding]:
    findings: list[Finding] = []
    before = set(current.persona_rules)
    after = set(proposed.persona_rules)
    for rule in sorted(before - after):
        findings.append(
            Finding(
                kind="persona_rule_removed",
                severity=Severity.MEDIUM,
                summary=f"Persona rule removed: \"{_clip(rule)}\"",
                detail=(
                    "Persona drift is the classic silent breakage: nothing errors, the "
                    "output just stops looking like what downstream agents were tuned for."
                ),
                evidence=[f"rule present in v{current.version}, absent in v{proposed.version}"],
                recommendation="Confirm no dependent parses or relies on the previous voice/format.",
            )
        )
    for rule in sorted(after - before):
        findings.append(
            Finding(
                kind="persona_rule_added",
                severity=Severity.LOW,
                summary=f"Persona rule added: \"{_clip(rule)}\"",
                evidence=[f"rule is new in v{proposed.version}"],
                recommendation="Verify the new voice still satisfies downstream expectations.",
            )
        )
    return findings


def _identity_findings(current: Contract, proposed: Contract) -> list[Finding]:
    if current.identity.strip() == proposed.identity.strip():
        return []
    if not current.identity.strip():
        return []
    return [
        Finding(
            kind="identity_changed",
            severity=Severity.LOW,
            summary="Agent identity / role statement changed",
            detail=(
                f"before: {_clip(current.identity, 160)}\n"
                f"after:  {_clip(proposed.identity, 160)}"
            ),
            evidence=["identity text differs"],
            recommendation="Check the new role framing still matches how dependents address this agent.",
        )
    ]


def _dependency_findings(current: Contract, proposed: Contract) -> list[Finding]:
    findings: list[Finding] = []
    before = set(current.dependency_ids())
    after = set(proposed.dependency_ids())
    for dep in sorted(after - before):
        findings.append(
            Finding(
                kind="dependency_added",
                severity=Severity.INFO,
                summary=f"Now depends on `{dep}`",
                evidence=[f"`{dep}` added to depends_on"],
                recommendation="Make sure that contract is registered so it is covered by checks.",
            )
        )
    for dep in sorted(before - after):
        findings.append(
            Finding(
                kind="dependency_removed",
                severity=Severity.INFO,
                summary=f"No longer depends on `{dep}`",
                evidence=[f"`{dep}` removed from depends_on"],
                recommendation="Upstream changes to that contract will no longer flag this agent.",
            )
        )
    return findings


def _version_findings(
    current: Contract, proposed: Contract, findings_so_far: list[Finding]
) -> list[Finding]:
    findings: list[Finding] = []
    old_v = parse_version(current.version)
    new_v = parse_version(proposed.version)
    changed = current.fingerprint() != proposed.fingerprint()

    if new_v < old_v:
        findings.append(
            Finding(
                kind="version_regression",
                severity=Severity.MEDIUM,
                summary=f"Version went backwards: {current.version} -> {proposed.version}",
                detail="Dependents pinning a version range may silently resolve to the wrong contract.",
                evidence=[f"{current.version} -> {proposed.version}"],
                recommendation="Bump forward instead.",
            )
        )
    elif changed and new_v == old_v:
        findings.append(
            Finding(
                kind="version_not_bumped",
                severity=Severity.LOW,
                summary=f"Contract changed but version stayed at {current.version}",
                detail="Nothing downstream can tell that the contract moved.",
                evidence=[f"fingerprint {current.fingerprint()} -> {proposed.fingerprint()}"],
                recommendation=f"Bump to {proposed.bumped('minor')} (or a major if the change is breaking).",
            )
        )

    breaking = [f for f in findings_so_far if f.severity >= Severity.HIGH]
    if breaking and new_v[0] == old_v[0] and new_v >= old_v:
        findings.append(
            Finding(
                kind="breaking_change_without_major_bump",
                severity=Severity.MEDIUM,
                summary=f"Breaking change shipped as {proposed.version} (no major bump)",
                detail=(
                    f"{len(breaking)} finding(s) at HIGH or above, but the major version is "
                    f"still {new_v[0]}."
                ),
                evidence=[f.summary for f in breaking[:5]],
                recommendation=f"Release this as {proposed.bumped('major')}.",
            )
        )
    return findings


def _clip(text: str, limit: int = 80) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
