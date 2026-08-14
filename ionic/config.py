"""Ionic configuration.

Resolution order, lowest priority first: built-in defaults, then
``.ionic/config.toml`` at the project root, then environment variables.

There is no remote configuration, no account, and nothing to sign in to.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .models import Severity
from .registry import REGISTRY_DIRNAME, default_registry_path, find_project_root

CONFIG_FILENAME = "config.toml"

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5"
DEFAULT_OPENAI_MODEL = "gpt-5.2"
DEFAULT_GOOGLE_MODEL = "gemini-3.6-flash"
DEFAULT_XAI_MODEL = "grok-4.5"
DEFAULT_LOCAL_MODEL = "qwen2.5-coder"
DEFAULT_LOCAL_BASE_URL = "http://localhost:11434/v1"

JUDGE_PROVIDERS = ("anthropic", "openai", "google", "xai", "local", "none")
MODEL_ACCESS_MODES = ("api", "subscription")
SUBSCRIPTION_RUNTIMES = ("openai-codex", "xai-grok-build")
SUBSCRIPTION_CONSENT_VERSION = "2026-08-14.3"
DEFAULT_PROVIDER_MODELS = {
    "anthropic": DEFAULT_ANTHROPIC_MODEL,
    "openai": DEFAULT_OPENAI_MODEL,
    "google": DEFAULT_GOOGLE_MODEL,
    "xai": DEFAULT_XAI_MODEL,
    "local": DEFAULT_LOCAL_MODEL,
}

_PROVIDER_ALIASES = {
    "gemini": "google",
    "openai-compatible": "local",
    "openai_compatible": "local",
}


@dataclass
class Config:
    """Everything Ionic needs to run. All of it stays on this machine."""

    registry_path: Path = field(default_factory=default_registry_path)
    model_access: str = "api"
    subscription_runtime: str = "openai-codex"
    subscription_consent_version: str | None = None
    judge_provider: str = "anthropic"
    # ``None`` means "use the selected provider's current Ionic default".
    # Config.load resolves it to a concrete model so direct dataclass
    # construction and file/env-backed configuration both remain convenient.
    judge_model: str | None = None
    judge_effort: str | None = None  # low | medium | high | xhigh | max
    judge_max_tokens: int = 32000
    local_base_url: str = DEFAULT_LOCAL_BASE_URL
    anthropic_api_key: str | None = field(default=None, repr=False)
    anthropic_auth_token: str | None = field(default=None, repr=False)
    openai_api_key: str | None = field(default=None, repr=False)
    google_api_key: str | None = field(default=None, repr=False)
    xai_api_key: str | None = field(default=None, repr=False)
    local_api_key: str | None = field(default=None, repr=False)
    fail_on: Severity = Severity.HIGH
    project_root: Path = field(default_factory=find_project_root)

    def __post_init__(self) -> None:
        self.model_access = _model_access(self.model_access)
        self.subscription_runtime = _subscription_runtime(self.subscription_runtime)

    @classmethod
    def load(cls, start: Path | None = None, **overrides: Any) -> "Config":
        root = find_project_root(start)
        data: dict[str, Any] = {}
        config_file = root / REGISTRY_DIRNAME / CONFIG_FILENAME
        if config_file.is_file():
            try:
                data = tomllib.loads(config_file.read_text(encoding="utf-8"))
            except tomllib.TOMLDecodeError as exc:  # pragma: no cover - user typo path
                raise ValueError(f"{config_file} is not valid TOML: {exc}") from exc

        judge = data.get("judge", {}) if isinstance(data.get("judge"), dict) else {}
        registry = data.get("registry", {}) if isinstance(data.get("registry"), dict) else {}
        checks = data.get("check", {}) if isinstance(data.get("check"), dict) else {}

        model_access = _model_access(
            overrides.get("model_access")
            or os.environ.get("IONIC_MODEL_ACCESS")
            or judge.get("access")
            or judge.get("access_mode")
            or judge.get("model_access")
            or "api"
        )
        subscription_config = (
            judge.get("subscription")
            if isinstance(judge.get("subscription"), dict)
            else {}
        )
        subscription_runtime = _subscription_runtime(
            overrides.get("subscription_runtime")
            or os.environ.get("IONIC_SUBSCRIPTION_RUNTIME")
            or judge.get("subscription_runtime")
            or subscription_config.get("runtime")
            or "openai-codex"
        )

        provider = _provider(
            overrides.get("judge_provider")
            or os.environ.get("IONIC_JUDGE_PROVIDER")
            or judge.get("provider")
            or "anthropic"
        )
        provider_config = _provider_table(judge, provider)
        legacy_api_key = _secret(judge.get("api_key"))

        config = cls(
            registry_path=Path(
                os.environ.get("IONIC_REGISTRY")
                or registry.get("path")
                or default_registry_path(start)
            ).expanduser(),
            model_access=model_access,
            subscription_runtime=subscription_runtime,
            subscription_consent_version=_secret(
                overrides.get("subscription_consent_version")
                or os.environ.get("IONIC_SUBSCRIPTION_CONSENT_VERSION")
            ),
            judge_provider=provider,
            judge_model=(
                _subscription_model(subscription_config)
                if model_access == "subscription"
                else _model_for(provider, judge, provider_config)
            ),
            judge_effort=(
                os.environ.get("IONIC_JUDGE_EFFORT")
                or (
                    subscription_config.get("effort")
                    if model_access == "subscription"
                    else judge.get("effort")
                )
            ),
            judge_max_tokens=int(
                os.environ.get("IONIC_JUDGE_MAX_TOKENS") or judge.get("max_tokens") or 32000
            ),
            local_base_url=(
                os.environ.get("IONIC_LOCAL_BASE_URL")
                or provider_config.get("base_url")
                or judge.get("base_url")  # pre-provider-table compatibility
                or DEFAULT_LOCAL_BASE_URL
            ),
            anthropic_api_key=(
                _secret(os.environ.get("ANTHROPIC_API_KEY"))
                or _provider_secret(judge, "anthropic")
                or (legacy_api_key if provider == "anthropic" else None)
            ),
            anthropic_auth_token=_secret(os.environ.get("ANTHROPIC_AUTH_TOKEN")),
            openai_api_key=(
                _secret(os.environ.get("OPENAI_API_KEY"))
                or _provider_secret(judge, "openai")
                or (legacy_api_key if provider == "openai" else None)
            ),
            google_api_key=(
                _secret(os.environ.get("GEMINI_API_KEY"))
                or _secret(os.environ.get("GOOGLE_API_KEY"))
                or _provider_secret(judge, "google")
                or (legacy_api_key if provider == "google" else None)
            ),
            xai_api_key=(
                _secret(os.environ.get("XAI_API_KEY"))
                or _provider_secret(judge, "xai")
                or (legacy_api_key if provider == "xai" else None)
            ),
            local_api_key=(
                _secret(os.environ.get("IONIC_LOCAL_API_KEY"))
                or _provider_secret(judge, "local")
                or (legacy_api_key if provider == "local" else None)
            ),
            fail_on=_severity(
                os.environ.get("IONIC_FAIL_ON") or checks.get("fail_on") or "high"
            ),
            project_root=root,
        )

        for key, value in overrides.items():
            if value is not None and hasattr(config, key):
                if key == "judge_provider":
                    value = provider
                elif key == "model_access":
                    value = _model_access(value)
                elif key == "subscription_runtime":
                    value = _subscription_runtime(value)
                setattr(config, key, value)
        return config

    @property
    def anthropic_key_present(self) -> bool:
        return bool(
            self.anthropic_api_key
            or self.anthropic_auth_token
            or os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        )

    @property
    def effective_judge_model(self) -> str:
        model = str(self.judge_model or "").strip()
        if model:
            return model
        return DEFAULT_PROVIDER_MODELS.get(_provider(self.judge_provider), "")

    def api_key_for(self, provider: str | None = None) -> str | None:
        """Return one provider's credential without ever including it in status text."""
        selected = _provider(provider or self.judge_provider)
        if selected == "none":
            return None
        return getattr(self, f"{selected}_api_key")

    @property
    def judge_credentials_present(self) -> bool:
        """Whether the selected remote provider has usable credentials configured."""
        if self.model_access == "subscription":
            # Authentication belongs to the official runtime and Ionic does not
            # inspect its token store. Runtime readiness is determined only when
            # the user explicitly requests a semantic check.
            return False
        selected = _provider(self.judge_provider)
        if selected == "anthropic":
            return bool(self.anthropic_api_key or self.anthropic_auth_token)
        if selected in {"openai", "google", "xai", "local"}:
            return bool(getattr(self, f"{selected}_api_key"))
        return False

    def describe_judge(self) -> str:
        if self.model_access == "subscription":
            labels = {
                "openai-codex": "OpenAI Codex subscription runtime",
                "xai-grok-build": "xAI Grok Build subscription runtime",
            }
            return labels[self.subscription_runtime]
        provider = _provider(self.judge_provider)
        if provider == "none":
            return "disabled (structural analysis only)"
        if provider == "local":
            return (
                f"local OpenAI-compatible model {self.effective_judge_model} "
                f"at {_display_url(self.local_base_url)}"
            )
        labels = {
            "anthropic": "Anthropic",
            "openai": "OpenAI",
            "google": "Google Gemini",
            "xai": "SpaceXAI · Grok",
        }
        return f"{labels.get(provider, provider)} {self.effective_judge_model}"


