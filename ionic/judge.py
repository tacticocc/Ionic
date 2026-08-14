"""LLM-as-judge: the semantic half of a compatibility check.

The structural engine in `diff.py` catches everything provable. This module
catches the rest -- meaning drift, weakened guarantees, persona changes that
break a downstream parser, capabilities quietly narrowed by a reworded
sentence. It is strictly additive: Ionic is fully useful with the judge
turned off, and turning it on is the only thing that ever sends data off the
machine.

Five direct-provider choices ship:

* ``AnthropicJudge``  -- your own ANTHROPIC_API_KEY, via the official SDK.
* ``OpenAIJudge``     -- OpenAI's fixed public API endpoint.
* ``GoogleJudge``     -- the native Gemini generateContent REST API.
* ``XAIJudge``        -- xAI's fixed OpenAI-compatible API endpoint.
* ``LocalJudge``      -- any user-selected OpenAI-compatible endpoint (Ollama,
                         LM Studio, vLLM) for fully local operation.

Desktop builds can alternatively select an official OpenAI Codex or xAI Grok
Build subscription runtime. Those adapters own their login sessions; Ionic
supplies only this module's prompt and JSON schema.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping
from typing import Any, Protocol
from urllib.parse import quote, urlsplit, urlunsplit

from .config import (
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_GOOGLE_MODEL,
    DEFAULT_LOCAL_BASE_URL,
    DEFAULT_OPENAI_MODEL,
    DEFAULT_XAI_MODEL,
    Config,
)
from .models import Contract, Finding, JudgeInfo, Severity
from .runtimes.errors import RuntimeAdapterError
from .runtimes.models import StructuredInvocation, StructuredRuntimeResult

SEMANTIC_KINDS = [
    "semantic_drift",
    "constraint_meaning_changed",
    "guarantee_weakened",
    "persona_drift",
    "output_meaning_changed",
    "capability_narrowed",
    "implicit_contract_broken",
    "ambiguity_introduced",
    "coordination_risk",
    "other",
]

OPENAI_BASE_URL = "https://api.openai.com/v1"
XAI_BASE_URL = "https://api.x.ai/v1"
GOOGLE_GENERATE_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

JUDGE_SYSTEM_PROMPT = """\
You are the semantic reviewer inside Ionic, a compatibility layer for
multi-agent systems. You are reviewing a proposed change to one agent's
behavioral contract, and your job is to predict whether that change will break
the other agents that depend on it.

# What a contract is

An Ionic contract describes an agent's behavioral promises: its identity and
role, the inputs it accepts, the outputs it produces, the tools and
capabilities it exposes, and the constraints and persona rules that other
agents build on. Dependents declare *what they need* from an upstream
contract -- specific tools, capabilities, outputs, formats, constraints.

# What you are looking for

A deterministic engine has already run and its findings are given to you. It
reliably catches structural breakage: removed tools, removed outputs, changed
formats, narrowed schemas, removed constraints, version problems.

**Do not repeat those findings.** You are here for what a diff cannot see:

- **Meaning drift.** A constraint or capability whose words changed such that
  it no longer promises what a dependent relies on -- even though the diff
  sees "text changed" and nothing more.
- **Weakened guarantees.** "Always returns sources" becoming "returns sources
  when available"; "sorted by relevance" becoming "sorted"; an absolute
  becoming a default.
- **Persona and format drift.** Tone, verbosity, or structure changes that
  will break a downstream agent parsing or pattern-matching the output, even
  when the declared format is unchanged.
- **Output meaning changes.** Same field name, same type, different content or
  semantics (ids become opaque, a score changes scale, a summary becomes a
  headline).
- **Capability narrowing by rewording.** Scope quietly reduced in prose while
  the capability list looks similar.
- **Coordination risk.** The change is individually fine but interacts badly
  with a specific dependent's declared workflow.

# How to judge severity

- `critical` -- a named dependent will fail. You can point at the exact
  expectation it declared and the exact change that invalidates it.
- `high`     -- a named dependent will very likely produce wrong output or
  silently degrade, though it may not error.
- `medium`   -- a real risk that needs a human decision before merging.
- `low`      -- worth noting; unlikely to cause a failure on its own.
- `info`     -- observation only.

# Rules

1. **Ground every finding.** Quote the specific text from the contracts in
   `evidence`. If you cannot quote it, do not report it.
