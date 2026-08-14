"""Compatibility checking: put the two halves together and return a verdict.

`structural_findings` proves what it can. The judge, if enabled, adds what it
can see and the diff cannot. This module merges them, de-duplicates, and
decides APPROVED vs REQUEST_CHANGES.
"""

from __future__ import annotations

import re
from typing import Iterable

from .diff import structural_findings
from .judge import Judge, JudgeUnavailable, NullJudge
from .models import (
    CompatibilityReport,
    Contract,
    Finding,
    JudgeInfo,
    Severity,
    Verdict,
)
from .registry import Registry


def check_compatibility(
    current: Contract | None,
    proposed: Contract,
    dependents: Iterable[Contract] = (),
    *,
    judge: Judge | None = None,
    fail_on: Severity = Severity.HIGH,
) -> CompatibilityReport:
    """Decide whether `proposed` can replace `current` without breaking dependents."""
    dependents = list(dependents)

    if current is None:
        # Nothing to compare against. Diffing a first registration against a
        # fabricated empty contract would report every field as an addition and
        # every required input as newly-required, which is noise, not signal.
        return _first_registration_report(proposed, dependents, fail_on)

    baseline = current
    findings = structural_findings(baseline, proposed, dependents)

    judge = judge or NullJudge()
    judge_info: JudgeInfo = getattr(judge, "info", JudgeInfo())
    assessment = ""
    try:
        result = judge.evaluate(baseline, proposed, dependents, findings)
        judge_info = result.info
        assessment = result.assessment
        findings.extend(_dedupe_semantic(result.findings, findings))
    except JudgeUnavailable as exc:
        judge_info = JudgeInfo(
            enabled=False,
            provider=judge_info.provider,
            model=judge_info.model,
            error=str(exc),
        )

    return CompatibilityReport(
        verdict=_verdict(findings, fail_on),
        contract_id=proposed.id,
        from_version=baseline.version,
        to_version=proposed.version,
        fingerprint_before=baseline.fingerprint(),
        fingerprint_after=proposed.fingerprint(),
        findings=findings,
        dependents_checked=[d.id for d in dependents],
        fail_on=fail_on,
        judge=judge_info,
        assessment=assessment,
    )


def _first_registration_report(
    proposed: Contract, dependents: list[Contract], fail_on: Severity
) -> CompatibilityReport:
    unresolved = [
        dep.contract_id
        for dep in proposed.depends_on
        if dep.contract_id not in {d.id for d in dependents}
    ]
    findings = [
        Finding(
            kind="first_registration",
            severity=Severity.INFO,
            summary=f"`{proposed.id}` is not registered yet; nothing to compare against",
            detail=(
                f"Registering it at v{proposed.version} establishes the baseline. "
                "Later changes will be checked against this."
            ),
            changed_contract=proposed.id,
            evidence=[
                f"{len(proposed.tools)} tools, {len(proposed.outputs)} outputs, "
                f"{len(proposed.constraints)} constraints",
            ],
            recommendation="Register it, then re-run checks on future changes.",
        )
    ]
    if unresolved:
        findings.append(
            Finding(
                kind="unresolved_dependency",
                severity=Severity.LOW,
                summary=f"Depends on {len(unresolved)} unregistered contract(s)",
                detail=(
                    "Changes to those contracts will not flag this agent until they "
                    "are registered too."
                ),
                changed_contract=proposed.id,
                evidence=[f"`{cid}` is not in the registry" for cid in unresolved],
                recommendation="Register the upstream contracts to close the loop.",
            )
        )
    return CompatibilityReport(
        verdict=_verdict(findings, fail_on),
        contract_id=proposed.id,
        from_version="0.0.0",
        to_version=proposed.version,
        fingerprint_after=proposed.fingerprint(),
        findings=findings,
        dependents_checked=[d.id for d in dependents],
        fail_on=fail_on,
    )


def check_against_registry(
    registry: Registry,
    proposed: Contract,
    *,
    judge: Judge | None = None,
    fail_on: Severity = Severity.HIGH,
    transitive: bool = False,
) -> CompatibilityReport:
    """Check a proposed contract against everything registered that depends on it."""
    current = registry.get(proposed.id, missing_ok=True)
    direct = registry.dependents(proposed.id)
    dependents = registry.transitive_dependents(proposed.id) if transitive else direct

    report = check_compatibility(
        current, proposed, dependents, judge=judge, fail_on=fail_on
    )

    if transitive:
        direct_ids = {d.id for d in direct}
        indirect = {d.id for d in dependents} - direct_ids
        if indirect:
            report = _soften_indirect(report, indirect, fail_on)
    return report


