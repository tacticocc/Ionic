"""Build the self-contained Ionic CLI sidecar used by Ionic Desktop.

The output layout intentionally matches electron-builder's ``${os}-${arch}``
macros::

    desktop/build/essential/cli/win-x64/ionic/ionic.exe
    desktop/build/essential/cli/mac-arm64/ionic/ionic
    desktop/build/essential/cli/linux-x64/ionic/ionic

Run this script with the Python environment that contains Ionic's ``dev``
dependencies. PyInstaller builds for the current operating system and CPU;
it does not cross-compile native executables.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import importlib.metadata as metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


DESKTOP_PROTOCOL = 4
DESKTOP_EDITION = "essential"
DESKTOP_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = DESKTOP_DIR.parent
ENTRYPOINT = DESKTOP_DIR / "cli" / "entrypoint.py"
BUILD_CONSTRAINTS = REPO_ROOT / "requirements" / "desktop-build-constraints.txt"

# A reviewed build interpreter may be shared by the two edition worktrees and
# may contain either checkout as an editable install.  Always resolve Ionic's
# source/version from the checkout being built, never from that ambient editable
# pointer.  PyInstaller receives the same repository root explicitly below.
sys.path.insert(0, str(REPO_ROOT))

PLATFORM_NAMES = {
    "win32": "win",
    "darwin": "mac",
    "linux": "linux",
}

ARCH_NAMES = {
    "amd64": "x64",
    "x86_64": "x64",
    "x64": "x64",
    "arm64": "arm64",
    "aarch64": "arm64",
    "i386": "ia32",
    "i686": "ia32",
    "x86": "ia32",
}

REQUIRED_MODULES = {
    "PyInstaller": "pyinstaller",
    "pydantic": "pydantic",
    "typer": "typer",
    "rich": "rich",
    "yaml": "PyYAML",
    "mcp": "mcp",
    "anthropic": "anthropic",
    "httpx": "httpx",
    "packaging": "packaging",
}

DIRECT_RUNTIME_DISTRIBUTIONS = (
    "pydantic",
    "typer",
    "rich",
    "PyYAML",
    "mcp",
    "anthropic",
    "httpx",
)
BUILD_DISTRIBUTIONS = ("pyinstaller",)
LICENSE_PREFIXES = ("license", "copying", "notice")
PYINSTALLER_LICENSE_LABEL = "GPL-2.0-or-later with PyInstaller Bootloader Exception"
FINAL_TOC_NAMES = ("PYZ-00.toc", "PKG-00.toc", "COLLECT-00.toc")
MAX_FINAL_TOC_BYTES = 64 * 1024 * 1024
MAX_LICENSE_FILE_BYTES = 2 * 1024 * 1024


def _target_tuple() -> tuple[str, str]:
    try:
        os_name = PLATFORM_NAMES[sys.platform]
    except KeyError as exc:
        raise SystemExit(f"Unsupported sidecar build platform: {sys.platform}") from exc

    machine = platform.machine().lower()
    try:
        arch = ARCH_NAMES[machine]
    except KeyError as exc:
        raise SystemExit(f"Unsupported sidecar build architecture: {machine or 'unknown'}") from exc
    return os_name, arch


def _check_environment() -> None:
    if sys.version_info < (3, 11):
        raise SystemExit(
            f"Ionic Desktop sidecars require Python 3.11+; current interpreter is {sys.version.split()[0]}."
        )

    missing = [
        label for module, label in REQUIRED_MODULES.items() if importlib.util.find_spec(module) is None
    ]
    if missing:
        joined = ", ".join(missing)
        raise SystemExit(
            "Missing sidecar build dependencies: "
            f"{joined}.\nInstall them into this interpreter with:\n"
            f'  cd "{REPO_ROOT}"\n'
            f'  "{sys.executable}" -m pip install -e ".[dev]"'
        )

    if not ENTRYPOINT.is_file():
        raise SystemExit(f"Sidecar entry point is missing: {ENTRYPOINT}")

    _verify_locked_environment()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalise_distribution_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _locked_versions(path: Path = BUILD_CONSTRAINTS) -> dict[str, str]:
    if not path.is_file():
        raise SystemExit(f"Desktop build constraints are missing: {path}")
    locked: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, version = line.partition("==")
        if (
            not separator
            or not re.fullmatch(r"[A-Za-z0-9_.-]+", name)
            or not version
            or any(character.isspace() for character in version)
        ):
            raise SystemExit(
                f"Desktop build constraint line {number} must be an exact name==version pin."
            )
        normalized = _normalise_distribution_name(name)
        if normalized in locked:
            raise SystemExit(f"Desktop build constraints repeat distribution {name!r}.")
        locked[normalized] = version
    if not locked:
        raise SystemExit("Desktop build constraints contain no exact pins.")
    return locked


def _verify_locked_environment(
    path: Path = BUILD_CONSTRAINTS,
    *,
    installed_distributions: list[metadata.Distribution] | None = None,
) -> str:
    """Fail before freezing if the build environment differs from the reviewed lock."""

    locked = _locked_versions(path)
    distributions = list(
        metadata.distributions()
        if installed_distributions is None
        else installed_distributions
    )
    ignored = {"ionic-contracts", "pip", "wheel"}
    mismatches: list[str] = []
    unexpected: list[str] = []
    for distribution in distributions:
        name = distribution.metadata.get("Name")
        if not name:
            raise SystemExit("Installed Python distribution metadata is missing its name.")
        normalized = _normalise_distribution_name(name)
        if normalized in ignored:
            continue
        expected = locked.get(normalized)
        if expected is None:
            unexpected.append(f"{name}=={distribution.version}")
        elif distribution.version != expected:
            mismatches.append(f"{name}: installed {distribution.version}, locked {expected}")
    if mismatches or unexpected:
        detail = "; ".join(
            [
                *(f"version mismatch {item}" for item in sorted(mismatches)),
                *(f"not in lock {item}" for item in sorted(unexpected)),
            ]
        )
        raise SystemExit(
            "Desktop sidecar build environment does not match "
            f"{path.name}: {detail}"
        )
    return _sha256(path)


def _license_expression(distribution: metadata.Distribution) -> str | None:
    value = distribution.metadata.get("License-Expression") or distribution.metadata.get("License")
    if value and value.strip() and value.strip().upper() != "UNKNOWN":
        return " ".join(value.split())
    for classifier in distribution.metadata.get_all("Classifier", []):
        prefix = "License :: OSI Approved :: "
        if classifier.startswith(prefix):
            return classifier.removeprefix(prefix).strip()
    return None


def _homepage(distribution: metadata.Distribution) -> str | None:
    urls: dict[str, str] = {}
    for raw in distribution.metadata.get_all("Project-URL", []):
        label, separator, url = raw.partition(",")
        if separator and url.strip():
            urls[label.strip().lower()] = url.strip()
    for label in ("homepage", "source", "repository", "documentation"):
        if label in urls:
            return urls[label]
    value = distribution.metadata.get("Home-page")
    return value.strip() if value and value.strip() else None


def _resolved_path(value: str | os.PathLike[str]) -> str:
    return os.path.normcase(os.path.realpath(os.fspath(value)))


def _is_within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath((path, root)) == root
    except ValueError:
        return False


def _read_final_toc(path: Path) -> object:
    if not path.is_file():
        raise SystemExit(f"PyInstaller did not produce the required final bundle table: {path}")
    size = path.stat().st_size
    if size <= 0 or size > MAX_FINAL_TOC_BYTES:
        raise SystemExit(f"PyInstaller final bundle table has an invalid size: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise SystemExit(f"Could not read PyInstaller final bundle table {path}: {exc}") from exc
    try:
        value = ast.literal_eval(text)
    except (MemoryError, RecursionError, SyntaxError, ValueError) as exc:
        raise SystemExit(f"PyInstaller final bundle table is invalid: {path}") from exc
    if not isinstance(value, (list, tuple, dict)):
        raise SystemExit(f"PyInstaller final bundle table has an invalid root value: {path}")
    return value


def _toc_strings(value: object) -> list[str]:
    strings: list[str] = []
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, str):
            strings.append(current)
        elif isinstance(current, dict):
            pending.extend(current.keys())
            pending.extend(current.values())
        elif isinstance(current, (list, tuple)):
            pending.extend(current)
    return strings


def _distribution_ownership(
    distributions: list[metadata.Distribution],
) -> tuple[dict[str, metadata.Distribution], set[str]]:
    ownership: dict[str, metadata.Distribution] = {}
    roots: set[str] = set()
    for distribution in distributions:
        name = distribution.metadata.get("Name")
        if not name:
            raise SystemExit("Installed Python distribution metadata is missing its name.")
        root = _resolved_path(distribution.locate_file(""))
        roots.add(root)
        for item in distribution.files or ():
            owned_path = _resolved_path(distribution.locate_file(item))
            previous = ownership.get(owned_path)
            if previous is not None and previous is not distribution:
                previous_name = previous.metadata.get("Name") or "unknown"
                raise SystemExit(
                    "Installed Python distributions claim the same file: "
                    f"{previous_name}, {name}: {owned_path}"
                )
            ownership[owned_path] = distribution
    return ownership, roots


def _collected_distributions(
    toc_paths: tuple[Path, ...] | list[Path],
    *,
    installed_distributions: list[metadata.Distribution] | None = None,
) -> list[tuple[metadata.Distribution, str]]:
    """Return only distributions whose files occur in PyInstaller's final bundle.

    Dependency metadata is not a reliable bundle manifest: PyInstaller hooks can
    add optional packages and runtime helpers, while declared dependencies can be
    absent. PYZ, PKG, and COLLECT are the final archive/output tables, so their
    source origins are the authority for what is actually distributed.
    """

    distributions = list(
        metadata.distributions()
        if installed_distributions is None
        else installed_distributions
    )
    ownership, distribution_roots = _distribution_ownership(distributions)
    repository_root = _resolved_path(REPO_ROOT)
    collected: dict[str, metadata.Distribution] = {}
    unmapped: set[str] = set()

    for toc_path in toc_paths:
        for raw in _toc_strings(_read_final_toc(toc_path)):
            if not os.path.isabs(raw):
                continue
            origin = _resolved_path(raw)
            if not os.path.isfile(origin):
                continue
            distribution = ownership.get(origin)
            if distribution is not None:
                name = distribution.metadata.get("Name") or "unknown"
                normalised = _normalise_distribution_name(name)
                # Ionic's own sources are already covered by the repository's
                # MIT notice. An editable install may expose those files through
                # importlib.metadata, but that must not turn the application
                # itself into a third-party component. Other owned files inside
                # a repository-local virtualenv are still shipped dependencies.
                if normalised == "ionic" and _is_within(origin, repository_root):
                    continue
                previous = collected.get(normalised)
                if previous is not None and previous is not distribution:
                    raise SystemExit(
                        f"Collected distribution name is ambiguous after normalization: {name}"
                    )
                collected[normalised] = distribution
                continue
            if any(_is_within(origin, root) for root in distribution_roots):
                unmapped.add(origin)

    if unmapped:
        examples = ", ".join(sorted(unmapped)[:5])
        suffix = "" if len(unmapped) <= 5 else f" (and {len(unmapped) - 5} more)"
        raise SystemExit(
            "Cannot identify the installed distribution that owns collected sidecar files: "
            f"{examples}{suffix}"
        )
    if not collected:
        raise SystemExit("PyInstaller's final bundle tables contain no owned Python distributions.")

    direct = {_normalise_distribution_name(name) for name in DIRECT_RUNTIME_DISTRIBUTIONS}
    build = {_normalise_distribution_name(name) for name in BUILD_DISTRIBUTIONS}
    result: list[tuple[metadata.Distribution, str]] = []
    for normalised in sorted(collected):
        if normalised in direct:
            role = "direct"
        elif normalised in build:
            role = "build-tool"
        else:
            role = "transitive"
        result.append((collected[normalised], role))
    return result


def _copy_distribution_licenses(
    distribution: metadata.Distribution, destination: Path
) -> list[str]:
    copied: list[str] = []
    seen_names: set[str] = set()
    declared = {
        value.strip().replace("\\", "/").lower().lstrip("./")
        for value in distribution.metadata.get_all("License-File", [])
        if value.strip()
    }
    for item in sorted(distribution.files or [], key=lambda value: str(value).lower()):
        item_name = str(item).replace("\\", "/").lower().lstrip("./")
        source_name = Path(str(item)).name
        declared_match = any(
            item_name == value
            or item_name.endswith(f"/{value}")
            or item_name.endswith(f"/licenses/{value}")
            for value in declared
        )
        if not declared_match and not source_name.lower().startswith(LICENSE_PREFIXES):
            continue
        source = Path(distribution.locate_file(item))
        if not source.is_file():
            continue
        try:
            size = source.stat().st_size
            if size <= 0 or size > MAX_LICENSE_FILE_BYTES:
                raise SystemExit(f"License document has an invalid size: {source}")
            data = source.read_bytes()
            data.decode("utf-8")
        except UnicodeError as exc:
            raise SystemExit(f"License document is not valid UTF-8 text: {source}") from exc
        except OSError as exc:
            raise SystemExit(f"Could not read license document {source}: {exc}") from exc
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", source_name)
        candidate = safe_name
        counter = 2
        while candidate.lower() in seen_names:
            candidate = f"{Path(safe_name).stem}-{counter}{Path(safe_name).suffix}"
            counter += 1
        seen_names.add(candidate.lower())
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / candidate
        target.write_bytes(data)
        copied.append(candidate)
    return copied


def _build_license_inventory(
    distributions: list[tuple[metadata.Distribution, str]],
) -> Path:
    licenses_root = DESKTOP_DIR / "build" / DESKTOP_EDITION / "legal" / "licenses"
    if licenses_root.exists():
        shutil.rmtree(licenses_root)
    licenses_root.mkdir(parents=True)

    index: list[dict[str, object]] = []
    missing_documents: list[str] = []
    for distribution, role in distributions:
        name = distribution.metadata.get("Name") or "unknown"
        version = distribution.version
        directory_name = re.sub(r"[^A-Za-z0-9._-]", "_", f"{name}-{version}")
        copied = _copy_distribution_licenses(distribution, licenses_root / directory_name)
        expression = _license_expression(distribution)
        if not copied:
            missing_documents.append(f"{name} {version}")
        if _normalise_distribution_name(name) == "pyinstaller":
            pyinstaller_terms = "\n".join(
                (licenses_root / directory_name / filename).read_text(encoding="utf-8")
                for filename in copied
            )
            if "Bootloader Exception" not in pyinstaller_terms:
                raise SystemExit(
                    "Cannot package PyInstaller without its exact Bootloader Exception terms."
                )
            expression = PYINSTALLER_LICENSE_LABEL
        index.append(
            {
                "name": name,
                "version": version,
                "role": role,
                "license_expression": expression,
                "homepage": _homepage(distribution),
                "license_files": [f"{directory_name}/{filename}" for filename in copied],
            }
        )

    if missing_documents:
        raise SystemExit(
            "Cannot package the sidecar because collected distributions lack exact license "
            f"or notice text: {', '.join(missing_documents)}"
        )

    python_license = None
    for candidate in (
        Path(sys.base_prefix) / "LICENSE.txt",
        Path(sys.base_prefix) / "LICENSE",
        Path(sys.prefix) / "LICENSE.txt",
        Path(sys.prefix) / "LICENSE",
    ):
        if candidate.is_file():
            runtime_dir = licenses_root / f"Python-{platform.python_version()}"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            target = runtime_dir / candidate.name
            shutil.copyfile(candidate, target)
            python_license = str(target.relative_to(licenses_root)).replace("\\", "/")
            break
    if not python_license:
        raise SystemExit(
            "Cannot package the embedded Python runtime because its license file was not found."
        )

    index_path = licenses_root / "index.json"
    index_path.write_text(
        json.dumps(
            {
                "generated_by": "desktop/scripts/build_cli.py",
                "edition": DESKTOP_EDITION,
                "python": platform.python_version(),
                "python_license": python_license,
                "distributions": index,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return index_path


def _verify_executable(executable: Path, version: str) -> None:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["NO_COLOR"] = "1"
    with tempfile.TemporaryDirectory(prefix="ionic-sidecar-smoke-") as temp_dir:
        env["IONIC_REGISTRY"] = str(Path(temp_dir) / "registry.db")
        result = subprocess.run(
            [str(executable), "status", "--json"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            check=False,
        )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
        raise SystemExit(f"Built sidecar failed its status handshake: {detail}")
    try:
        status = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Built sidecar returned invalid status JSON: {exc}") from exc
    if status.get("version") != version or status.get("desktop_protocol") != DESKTOP_PROTOCOL:
        raise SystemExit(
            "Built sidecar returned an incompatible handshake: "
            f"version={status.get('version')!r}, desktop_protocol={status.get('desktop_protocol')!r}"
        )


def main() -> None:
    _check_environment()
    constraints_sha256 = _sha256(BUILD_CONSTRAINTS)

    # Import only after the explicit dependency check so a missing package
    # produces one concise, actionable error instead of a traceback.
    from PyInstaller.__main__ import run as run_pyinstaller
    from ionic import __version__

    os_name, arch = _target_tuple()
    target_root = DESKTOP_DIR / "build" / DESKTOP_EDITION / "cli" / f"{os_name}-{arch}"
    work_root = DESKTOP_DIR / ".pyinstaller" / DESKTOP_EDITION / f"{os_name}-{arch}"
    executable_name = "ionic.exe" if os_name == "win" else "ionic"
    (work_root / "spec").mkdir(parents=True, exist_ok=True)

    args = [
        str(ENTRYPOINT),
        "--name=ionic",
        "--onedir",
        "--noconfirm",
        "--clean",
        f"--distpath={target_root}",
        f"--workpath={work_root / 'work'}",
        f"--specpath={work_root / 'spec'}",
        f"--paths={REPO_ROOT}",
        "--hidden-import=ionic.mcp_server",
        "--collect-submodules=mcp",
        "--collect-submodules=anthropic",
        "--collect-submodules=httpx",
        "--copy-metadata=mcp",
        "--copy-metadata=anthropic",
    ]

    print(f"Building Ionic CLI sidecar for {os_name}-{arch} with {sys.executable}")
    run_pyinstaller(args)

    bundle_dir = target_root / "ionic"
    executable = bundle_dir / executable_name
    if not executable.is_file():
        raise SystemExit(f"PyInstaller completed without producing {executable}")

    _verify_executable(executable, __version__)
    final_tocs = tuple(work_root / "work" / "ionic" / name for name in FINAL_TOC_NAMES)
    shipped_distributions = _collected_distributions(final_tocs)
    license_index = _build_license_inventory(shipped_distributions)
    stat = executable.stat()
    manifest = {
        "edition": DESKTOP_EDITION,
        "version": __version__,
        "desktop_protocol": DESKTOP_PROTOCOL,
        "platform": os_name,
        "arch": arch,
        "build_constraints_sha256": constraints_sha256,
        "executable": {
            "name": executable_name,
            "sha256": _sha256(executable),
            "size": stat.st_size,
        },
    }
    manifest_path = bundle_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(f"Sidecar ready: {executable}")
    print(f"Manifest:      {manifest_path}")
    print(f"License index: {license_index}")


if __name__ == "__main__":
    main()
