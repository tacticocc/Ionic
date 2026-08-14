from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from ionic.compat import check_against_registry, check_compatibility, render_markdown
from ionic.judge import AnthropicJudge, JudgeUnavailable
from ionic.models import Finding, Severity, Verdict


def test_safe_change_is_approved(planner, researcher):
    proposed = planner.revise(version="1.1.0", capabilities=[*planner.capabilities, "new"])
    report = check_compatibility(planner, proposed, [researcher])
    assert report.verdict is Verdict.APPROVED
    assert report.blocking == []


def test_breaking_change_requests_changes(planner, researcher):
    proposed = planner.revise(
        version="1.1.0", tools=[t for t in planner.tools if t.name != "search_web"]
    )
    report = check_compatibility(planner, proposed, [researcher])
    assert report.verdict is Verdict.REQUEST_CHANGES
    assert report.highest_severity is Severity.CRITICAL
    assert report.dependents_checked == ["researcher"]


def test_fail_on_threshold_is_respected(planner):
    proposed = planner.revise(persona_rules=[])  # medium finding
    assert check_compatibility(planner, proposed, []).verdict is Verdict.APPROVED
    strict = check_compatibility(planner, proposed, [], fail_on=Severity.MEDIUM)
    assert strict.verdict is Verdict.REQUEST_CHANGES


def test_brand_new_contract_has_no_baseline(planner):
    report = check_compatibility(None, planner, [])
    assert report.from_version == "0.0.0"
    assert report.verdict is Verdict.APPROVED
    assert all(f.severity <= Severity.LOW for f in report.findings)


def test_check_against_registry_pulls_dependents(registry, planner, researcher):
    registry.register(planner)
    registry.register(researcher)
    proposed = planner.revise(version="2.0.0", tools=[])
    report = check_against_registry(registry, proposed)
    assert report.verdict is Verdict.REQUEST_CHANGES
    assert "researcher" in report.dependents_checked


def test_transitive_dependents_are_included(registry, planner, researcher):
    from ionic.models import Contract

    registry.register(planner)
    registry.register(researcher)
    registry.register(Contract(id="publisher", depends_on=["researcher"]))
    report = check_against_registry(
        registry, planner.revise(version="1.1.0"), transitive=True
    )
    assert sorted(report.dependents_checked) == ["publisher", "researcher"]


# ---------------------------------------------------------------------------
# judge integration
# ---------------------------------------------------------------------------


class FakeJudge:
    def __init__(self, findings, assessment="looks risky"):
        self._findings = findings
        self._assessment = assessment
        from ionic.models import JudgeInfo

        self.info = JudgeInfo(enabled=True, provider="fake", model="fake-1")

    def evaluate(self, current, proposed, dependents, structural):
        from ionic.judge import JudgeResult

        return JudgeResult(self._findings, self._assessment, self.info)


def test_semantic_findings_can_block_on_their_own(planner):
    semantic = Finding(
        kind="guarantee_weakened",
        severity=Severity.CRITICAL,
        summary="Sourcing guarantee quietly downgraded to best-effort",
        affected_contract="researcher",
        origin="semantic",
    )
    report = check_compatibility(
        planner,
        planner.revise(version="1.1.0"),
        [],
        judge=FakeJudge([semantic]),
    )
    assert report.verdict is Verdict.REQUEST_CHANGES
    assert report.assessment == "looks risky"


def test_semantic_duplicates_of_structural_findings_are_dropped(planner, researcher):
    proposed = planner.revise(
        version="1.1.0", tools=[t for t in planner.tools if t.name != "search_web"]
    )
    duplicate = Finding(
        kind="implicit_contract_broken",
        severity=Severity.CRITICAL,
        summary="Required tool `search_web` removed",
        affected_contract="researcher",
        origin="semantic",
    )
    report = check_compatibility(planner, proposed, [researcher], judge=FakeJudge([duplicate]))
    assert [f for f in report.findings if f.origin == "semantic"] == []


