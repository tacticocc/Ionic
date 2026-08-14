"""Runtime adapter catalog kept separate from direct API judge providers."""

from __future__ import annotations

from collections.abc import Iterable

from .base import RuntimeAdapter
from .codex import CodexRuntime
from .grok import GrokBuildRuntime
from .models import RuntimeStatus


def subscription_runtimes() -> tuple[RuntimeAdapter, ...]:
    """Return subscription runtimes cleared for the shipped product catalog.

    Anthropic subscription authentication is not supported for third-party
    runtimes, so no Claude subscription adapter is registered.
    Grok is exposed only through xAI's official local Grok Build login and ACP
    integration boundary; Ionic never implements xAI OAuth or reads its cached
    credentials. Direct API-key providers are configured independently.
    """

    return (CodexRuntime(), GrokBuildRuntime())


def discover_runtimes(
    runtimes: Iterable[RuntimeAdapter] | None = None,
    *,
    probe_versions: bool = True,
) -> tuple[RuntimeStatus, ...]:
    selected = tuple(runtimes) if runtimes is not None else subscription_runtimes()
    return tuple(runtime.discover(probe_version=probe_versions) for runtime in selected)
