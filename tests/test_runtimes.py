"""Shared subscription-runtime safety and adapter contracts."""

from __future__ import annotations

import json
import os
import queue
import sys
from pathlib import Path
from typing import Callable, Sequence

import pytest

from ionic import __version__
from ionic.runtimes import (
    ACPResult,
    CODEX_METADATA,
    GROK_METADATA,
    CodexAppServerAccountProbe,
    CodexRuntime,
    GrokACPExecutor,
    GrokBuildRuntime,
    InvocationLimits,
    RuntimeCapability,
    RuntimeKind,
    RuntimeMaturity,
    RuntimeOutputError,
    RuntimeOutputLimit,
    RuntimePolicyError,
    RuntimeState,
    RuntimeTimeout,
    RuntimeUnavailable,
    SafeSubprocessExecutor,
    StructuredInvocation,
    clean_runtime_environment,
    discover_runtimes,
    subscription_runtimes,
)
from ionic.runtimes.models import ExecutionResult
from ionic.runtimes.codex import (
    CODEX_APP_SERVER_CONFIG,
    CodexAppServerRunner,
    _prepare_codex_profile,
    _schema_proves_ephemeral_thread,
    _schema_proves_restricted_read_roots,
    ionic_codex_profile_directory,
)


SCHEMA = {
    "type": "object",
    "properties": {
        "assessment": {"type": "string"},
        "findings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["assessment", "findings"],
    "additionalProperties": False,
}
PAYLOAD = {"assessment": "Compatible.", "findings": []}


class FakeExecutor:
    def __init__(
        self,
        executable: str | None,
        responder: Callable[[Path, Sequence[str], str], ExecutionResult] | None = None,
    ) -> None:
        self.executable = Path(executable) if executable else None
        self.responder = responder
        self.calls: list[dict[str, object]] = []

    def locate(self, executable_names: Sequence[str]) -> Path | None:
        self.calls.append({"operation": "locate", "names": tuple(executable_names)})
        return self.executable

    def run(
        self,
        executable: Path,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        input_text: str = "",
        limits: InvocationLimits | None = None,
    ) -> ExecutionResult:
        self.calls.append(
            {
                "operation": "run",
                "executable": executable,
                "args": tuple(args),
                "cwd": cwd,
                "input_text": input_text,
                "limits": limits,
            }
        )
        if self.responder:
            return self.responder(executable, args, input_text)
        return ExecutionResult(0, "vendor 1.2.3\n", "", 0.01)


class FakeACPExecutor:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[dict[str, object]] = []

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
        self.calls.append(
            {
                "executable": executable,
                "prompt": prompt,
                "cwd": cwd,
                "limits": limits,
                "model": model,
                "effort": effort,
            }
        )
        return ACPResult(self.text, 0.25, "end_turn")


class FakeCodexRunner:
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.calls: list[dict[str, object]] = []

    def invoke(
        self,
        executable: Path,
        *,
        prompt: str,
        schema: dict[str, object],
        model: str | None,
        effort: str | None,
        limits: InvocationLimits,
    ) -> tuple[dict[str, object], float]:
        self.calls.append(
            {
                "executable": executable,
                "prompt": prompt,
                "schema": schema,
                "model": model,
                "effort": effort,
                "limits": limits,
            }
        )
        if self.failure is not None:
            raise self.failure
        return dict(PAYLOAD), 0.2


class FakeCodexAppServerProcess:
    def __init__(self, handler: Callable[[dict[str, object]], None]) -> None:
        self.handler = handler
        self.stdout = self.FakeStdout()
        self.stderr = self.FakeStderr()
        self.stdin = self
        self.killed = False

    class FakeStdout:
        def __init__(self) -> None:
            self.messages: queue.Queue[bytes] = queue.Queue()

        def readline(self) -> bytes:
            return self.messages.get(timeout=2)

        def close(self) -> None:
            return None

        def emit(self, value: dict[str, object]) -> None:
            self.messages.put(json.dumps(value).encode("utf-8") + b"\n")

        def finish(self) -> None:
            self.messages.put(b"")

    class FakeStderr:
        def read(self, _size: int) -> bytes:
            return b""

        def close(self) -> None:
            return None

    def write(self, wire: bytes) -> int:
        self.handler(json.loads(wire))
        return len(wire)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None

    def poll(self) -> int | None:
        return -9 if self.killed else None

    def kill(self) -> None:
        if not self.killed:
            self.killed = True
            self.stdout.finish()

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return -9 if self.killed else 0


def _run_calls(executor: FakeExecutor) -> list[dict[str, object]]:
    return [call for call in executor.calls if call["operation"] == "run"]


def test_runtime_metadata_is_explicitly_separate_from_direct_api_providers() -> None:
    for metadata in (CODEX_METADATA, GROK_METADATA):
        assert metadata.kind is RuntimeKind.SUBSCRIPTION_CLI
        assert metadata.direct_api_provider is False
        assert RuntimeCapability.USER_SESSION_AUTH in metadata.capabilities
        assert "does not read" in metadata.policy_note or "official login" in metadata.policy_note

    assert RuntimeCapability.STRUCTURED_OUTPUT in CODEX_METADATA.capabilities
    assert RuntimeCapability.BEST_EFFORT_STRUCTURED_OUTPUT in GROK_METADATA.capabilities
    assert RuntimeCapability.ACP in GROK_METADATA.capabilities
    assert GROK_METADATA.maturity is RuntimeMaturity.EXPERIMENTAL


@pytest.mark.parametrize(
    ("runtime", "expected_name"),
    [
        (CodexRuntime(executor=FakeExecutor(None)), "OpenAI Codex"),
        (GrokBuildRuntime(executor=FakeExecutor(None)), "xAI Grok Build"),
    ],
)
def test_missing_runtime_has_actionable_status(runtime, expected_name: str) -> None:
    status = runtime.discover()

    assert status.state is RuntimeState.MISSING
    assert not status.available
    assert expected_name in status.message
    assert "official CLI" in status.message

    with pytest.raises(RuntimeUnavailable, match="official CLI"):
        runtime.invoke_structured(StructuredInvocation("Judge this", SCHEMA))


def test_discovery_reports_native_runtime_version() -> None:
    executor = FakeExecutor("C:/Vendor/codex.exe")
    status = CodexRuntime(executor=executor).discover()

    assert status.available
    assert status.version == "vendor 1.2.3"
    assert status.executable == Path("C:/Vendor/codex.exe")


def test_passive_discovery_never_executes_a_found_path_entry() -> None:
    executor = FakeExecutor("C:/Vendor/codex.exe")
    status = CodexRuntime(executor=executor).discover(probe_version=False)

    assert status.available
    assert status.version is None
    assert _run_calls(executor) == []
    assert "not executed" in status.message


def test_catalog_discovery_accepts_injected_adapters() -> None:
    statuses = discover_runtimes(
        [
            CodexRuntime(executor=FakeExecutor("C:/Vendor/codex.exe")),
            GrokBuildRuntime(executor=FakeExecutor(None)),
        ]
    )

    assert [status.state for status in statuses] == [RuntimeState.READY, RuntimeState.MISSING]


def test_shipped_catalog_excludes_claude_subscription_runtime() -> None:
    assert [runtime.metadata.runtime_id for runtime in subscription_runtimes()] == [
        "openai-codex",
        "xai-grok-build",
    ]


def test_catalog_can_passively_discover_without_version_probes() -> None:
    executor = FakeExecutor("C:/Vendor/codex.exe")
    statuses = discover_runtimes(
        [CodexRuntime(executor=executor)], probe_versions=False
    )

    assert statuses[0].available
    assert _run_calls(executor) == []


def test_clean_environment_strips_every_credential_and_keeps_runtime_basics() -> None:
    clean = clean_runtime_environment(
        {
            "PATH": "C:/bin",
            "USERPROFILE": "C:/Users/example",
            "CODEX_HOME": "D:/Profiles/Codex Home",
            "GROK_HOME": "D:/Profiles/Grok Home",
            "XDG_CONFIG_HOME": "D:/Profiles/XDG Config",
            "OPENAI_API_KEY": "openai-secret",
            "CODEX_API_KEY": "codex-secret",
            "CODEX_TOKEN": "codex-token",
            "ANTHROPIC_API_KEY": "anthropic-secret",
            "ANTHROPIC_AUTH_TOKEN": "anthropic-token",
            "XAI_API_KEY": "xai-secret",
            "GROK_API_KEY": "grok-secret",
            "GROK_TOKEN": "grok-token",
            "AWS_SECRET_ACCESS_KEY": "aws-secret",
            "UNRELATED_PASSWORD": "password",
        }
    )

    assert clean["PATH"] == "C:/bin"
    assert clean["USERPROFILE"] == "C:/Users/example"
    assert clean["CODEX_HOME"] == "D:/Profiles/Codex Home"
    assert clean["GROK_HOME"] == "D:/Profiles/Grok Home"
    assert clean["XDG_CONFIG_HOME"] == "D:/Profiles/XDG Config"
    assert clean["NO_COLOR"] == "1"
    assert clean["IONIC_RUNTIME_BOUNDARY"] == "1"
    assert "OPENAI_API_KEY" not in clean
    assert "CODEX_API_KEY" not in clean
    assert "CODEX_TOKEN" not in clean
    assert "XAI_API_KEY" not in clean
    assert "GROK_API_KEY" not in clean
    assert "GROK_TOKEN" not in clean
    assert not any("secret" in value or value == "password" for value in clean.values())


def test_safe_executor_rejects_non_allowlisted_and_shell_wrapper_commands() -> None:
    executor = SafeSubprocessExecutor()

    with pytest.raises(RuntimePolicyError, match="not allowlisted"):
        executor.run(Path("powershell.exe"), ["-Command", "Write-Host nope"])
    with pytest.raises(RuntimePolicyError, match="shell-script"):
        executor.run(Path("grok.cmd"), ["--version"])


def test_safe_executor_rejects_control_characters_before_spawn() -> None:
    executor = SafeSubprocessExecutor()

    with pytest.raises(RuntimePolicyError, match="control character"):
        executor.run(Path("codex.exe"), ["exec", "bad\nargument"])


def test_safe_executor_enforces_output_limit_without_shell() -> None:
    executable = Path(sys.executable)
    executor = SafeSubprocessExecutor(
        allowed_executables=frozenset({executable.name.lower()}),
        environment=os.environ,
    )

    with pytest.raises(RuntimeOutputLimit, match="combined output limit"):
        executor.run(
            executable,
            ["-c", "import sys; sys.stdout.write('x' * 200000)"],
            limits=InvocationLimits(
                timeout_seconds=5,
                max_input_bytes=1024,
                max_output_bytes=1024,
                max_schema_bytes=1024,
            ),
        )


def test_safe_executor_enforces_wall_clock_timeout_without_shell() -> None:
    executable = Path(sys.executable)
    executor = SafeSubprocessExecutor(
        allowed_executables=frozenset({executable.name.lower()}),
        environment=os.environ,
    )

    with pytest.raises(RuntimeTimeout, match="timeout"):
        executor.run(
            executable,
            ["-c", "import time; time.sleep(5)"],
            limits=InvocationLimits(
                timeout_seconds=0.1,
                max_input_bytes=1024,
                max_output_bytes=1024,
                max_schema_bytes=1024,
            ),
        )


def test_codex_forwards_only_validated_prompt_schema_model_and_effort(tmp_path: Path) -> None:
    prompt = "PRIVATE-CONTRACT-CONTENT"
    executor = FakeExecutor("C:/Vendor/codex.exe")
    runner = FakeCodexRunner()
    result = CodexRuntime(executor=executor, runner=runner).invoke_structured(
        StructuredInvocation(
            prompt=prompt,
            schema=SCHEMA,
            model="gpt-5.4",
            effort="high",
            working_directory=tmp_path,
        )
    )

    invocation = runner.calls[0]
    assert invocation["prompt"] == prompt
    assert invocation["schema"] == SCHEMA
    assert invocation["model"] == "gpt-5.4"
    assert invocation["effort"] == "high"
    assert [call["args"] for call in _run_calls(executor)] == [("--version",)]
    assert result.payload == PAYLOAD
    assert result.model == "gpt-5.4"


@pytest.mark.parametrize(
    ("model", "effort", "message"),
    [
        ("bad model", None, "model identifier"),
        (None, "extreme", "reasoning effort"),
    ],
)
def test_codex_rejects_unvalidated_model_controls(
    model: str | None, effort: str | None, message: str
) -> None:
    runtime = CodexRuntime(executor=FakeExecutor("C:/Vendor/codex.exe"))

    with pytest.raises(RuntimePolicyError, match=message):
        runtime.invoke_structured(
            StructuredInvocation(
                prompt="Judge",
                schema=SCHEMA,
                model=model,
                effort=effort,
            )
        )


def test_codex_fails_closed_when_installed_app_server_lacks_restricted_roots() -> None:
    runtime = CodexRuntime(
        executor=FakeExecutor("C:/Vendor/codex.exe"),
        runner=FakeCodexRunner(
            RuntimeUnavailable(
                "version-specific app-server schema does not prove restricted read-only roots"
            )
        ),
    )

    with pytest.raises(RuntimeUnavailable, match="does not prove restricted read-only roots"):
        runtime.invoke_structured(StructuredInvocation("Judge", SCHEMA))


@pytest.mark.parametrize(
    ("account_type", "should_succeed"),
    [("chatgpt", True), ("apiKey", False), (None, False)],
)
def test_codex_account_probe_fails_closed_unless_app_server_reports_chatgpt(
    account_type: str | None,
    should_succeed: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: dict[str, object] = {"requests": []}

    class FakeStdout:
        def __init__(self) -> None:
            self.messages: queue.Queue[bytes] = queue.Queue()

        def readline(self) -> bytes:
            return self.messages.get(timeout=2)

        def close(self) -> None:
            return None

        def emit(self, value: dict[str, object]) -> None:
            self.messages.put(json.dumps(value).encode("utf-8") + b"\n")

        def finish(self) -> None:
            self.messages.put(b"")

    class FakeStderr:
        def read(self, _size: int) -> bytes:
            return b""

        def close(self) -> None:
            return None

    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = FakeStdout()
            self.stderr = FakeStderr()
            self.stdin = self
            self.killed = False

        def write(self, wire: bytes) -> int:
            request = json.loads(wire)
            calls["requests"].append(request)  # type: ignore[union-attr]
            if request["method"] == "initialize":
                self.stdout.emit(
                    {"jsonrpc": "2.0", "id": request["id"], "result": {}}
                )
            elif request["method"] == "account/read":
                account = {"type": account_type} if account_type else None
                self.stdout.emit(
                    {
                        "jsonrpc": "2.0",
                        "id": request["id"],
                        "result": {"account": account},
                    }
                )
            return len(wire)

        def flush(self) -> None:
            return None

        def close(self) -> None:
            return None

        def poll(self) -> int | None:
            return -9 if self.killed else None

        def kill(self) -> None:
            if not self.killed:
                self.killed = True
                self.stdout.finish()

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return -9 if self.killed else 0

    process = FakeProcess()

    def fake_popen(argv: list[str], **options: object) -> FakeProcess:
        calls["argv"] = argv
        calls["options"] = options
        return process

    executable = tmp_path / "codex.exe"
    probe = CodexAppServerAccountProbe(
        environment={"PATH": "C:/bin", "OPENAI_API_KEY": "must-not-leak"},
        profile_directory=tmp_path / "profile",
        popen_factory=fake_popen,
    )

    if should_succeed:
        probe.require_chatgpt(executable, limits=InvocationLimits(timeout_seconds=2))
    else:
        with pytest.raises(RuntimeUnavailable, match="not signed in with ChatGPT"):
            probe.require_chatgpt(executable, limits=InvocationLimits(timeout_seconds=2))

    expected_args = [str(executable), "app-server", "--strict-config"]
    for setting in CODEX_APP_SERVER_CONFIG:
        expected_args.extend(("--config", setting))
    assert calls["argv"] == expected_args
    assert calls["options"]["shell"] is False  # type: ignore[index]
    assert "OPENAI_API_KEY" not in calls["options"]["env"]  # type: ignore[index,operator]
    assert calls["options"]["env"]["CODEX_HOME"] == str(tmp_path / "profile")  # type: ignore[index]
    assert [request["method"] for request in calls["requests"]] == [  # type: ignore[index]
        "initialize",
        "initialized",
        "account/read",
    ]
    assert calls["requests"][0]["params"]["clientInfo"] == {  # type: ignore[index]
        "name": "ionic_cli",
        "title": "Ionic CLI",
        "version": __version__,
    }


def test_codex_runtime_never_executes_when_chatgpt_auth_is_not_confirmed() -> None:
    executor = FakeExecutor("C:/Vendor/codex.exe")
    runtime = CodexRuntime(
        executor=executor,
        runner=FakeCodexRunner(
            RuntimeUnavailable("Codex is not signed in with ChatGPT")
        ),
    )

    with pytest.raises(RuntimeUnavailable, match="not signed in with ChatGPT"):
        runtime.invoke_structured(StructuredInvocation("Judge", SCHEMA))

    assert [call["args"] for call in _run_calls(executor)] == [("--version",)]


def test_codex_app_server_schema_gate_requires_restricted_read_roots() -> None:
    legacy = {
        "definitions": {
            "SandboxPolicy": {
                "oneOf": [
                    {
                        "properties": {
                            "type": {"enum": ["readOnly"]},
                            "networkAccess": {"type": "boolean"},
                        }
                    }
                ]
            }
        }
    }
    supported = {
        "properties": {
            key: {}
            for key in (
                "approvalPolicy",
                "cwd",
                "effort",
                "input",
                "model",
                "outputSchema",
                "sandboxPolicy",
                "threadId",
            )
        },
        "definitions": {
            "SandboxPolicy": {
                "oneOf": [
                    {
                        "properties": {
                            "type": {"enum": ["readOnly"]},
                            "access": {"$ref": "#/definitions/ReadOnlyAccess"},
                        }
                    }
                ]
            },
            "ReadOnlyAccess": {
                "oneOf": [
                    {
                        "properties": {
                            "type": {"enum": ["restricted"]},
                            "includePlatformDefaults": {"type": "boolean"},
                            "readableRoots": {"type": "array"},
                        }
                    }
                ]
            },
        }
    }

    assert _schema_proves_restricted_read_roots(legacy) is False
    assert _schema_proves_restricted_read_roots(supported) is True
    thread_properties = {
        key: {}
        for key in (
            "approvalPolicy",
            "baseInstructions",
            "cwd",
            "developerInstructions",
            "ephemeral",
            "model",
            "sandbox",
        )
    }
    assert _schema_proves_ephemeral_thread({"properties": thread_properties}) is True
    del thread_properties["ephemeral"]
    assert _schema_proves_ephemeral_thread({"properties": thread_properties}) is False


def test_codex_app_server_review_uses_ephemeral_restricted_turn(
    tmp_path: Path,
) -> None:
    requests: list[dict[str, object]] = []
    process: FakeCodexAppServerProcess

    def handler(message: dict[str, object]) -> None:
        requests.append(message)
        method = message.get("method")
        request_id = message.get("id")
        if method == "initialize":
            process.stdout.emit({"id": request_id, "result": {}})
        elif method == "account/read":
            process.stdout.emit(
                {"id": request_id, "result": {"account": {"type": "chatgpt"}}}
            )
        elif method == "thread/start":
            process.stdout.emit(
                {
                    "id": request_id,
                    "result": {
                        "thread": {"id": "thread-1", "ephemeral": True},
                        "instructionSources": [],
                    },
                }
            )
        elif method == "turn/start":
            process.stdout.emit(
                {
                    "id": request_id,
                    "result": {
                        "turn": {"id": "turn-1", "status": "inProgress", "items": []}
                    },
                }
            )
            process.stdout.emit(
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "item": {
                            "id": "item-1",
                            "type": "agentMessage",
                            "phase": "final_answer",
                            "text": json.dumps(PAYLOAD),
                        },
                    },
                }
            )
            process.stdout.emit(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": "thread-1",
                        "turn": {"id": "turn-1", "status": "completed", "items": []},
                    },
                }
            )

    process = FakeCodexAppServerProcess(handler)
    spawn_call: dict[str, object] = {}

    def fake_popen(argv: list[str], **options: object) -> FakeCodexAppServerProcess:
        spawn_call.update({"argv": argv, "options": options})
        return process

    probe_calls: list[dict[str, object]] = []

    def capability_probe(executable: Path, **options: object) -> None:
        probe_calls.append({"executable": executable, **options})

    executable = tmp_path / "codex.exe"
    profile = tmp_path / "profile"
    runner = CodexAppServerRunner(
        environment={
            "PATH": "C:/bin",
            "LOCALAPPDATA": str(tmp_path),
            "OPENAI_API_KEY": "must-not-leak",
        },
        profile_directory=profile,
        popen_factory=fake_popen,
        capability_probe=capability_probe,
    )
    payload, _elapsed = runner.invoke(
        executable,
        prompt="PRIVATE CONTRACT",
        schema=SCHEMA,
        model="gpt-5.6",
        effort="high",
        limits=InvocationLimits(timeout_seconds=2),
    )

    assert payload == PAYLOAD
    assert len(probe_calls) == 1
    assert spawn_call["argv"][0] == str(executable)  # type: ignore[index]
    assert spawn_call["argv"][1:3] == ["app-server", "--strict-config"]  # type: ignore[index]
    options = spawn_call["options"]  # type: ignore[assignment]
    assert options["shell"] is False  # type: ignore[index]
    assert options["env"]["CODEX_HOME"] == str(profile)  # type: ignore[index]
    assert "OPENAI_API_KEY" not in options["env"]  # type: ignore[operator]

    thread_request = next(item for item in requests if item.get("method") == "thread/start")
    thread_params = thread_request["params"]  # type: ignore[assignment]
    assert thread_params["ephemeral"] is True  # type: ignore[index]
    assert thread_params["approvalPolicy"] == "never"  # type: ignore[index]
    assert thread_params["sandbox"] == "read-only"  # type: ignore[index]
    assert thread_params["model"] == "gpt-5.6"  # type: ignore[index]

    turn_request = next(item for item in requests if item.get("method") == "turn/start")
    turn_params = turn_request["params"]  # type: ignore[assignment]
    assert turn_params["input"] == [{"type": "text", "text": "PRIVATE CONTRACT"}]  # type: ignore[index]
    assert turn_params["outputSchema"] == SCHEMA  # type: ignore[index]
    assert turn_params["approvalPolicy"] == "never"  # type: ignore[index]
    assert turn_params["effort"] == "high"  # type: ignore[index]
    assert turn_params["sandboxPolicy"] == {  # type: ignore[index]
        "type": "readOnly",
        "access": {
            "type": "restricted",
            "includePlatformDefaults": True,
            "readableRoots": [turn_params["cwd"]],  # type: ignore[index]
        },
        "networkAccess": False,
    }
    assert "PRIVATE CONTRACT" not in json.dumps(spawn_call["argv"])