2. **Name the dependent.** Set `affected_contract` to the id of the agent that
   breaks. Use "" only for a system-wide observation with no single victim.
3. **Precision over recall.** A false alarm trains people to ignore Ionic.
   Returning zero findings is the correct answer for a safe change, and you
   should return zero findings often.
4. **No structural duplicates.** If the deterministic engine already reported
   it, skip it. Reporting the same removal in different words is noise.
5. **Be concrete in `recommendation`.** Say what to change, not "consider
   reviewing this".
6. **Judge the change, not the design.** Do not critique the agent's
   architecture, naming, or prompt quality. Only compatibility matters here.

Return only the JSON object required by the schema."""


JUDGE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "assessment": {
            "type": "string",
            "description": "One or two sentences on the semantic risk of this change overall.",
        },
        "findings": {
            "type": "array",
            "description": "Semantic breakages only. Empty if the change is semantically safe.",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": SEMANTIC_KINDS},
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "high", "medium", "low", "info"],
                    },
                    "summary": {
                        "type": "string",
                        "description": "One line, specific, names the affected agent.",
                    },
                    "detail": {
                        "type": "string",
                        "description": "Why this breaks the dependent, mechanically.",
                    },
                    "affected_contract": {
                        "type": "string",
                        "description": "Contract id of the agent that breaks, or \"\".",
                    },
                    "evidence": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Quoted text from the contracts supporting the finding.",
                    },
                    "recommendation": {
                        "type": "string",
                        "description": "The concrete change that resolves it.",
                    },
                },
                "required": [
                    "kind",
                    "severity",
                    "summary",
                    "detail",
                    "affected_contract",
                    "evidence",
                    "recommendation",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["assessment", "findings"],
    "additionalProperties": False,
}


class JudgeUnavailable(RuntimeError):
    """The configured judge cannot run (missing key, missing dep, bad endpoint)."""


class JudgeResult:
    def __init__(self, findings: list[Finding], assessment: str, info: JudgeInfo) -> None:
        self.findings = findings
        self.assessment = assessment
        self.info = info


class Judge(Protocol):
    info: JudgeInfo

    def evaluate(
        self,
        current: Contract,
        proposed: Contract,
        dependents: list[Contract],
        structural: list[Finding],
    ) -> JudgeResult: ...


class StructuredRuntime(Protocol):
    """The narrow runtime surface a subscription-backed judge needs."""

    def invoke_structured(
        self, request: StructuredInvocation
    ) -> StructuredRuntimeResult: ...


RuntimeFactory = Callable[[], StructuredRuntime]


# ---------------------------------------------------------------------------
# prompt construction (shared by both backends)
# ---------------------------------------------------------------------------


def build_user_prompt(
    current: Contract,
    proposed: Contract,
    dependents: list[Contract],
    structural: list[Finding],
) -> str:
    parts: list[str] = []
    parts.append(
        f"# Change under review\n\n"
        f"Contract `{current.id}` is moving from v{current.version} to "
        f"v{proposed.version}.\n"
    )
    parts.append("## Current contract\n\n```json\n" + current.to_json() + "\n```\n")
    parts.append("## Proposed contract\n\n```json\n" + proposed.to_json() + "\n```\n")

    if dependents:
        parts.append("## Dependent contracts\n")
        parts.append(
            "These agents declare a dependency on `"
            + current.id
            + "`. Each `depends_on` entry states exactly what it needs.\n"
        )
        for dependent in dependents:
            parts.append(f"### `{dependent.id}`\n\n```json\n{dependent.to_json()}\n```\n")
    else:
        parts.append(
            "## Dependent contracts\n\nNone registered. Judge the change on its own "
            "terms and flag only self-evident semantic problems.\n"
        )

    if structural:
        parts.append(
            "## Already reported by the deterministic engine (do not repeat these)\n"
        )
        for finding in structural:
            target = finding.affected_contract or "-"
            parts.append(f"- [{finding.severity.value}] ({target}) {finding.summary}")
        parts.append("")
    else:
        parts.append(
            "## Already reported by the deterministic engine\n\nNothing. The structural "
            "diff is clean, so any breakage here is purely semantic.\n"
        )

    parts.append(
        "Review the change and return findings for semantic breakage only, per your "
        "instructions."
    )
    return "\n".join(parts)


def _findings_from_payload(payload: dict[str, Any], changed_contract: str) -> list[Finding]:
    raw = payload.get("findings") or []
    findings: list[Finding] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            severity = Severity(str(item.get("severity", "medium")).strip().lower())
        except ValueError:
            severity = Severity.MEDIUM
        affected = (item.get("affected_contract") or "").strip() or None
        findings.append(
            Finding(
                kind=str(item.get("kind") or "semantic_drift"),
                severity=severity,
                summary=str(item.get("summary") or "").strip() or "Semantic risk",
                detail=str(item.get("detail") or "").strip(),
                changed_contract=changed_contract,
                affected_contract=affected,
                evidence=[str(e) for e in (item.get("evidence") or []) if str(e).strip()],
                recommendation=str(item.get("recommendation") or "").strip(),
                origin="semantic",
            )
        )
    return findings


def _extract_json(text: str) -> dict[str, Any]:
    """Parse a JSON object out of model output, tolerating code fences."""
    text = text.strip()
    if not text:
        raise JudgeUnavailable("judge returned an empty response")
    fence = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise JudgeUnavailable(
                "judge did not return JSON; try a stronger model"
            ) from None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise JudgeUnavailable(f"judge returned malformed JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise JudgeUnavailable("judge returned JSON that is not an object")
    return data


# ---------------------------------------------------------------------------
# Anthropic backend
# ---------------------------------------------------------------------------


def _redact(value: Any, secret: str | None = None) -> str:
    """Keep credentials out of diagnostics even if a vendor exception echoes one."""
    text = str(value)
    if secret:
        text = text.replace(secret, "[REDACTED]")
    # URL query strings can contain credentials on misconfigured endpoints.
    text = re.sub(r"([?&](?:key|api_key|token)=)[^&\s]+", r"\1[REDACTED]", text, flags=re.I)
    return text


def _explain_api_failure(exc: Exception, api_key: str | None = None) -> str:
    """Turn SDK errors into something a user can act on."""
    text = _redact(exc, api_key)
    lowered = text.lower()
    if "authentication" in lowered or "api_key" in lowered or "api key" in lowered:
        return (
            "no Anthropic credentials found. Set ANTHROPIC_API_KEY, or run "
            "`ant auth login`, or use --no-llm (structural analysis is complete "
            "on its own)"
        )
    if "rate_limit" in lowered or "429" in lowered:
        return f"rate limited by the Claude API; retry shortly ({text})"
    if "not_found" in lowered or "404" in lowered:
        return (
            f"the configured model was rejected by the API ({text}). "
            "Check judge.model in .ionic/config.toml"
        )
    return f"Claude API call failed: {text}"


_SECRET_ENV_NAMES = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "XAI_API_KEY",
    "IONIC_LOCAL_API_KEY",
)


def _safe_runtime_failure(runtime_id: str, exc: Exception) -> str:
    """Return an actionable runtime failure without forwarding credentials."""
    detail = str(exc).replace("\x00", "")
    for name in _SECRET_ENV_NAMES:
        detail = _redact(detail, os.environ.get(name))
    detail = re.sub(
        r"(?i)\b(bearer|api[_ -]?key|auth(?:entication)?[_ -]?token|token)"
        r"(\s*[:=]\s*|\s+)[^\s,;]+",
        r"\1\2[REDACTED]",
        detail,
    )
    detail = " ".join(detail.split())[:1000]
    if not detail:
        detail = "the official runtime did not provide an error message"
    return f"{runtime_id} subscription runtime unavailable: {detail}"


class AnthropicJudge:
    """Semantic review via the Claude API, using your own API key."""

    def __init__(
        self,
        model: str = DEFAULT_ANTHROPIC_MODEL,
        *,
        api_key: str | None = None,
        auth_token: str | None = None,
        max_tokens: int = 32000,
        effort: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.model = _validate_model(model)
        self.api_key = api_key.strip() if api_key else None
        self.auth_token = auth_token.strip() if auth_token else None
        self.max_tokens = _validate_max_tokens(max_tokens)
        self.effort = effort
        self._client = client
        self.info = JudgeInfo(enabled=True, provider="anthropic", model=self.model)

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import anthropic
        except ModuleNotFoundError as exc:  # pragma: no cover
            raise JudgeUnavailable(
                "the `anthropic` package is not installed; "
                "run `pip install ionic-contracts[anthropic]` or use --no-llm"
            ) from exc
        try:
            kwargs: dict[str, str] = {}
            if self.api_key:
                kwargs["api_key"] = self.api_key
            elif self.auth_token:
                kwargs["auth_token"] = self.auth_token
            self._client = anthropic.Anthropic(**kwargs)
        except Exception as exc:
            raise JudgeUnavailable(
                "could not construct an Anthropic client. Set ANTHROPIC_API_KEY, "
                "or run `ant auth login`, or use --no-llm for offline analysis. "
                f"({_redact(_redact(exc, self.api_key), self.auth_token)})"
            ) from exc
        return self._client

    def evaluate(
        self,
        current: Contract,
        proposed: Contract,
        dependents: list[Contract],
        structural: list[Finding],
    ) -> JudgeResult:
        client = self._get_client()
        prompt = build_user_prompt(current, proposed, dependents, structural)

        output_config: dict[str, Any] = {
            "format": {"type": "json_schema", "schema": JUDGE_OUTPUT_SCHEMA}
        }
        if self.effort:
            output_config["effort"] = self.effort

        try:
            # Streaming: contract bundles get long, and a large max_tokens on a
            # non-streaming request risks an HTTP timeout.
            with client.messages.stream(
                model=self.model,
                max_tokens=self.max_tokens,
                system=[
                    {
                        "type": "text",
                        "text": JUDGE_SYSTEM_PROMPT,
                        # The system prompt is byte-stable across every check,
                        # so it caches cleanly and later checks are cheap.
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                output_config=output_config,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                message = stream.get_final_message()
        except JudgeUnavailable:
            raise
        except Exception as exc:
            explanation = _explain_api_failure(exc, self.api_key)
            raise JudgeUnavailable(_redact(explanation, self.auth_token)) from exc

        if message.stop_reason == "refusal":
            raise JudgeUnavailable(
                "the model declined to review this change "
                f"({getattr(message.stop_details, 'category', 'unspecified')})"
            )
        if message.stop_reason == "max_tokens":
            raise JudgeUnavailable(
                "the review was cut off by max_tokens; raise judge.max_tokens "
                "or check fewer dependents at once"
            )

        text = next(
            (block.text for block in message.content if getattr(block, "type", "") == "text"),
            "",
        )
        payload = _extract_json(text)
        return JudgeResult(
            findings=_findings_from_payload(payload, proposed.id),
            assessment=str(payload.get("assessment") or "").strip(),
            info=self.info,
        )


# ---------------------------------------------------------------------------
# OpenAI-compatible providers (OpenAI, SpaceXAI/xAI, and local servers)
# ---------------------------------------------------------------------------


def _validate_model(model: Any) -> str:
    value = str(model or "").strip()
    if not value:
        raise JudgeUnavailable("the configured judge model is empty")
    if len(value) > 256 or any(char in value for char in "\r\n\0"):
        raise JudgeUnavailable("the configured judge model is invalid")
    return value


def _validate_max_tokens(value: Any) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise JudgeUnavailable("judge.max_tokens must be a positive integer") from exc
    if result <= 0:
        raise JudgeUnavailable("judge.max_tokens must be a positive integer")
    return result


def _validate_base_url(value: str) -> str:
    try:
        parsed = urlsplit(str(value).strip())
        port = parsed.port  # force validation of malformed port values
    except (TypeError, ValueError) as exc:
        raise JudgeUnavailable("the local judge base URL is invalid") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise JudgeUnavailable("the local judge base URL must use http:// or https://")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise JudgeUnavailable(
            "the local judge base URL must not contain credentials, a query, or a fragment"
        )
    del port
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _safe_endpoint(value: str) -> str:
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    except (TypeError, ValueError):
        return "configured endpoint"


def _structured_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "ionic_compatibility_report",
            "strict": True,
            "schema": JUDGE_OUTPUT_SCHEMA,
        },
    }


def _status_code(response: Any, exc: Exception) -> int | None:
    direct = getattr(response, "status_code", None)
    nested = getattr(getattr(exc, "response", None), "status_code", None)
    value = direct if isinstance(direct, int) else nested
    return value if isinstance(value, int) else None


class OpenAICompatibleJudge:
    """Shared raw-HTTP implementation for OpenAI-compatible chat APIs."""

    def __init__(
        self,
        model: str,
        *,
        provider: str,
        base_url: str,
        api_key: str | None,
        max_tokens: int,
        timeout: float = 300.0,
        require_api_key: bool = True,
        credential_env: str = "",
        token_parameter: str = "max_tokens",
        strict_schema: bool = True,
        effort: str | None = None,
        http_client: Any | None = None,
    ) -> None:
        self.model = _validate_model(model)
        self.provider = provider
        self.base_url = _validate_base_url(base_url)
        self.api_key = api_key.strip() if api_key else None
        self.max_tokens = _validate_max_tokens(max_tokens)
        self.timeout = float(timeout)
        self.require_api_key = require_api_key
        self.credential_env = credential_env
        self.token_parameter = token_parameter
        self.strict_schema = strict_schema
        self.effort = effort
        self._http_client = http_client
        self.info = JudgeInfo(enabled=True, provider=provider, model=self.model)

    @property
    def display_name(self) -> str:
        return {
            "openai": "OpenAI",
            "xai": "SpaceXAI · Grok",
            "local": "local judge",
        }.get(self.provider, self.provider)

    def _failure(self, exc: Exception, response: Any = None) -> str:
        safe = _redact(exc, self.api_key)
        status = _status_code(response, exc)
        if self.provider == "local":
            return f"local judge at {_safe_endpoint(self.base_url)} did not respond: {safe}"
        if status in {401, 403}:
            return (
                f"{self.display_name} rejected the configured credential. "
                f"Set {self.credential_env}, or use --no-llm"
            )
        if status == 404:
            return (
                f"{self.display_name} rejected the configured model or endpoint. "
                "Check judge.model in .ionic/config.toml"
            )
        if status == 429:
            return f"rate limited by {self.display_name}; retry shortly"
        return f"{self.display_name} API call failed: {safe}"

    def _request_body(self, prompt: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            self.token_parameter: self.max_tokens,
            "messages": [
                {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": prompt
                    + "\n\nRespond with a single JSON object matching this schema:\n"
                    + json.dumps(JUDGE_OUTPUT_SCHEMA),
                },
            ],
            "response_format": (
                _structured_response_format()
                if self.strict_schema
                else {"type": "json_object"}
            ),
            "stream": False,
        }
        if self.effort and self.provider == "openai":
            body["reasoning_effort"] = self.effort
        return body

    def _post(self, headers: dict[str, str], body: dict[str, Any]) -> Any:
        endpoint = f"{self.base_url}/chat/completions"
        if self._http_client is not None:
            return self._http_client.post(endpoint, headers=headers, json=body)
        try:
            import httpx
        except ModuleNotFoundError as exc:  # pragma: no cover
            raise JudgeUnavailable(
                "the `httpx` package is required for OpenAI-compatible judges"
            ) from exc
        with httpx.Client(timeout=self.timeout) as client:
            return client.post(endpoint, headers=headers, json=body)

    def evaluate(
        self,
        current: Contract,
        proposed: Contract,
        dependents: list[Contract],
        structural: list[Finding],
    ) -> JudgeResult:
        if self.require_api_key and not self.api_key:
            raise JudgeUnavailable(
                f"{self.display_name} credentials are not configured. "
                f"Set {self.credential_env}, or use --no-llm"
            )

        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        body = self._request_body(
            build_user_prompt(current, proposed, dependents, structural)
        )
        response: Any = None
        try:
            response = self._post(headers, body)
            response.raise_for_status()
        except JudgeUnavailable:
            raise
        except Exception as exc:
            raise JudgeUnavailable(self._failure(exc, response)) from exc

        try:
            data = response.json()
            choice = data["choices"][0]
            message = choice["message"]
            finish_reason = str(choice.get("finish_reason") or "").lower()
            refusal = message.get("refusal")
            content = message["content"]
            if isinstance(content, list):
                content = "".join(
                    str(part.get("text") or "")
                    for part in content
                    if isinstance(part, dict)
                )
            text = str(content or "")
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise JudgeUnavailable(
                f"unexpected response shape from {self.display_name}: {_redact(exc)}"
            ) from exc

        if refusal:
            raise JudgeUnavailable(f"{self.display_name} declined to review this change")
        if finish_reason in {"length", "max_tokens"}:
            raise JudgeUnavailable(
                "the review was cut off by the output-token limit; raise judge.max_tokens"
            )

        payload = _extract_json(text)
        return JudgeResult(
            findings=_findings_from_payload(payload, proposed.id),
            assessment=str(payload.get("assessment") or "").strip(),
            info=self.info,
        )


class OpenAIJudge(OpenAICompatibleJudge):
    """Semantic review using OpenAI's fixed public API endpoint."""

    def __init__(
        self,
        model: str = DEFAULT_OPENAI_MODEL,
        *,
        api_key: str | None = None,
        max_tokens: int = 32000,
        timeout: float = 300.0,
        http_client: Any | None = None,
    ) -> None:
        super().__init__(
            model,
            provider="openai",
            base_url=OPENAI_BASE_URL,
            api_key=api_key,
            max_tokens=max_tokens,
            timeout=timeout,
            credential_env="OPENAI_API_KEY",
            token_parameter="max_completion_tokens",
            strict_schema=True,
            http_client=http_client,
        )


