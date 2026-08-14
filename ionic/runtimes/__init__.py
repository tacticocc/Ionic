"""Official local subscription runtimes for Ionic desktop deployments.

This package does not contain direct API providers and never handles vendor
tokens. It delegates authentication to native official CLIs already installed
and signed in by the user.
"""

from .base import RuntimeAdapter
from .codex import CODEX_METADATA, CodexAppServerAccountProbe, CodexRuntime
from .errors import (
    RuntimeAdapterError,
    RuntimeExecutionError,
    RuntimeOutputError,
    RuntimeOutputLimit,
    RuntimePolicyError,
    RuntimeTimeout,
    RuntimeUnavailable,
)
from .executor import SafeSubprocessExecutor, clean_runtime_environment
from .grok import ACPResult, GROK_METADATA, GrokACPExecutor, GrokBuildRuntime
from .models import (
    InvocationLimits,
    RuntimeCapability,
    RuntimeKind,
    RuntimeMaturity,
    RuntimeMetadata,
    RuntimeState,
    RuntimeStatus,
    StructuredInvocation,
    StructuredRuntimeResult,
)
from .registry import discover_runtimes, subscription_runtimes

__all__ = [
    "ACPResult",
    "CODEX_METADATA",
    "CodexAppServerAccountProbe",
    "GROK_METADATA",
    "CodexRuntime",
    "GrokACPExecutor",
    "GrokBuildRuntime",
    "InvocationLimits",
    "RuntimeAdapter",
    "RuntimeAdapterError",
    "RuntimeCapability",
    "RuntimeExecutionError",
    "RuntimeKind",
    "RuntimeMaturity",
    "RuntimeMetadata",
    "RuntimeOutputError",
    "RuntimeOutputLimit",
    "RuntimePolicyError",
    "RuntimeState",
    "RuntimeStatus",
    "RuntimeTimeout",
    "RuntimeUnavailable",
    "SafeSubprocessExecutor",
    "StructuredInvocation",
    "StructuredRuntimeResult",
    "clean_runtime_environment",
    "discover_runtimes",
    "subscription_runtimes",
]