def test_codex_dedicated_profile_is_not_the_user_codex_home(tmp_path: Path) -> None:
    profile = ionic_codex_profile_directory(
        {
            "LOCALAPPDATA": str(tmp_path),
            "XDG_DATA_HOME": str(tmp_path),
            "HOME": str(tmp_path),
            "CODEX_HOME": str(tmp_path / "user-codex"),
        }
    )

    if os.name == "nt":
        expected = tmp_path
    elif sys.platform == "darwin":
        expected = tmp_path / "Library" / "Application Support"
    else:
        expected = tmp_path
    assert profile == (expected / "Tactico Technologies" / "Ionic" / "CodexSubscription").resolve()
    assert profile != (tmp_path / "user-codex").resolve()


def test_codex_profile_allows_only_runtime_system_skills(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    (profile / "skills" / ".system").mkdir(parents=True)
    _prepare_codex_profile(profile)

    (profile / "skills" / "user-skill").mkdir()
    with pytest.raises(RuntimePolicyError, match="user-added skills"):
        _prepare_codex_profile(profile)


def test_codex_stops_before_sending_contract_when_instructions_are_loaded(
    tmp_path: Path,
) -> None:
    requests: list[dict[str, object]] = []
    process: FakeCodexAppServerProcess

    def handler(message: dict[str, object]) -> None:
        requests.append(message)
        method = message.get("method")
        if method == "initialize":
            process.stdout.emit({"id": message["id"], "result": {}})
        elif method == "account/read":
            process.stdout.emit(
                {"id": message["id"], "result": {"account": {"type": "chatgpt"}}}
            )
        elif method == "thread/start":
            process.stdout.emit(
                {
                    "id": message["id"],
                    "result": {
                        "thread": {"id": "thread-1"},
                        "instructionSources": ["C:/unexpected/AGENTS.md"],
                    },
                }
            )

    process = FakeCodexAppServerProcess(handler)
    runner = CodexAppServerRunner(
        environment={"LOCALAPPDATA": str(tmp_path)},
        profile_directory=tmp_path / "profile",
        popen_factory=lambda *_args, **_options: process,
        capability_probe=lambda *_args, **_options: None,
    )

    with pytest.raises(RuntimePolicyError, match="external instruction sources"):
        runner.invoke(
            tmp_path / "codex.exe",
            prompt="PRIVATE CONTRACT",
            schema=SCHEMA,
            model=None,
            effort=None,
            limits=InvocationLimits(timeout_seconds=2),
        )

    assert not any(request.get("method") == "turn/start" for request in requests)
    assert "PRIVATE CONTRACT" not in json.dumps(requests)


def test_codex_declines_permission_requests_and_discards_the_result(
    tmp_path: Path,
) -> None:
    requests: list[dict[str, object]] = []
    process: FakeCodexAppServerProcess

    def handler(message: dict[str, object]) -> None:
        requests.append(message)
        method = message.get("method")
        if method == "initialize":
            process.stdout.emit({"id": message["id"], "result": {}})
        elif method == "account/read":
            process.stdout.emit(
                {"id": message["id"], "result": {"account": {"type": "chatgpt"}}}
            )
        elif method == "thread/start":
            process.stdout.emit(
                {
                    "id": message["id"],
                    "result": {
                        "thread": {"id": "thread-1"},
                        "instructionSources": [],
                    },
                }
            )
        elif method == "turn/start":
            process.stdout.emit(
                {
                    "id": message["id"],
                    "result": {"turn": {"id": "turn-1", "status": "inProgress"}},
                }
            )
            process.stdout.emit(
                {
                    "id": 900,
                    "method": "item/permissions/requestApproval",
                    "params": {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "itemId": "item-1",
                        "cwd": str(tmp_path),
                        "permissions": {"network": {"enabled": True}},
                    },
                }
            )
        elif message.get("id") == 900 and "result" in message:
            process.stdout.emit(
                {
                    "method": "turn/completed",
                    "params": {
                        "turn": {"id": "turn-1", "status": "interrupted"}
                    },
                }
            )

    process = FakeCodexAppServerProcess(handler)
    runner = CodexAppServerRunner(
        environment={"LOCALAPPDATA": str(tmp_path)},
        profile_directory=tmp_path / "profile",
        popen_factory=lambda *_args, **_options: process,
        capability_probe=lambda *_args, **_options: None,
    )

    with pytest.raises(RuntimePolicyError, match="prohibited tool or permission"):
        runner.invoke(
            tmp_path / "codex.exe",
            prompt="Judge",
            schema=SCHEMA,
            model=None,
            effort=None,
            limits=InvocationLimits(timeout_seconds=2),
        )

    decline = next(request for request in requests if request.get("id") == 900 and "result" in request)
    assert decline["result"] == {"permissions": {}, "scope": "turn"}


def test_grok_uses_acp_stdin_boundary_and_marks_result_experimental() -> None:
    process = FakeExecutor("C:/Vendor/grok.exe")
    acp = FakeACPExecutor(json.dumps(PAYLOAD))
    runtime = GrokBuildRuntime(executor=process, acp_executor=acp)

    result = runtime.invoke_structured(
        StructuredInvocation(prompt="PRIVATE-GROK-CONTRACT", schema=SCHEMA)
    )

    assert result.payload == PAYLOAD
    assert result.experimental is True
    assert len(acp.calls) == 1
    assert "PRIVATE-GROK-CONTRACT" in acp.calls[0]["prompt"]
    # Process discovery receives only `grok version`; the prompt is sent through ACP.
    assert _run_calls(process)[0]["args"] == ("version",)


def test_grok_forwards_selected_model_and_effort_to_acp() -> None:
    acp = FakeACPExecutor(json.dumps(PAYLOAD))
    runtime = GrokBuildRuntime(
        executor=FakeExecutor("C:/Vendor/grok.exe"),
        acp_executor=acp,
    )

    result = runtime.invoke_structured(
        StructuredInvocation(
            prompt="Judge",
            schema=SCHEMA,
            model="grok-4.5",
            effort="medium",
        )
    )

    assert result.model == "grok-4.5"
    assert acp.calls[0]["model"] == "grok-4.5"
    assert acp.calls[0]["effort"] == "medium"


def test_grok_acp_uses_reported_account_auth_and_exact_model_effort_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: dict[str, object] = {"requests": []}

    class FakeStdout:
        def __init__(self) -> None:
            self.messages: queue.Queue[bytes] = queue.Queue()

        def readline(self) -> bytes:
            return self.messages.get(timeout=2)

        def close(self) -> None:
            return None

        def emit(self, value: dict[str, object]) -> None:
            self.messages.put(json.dumps(value).encode("utf-8") + b"\n")

        def finish(self) -> None:
            self.messages.put(b"")

    class FakeStderr:
        def read(self, _size: int) -> bytes:
            return b""

        def close(self) -> None:
            return None

    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = FakeStdout()
            self.stderr = FakeStderr()
            self.stdin = self
            self.killed = False

        def write(self, wire: bytes) -> int:
            request = json.loads(wire)
            calls["requests"].append(request)  # type: ignore[union-attr]
            method = request["method"]
            if method == "initialize":
                result = {
                    "authMethods": [
                        {"id": "grok.com"},
                        {"id": "xai.api_key"},
                    ]
                }
            elif method == "authenticate":
                assert request["params"]["methodId"] == "grok.com"
                result = {}
            elif method == "session/new":
                result = {"sessionId": "session-1"}
            elif method == "session/prompt":
                self.stdout.emit(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "update": {
                                "sessionUpdate": "agent_message_chunk",
                                "content": {"text": json.dumps(PAYLOAD)},
                            }
                        },
                    }
                )
                result = {"stopReason": "end_turn"}
            else:  # pragma: no cover - protects the test protocol
                raise AssertionError(f"unexpected method {method}")
            self.stdout.emit({"jsonrpc": "2.0", "id": request["id"], "result": result})
            return len(wire)

        def flush(self) -> None:
            return None

        def close(self) -> None:
            return None

        def poll(self) -> int | None:
            return -9 if self.killed else None

        def kill(self) -> None:
            if not self.killed:
                self.killed = True
                self.stdout.finish()

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return -9 if self.killed else 0

    process = FakeProcess()

    def fake_popen(argv: list[str], **options: object) -> FakeProcess:
        calls["argv"] = argv
        calls["options"] = options
        return process

    monkeypatch.setattr("ionic.runtimes.grok.subprocess.Popen", fake_popen)
    executable = tmp_path / "grok.exe"
    result = GrokACPExecutor(
        environment={"PATH": "C:/bin", "XAI_API_KEY": "must-not-leak"}
    ).invoke(
        executable,
        "Judge this",
        cwd=tmp_path,
        limits=InvocationLimits(timeout_seconds=2),
        model="grok-4.5",
        effort="medium",
    )

    assert calls["argv"] == [
        str(executable),
        "--no-auto-update",
        "--no-memory",
        "--no-subagents",
        "--no-plan",
        "--disable-web-search",
        "--permission-mode",
        "dontAsk",
        "--model",
        "grok-4.5",
        "--effort",
        "medium",
        "agent",
        "stdio",
    ]
    assert calls["options"]["shell"] is False  # type: ignore[index]
    assert "XAI_API_KEY" not in calls["options"]["env"]  # type: ignore[index,operator]
    assert [request["method"] for request in calls["requests"]] == [  # type: ignore[index]
        "initialize",
        "authenticate",
        "session/new",
        "session/prompt",
    ]
    assert result.text == json.dumps(PAYLOAD)