def test_distinct_semantic_findings_survive_dedupe(planner, researcher):
    proposed = planner.revise(
        version="1.1.0", tools=[t for t in planner.tools if t.name != "search_web"]
    )
    distinct = Finding(
        kind="persona_drift",
        severity=Severity.HIGH,
        summary="Tone shift will break the publisher's section splitter",
        affected_contract="researcher",
        origin="semantic",
    )
    report = check_compatibility(planner, proposed, [researcher], judge=FakeJudge([distinct]))
    assert [f.kind for f in report.findings if f.origin == "semantic"] == ["persona_drift"]


class ExplodingJudge:
    def __init__(self):
        from ionic.models import JudgeInfo

        self.info = JudgeInfo(enabled=True, provider="anthropic", model="claude-opus-5")

    def evaluate(self, *args):
        raise JudgeUnavailable("no API key configured")


def test_judge_failure_degrades_to_structural_only(planner, researcher):
    proposed = planner.revise(version="1.1.0", tools=[])
    report = check_compatibility(planner, proposed, [researcher], judge=ExplodingJudge())
    assert report.verdict is Verdict.REQUEST_CHANGES  # structural still fired
    assert report.judge.enabled is False
    assert "no API key" in (report.judge.error or "")


def test_null_judge_is_the_default(planner):
    report = check_compatibility(planner, planner, [])
    assert report.judge.enabled is False
    assert report.judge.provider == "none"


# ---------------------------------------------------------------------------
# Anthropic backend, with a stubbed client
# ---------------------------------------------------------------------------


class _StubStream:
    def __init__(self, message):
        self._message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._message


class StubAnthropic:
    """Minimal stand-in for anthropic.Anthropic covering the surface we use."""

    def __init__(self, payload, *, stop_reason="end_turn"):
        self.payload = payload
        self.stop_reason = stop_reason
        self.calls: list[dict] = []
        outer = self

        class _Messages:
            def stream(self, **kwargs):
                outer.calls.append(kwargs)
                message = SimpleNamespace(
                    stop_reason=outer.stop_reason,
                    stop_details=None,
                    content=[SimpleNamespace(type="text", text=json.dumps(outer.payload))],
                )
                return _StubStream(message)

        self.messages = _Messages()


def test_anthropic_judge_parses_structured_output(planner, researcher):
    payload = {
        "assessment": "The sourcing guarantee is weaker than it looks.",
        "findings": [
            {
                "kind": "guarantee_weakened",
                "severity": "high",
                "summary": "Sourcing became best-effort",
                "detail": "…",
                "affected_contract": "researcher",
                "evidence": ["'always' -> 'when available'"],
                "recommendation": "Restore the absolute wording.",
            }
        ],
    }
    client = StubAnthropic(payload)
    judge = AnthropicJudge(client=client)
    result = judge.evaluate(planner, planner, [researcher], [])

    assert result.assessment.startswith("The sourcing guarantee")
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.severity is Severity.HIGH
    assert finding.origin == "semantic"
    assert finding.affected_contract == "researcher"

    call = client.calls[0]
    assert call["model"] == "claude-sonnet-5"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "temperature" not in call  # removed on current models


def test_anthropic_judge_surfaces_refusals(planner):
    judge = AnthropicJudge(client=StubAnthropic({}, stop_reason="refusal"))
    with pytest.raises(JudgeUnavailable, match="declined"):
        judge.evaluate(planner, planner, [], [])


def test_anthropic_judge_surfaces_truncation(planner):
    judge = AnthropicJudge(client=StubAnthropic({}, stop_reason="max_tokens"))
    with pytest.raises(JudgeUnavailable, match="cut off"):
        judge.evaluate(planner, planner, [], [])


