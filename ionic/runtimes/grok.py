"""Experimental Grok Build subscription runtime via the official ACP path."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from .base import RuntimeAdapter
from .errors import (
    RuntimeExecutionError,
    RuntimeOutputLimit,
    RuntimePolicyError,
    RuntimeTimeout,
    RuntimeUnavailable,
)
from .executor import clean_runtime_environment
from .models import (
    InvocationLimits,
    RuntimeCapability,
    RuntimeMaturity,
    RuntimeMetadata,
    StructuredInvocation,
    StructuredRuntimeResult,
)
from .schema import parse_json_object, validate_payload


GROK_METADATA = RuntimeMetadata(
    runtime_id="xai-grok-build",
    display_name="xAI Grok Build",
    vendor="xAI",
    executable_names=("grok", "grok.exe"),
    capabilities=frozenset(
        {
            RuntimeCapability.DISCOVERY,
            RuntimeCapability.USER_SESSION_AUTH,
            RuntimeCapability.ONE_SHOT,
            RuntimeCapability.BEST_EFFORT_STRUCTURED_OUTPUT,
            RuntimeCapability.ACP,
        }
    ),
    maturity=RuntimeMaturity.EXPERIMENTAL,
    docs_url="https://docs.x.ai/build/cli/headless-scripting",
    policy_note=(
        "Experimental ACP adapter. It asks Grok Build to authenticate through an "
        "advertised non-API account method, supplies prompts over JSON-RPC stdin, "
        "disables auto-update, does not read Grok's saved login, and does not pass "
        "XAI_API_KEY into the child process."
    ),
)


@dataclass(frozen=True, slots=True)
class ACPResult:
    text: str
    elapsed_seconds: float
    stop_reason: str | None = None


class ACPExecutor(Protocol):
    def invoke(
        self,
        executable: Path,
        prompt: str,
        *,
        cwd: Path,
        limits: InvocationLimits,
        model: str | None = None,
        effort: str | None = None,
    ) -> ACPResult: ...


class GrokACPExecutor:
    """Minimal bounded client for Grok Build's documented ACP JSON-RPC flow."""

    def __init__(self, *, environment: Mapping[str, str] | None = None) -> None:
        self.environment = clean_runtime_environment(environment)

    def invoke(
        self,
        executable: Path,
        prompt: str,
        *,
        cwd: Path,
        limits: InvocationLimits,
        model: str | None = None,
        effort: str | None = None,
    ) -> ACPResult:
        if executable.name.lower() not in {"grok", "grok.exe"}:
            raise RuntimePolicyError("ACP execution is allowlisted only for Grok Build")
        if executable.suffix.lower() in {".cmd", ".bat", ".ps1"}:
            raise RuntimePolicyError("Grok Build ACP requires a native executable")
        encoded_prompt = prompt.encode("utf-8")
        if len(encoded_prompt) > limits.max_input_bytes:
            raise RuntimePolicyError("Grok Build ACP prompt exceeds the input limit")
        cwd = cwd.resolve()
        if not cwd.is_dir():
            raise RuntimePolicyError("Grok Build ACP working directory must exist")

        creationflags = 0
        if os.name == "nt":  # pragma: no branch
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        started = time.monotonic()
        args = [
            str(executable),
            "--no-auto-update",
            "--no-memory",
            "--no-subagents",
            "--no-plan",
            "--disable-web-search",
            "--permission-mode",
            "dontAsk",
        ]
        if model:
            selected_model = model.strip()
            if not selected_model or len(selected_model) > 200 or any(
                character in selected_model for character in "\0\r\n"
            ) or not selected_model.isascii() or not all(
                character.isalnum() or character in "._:/-"
                for character in selected_model
            ):
                raise RuntimePolicyError("Grok Build received an invalid model identifier")
            args.extend(("--model", selected_model))
        if effort:
            selected_effort = effort.strip().lower()
            if selected_effort not in {"low", "medium", "high", "xhigh"}:
                raise RuntimePolicyError("Grok Build received an unsupported reasoning effort")
            args.extend(("--effort", selected_effort))
        args.extend(("agent", "stdio"))
        try:
            process = subprocess.Popen(
                args,
                cwd=str(cwd),
                env=dict(self.environment),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                close_fds=True,
                creationflags=creationflags,
            )
        except OSError as exc:
            raise RuntimeExecutionError(f"could not start Grok Build ACP: {exc}") from exc

        messages: queue.Queue[bytes | None] = queue.Queue()
        stderr = bytearray()
        total_bytes = 0
        total_lock = threading.Lock()
        limit_hit = threading.Event()

        def account(chunk: bytes) -> bool:
            nonlocal total_bytes
            with total_lock:
                total_bytes += len(chunk)
                if total_bytes > limits.max_output_bytes:
                    limit_hit.set()
                    try:
                        process.kill()
                    except OSError:
                        pass
                    return False
            return True

        def read_stdout() -> None:
            assert process.stdout is not None
            try:
                for line in iter(process.stdout.readline, b""):
                    if not account(line):
                        break
                    messages.put(line)
            finally:
                messages.put(None)

        def read_stderr() -> None:
            assert process.stderr is not None
            for chunk in iter(lambda: process.stderr.read(65536), b""):
                if not account(chunk):
                    break
                stderr.extend(chunk)

        threads = [
            threading.Thread(target=read_stdout, daemon=True),
            threading.Thread(target=read_stderr, daemon=True),
        ]
        for thread in threads:
            thread.start()

        deadline = started + limits.timeout_seconds
        next_id = 1
        assistant_chunks: list[str] = []

        def send(method: str, params: Mapping[str, Any]) -> dict[str, Any]:
            nonlocal next_id
            request_id = next_id
            next_id += 1
            wire = json.dumps(
                {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
                separators=(",", ":"),
            ).encode("utf-8") + b"\n"
            assert process.stdin is not None
            write_errors: list[BaseException] = []

            def write_request() -> None:
                try:
                    process.stdin.write(wire)
                    process.stdin.flush()
                except (BrokenPipeError, OSError) as exc:
                    write_errors.append(exc)

            writer = threading.Thread(target=write_request, daemon=True)
            writer.start()
            while writer.is_alive():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    process.kill()
                    raise RuntimeTimeout(
                        f"Grok Build ACP exceeded the {limits.timeout_seconds:g}-second timeout"
                    )
                writer.join(timeout=min(remaining, 0.05))
            if write_errors:
                detail = stderr.decode("utf-8", errors="replace")[:1000]
                raise RuntimeExecutionError(
                    f"Grok Build ACP closed before {method}: {detail}"
                ) from write_errors[0]
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeTimeout(
                        f"Grok Build ACP exceeded the {limits.timeout_seconds:g}-second timeout"
                    )
                try:
                    line = messages.get(timeout=min(remaining, 0.25))
                except queue.Empty:
                    if limit_hit.is_set():
                        raise RuntimeOutputLimit("Grok Build ACP exceeded the output limit")
                    if process.poll() is not None:
                        detail = stderr.decode("utf-8", errors="replace")[:1000]
                        raise RuntimeExecutionError(
                            f"Grok Build ACP exited before {method}: {detail}"
                        )
                    continue
                if line is None:
                    if limit_hit.is_set():
                        raise RuntimeOutputLimit("Grok Build ACP exceeded the output limit")
                    detail = stderr.decode("utf-8", errors="replace")[:1000]
                    raise RuntimeExecutionError(
                        f"Grok Build ACP closed before {method}: {detail}"
                    )
                try:
                    message = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise RuntimeExecutionError("Grok Build ACP emitted invalid JSON-RPC") from exc
                if message.get("method") == "session/update":
                    update = (message.get("params") or {}).get("update") or {}
                    content = update.get("content") or {}
                    if (
                        update.get("sessionUpdate") == "agent_message_chunk"
                        and isinstance(content.get("text"), str)
                    ):
                        assistant_chunks.append(content["text"])
                    continue
                if message.get("id") != request_id:
                    continue
                if message.get("error"):
                    error = message["error"]
                    detail = error.get("message") if isinstance(error, dict) else str(error)
                    raise RuntimeExecutionError(f"Grok Build ACP {method} failed: {detail}")
                result = message.get("result") or {}
                return result if isinstance(result, dict) else {}

        try:
            initialized = send(
                "initialize",
                {
                    "protocolVersion": 1,
                    "clientCapabilities": {
                        "fs": {"readTextFile": False, "writeTextFile": False},
                        "terminal": False,
                    },
                },
            )
            # ACP authMethods advertises authentication mechanisms; it is not
            # authentication state. Always invoke an explicitly non-API method
            # and fail closed if Grok Build cannot confirm it.
            methods = [
                item.get("id")
                for item in initialized.get("authMethods", [])
                if isinstance(item, dict)
                and isinstance(item.get("id"), str)
            ]
            auth_method = "grok.com" if "grok.com" in methods else None
            if auth_method is None:
                raise RuntimeUnavailable(
                    "Grok Build did not advertise a supported account authentication method. "
                    "Run `grok login` first. Ionic deliberately will not use XAI_API_KEY "
                    "in a subscription runtime."
                )
            try:
                send(
                    "authenticate",
                    {"methodId": auth_method, "_meta": {"headless": True}},
                )
            except RuntimeExecutionError as exc:
                raise RuntimeUnavailable(
                    "Grok Build could not confirm its account session. Run `grok login` "
                    "and try again; Ionic will not fall back to an API key."
                ) from exc
            session = send("session/new", {"cwd": str(cwd), "mcpServers": []})
            session_id = session.get("sessionId")
            if not isinstance(session_id, str) or not session_id:
                raise RuntimeExecutionError("Grok Build ACP returned no sessionId")
            completion = send(
                "session/prompt",
                {"sessionId": session_id, "prompt": [{"type": "text", "text": prompt}]},
            )
            # Some ACP implementations send the response just before their last
            # update notification. Drain a short bounded idle window.
            idle = 0
            while idle < 2 and time.monotonic() < deadline:
                try:
                    line = messages.get(timeout=0.15)
                except queue.Empty:
                    idle += 1
                    continue
                if line is None:
                    break
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                update = (message.get("params") or {}).get("update") or {}
                content = update.get("content") or {}
                if (
                    update.get("sessionUpdate") == "agent_message_chunk"
                    and isinstance(content.get("text"), str)
                ):
                    assistant_chunks.append(content["text"])
                    idle = 0
            if limit_hit.is_set():
                raise RuntimeOutputLimit("Grok Build ACP exceeded the output limit")
            return ACPResult(
                text="".join(assistant_chunks).strip(),
                elapsed_seconds=time.monotonic() - started,
                stop_reason=(
                    str(completion.get("stopReason"))
                    if completion.get("stopReason") is not None
                    else None
                ),
            )
        finally:
            try:
                if process.stdin:
                    process.stdin.close()
            except OSError:
                pass
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
            for thread in threads:
                thread.join(timeout=1)


class GrokBuildRuntime(RuntimeAdapter):
    metadata = GROK_METADATA
    version_args = ("version",)

    def __init__(
        self,
        *,
        executor=None,
        acp_executor: ACPExecutor | None = None,
    ) -> None:
        super().__init__(executor=executor)
        self.acp_executor = acp_executor or GrokACPExecutor()

    def invoke_structured(
        self, request: StructuredInvocation
    ) -> StructuredRuntimeResult:
        prompt, schema_json = self.prepare_request(request)
        executable = self._require_executable()
        structured_prompt = (
            prompt
            + "\n\nReturn only one JSON object matching this schema. Do not wrap it "
            + "in Markdown fences.\n"
            + schema_json
        )
        with tempfile.TemporaryDirectory(prefix="ionic-grok-runtime-") as directory:
            # The judge boundary does not need repository tools or instructions.
            # An empty cwd prevents repository-local discovery. Grok Build still
            # owns and may apply its user/admin configuration and local history.
            cwd = Path(directory)
            completed = self.acp_executor.invoke(
                executable,
                structured_prompt,
                cwd=cwd,
                limits=request.limits,
                model=request.model,
                effort=request.effort,
            )
        payload = parse_json_object(completed.text)
        validate_payload(payload, request.schema)
        return StructuredRuntimeResult(
            runtime_id=self.metadata.runtime_id,
            payload=payload,
            elapsed_seconds=completed.elapsed_seconds,
            model=request.model,
            experimental=True,
        )
