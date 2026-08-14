"""Subscription-runtime semantic judge selection and fallback contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from ionic.compat import check_compatibility
from ionic.config import SUBSCRIPTION_CONSENT_VERSION, Config
from ionic.judge import (
    JUDGE_OUTPUT_SCHEMA,
    JUDGE_SYSTEM_PROMPT,
    JudgeUnavailable,
    NullJudge,
    OpenAIJudge,
    RuntimeJudge,
    build_judge,
)
from ionic.runtimes.errors import RuntimeExecutionError
from ionic.runtimes.models import StructuredInvocation, StructuredRuntimeResult


SAFE_PAYLOAD = {"assessment": "No semantic breakage.", "findings": []}


@dataclass
class FakeRuntime:
    runtime_id: str
    payload: dict[str, Any] = field(default_factory=lambda: dict(SAFE_PAYLOAD))
    model: str | None = None
    failure: Exception | None = None
    requests: list[StructuredInvocation] = field(default_factory=list)

    def invoke_structured(
        self, request: StructuredInvocation
    ) -> StructuredRuntimeResult:
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        return StructuredRuntimeResult(
            runtime_id=self.runtime_id,
            payload=self.payload,
            elapsed_seconds=0.01,
            model=self.model,
            experimental=self.runtime_id == "xai-grok-build",
        )


def _write_config(root: Path, body: str) -> None:
    ionic_dir = root / ".ionic"
    ionic_dir.mkdir()
    (ionic_dir / "config.toml").write_text(body, encoding="utf-8")


def _subscription_config(runtime_id: str, **values: Any) -> Config:
    return Config(
        model_access="subscription",
        subscription_runtime=runtime_id,
        subscription_consent_version=SUBSCRIPTION_CONSENT_VERSION,
        **values,
    )


def test_config_loads_subscription_access_and_runtime_from_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("IONIC_MODEL_ACCESS", raising=False)
    monkeypatch.delenv("IONIC_SUBSCRIPTION_RUNTIME", raising=False)
    _write_config(
        tmp_path,
        """