def test_structured_boundary_rejects_schema_mismatch() -> None:
    def respond(_executable: Path, args: Sequence[str], _input: str) -> ExecutionResult:
        if list(args) in (["--version"], ["version"]):
            return ExecutionResult(0, "runtime 1", "", 0.01)
        return ExecutionResult(
            0,
            json.dumps({"structured_output": {"assessment": "ok"}}),
            "",
            0.1,
        )

    runtime = GrokBuildRuntime(
        executor=FakeExecutor("C:/Vendor/grok.exe", respond),
        acp_executor=FakeACPExecutor('{"assessment":"ok"}'),
    )
    with pytest.raises(RuntimeOutputError, match="missing required"):
        runtime.invoke_structured(StructuredInvocation("Judge", SCHEMA))


def test_remote_schema_references_are_rejected_without_network() -> None:
    runtime = CodexRuntime(executor=FakeExecutor("C:/Vendor/codex.exe"))

    with pytest.raises(RuntimePolicyError, match="references are disabled"):
        runtime.invoke_structured(
            StructuredInvocation(
                "Judge",
                {
                    "type": "object",
                    "properties": {"value": {"$ref": "https://example.com/schema.json"}},
                },
            )
        )


def test_prompt_size_is_checked_before_runtime_discovery() -> None:
    executor = FakeExecutor("C:/Vendor/codex.exe")
    runtime = CodexRuntime(executor=executor)

    with pytest.raises(RuntimePolicyError, match="input exceeds"):
        runtime.invoke_structured(
            StructuredInvocation(
                prompt="x" * 100,
                schema=SCHEMA,
                limits=InvocationLimits(
                    timeout_seconds=5,
                    max_input_bytes=10,
                    max_output_bytes=1024,
                    max_schema_bytes=1024,
                ),
            )
        )
    assert executor.calls == []