def test_judge_output_tolerates_code_fences(planner):
    from ionic.judge import _extract_json

    assert _extract_json('```json\n{"findings": []}\n```') == {"findings": []}
    assert _extract_json('Sure!\n{"findings": []}\n') == {"findings": []}
    with pytest.raises(JudgeUnavailable):
        _extract_json("no json here at all")


def test_empty_findings_from_the_judge_keeps_the_verdict_clean(planner):
    client = StubAnthropic({"assessment": "Safe.", "findings": []})
    report = check_compatibility(
        planner,
        planner.revise(version="1.1.0"),
        [],
        judge=AnthropicJudge(client=client),
    )
    assert report.verdict is Verdict.APPROVED
    assert report.judge.provider == "anthropic"


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def test_markdown_report_contains_the_essentials(planner, researcher):
    proposed = planner.revise(version="1.1.0", tools=[])
    report = check_compatibility(planner, proposed, [researcher])
    markdown = render_markdown(report)
    assert "REQUEST_CHANGES" in markdown
    assert "planner" in markdown
    assert "search_web" in markdown
    assert "**Fix:**" in markdown


def test_markdown_report_for_a_clean_check(planner):
    markdown = render_markdown(check_compatibility(planner, planner, []))
    assert "APPROVED" in markdown
    assert "No differences" in markdown


def test_api_failures_are_explained_in_plain_language():
    from ionic.judge import _explain_api_failure

    auth = _explain_api_failure(Exception("Could not resolve authentication method"))
    assert "ANTHROPIC_API_KEY" in auth and "--no-llm" in auth

    assert "rate limited" in _explain_api_failure(Exception("429 rate_limit_error"))
    assert "judge.model" in _explain_api_failure(Exception("404 not_found_error"))
    assert "Claude API call failed" in _explain_api_failure(Exception("kaboom"))


# ---------------------------------------------------------------------------
# transitive severity
# ---------------------------------------------------------------------------


def test_indirect_dependents_are_capped_below_critical(registry, planner, researcher):
    """A direct dependent declares what it needs, so a break is provable. An
    indirect one is reached through a chain the middle agent may absorb."""
    from ionic.models import Contract

    registry.register(planner)
    registry.register(researcher)
    registry.register(
        Contract.model_validate(
            {
                "id": "publisher",
                "depends_on": [
                    {"contract_id": "researcher", "expects_outputs": ["findings"]}
                ],
            }
        )
    )

    proposed = planner.revise(version="2.0.0", tools=[])
    report = check_against_registry(registry, proposed, transitive=True)

    direct = [f for f in report.findings if f.affected_contract == "researcher"]
    indirect = [f for f in report.findings if f.affected_contract == "publisher"]

    assert any(f.severity is Severity.CRITICAL for f in direct)
    assert all(f.severity <= Severity.HIGH for f in indirect)
    assert report.verdict is Verdict.REQUEST_CHANGES  # still blocks


def test_a_direct_dependent_keeps_critical_even_when_also_indirect(registry, planner, researcher):
    """The demo's publisher depends on the planner both directly and through
    the researcher. The direct declaration must win."""
    from ionic.models import Contract

    registry.register(planner)
    registry.register(researcher)
    registry.register(
        Contract.model_validate(
            {
                "id": "publisher",
                "depends_on": [
                    {"contract_id": "researcher"},
                    {"contract_id": "planner", "requires_tools": ["search_web"]},
                ],
            }
        )
    )

    report = check_against_registry(
        registry, planner.revise(version="2.0.0", tools=[]), transitive=True
    )
    publisher = [f for f in report.findings if f.affected_contract == "publisher"]
    assert any(f.severity is Severity.CRITICAL for f in publisher)


def test_non_transitive_checks_are_untouched(registry, planner, researcher):
    registry.register(planner)
    registry.register(researcher)
    report = check_against_registry(registry, planner.revise(version="2.0.0", tools=[]))
    assert any(f.severity is Severity.CRITICAL for f in report.findings)
