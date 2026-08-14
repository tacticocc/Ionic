"""First-class remote-provider configuration and request contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ionic.compat import check_compatibility
from ionic.config import (
    DEFAULT_GOOGLE_MODEL,
    DEFAULT_OPENAI_MODEL,
    DEFAULT_XAI_MODEL,
    Config,
)
from ionic.judge import (
    GOOGLE_GENERATE_BASE_URL,
    OPENAI_BASE_URL,
    XAI_BASE_URL,
    GoogleJudge,
    JudgeUnavailable,
    LocalJudge,
    NullJudge,
    OpenAIJudge,
    XAIJudge,
    AnthropicJudge,
    build_judge,
)


SAFE_PAYLOAD = {"assessment": "Safe.", "findings": []}


class FakeResponse:
    def __init__(self, payload: dict[str, Any], *, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            exc = RuntimeError(f"HTTP {self.status_code}")
            exc.response = self  # type: ignore[attr-defined]
            raise exc

    def json(self) -> dict[str, Any]:
        return self.payload


class RecordingClient:
    def __init__(self, response: FakeResponse | None = None, error: Exception | None = None):
        self.response = response or FakeResponse({})
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if self.error:
            raise self.error
        return self.response


def chat_response(payload: dict[str, Any], *, finish_reason: str = "stop") -> FakeResponse:
    return FakeResponse(
        {
            "choices": [
                {
                    "finish_reason": finish_reason,
                    "message": {"role": "assistant", "content": json.dumps(payload)},
                }
            ]
        }
    )


def google_response(payload: dict[str, Any], *, finish_reason: str = "STOP") -> FakeResponse:
    return FakeResponse(
        {
            "candidates": [
                {
                    "finishReason": finish_reason,
                    "content": {"parts": [{"text": json.dumps(payload)}]},
                }
            ]
        }
    )


def clear_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "IONIC_JUDGE_PROVIDER",
        "IONIC_JUDGE_MODEL",
        "IONIC_OPENAI_MODEL",
        "IONIC_GOOGLE_MODEL",
        "IONIC_GEMINI_MODEL",
        "IONIC_XAI_MODEL",
        "IONIC_LOCAL_MODEL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "XAI_API_KEY",
        "IONIC_LOCAL_API_KEY",
        "IONIC_LOCAL_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_config_loads_provider_specific_model_and_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clear_provider_env(monkeypatch)
    ionic_dir = tmp_path / ".ionic"
    ionic_dir.mkdir()
    (ionic_dir / "config.toml").write_text(
        """
[judge]
provider = "openai"
model = "legacy-selected-model"

[judge.openai]
model = "saved-openai-model"
api_key = "saved-openai-key"

