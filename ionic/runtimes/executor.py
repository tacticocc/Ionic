"""Bounded subprocess execution for explicitly allowlisted vendor CLIs."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Mapping, Protocol, Sequence

from .errors import (
    RuntimeExecutionError,
    RuntimeOutputLimit,
    RuntimePolicyError,
    RuntimeTimeout,
)
from .models import ExecutionResult, InvocationLimits


ALLOWED_RUNTIME_EXECUTABLES = frozenset(
    {"codex", "codex.exe", "grok", "grok.exe"}
)

# These values are profile *locations*, not credentials. Preserving them lets
# an official Codex or Grok process find the session it owns when the user has
# moved its profile out of the platform default. Ionic still never opens those
# directories and does not pass token/key environment variables to the child.
RUNTIME_PROFILE_ENV_NAMES = frozenset(
    {
        "CODEX_HOME",
        "GROK_HOME",
        "XDG_CONFIG_HOME",
    }
)

# Deliberately excludes API keys, bearer tokens, cloud credentials, and generic
# credential variables. Official CLIs may use their own saved login internally;
# Ionic never opens or copies those files.
SAFE_ENV_NAMES = frozenset(
    {
        "APPDATA",
        "COLORTERM",
        "COMSPEC",
        "HOME",
        "LANG",
        "LC_ALL",
        "LOCALAPPDATA",
        "NO_COLOR",
        "PATH",
        "PATHEXT",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TERM",
        "TMP",
        "USERPROFILE",
        "WINDIR",
    }
) | RUNTIME_PROFILE_ENV_NAMES


def clean_runtime_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    source = os.environ if source is None else source
    clean = {name: source[name] for name in SAFE_ENV_NAMES if source.get(name)}
    clean["NO_COLOR"] = "1"
    clean["IONIC_RUNTIME_BOUNDARY"] = "1"
    return clean


class ProcessExecutor(Protocol):
    def locate(self, executable_names: Sequence[str]) -> Path | None: ...

    def run(
        self,
        executable: Path,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        input_text: str = "",
        limits: InvocationLimits | None = None,
    ) -> ExecutionResult: ...


class SafeSubprocessExecutor:
    """Run one approved native executable without a shell.

    Output is drained concurrently and the process is killed as soon as the
    combined stdout/stderr cap is crossed. Prompts are supplied over stdin by
    the adapters, never through a shell command or command-line argument.
    """

    def __init__(
        self,
        *,
        allowed_executables: frozenset[str] = ALLOWED_RUNTIME_EXECUTABLES,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self.allowed_executables = frozenset(name.lower() for name in allowed_executables)
        self.environment = clean_runtime_environment(environment)

    def locate(self, executable_names: Sequence[str]) -> Path | None:
        for name in executable_names:
            if name.lower() not in self.allowed_executables:
                continue
            found = shutil.which(name, path=self.environment.get("PATH"))
            if not found:
                continue
            path = Path(found)
            try:
                self._validate_executable(path)
            except RuntimePolicyError:
                continue
            return path
        return None

    def run(
        self,
        executable: Path,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        input_text: str = "",
        limits: InvocationLimits | None = None,
    ) -> ExecutionResult:
        limits = limits or InvocationLimits()
        executable = Path(executable)
        self._validate_executable(executable)
        argv = [str(executable), *self._validate_args(args)]
        input_bytes = input_text.encode("utf-8")
        if len(input_bytes) > limits.max_input_bytes:
            raise RuntimePolicyError(
                f"runtime input exceeds the {limits.max_input_bytes}-byte policy limit"
            )
        resolved_cwd = self._validate_cwd(cwd)

        creationflags = 0
        if os.name == "nt":  # pragma: no branch - platform-specific constant
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        started = time.monotonic()
        try:
            process = subprocess.Popen(
                argv,
                cwd=str(resolved_cwd) if resolved_cwd else None,
                env=dict(self.environment),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                close_fds=True,
                creationflags=creationflags,
            )
        except OSError as exc:
            raise RuntimeExecutionError(
                f"could not start approved runtime executable {executable.name}: {exc}"
            ) from exc

        stdout = bytearray()
        stderr = bytearray()
        lock = threading.Lock()
        exceeded = threading.Event()

        def drain(stream: object, destination: bytearray) -> None:
            reader = stream
            try:
                while True:
                    chunk = reader.read(65536)  # type: ignore[attr-defined]
                    if not chunk:
                        return
                    with lock:
                        remaining = limits.max_output_bytes - len(stdout) - len(stderr)
                        if remaining <= 0:
                            exceeded.set()
                        else:
                            destination.extend(chunk[:remaining])
                            if len(chunk) > remaining:
                                exceeded.set()
                    if exceeded.is_set():
                        try:
                            process.kill()
                        except OSError:
                            pass
                        return
            finally:
                try:
                    reader.close()  # type: ignore[attr-defined]
                except OSError:
                    pass

        threads = [
            threading.Thread(target=drain, args=(process.stdout, stdout), daemon=True),
            threading.Thread(target=drain, args=(process.stderr, stderr), daemon=True),
        ]
        for thread in threads:
            thread.start()

        def feed_stdin() -> None:
            assert process.stdin is not None
            try:
                process.stdin.write(input_bytes)
            except (BrokenPipeError, OSError):
                pass
            finally:
                try:
                    process.stdin.close()
                except OSError:
                    pass

        writer = threading.Thread(target=feed_stdin, daemon=True)
        writer.start()

        try:
            process.wait(timeout=limits.timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.wait()
            raise RuntimeTimeout(
                f"runtime exceeded the {limits.timeout_seconds:g}-second timeout"
            ) from exc
        finally:
            writer.join(timeout=2)
            for thread in threads:
                thread.join(timeout=2)

        if exceeded.is_set():
            raise RuntimeOutputLimit(
                f"runtime exceeded the {limits.max_output_bytes}-byte combined output limit"
            )
        return ExecutionResult(
            returncode=process.returncode,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
            elapsed_seconds=time.monotonic() - started,
        )

    def _validate_executable(self, executable: Path) -> None:
        name = executable.name.lower()
        if executable.suffix.lower() in {".cmd", ".bat", ".ps1"}:
            raise RuntimePolicyError(
                "shell-script runtime wrappers are disabled; install the vendor's native executable"
            )
        if name not in self.allowed_executables:
            raise RuntimePolicyError(f"runtime executable {executable.name!r} is not allowlisted")
        if "\x00" in str(executable) or "\n" in str(executable) or "\r" in str(executable):
            raise RuntimePolicyError("runtime executable path contains invalid characters")

    @staticmethod
    def _validate_args(args: Sequence[str]) -> list[str]:
        result: list[str] = []
        for arg in args:
            if not isinstance(arg, str):
                raise RuntimePolicyError("runtime arguments must be strings")
            if "\x00" in arg or "\r" in arg or "\n" in arg:
                raise RuntimePolicyError("runtime argument contains an invalid control character")
            if len(arg.encode("utf-8")) > 128 * 1024:
                raise RuntimePolicyError("runtime argument exceeds the 131072-byte policy limit")
            result.append(arg)
        return result

    @staticmethod
    def _validate_cwd(cwd: Path | None) -> Path | None:
        if cwd is None:
            return None
        path = Path(cwd).resolve()
        if not path.is_dir():
            raise RuntimePolicyError("runtime working_directory must be an existing directory")
        return path
