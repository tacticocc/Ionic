"""Shared discovery and structured-boundary helpers."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path

from .errors import RuntimeAdapterError, RuntimeExecutionError, RuntimePolicyError, RuntimeUnavailable
from .executor import ProcessExecutor, SafeSubprocessExecutor
from .models import (
    InvocationLimits,
    RuntimeMetadata,
    RuntimeState,
    RuntimeStatus,
    StructuredInvocation,
    StructuredRuntimeResult,
)
from .schema import serialize_schema


class RuntimeAdapter(ABC):
    metadata: RuntimeMetadata
    version_args: tuple[str, ...] = ("--version",)

    def __init__(self, *, executor: ProcessExecutor | None = None) -> None:
        self.executor = executor or SafeSubprocessExecutor()

    def discover(self, *, probe_version: bool = True) -> RuntimeStatus:
        executable = self.executor.locate(self.metadata.executable_names)
        if executable is None:
            names = ", ".join(self.metadata.executable_names)
            return RuntimeStatus(
                metadata=self.metadata,
                state=RuntimeState.MISSING,
                message=(
                    f"{self.metadata.display_name} is not installed as a native executable "
                    f"({names}). Install the official CLI, sign in there, then retry."
                ),
            )
        if not probe_version:
            return RuntimeStatus(
                metadata=self.metadata,
                state=RuntimeState.READY,
                executable=executable,
                message=(
                    f"{self.metadata.display_name} was found. Ionic has not executed it or "
                    "inspected its authentication; both happen only after an explicit invocation."
                ),
            )
        try:
            probe = self.executor.run(
                executable,
                self.version_args,
                limits=InvocationLimits(
                    timeout_seconds=5,
                    max_input_bytes=1,
                    max_output_bytes=64 * 1024,
                    max_schema_bytes=1024,
                ),
            )
        except (RuntimeExecutionError, RuntimePolicyError) as exc:
            return RuntimeStatus(
                metadata=self.metadata,
                state=RuntimeState.UNAVAILABLE,
                executable=executable,
                message=f"Found {self.metadata.display_name}, but version probing failed: {exc}",
            )
        version_text = (probe.stdout or probe.stderr).strip()
        version = _first_line(version_text) if probe.returncode == 0 else None
        message = (
            self.metadata.policy_note
            if probe.returncode == 0
            else "The native executable was found, but its version command failed."
        )
        return RuntimeStatus(
            metadata=self.metadata,
            state=RuntimeState.READY,
            executable=executable,
            version=version,
            message=message,
        )

    def _require_executable(self) -> Path:
        status = self.discover(probe_version=True)
        if not status.available or status.executable is None:
            raise RuntimeUnavailable(status.message)
        return status.executable

    @staticmethod
    def prepare_request(request: StructuredInvocation) -> tuple[str, str]:
        if not isinstance(request.prompt, str) or not request.prompt.strip():
            raise RuntimePolicyError("runtime prompt must be a non-empty string")
        prompt = request.prompt.strip()
        if request.system_prompt.strip():
            prompt = request.system_prompt.strip() + "\n\n" + prompt
        if len(prompt.encode("utf-8")) > request.limits.max_input_bytes:
            raise RuntimePolicyError(
                f"runtime input exceeds the {request.limits.max_input_bytes}-byte policy limit"
            )
        if request.model is not None:
            model = request.model.strip()
            if not model or len(model) > 256 or re.search(r"[\x00\r\n]", model):
                raise RuntimePolicyError("runtime model identifier is invalid")
        if request.working_directory is not None and not Path(
            request.working_directory
        ).resolve().is_dir():
            raise RuntimePolicyError("runtime working_directory must be an existing directory")
        schema_json = serialize_schema(request.schema, request.limits.max_schema_bytes)
        return prompt, schema_json

    @abstractmethod
    def invoke_structured(
        self, request: StructuredInvocation
    ) -> StructuredRuntimeResult: ...


def _first_line(value: str) -> str | None:
    if not value:
        return None
    return value.splitlines()[0][:256]


def explain_failure(display_name: str, stderr: str, returncode: int) -> RuntimeAdapterError:
    detail = stderr.strip().replace("\x00", "")[:1000]
    lowered = detail.lower()
    if any(word in lowered for word in ("login", "authenticate", "unauthorized", "401")):
        return RuntimeUnavailable(
            f"{display_name} is not signed in. Use the official CLI's login command; "
            "Ionic will not import or copy its tokens."
        )
    suffix = f": {detail}" if detail else ""
    return RuntimeExecutionError(
        f"{display_name} exited with code {returncode}{suffix}"
    )