[judge.google]
model = "saved-google-model"
api_key = "saved-google-key"
""",
        encoding="utf-8",
    )

    config = Config.load(start=tmp_path)

    assert config.judge_provider == "openai"
    assert config.judge_model == "saved-openai-model"
    assert config.openai_api_key == "saved-openai-key"
    assert config.google_api_key == "saved-google-key"
    assert "saved-openai-key" not in repr(config)
    assert "saved-openai-key" not in config.describe_judge()


def test_environment_has_precedence_and_google_key_has_documented_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clear_provider_env(monkeypatch)
    (tmp_path / ".ionic").mkdir()
    monkeypatch.setenv("IONIC_JUDGE_PROVIDER", "google")
    monkeypatch.setenv("IONIC_GOOGLE_MODEL", "gemini-env-model")
    monkeypatch.setenv("GOOGLE_API_KEY", "google-fallback-key")

    config = Config.load(start=tmp_path)

    assert config.judge_provider == "google"
    assert config.judge_model == "gemini-env-model"
    assert config.google_api_key == "google-fallback-key"


def test_anthropic_auth_token_is_not_misclassified_as_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clear_provider_env(monkeypatch)
    (tmp_path / ".ionic").mkdir()
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "oauth-token")

    config = Config.load(start=tmp_path)

    assert config.anthropic_api_key is None
    assert config.anthropic_auth_token == "oauth-token"
    assert "oauth-token" not in repr(config)
    judge = build_judge(config)
    assert isinstance(judge, AnthropicJudge)
    assert judge.api_key is None
    assert judge.auth_token == "oauth-token"
    assert config.judge_credentials_present is True


@pytest.mark.parametrize(
    ("provider", "config_key", "present"),
    [
        ("openai", "openai_api_key", True),
        ("google", "google_api_key", True),
        ("xai", "xai_api_key", True),
        ("local", "local_api_key", True),
        ("none", "openai_api_key", False),
    ],
)
def test_selected_provider_credential_status(
    provider: str, config_key: str, present: bool
) -> None:
    config = Config(judge_provider=provider, **{config_key: "secret"})
    assert config.judge_credentials_present is present


def test_provider_switch_never_reuses_another_providers_legacy_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clear_provider_env(monkeypatch)
    ionic_dir = tmp_path / ".ionic"
    ionic_dir.mkdir()
    (ionic_dir / "config.toml").write_text(
        '[judge]\nprovider = "anthropic"\nmodel = "claude-sonnet-5"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("IONIC_JUDGE_PROVIDER", "openai")

    config = Config.load(start=tmp_path)

    assert config.judge_model == DEFAULT_OPENAI_MODEL


def test_programmatic_provider_override_selects_that_providers_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clear_provider_env(monkeypatch)
    ionic_dir = tmp_path / ".ionic"
    ionic_dir.mkdir()
    (ionic_dir / "config.toml").write_text(
        '[judge]\nprovider = "anthropic"\nmodel = "claude-sonnet-5"\n',
        encoding="utf-8",
    )

    config = Config.load(start=tmp_path, judge_provider="xai")

    assert config.judge_provider == "xai"
    assert config.judge_model == DEFAULT_XAI_MODEL


@pytest.mark.parametrize(
    ("provider", "expected_type", "expected_model"),
    [
        ("openai", OpenAIJudge, DEFAULT_OPENAI_MODEL),
        ("google", GoogleJudge, DEFAULT_GOOGLE_MODEL),
        ("xai", XAIJudge, DEFAULT_XAI_MODEL),
        ("local", LocalJudge, "qwen2.5-coder"),
        ("none", NullJudge, ""),
    ],
)
def test_build_judge_maps_every_provider(
    provider: str, expected_type: type, expected_model: str
) -> None:
    judge = build_judge(Config(judge_provider=provider))
    assert isinstance(judge, expected_type)
    if expected_model:
        assert judge.info.model == expected_model


def test_openai_request_uses_fixed_endpoint_and_strict_schema(planner) -> None:
    client = RecordingClient(chat_response(SAFE_PAYLOAD))
    judge = OpenAIJudge(api_key="openai-secret", http_client=client)

    result = judge.evaluate(planner, planner, [], [])

    assert result.assessment == "Safe."
    call = client.calls[0]
    assert call["url"] == f"{OPENAI_BASE_URL}/chat/completions"
    assert call["headers"]["authorization"] == "Bearer openai-secret"
    assert call["json"]["model"] == DEFAULT_OPENAI_MODEL
    assert call["json"]["max_completion_tokens"] == 32000
    assert "max_tokens" not in call["json"]
    response_format = call["json"]["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True


def test_anthropic_only_effort_never_reaches_openai_request(planner) -> None:
    client = RecordingClient(chat_response(SAFE_PAYLOAD))
    judge = build_judge(
        Config(
            judge_provider="openai",
            judge_model=DEFAULT_OPENAI_MODEL,
            judge_effort="max",
            openai_api_key="openai-secret",
        )
    )
    judge._http_client = client

    judge.evaluate(planner, planner, [], [])

    assert "reasoning_effort" not in client.calls[0]["json"]


def test_xai_request_uses_fixed_endpoint_and_provider_identity(planner) -> None:
    client = RecordingClient(chat_response(SAFE_PAYLOAD))
    judge = XAIJudge(api_key="xai-secret", http_client=client)

    result = judge.evaluate(planner, planner, [], [])

    call = client.calls[0]
    assert call["url"] == f"{XAI_BASE_URL}/chat/completions"
    assert call["headers"]["authorization"] == "Bearer xai-secret"
    assert call["json"]["model"] == DEFAULT_XAI_MODEL
    assert call["json"]["max_tokens"] == 32000
    assert result.info.provider == "xai"


def test_google_request_uses_native_api_and_keeps_key_out_of_url(planner) -> None:
    client = RecordingClient(google_response(SAFE_PAYLOAD))
    judge = GoogleJudge(api_key="gemini-secret", http_client=client)

    result = judge.evaluate(planner, planner, [], [])

    call = client.calls[0]
    assert call["url"] == (
        f"{GOOGLE_GENERATE_BASE_URL}/models/{DEFAULT_GOOGLE_MODEL}:generateContent"
    )
    assert "gemini-secret" not in call["url"]
    assert call["headers"]["x-goog-api-key"] == "gemini-secret"
    config = call["json"]["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseJsonSchema"]["additionalProperties"] is False
    assert config["maxOutputTokens"] == 32000
    assert result.info.provider == "google"


@pytest.mark.parametrize(
    ("judge", "env_name"),
    [
        (OpenAIJudge(http_client=RecordingClient()), "OPENAI_API_KEY"),
        (XAIJudge(http_client=RecordingClient()), "XAI_API_KEY"),
        (GoogleJudge(http_client=RecordingClient()), "GEMINI_API_KEY"),
    ],
)
def test_remote_provider_requires_its_own_key_before_network(
    judge: Any, env_name: str, planner
) -> None:
    with pytest.raises(JudgeUnavailable, match=env_name):
        judge.evaluate(planner, planner, [], [])
    assert judge._http_client.calls == []


def test_provider_error_redacts_credential(planner) -> None:
    secret = "super-secret-provider-token"
    client = RecordingClient(error=RuntimeError(f"transport accidentally echoed {secret}"))
    judge = XAIJudge(api_key=secret, http_client=client)

    with pytest.raises(JudgeUnavailable) as captured:
        judge.evaluate(planner, planner, [], [])

    assert secret not in str(captured.value)
    assert "[REDACTED]" in str(captured.value)


def test_local_endpoint_rejects_credentials_in_url() -> None:
    with pytest.raises(JudgeUnavailable, match="must not contain credentials"):
        LocalJudge("model", base_url="http://user:password@localhost:11434/v1")


def test_missing_remote_key_preserves_structural_fallback(planner, researcher) -> None:
    proposed = planner.revise(version="1.1.0", tools=[])
    report = check_compatibility(
        planner,
        proposed,
        [researcher],
        judge=OpenAIJudge(api_key=None, http_client=RecordingClient()),
    )

    assert report.judge.enabled is False
    assert "OPENAI_API_KEY" in (report.judge.error or "")
    assert any(finding.origin == "structural" for finding in report.findings)
