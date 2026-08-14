"""Typed metadata and request/result contracts for subscription runtimes."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping


class RuntimeKind(StrEnum):
    """How a model is reached.

    ``SUBSCRIPTION_CLI`` is intentionally separate from Ionic's direct API
    providers. These adapters reuse an official vendor runtime's own login;
    they never turn a consumer subscription into an API credential.
    """

    SUBSCRIPTION_CLI = "subscription_cli"


class RuntimeMaturity(StrEnum):
    STABLE = "stable"
    BETA = "beta"
    EXPERIMENTAL = "experimental"


class RuntimeCapability(StrEnum):
    DISCOVERY = "discovery"
    USER_SESSION_AUTH = "user_session_auth"
    ONE_SHOT = "one_shot"
    STRUCTURED_OUTPUT = "structured_output"
    BEST_EFFORT_STRUCTURED_OUTPUT = "best_effort_structured_output"
    NON_PERSISTENT = "non_persistent"
    READ_ONLY = "read_only"
    APP_SERVER = "app_server"
    ACP = "acp"


class RuntimeState(StrEnum):
    READY = "ready"
    MISSING = "missing"
    UNSAFE_WRAPPER = "unsafe_wrapper"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class RuntimeMetadata:
    runtime_id: str
    display_name: str
    vendor: str
    executable_names: tuple[str, ...]
    capabilities: frozenset[RuntimeCapability]
    maturity: RuntimeMaturity
    docs_url: str
    kind: RuntimeKind = RuntimeKind.SUBSCRIPTION_CLI
    direct_api_provider: bool = False
    policy_note: str = (
        "Uses the official runtime's existing user session. Ionic does not read, "
        "copy, export, or refresh vendor authentication files."
    )


@dataclass(frozen=True, slots=True)
class RuntimeStatus:
    metadata: RuntimeMetadata
    state: RuntimeState
    executable: Path | None = None
    version: str | None = None
    message: str = ""

    @property
    def available(self) -> bool:
        return self.state is RuntimeState.READY and self.executable is not None


@dataclass(frozen=True, slots=True)
class InvocationLimits:
    timeout_seconds: float = 120.0
    max_input_bytes: int = 512 * 1024
    max_output_bytes: int = 1024 * 1024
    max_schema_bytes: int = 64 * 1024

    def __post_init__(self) -> None:
        if not (0 < self.timeout_seconds <= 900):
            raise ValueError("timeout_seconds must be between 0 and 900")
        for name in ("max_input_bytes", "max_output_bytes", "max_schema_bytes"):
            value = getattr(self, name)
            if not (0 < value <= 16 * 1024 * 1024):
                raise ValueError(f"{name} must be between 1 and 16777216")


@dataclass(frozen=True, slots=True)
class StructuredInvocation:
    prompt: str
    schema: Mapping[str, Any]
    system_prompt: str = ""
    model: str | None = None
    effort: str | None = None
    working_directory: Path | None = None
    limits: InvocationLimits = field(default_factory=InvocationLimits)


@dataclass(frozen=True, slots=True)
class StructuredRuntimeResult:
    runtime_id: str
    payload: Mapping[str, Any]
    elapsed_seconds: float
    model: str | None = None
    experimental: bool = False


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    returncode: int
    stdout: str
    stderr: str
    elapsed_seconds: float