[judge]
access = "subscription"
subscription_runtime = "xai-grok-build"
provider = "anthropic"
""",
    )

    config = Config.load(start=tmp_path)

    assert config.model_access == "subscription"
    assert config.subscription_runtime == "xai-grok-build"
    assert config.describe_judge() == "xAI Grok Build subscription runtime"
    assert config.judge_credentials_present is False


def test_subscription_environment_overrides_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(
        tmp_path,
        '[judge]\naccess = "api"\nsubscription_runtime = "xai-grok-build"\n',
    )
    monkeypatch.setenv("IONIC_MODEL_ACCESS", "subscription")
    monkeypatch.setenv("IONIC_SUBSCRIPTION_RUNTIME", "openai-codex")

    config = Config.load(start=tmp_path)

    assert config.model_access == "subscription"
    assert config.subscription_runtime == "openai-codex"
    assert config.describe_judge() == "OpenAI Codex subscription runtime"


@pytest.mark.parametrize("runtime", ["claude", "anthropic-claude-code"])
def test_anthropic_subscription_runtime_is_explicitly_rejected(runtime: str) -> None:
    with pytest.raises(ValueError, match="Anthropic subscriptions are not supported"):
        Config(model_access="subscription", subscription_runtime=runtime)


def test_unknown_subscription_runtime_is_rejected() -> None:
    with pytest.raises(ValueError, match="invalid subscription runtime"):
        Config(model_access="subscription", subscription_runtime="mystery-agent")


def test_unknown_model_access_is_rejected() -> None:
    with pytest.raises(ValueError, match="invalid model access"):
        Config(model_access="browser-cookie")


@pytest.mark.parametrize("runtime_id", ["openai-codex", "xai-grok-build"])
def test_build_judge_selects_only_the_requested_runtime(runtime_id: str) -> None:
    selected = FakeRuntime(runtime_id)
    constructed: list[str] = []

    def factory() -> FakeRuntime:
        constructed.append(runtime_id)
        return selected

    judge = build_judge(
        _subscription_config(runtime_id),
        runtime_factories={runtime_id: factory},
    )

    assert isinstance(judge, RuntimeJudge)
    assert judge.info.provider == runtime_id
    assert judge.info.model == ""
    assert constructed == [runtime_id]
    assert selected.requests == []


def test_disabled_semantic_review_never_constructs_a_subscription_runtime() -> None:
    def forbidden_factory() -> FakeRuntime:  # pragma: no cover - assertion path
        raise AssertionError("runtime must not be constructed")

    judge = build_judge(
        Config(model_access="subscription", subscription_runtime="openai-codex"),
        enabled=False,
        runtime_factories={"openai-codex": forbidden_factory},
    )

    assert isinstance(judge, NullJudge)


def test_subscription_semantic_review_fails_closed_without_current_consent() -> None:
    with pytest.raises(JudgeUnavailable, match="explicit data-access consent"):
        build_judge(
            Config(model_access="subscription", subscription_runtime="openai-codex"),
            runtime_factories={"openai-codex": lambda: FakeRuntime("openai-codex")},
        )


def test_api_access_keeps_the_existing_direct_provider_path() -> None:
    judge = build_judge(
        Config(model_access="api", judge_provider="openai"),
        runtime_factories={},
    )
    assert isinstance(judge, OpenAIJudge)


def test_runtime_judge_reuses_prompt_schema_and_maps_result(planner, researcher) -> None:
    payload = {
        "assessment": "The terse output promise changed.",
        "findings": [
            {
                "kind": "persona_drift",
                "severity": "medium",
                "summary": "Researcher may receive verbose plans",
                "detail": "The downstream prompt assumes terse plans.",
                "affected_contract": "researcher",
                "evidence": ["Terse."],
                "recommendation": "Retain the terse output rule.",
            }
        ],
    }
    runtime = FakeRuntime("openai-codex", payload=payload, model="gpt-5.6")
    judge = build_judge(
        _subscription_config(
            "openai-codex",
            judge_model="gpt-5.6",
            judge_effort="high",
        ),
        runtime_factories={"openai-codex": lambda: runtime},
    )
    proposed = planner.revise(version="1.1.0", persona_rules=["Conversational."])

    result = judge.evaluate(planner, proposed, [researcher], [])

    assert result.assessment == payload["assessment"]
    assert len(result.findings) == 1
    assert result.findings[0].origin == "semantic"
    assert result.findings[0].changed_contract == "planner"
    assert result.findings[0].affected_contract == "researcher"
    assert result.info.provider == "openai-codex"
    assert result.info.model == "gpt-5.6"
    request = runtime.requests[0]
    assert request.system_prompt == JUDGE_SYSTEM_PROMPT
    assert request.schema == JUDGE_OUTPUT_SCHEMA
    assert request.model == "gpt-5.6"
    assert request.effort == "high"
    assert request.working_directory is None
    assert "Current contract" in request.prompt
    assert "Dependent contracts" in request.prompt
    assert "researcher" in request.prompt


def test_runtime_failure_becomes_redacted_structural_fallback(
    planner, researcher, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "subscription-secret-that-must-not-escape"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    runtime = FakeRuntime(
        "openai-codex",
        failure=RuntimeExecutionError(f"token={secret}; official runtime exited"),
    )
    judge = build_judge(
        _subscription_config("openai-codex"),
        runtime_factories={"openai-codex": lambda: runtime},
    )
    proposed = planner.revise(version="1.1.0", tools=[])

    report = check_compatibility(planner, proposed, [researcher], judge=judge)

    assert report.judge.enabled is False
    assert report.judge.provider == "openai-codex"
    assert "subscription runtime unavailable" in (report.judge.error or "")
    assert secret not in (report.judge.error or "")
    assert "[REDACTED]" in (report.judge.error or "")
    assert any(finding.origin == "structural" for finding in report.findings)


def test_runtime_judge_raises_typed_unavailable_error(planner) -> None:
    runtime = FakeRuntime(
        "xai-grok-build",
        failure=RuntimeExecutionError("Grok Build is not signed in"),
    )
    judge = RuntimeJudge(runtime, "xai-grok-build")

    with pytest.raises(JudgeUnavailable, match="xai-grok-build subscription runtime"):
        judge.evaluate(planner, planner, [], [])


def test_runtime_identity_mismatch_fails_closed(planner) -> None:
    runtime = FakeRuntime("xai-grok-build")
    judge = RuntimeJudge(runtime, "openai-codex")

    with pytest.raises(JudgeUnavailable, match="mismatched identity"):
        judge.evaluate(planner, planner, [], [])
