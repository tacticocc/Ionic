from __future__ import annotations

import importlib.util
from importlib import metadata
import json
from email.message import Message
from pathlib import Path

import pytest
from packaging.markers import default_environment
from packaging.requirements import Requirement


REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = REPO_ROOT / "desktop" / "scripts" / "build_cli.py"


def _load_build_module():
    spec = importlib.util.spec_from_file_location("ionic_desktop_build_cli", BUILD_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_cli = _load_build_module()


class FakeDistribution:
    def __init__(
        self,
        root: Path,
        name: str,
        files: list[str],
        *,
        version: str = "1.0.0",
        expression: str = "MIT",
    ) -> None:
        self._root = root
        self.files = [Path(item) for item in files]
        self.version = version
        message = Message()
        message["Name"] = name
        message["License-Expression"] = expression
        self.metadata = message

    def locate_file(self, item) -> Path:
        return self._root / Path(item)


def _write(root: Path, relative: str, data: bytes = b"module\n") -> Path:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def _write_toc(path: Path, value: object) -> Path:
    path.write_text(repr(value), encoding="utf-8")
    return path


def test_final_tocs_select_only_distributions_that_ship(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    site = tmp_path / "venv" / "site-packages"
    pytest_file = _write(site, "_pytest/outcomes.py")
    cffi_file = _write(site, "_cffi_backend.pyd", b"native")
    hook_file = _write(site, "_pyinstaller_hooks_contrib/rthooks/runtime.py")
    loader_file = _write(site, "PyInstaller/loader/bootstrap.py")
    _write(site, "unused/__init__.py")
    stdlib_file = _write(tmp_path / "python", "Lib/os.py")
    repo_file = _write(tmp_path / "repo", "ionic/cli.py")

    distributions = [
        FakeDistribution(site, "pytest", ["_pytest/outcomes.py"]),
        FakeDistribution(site, "cffi", ["_cffi_backend.pyd"]),
        FakeDistribution(
            site,
            "pyinstaller-hooks-contrib",
            ["_pyinstaller_hooks_contrib/rthooks/runtime.py"],
        ),
        FakeDistribution(site, "pyinstaller", ["PyInstaller/loader/bootstrap.py"]),
        FakeDistribution(site, "unused", ["unused/__init__.py"]),
        FakeDistribution(tmp_path / "repo", "Ionic", ["ionic/cli.py"]),
    ]
    tocs = [
        _write_toc(
            tmp_path / "PYZ-00.toc",
            ("archive.pyz", [("_pytest.outcomes", str(pytest_file), "PYMODULE")]),
        ),
        _write_toc(
            tmp_path / "PKG-00.toc",
            (
                "archive.pkg",
                {},
                [
                    ("runtime", str(hook_file), "PYSOURCE"),
                    ("bootstrap", str(loader_file), "PYSOURCE"),
                    ("stdlib", str(stdlib_file), "PYMODULE"),
                    ("ionic", str(repo_file), "PYMODULE"),
                ],
            ),
        ),
        _write_toc(
            tmp_path / "COLLECT-00.toc",
            ([("_cffi_backend.pyd", str(cffi_file), "EXTENSION")],),
        ),
    ]
    monkeypatch.setattr(build_cli, "REPO_ROOT", tmp_path / "repo")

    collected = build_cli._collected_distributions(
        tocs, installed_distributions=distributions
    )

    assert [(item.metadata["Name"], role) for item, role in collected] == [
        ("cffi", "transitive"),
        ("pyinstaller", "build-tool"),
        ("pyinstaller-hooks-contrib", "transitive"),
        ("pytest", "transitive"),
    ]


def test_final_tocs_fail_closed_for_unowned_site_package_file(tmp_path: Path):
    site = tmp_path / "venv" / "site-packages"
    owned = _write(site, "known/__init__.py")
    rogue = _write(site, "rogue/module.py")
    distribution = FakeDistribution(site, "known", ["known/__init__.py"])
    toc = _write_toc(
        tmp_path / "PYZ-00.toc",
        (
            "archive.pyz",
            [
                ("known", str(owned), "PYMODULE"),
                ("rogue.module", str(rogue), "PYMODULE"),
            ],
        ),
    )

    with pytest.raises(SystemExit, match="Cannot identify.*rogue"):
        build_cli._collected_distributions(
            [toc], installed_distributions=[distribution]
        )


def test_final_toc_parser_never_executes_input(tmp_path: Path):
    marker = tmp_path / "executed"
    toc = tmp_path / "PYZ-00.toc"
    toc.write_text(
        f"__import__('pathlib').Path({str(marker)!r}).write_text('bad')",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="final bundle table is invalid"):
        build_cli._read_final_toc(toc)
    assert not marker.exists()


def test_license_copy_requires_exact_nonempty_utf8_text(tmp_path: Path):
    site = tmp_path / "site-packages"
    valid = _write(site, "valid-1.0.dist-info/licenses/LICENSE", b"Exact terms\n")
    authors = _write(site, "valid-1.0.dist-info/licenses/AUTHORS", b"Exact authors\n")
    valid_distribution = FakeDistribution(
        site, "valid", [str(valid.relative_to(site)), str(authors.relative_to(site))]
    )
    valid_distribution.metadata["License-File"] = "AUTHORS"
    destination = tmp_path / "legal"

    assert build_cli._copy_distribution_licenses(valid_distribution, destination) == [
        "AUTHORS",
        "LICENSE",
    ]
    assert (destination / "LICENSE").read_bytes() == b"Exact terms\n"
    assert (destination / "AUTHORS").read_bytes() == b"Exact authors\n"

    invalid = _write(site, "invalid-1.0.dist-info/licenses/LICENSE", b"\xff\xfe")
    invalid_distribution = FakeDistribution(
        site, "invalid", [str(invalid.relative_to(site))]
    )
    with pytest.raises(SystemExit, match="not valid UTF-8"):
        build_cli._copy_distribution_licenses(
            invalid_distribution, tmp_path / "invalid-legal"
        )


def test_inventory_contains_only_passed_bundle_and_requires_each_exact_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    desktop = tmp_path / "desktop"
    site = tmp_path / "site-packages"
    module = _write(site, "shipped/__init__.py")
    license_file = _write(
        site, "shipped-1.0.dist-info/licenses/LICENSE", b"Shipped exact terms\n"
    )
    shipped = FakeDistribution(
        site,
        "shipped",
        [str(module.relative_to(site)), str(license_file.relative_to(site))],
    )
    _write(
        site, "unused-1.0.dist-info/licenses/LICENSE", b"Unused terms\n"
    )
    monkeypatch.setattr(build_cli, "DESKTOP_DIR", desktop)

    index_path = build_cli._build_license_inventory([(shipped, "transitive")])
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert [item["name"] for item in index["distributions"]] == ["shipped"]
    assert index["distributions"][0]["license_files"] == [
        "shipped-1.0.0/LICENSE"
    ]
    assert "unused" not in index_path.read_text(encoding="utf-8")

    no_text = FakeDistribution(site, "no-text", [str(module.relative_to(site))])
    with pytest.raises(SystemExit, match="lack exact license or notice text: no-text"):
        build_cli._build_license_inventory([(no_text, "transitive")])


def test_current_final_bundle_maps_hook_added_distributions_and_exception(tmp_path: Path):
    toc_root = (
        REPO_ROOT
        / "desktop"
        / ".pyinstaller"
        / build_cli.DESKTOP_EDITION
        / "win-x64"
        / "work"
        / "ionic"
    )
    tocs = [toc_root / name for name in build_cli.FINAL_TOC_NAMES]
    if not all(path.is_file() for path in tocs):
        pytest.skip(f"{build_cli.DESKTOP_EDITION.title()} PyInstaller final bundle tables are not built")

    ownership, _roots = build_cli._distribution_ownership(
        list(build_cli.metadata.distributions())
    )
    final_origins = {
        build_cli._resolved_path(raw)
        for toc in tocs
        for raw in build_cli._toc_strings(build_cli._read_final_toc(toc))
        if build_cli.os.path.isabs(raw) and build_cli.os.path.isfile(raw)
    }
    if not final_origins.intersection(ownership):
        pytest.skip(
            f"{build_cli.DESKTOP_EDITION.title()} final bundle tables were built by another Python environment"
        )

    collected = build_cli._collected_distributions(tocs)
    by_name = {
        build_cli._normalise_distribution_name(item.metadata["Name"]): item
        for item, _role in collected
    }
    assert {
        "cffi",
        "cryptography",
        "packaging",
        "pyinstaller-hooks-contrib",
        "pytest",
        "setuptools",
    } <= set(by_name)

    copied = build_cli._copy_distribution_licenses(
        by_name["pyinstaller"], tmp_path / "pyinstaller"
    )
    text = "\n".join(
        (tmp_path / "pyinstaller" / filename).read_text(encoding="utf-8")
        for filename in copied
    )
    assert "Bootloader Exception" in text
    assert "distribute those combinations without any restriction" in " ".join(text.split())


def test_pyinstaller_inventory_exposes_exception_in_license_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    site = tmp_path / "site-packages"
    terms = _write(
        site,
        "pyinstaller-6.22.dist-info/licenses/COPYING.txt",
        b"GNU terms\nBootloader Exception\n",
    )
    distribution = FakeDistribution(
        site,
        "PyInstaller",
        [str(terms.relative_to(site))],
        version="6.22.0",
        expression="GPL-2.0-or-later",
    )
    monkeypatch.setattr(build_cli, "DESKTOP_DIR", tmp_path / "desktop")

    index_path = build_cli._build_license_inventory([(distribution, "build-tool")])
    index = json.loads(index_path.read_text(encoding="utf-8"))

    assert index["distributions"][0]["license_expression"] == (
        "GPL-2.0-or-later with PyInstaller Bootloader Exception"
    )


def test_desktop_build_environment_requires_exact_reviewed_pins(tmp_path: Path):
    site = tmp_path / "site-packages"
    constraints = tmp_path / "constraints.txt"
    constraints.write_text("Example-Package==1.2.3\n", encoding="utf-8")
    exact = FakeDistribution(site, "example_package", [], version="1.2.3")

    digest = build_cli._verify_locked_environment(
        constraints, installed_distributions=[exact]
    )
    assert digest == build_cli._sha256(constraints)

    wrong = FakeDistribution(site, "example-package", [], version="1.2.4")
    with pytest.raises(SystemExit, match="version mismatch"):
        build_cli._verify_locked_environment(
            constraints, installed_distributions=[wrong]
        )

    unreviewed = FakeDistribution(site, "surprise-package", [], version="9.9.9")
    with pytest.raises(SystemExit, match="not in lock"):
        build_cli._verify_locked_environment(
            constraints, installed_distributions=[unreviewed]
        )


def test_desktop_lock_covers_pyinstaller_dependencies_on_every_ship_platform():
    locked = build_cli._locked_versions(
        REPO_ROOT / "requirements" / "desktop-build-constraints.txt"
    )
    requirements = [
        Requirement(raw) for raw in (metadata.distribution("pyinstaller").requires or ())
    ]
    platforms = (
        ("linux", "Linux"),
        ("darwin", "Darwin"),
        ("win32", "Windows"),
    )
    required = set()
    for sys_platform, platform_system in platforms:
        environment = default_environment()
        environment.update(
            {"sys_platform": sys_platform, "platform_system": platform_system, "extra": ""}
        )
        required.update(
            build_cli._normalise_distribution_name(item.name)
            for item in requirements
            if item.marker is None or item.marker.evaluate(environment)
        )
    assert required <= set(locked)


def test_repository_desktop_build_lock_is_exact_and_covers_runtime_dependencies():
    locked = build_cli._locked_versions()
    assert {
        "anthropic",
        "httpx",
        "mcp",
        "prompt-toolkit",
        "pydantic",
        "pyinstaller",
        "pyyaml",
        "rich",
        "typer",
        "wcwidth",
    } <= set(locked)
    assert all(version and not any(character.isspace() for character in version)
               for version in locked.values())
    assert len(build_cli._sha256(build_cli.BUILD_CONSTRAINTS)) == 64