def _provider(value: Any) -> str:
    provider = str(value or "").strip().lower()
    provider = _PROVIDER_ALIASES.get(provider, provider)
    if provider not in JUDGE_PROVIDERS:
        valid = ", ".join(JUDGE_PROVIDERS)
        raise ValueError(f"invalid judge provider {value!r}; expected one of: {valid}")
    return provider


def _model_access(value: Any) -> str:
    access = str(value or "").strip().lower()
    if access not in MODEL_ACCESS_MODES:
        valid = ", ".join(MODEL_ACCESS_MODES)
        raise ValueError(f"invalid model access {value!r}; expected one of: {valid}")
    return access


def _subscription_runtime(value: Any) -> str:
    runtime = str(value or "").strip().lower()
    if runtime in {"anthropic", "claude", "claude-code", "anthropic-claude-code"}:
        raise ValueError(
            "Anthropic subscriptions are not supported for third-party agent runtimes; "
            "use model access 'api' with your own Anthropic API credentials instead"
        )
    if runtime not in SUBSCRIPTION_RUNTIMES:
        valid = ", ".join(SUBSCRIPTION_RUNTIMES)
        raise ValueError(
            f"invalid subscription runtime {value!r}; expected one of: {valid}"
        )
    return runtime


def _provider_table(judge: dict[str, Any], provider: str) -> dict[str, Any]:
    value = judge.get(provider)
    return value if isinstance(value, dict) else {}


