"""Public package metadata checks."""

from __future__ import annotations

import tomllib
from pathlib import Path

from ionic import __version__


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_pypi_metadata_uses_the_repository_readme_and_current_version() -> None:
    metadata = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = metadata["project"]

    assert project["readme"] == "README.md"
    assert (REPO_ROOT / project["readme"]).is_file()
    assert __version__ == "0.7.0"
    assert "prompt-toolkit>=3.0.53" in project["dependencies"]


def test_sdist_allowlist_excludes_desktop_release_material() -> None:
    metadata = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    sdist = metadata["tool"]["hatch"]["build"]["targets"]["sdist"]

    assert sdist["include"] == [
        "/ionic",
        "/pyproject.toml",
        "/README.md",
        "/LICENSE",
        "/TRADEMARKS.md",
    ]
    assert sdist["ignore-vcs"] is True