def _soften_indirect(
    report: CompatibilityReport, indirect: set[str], fail_on: Severity
) -> CompatibilityReport:
    """Cap findings against indirect dependents below CRITICAL.

    A direct dependent declares exactly what it needs, so a break against it is
    provable. An indirect one is reached through a chain: the middle agent may
    absorb the change entirely. That is still worth blocking on, but claiming
    the same certainty for both would erode trust in CRITICAL.
    """
    softened: list[Finding] = []
    changed = False
    for finding in report.findings:
        if (
            finding.affected_contract in indirect
            and finding.severity is Severity.CRITICAL
        ):
            changed = True
            softened.append(
                finding.model_copy(
                    update={
                        "severity": Severity.HIGH,
                        "detail": (
                            finding.detail
                            + f"\n\n`{finding.affected_contract}` depends on "
                            f"`{report.contract_id}` indirectly, so the agent between "
                            "them may absorb this. Capped below CRITICAL for that "
                            "reason."
                        ).strip(),
                    }
                )
            )
        else:
            softened.append(finding)

    if not changed:
        return report
    return report.model_copy(
        update={"findings": softened, "verdict": _verdict(softened, fail_on)}
    )


def _verdict(findings: list[Finding], fail_on: Severity) -> Verdict:
    if any(f.severity >= fail_on for f in findings):
        return Verdict.REQUEST_CHANGES
    return Verdict.APPROVED


def _dedupe_semantic(
    semantic: list[Finding], structural: list[Finding]
) -> list[Finding]:
    """Drop semantic findings that restate something the diff already proved."""
    kept: list[Finding] = []
    for candidate in semantic:
        if any(_looks_like(candidate, existing) for existing in structural):
            continue
        if any(_looks_like(candidate, existing) for existing in kept):
            continue
        kept.append(candidate)
    return kept


_TOKEN_RE = re.compile(r"[a-z0-9_]+")
_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "to", "of", "and", "or",
    "but", "in", "on", "for", "it", "that", "this", "will", "be", "no",
    "longer", "from", "by", "with", "as",
}


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS}


def _looks_like(a: Finding, b: Finding) -> bool:
    if a.affected_contract != b.affected_contract:
        return False
    left, right = _tokens(a.summary), _tokens(b.summary)
    if not left or not right:
        return False
    overlap = len(left & right) / min(len(left), len(right))
    return overlap >= 0.7


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

_SEVERITY_EMOJI = {
    Severity.CRITICAL: "🛑",
    Severity.HIGH: "⚠️",
    Severity.MEDIUM: "🔸",
    Severity.LOW: "🔹",
    Severity.INFO: "ℹ️",
}


def render_markdown(report: CompatibilityReport) -> str:
    """Render a report as markdown, for PR comments and CI summaries."""
    assessment = report.assessment
    lines: list[str] = []
    badge = "✅ **APPROVED**" if report.verdict is Verdict.APPROVED else "🛑 **REQUEST_CHANGES**"
    lines.append(f"## Ionic compatibility check — {badge}")
    lines.append("")
    lines.append(
        f"`{report.contract_id}` v{report.from_version} → v{report.to_version}"
    )
    lines.append("")
    lines.append(report.headline())
    lines.append("")

    if report.dependents_checked:
        deps = ", ".join(f"`{d}`" for d in report.dependents_checked)
        lines.append(f"**Dependents checked:** {deps}")
    else:
        lines.append("**Dependents checked:** none registered")
    lines.append("")

    counts = report.counts()
    summary_bits = [
        f"{_SEVERITY_EMOJI[sev]} {counts[sev.value]} {sev.value}"
        for sev in (Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO)
        if counts[sev.value]
    ]
    if summary_bits:
        lines.append(" · ".join(summary_bits))
        lines.append("")

    if assessment:
        lines.append(f"> {assessment}")
        lines.append("")

    blocking = [f for f in report.sorted_findings() if f.severity >= report.fail_on]
    other = [f for f in report.sorted_findings() if f.severity < report.fail_on]

    if blocking:
        lines.append("### Blocking")
        lines.append("")
        for finding in blocking:
            lines.extend(_finding_markdown(finding))
        lines.append("")

    if other:
        lines.append("<details>")
        lines.append(f"<summary>{len(other)} non-blocking observation(s)</summary>")
        lines.append("")
        for finding in other:
            lines.extend(_finding_markdown(finding))
        lines.append("")
        lines.append("</details>")
        lines.append("")

    if not report.findings:
        lines.append("No differences that affect any dependent contract.")
        lines.append("")

    judge = report.judge
    if judge.enabled:
        lines.append(f"<sub>Semantic review: {judge.provider} `{judge.model}`.</sub>")
    elif judge.error:
        lines.append(f"<sub>Semantic review skipped: {judge.error}</sub>")
    else:
        lines.append("<sub>Structural analysis only (semantic review disabled).</sub>")
    return "\n".join(lines).rstrip() + "\n"


def _finding_markdown(finding: Finding) -> list[str]:
    emoji = _SEVERITY_EMOJI[finding.severity]
    target = f" → `{finding.affected_contract}`" if finding.affected_contract else ""
    lines = [f"#### {emoji} {finding.summary}{target}", ""]
    lines.append(
        f"`{finding.kind}` · **{finding.severity.value}** · {finding.origin} analysis"
    )
    lines.append("")
    if finding.detail:
        lines.append(finding.detail)
        lines.append("")
    if finding.evidence:
        lines.append("Evidence:")
        lines.extend(f"- {item}" for item in finding.evidence)
        lines.append("")
    if finding.recommendation:
        lines.append(f"**Fix:** {finding.recommendation}")
        lines.append("")
    return lines