def _provider_secret(judge: dict[str, Any], provider: str) -> str | None:
    return _secret(_provider_table(judge, provider).get("api_key"))


def _secret(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _model_for(
    provider: str, judge: dict[str, Any], provider_config: dict[str, Any]
) -> str:
    env_names = {
        "anthropic": ("IONIC_ANTHROPIC_MODEL",),
        "openai": ("IONIC_OPENAI_MODEL",),
        "google": ("IONIC_GOOGLE_MODEL", "IONIC_GEMINI_MODEL"),
        "xai": ("IONIC_XAI_MODEL",),
        "local": ("IONIC_LOCAL_MODEL",),
    }
    candidates: list[Any] = [os.environ.get("IONIC_JUDGE_MODEL")]
    candidates.extend(os.environ.get(name) for name in env_names.get(provider, ()))
    configured_provider = _PROVIDER_ALIASES.get(
        str(judge.get("provider") or provider).strip().lower(),
        str(judge.get("provider") or provider).strip().lower(),
    )
    # The top-level model is the pre-provider-table format. Only inherit it
    # when it belongs to the same provider; an environment provider switch
    # must never send (for example) a Claude model ID to OpenAI.
    legacy_model = judge.get("model") if configured_provider == provider else None
    candidates.extend(
        [provider_config.get("model"), legacy_model, DEFAULT_PROVIDER_MODELS.get(provider)]
    )
    for value in candidates:
        model = str(value or "").strip()
        if model:
            return model
    return ""


def _subscription_model(subscription_config: dict[str, Any]) -> str | None:
    """Return only an explicit runtime model; empty means runtime default."""
    value = os.environ.get("IONIC_JUDGE_MODEL") or subscription_config.get("model")
    model = str(value or "").strip()
    return model or None


def _display_url(value: str) -> str:
    """Strip credentials/query data before a local endpoint reaches diagnostics."""
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    except (TypeError, ValueError):
        return "configured endpoint"


def _severity(value: str | Severity) -> Severity:
    if isinstance(value, Severity):
        return value
    try:
        return Severity(str(value).strip().lower())
    except ValueError as exc:
        valid = ", ".join(s.value for s in Severity)
        raise ValueError(f"invalid severity {value!r}; expected one of: {valid}") from exc


DEFAULT_CONFIG_TOML = """\
# Ionic configuration. Everything here stays local.
# Ionic makes no network calls except to the LLM provider you choose below.

[judge]
# access: "api" | "subscription"
#   api          -- call the provider below with your own API credential
#   subscription -- invoke an official, locally installed agent runtime using
#                   its own existing login; Ionic never reads vendor tokens
access = "api"

# Used only when access = "subscription". Supported official runtimes:
# "openai-codex" | "xai-grok-build". Anthropic subscriptions are not supported.
subscription_runtime = "openai-codex"

# provider: "anthropic" | "openai" | "google" | "xai" | "local" | "none"
#   anthropic -- uses your own ANTHROPIC_API_KEY
#   openai    -- uses your own OPENAI_API_KEY
#   google    -- uses your own GEMINI_API_KEY (GOOGLE_API_KEY also works)
#   xai       -- uses your own XAI_API_KEY
#   local     -- any OpenAI-compatible endpoint (Ollama, LM Studio, vLLM)
#   none      -- structural analysis only, fully offline
provider = "anthropic"
model = "claude-sonnet-5"

# Provider-specific model choices and credentials can be kept independently.
# Prefer environment variables or the desktop credential store for API keys.
# [judge.openai]
# model = "gpt-5.2"
# api_key = "..."
# [judge.google]
# model = "gemini-3.6-flash"
# [judge.xai]
# model = "grok-4.5"

# For provider = "local":
# [judge.local]
# base_url = "http://localhost:11434/v1"
# model = "qwen2.5-coder"

[check]
# Findings at or above this severity turn a check into REQUEST_CHANGES.
fail_on = "high"
"""