class XAIJudge(OpenAICompatibleJudge):
    """Semantic review using SpaceXAI/xAI's fixed OpenAI-compatible endpoint."""

    def __init__(
        self,
        model: str = DEFAULT_XAI_MODEL,
        *,
        api_key: str | None = None,
        max_tokens: int = 32000,
        timeout: float = 300.0,
        http_client: Any | None = None,
    ) -> None:
        super().__init__(
            model,
            provider="xai",
            base_url=XAI_BASE_URL,
            api_key=api_key,
            max_tokens=max_tokens,
            timeout=timeout,
            credential_env="XAI_API_KEY",
            token_parameter="max_tokens",
            strict_schema=True,
            http_client=http_client,
        )


class LocalJudge(OpenAICompatibleJudge):
    """Semantic review via a user-selected local OpenAI-compatible server."""

    def __init__(
        self,
        model: str,
        *,
        base_url: str = DEFAULT_LOCAL_BASE_URL,
        api_key: str | None = None,
        max_tokens: int = 8192,
        timeout: float = 300.0,
        http_client: Any | None = None,
    ) -> None:
        super().__init__(
            model,
            provider="local",
            base_url=base_url,
            api_key=api_key,
            max_tokens=max_tokens,
            timeout=timeout,
            require_api_key=False,
            credential_env="IONIC_LOCAL_API_KEY",
            token_parameter="max_tokens",
            strict_schema=False,
            http_client=http_client,
        )


# ---------------------------------------------------------------------------
# Google Gemini native REST backend
# ---------------------------------------------------------------------------


class GoogleJudge:
    """Semantic review through Gemini's native generateContent REST API."""

    def __init__(
        self,
        model: str = DEFAULT_GOOGLE_MODEL,
        *,
        api_key: str | None = None,
        max_tokens: int = 32000,
        timeout: float = 300.0,
        http_client: Any | None = None,
    ) -> None:
        model_value = _validate_model(model)
        if model_value.startswith("models/"):
            model_value = model_value.removeprefix("models/")
        self.model = _validate_model(model_value)
        self.api_key = api_key.strip() if api_key else None
        self.max_tokens = _validate_max_tokens(max_tokens)
        self.timeout = float(timeout)
        self._http_client = http_client
        self.info = JudgeInfo(enabled=True, provider="google", model=self.model)

    def _post(self, endpoint: str, headers: dict[str, str], body: dict[str, Any]) -> Any:
        if self._http_client is not None:
            return self._http_client.post(endpoint, headers=headers, json=body)
        try:
            import httpx
        except ModuleNotFoundError as exc:  # pragma: no cover
            raise JudgeUnavailable("the `httpx` package is required for the Google judge") from exc
        with httpx.Client(timeout=self.timeout) as client:
            return client.post(endpoint, headers=headers, json=body)

    def evaluate(
        self,
        current: Contract,
        proposed: Contract,
        dependents: list[Contract],
        structural: list[Finding],
    ) -> JudgeResult:
        if not self.api_key:
            raise JudgeUnavailable(
                "Google Gemini credentials are not configured. Set GEMINI_API_KEY "
                "(or GOOGLE_API_KEY), or use --no-llm"
            )
        prompt = build_user_prompt(current, proposed, dependents, structural)
        endpoint = (
            f"{GOOGLE_GENERATE_BASE_URL}/models/{quote(self.model, safe='')}"
            ":generateContent"
        )
        headers = {
            "content-type": "application/json",
            # Header authentication keeps the secret out of URLs and logs.
            "x-goog-api-key": self.api_key,
        }
        body = {
            "systemInstruction": {"parts": [{"text": JUDGE_SYSTEM_PROMPT}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": prompt
                            + "\n\nReturn one JSON object matching the supplied response schema."
                        }
                    ],
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": JUDGE_OUTPUT_SCHEMA,
                "maxOutputTokens": self.max_tokens,
            },
        }
        response: Any = None
        try:
            response = self._post(endpoint, headers, body)
            response.raise_for_status()
        except JudgeUnavailable:
            raise
        except Exception as exc:
            status = _status_code(response, exc)
            if status in {401, 403}:
                message = (
                    "Google Gemini rejected the configured credential. Set GEMINI_API_KEY "
                    "(or GOOGLE_API_KEY), or use --no-llm"
                )
            elif status == 404:
                message = (
                    "Google Gemini rejected the configured model. "
                    "Check judge.model in .ionic/config.toml"
                )
            elif status == 429:
                message = "rate limited by Google Gemini; retry shortly"
            else:
                message = f"Google Gemini API call failed: {_redact(exc, self.api_key)}"
            raise JudgeUnavailable(message) from exc

        try:
            data = response.json()
            candidates = data.get("candidates") or []
            if not candidates:
                blocked = str((data.get("promptFeedback") or {}).get("blockReason") or "")
                if blocked:
                    raise JudgeUnavailable(
                        f"Google Gemini declined to review this change ({blocked})"
                    )
                raise JudgeUnavailable("Google Gemini returned no candidates")
            candidate = candidates[0]
            finish_reason = str(candidate.get("finishReason") or "").upper()
            parts = (candidate.get("content") or {}).get("parts") or []
            text = "".join(
                str(part.get("text") or "") for part in parts if isinstance(part, dict)
            )
        except JudgeUnavailable:
            raise
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise JudgeUnavailable(
                f"unexpected response shape from Google Gemini: {_redact(exc)}"
            ) from exc

        if finish_reason == "MAX_TOKENS":
            raise JudgeUnavailable(
                "the review was cut off by the output-token limit; raise judge.max_tokens"
            )
        if finish_reason in {"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "RECITATION"}:
            raise JudgeUnavailable(
                f"Google Gemini declined to review this change ({finish_reason})"
            )

        payload = _extract_json(text)
        return JudgeResult(
            findings=_findings_from_payload(payload, proposed.id),
            assessment=str(payload.get("assessment") or "").strip(),
            info=self.info,
        )


class RuntimeJudge:
    """Semantic judge backed by an official subscription-authenticated runtime.

    The adapter owns process isolation and authentication. This class supplies
    only the same prompt and output schema used by Ionic's direct API judges;
    it never reads a vendor token or turns a subscription into an API key.
    """

    def __init__(
        self,
        runtime: StructuredRuntime,
        runtime_id: str,
        *,
        model: str | None = None,
        effort: str | None = None,
    ) -> None:
        self.runtime = runtime
        self.runtime_id = runtime_id
        self.model = str(model or "").strip() or None
        self.effort = str(effort or "").strip().lower() or None
        # The official runtime selects its own model unless it reports one in
        # the structured result. An empty model is more truthful than claiming
        # the direct-API provider's configured default.
        self.info = JudgeInfo(enabled=True, provider=runtime_id, model="")

    def evaluate(
        self,
        current: Contract,
        proposed: Contract,
        dependents: list[Contract],
        structural: list[Finding],
    ) -> JudgeResult:
        request = StructuredInvocation(
            system_prompt=JUDGE_SYSTEM_PROMPT,
            prompt=build_user_prompt(current, proposed, dependents, structural),
            schema=JUDGE_OUTPUT_SCHEMA,
            model=self.model,
            effort=self.effort,
        )
        try:
            result = self.runtime.invoke_structured(request)
        except RuntimeAdapterError as exc:
            raise JudgeUnavailable(_safe_runtime_failure(self.runtime_id, exc)) from exc

        payload = dict(result.payload)
        result_runtime = str(result.runtime_id or self.runtime_id).strip()
        if result_runtime != self.runtime_id:
            raise JudgeUnavailable(
                f"{self.runtime_id} subscription runtime returned mismatched identity "
                f"{result_runtime!r}"
            )
        info = JudgeInfo(
            enabled=True,
            provider=result_runtime or self.runtime_id,
            model=str(result.model or "").strip(),
        )
        self.info = info
        return JudgeResult(
            findings=_findings_from_payload(payload, proposed.id),
            assessment=str(payload.get("assessment") or "").strip(),
            info=info,
        )


class NullJudge:
    """No semantic review. Structural analysis only, fully offline."""

    def __init__(self, reason: str = "") -> None:
        self.info = JudgeInfo(enabled=False, provider="none", error=reason or None)

    def evaluate(
        self,
        current: Contract,
        proposed: Contract,
        dependents: list[Contract],
        structural: list[Finding],
    ) -> JudgeResult:
        return JudgeResult(findings=[], assessment="", info=self.info)


def _default_runtime_factories() -> Mapping[str, RuntimeFactory]:
    # Imported only after the user explicitly enables semantic review in
    # subscription mode. Merely loading configuration never probes or launches
    # an installed agent runtime.
    from .runtimes.codex import CodexRuntime
    from .runtimes.grok import GrokBuildRuntime

    return {
        "openai-codex": CodexRuntime,
        "xai-grok-build": GrokBuildRuntime,
    }


def build_judge(
    config: Config,
    *,
    enabled: bool = True,
    runtime_factories: Mapping[str, RuntimeFactory] | None = None,
) -> Judge:
    """Pick a judge backend from configuration."""
    if not enabled:
        return NullJudge()

    access = str(config.model_access or "").strip().lower()
    if access == "subscription":
        from .config import SUBSCRIPTION_CONSENT_VERSION

        if config.subscription_consent_version != SUBSCRIPTION_CONSENT_VERSION:
            raise JudgeUnavailable(
                "subscription semantic review requires the current explicit data-access consent"
            )
        runtime_id = str(config.subscription_runtime or "").strip().lower()
        factories = (
            _default_runtime_factories()
            if runtime_factories is None
            else runtime_factories
        )
        factory = factories.get(runtime_id)
        if factory is None:
            supported = ", ".join(sorted(factories))
            raise JudgeUnavailable(
                f"unsupported subscription runtime {runtime_id!r}; expected one of: "
                f"{supported} (Anthropic subscriptions are not supported)"
            )
        try:
            runtime = factory()
        except RuntimeAdapterError as exc:
            raise JudgeUnavailable(_safe_runtime_failure(runtime_id, exc)) from exc
        return RuntimeJudge(
            runtime,
            runtime_id,
            model=config.judge_model,
            effort=config.judge_effort,
        )
    if access != "api":
        raise JudgeUnavailable(
            f"unknown model access mode {config.model_access!r}; expected api or subscription"
        )

    provider = str(config.judge_provider or "").strip().lower()
    provider = {
        "gemini": "google",
        "openai-compatible": "local",
        "openai_compatible": "local",
    }.get(provider, provider)
    if provider == "none":
        return NullJudge()
    model = config.effective_judge_model
    if provider == "local":
        return LocalJudge(
            model=model,
            base_url=config.local_base_url,
            api_key=config.local_api_key,
            max_tokens=config.judge_max_tokens,
        )
    if provider == "anthropic":
        return AnthropicJudge(
            model=model,
            api_key=config.anthropic_api_key,
            auth_token=config.anthropic_auth_token,
            max_tokens=config.judge_max_tokens,
            effort=config.judge_effort,
        )
    if provider == "openai":
        return OpenAIJudge(
            model=model,
            api_key=config.openai_api_key,
            max_tokens=config.judge_max_tokens,
        )
    if provider == "google":
        return GoogleJudge(
            model=model,
            api_key=config.google_api_key,
            max_tokens=config.judge_max_tokens,
        )
    if provider == "xai":
        return XAIJudge(
            model=model,
            api_key=config.xai_api_key,
            max_tokens=config.judge_max_tokens,
        )
    raise JudgeUnavailable(
        f"unknown judge provider {config.judge_provider!r}; expected "
        "anthropic, openai, google, xai, local, or none"
    )
