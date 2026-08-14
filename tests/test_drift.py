"""Drift detection.

A stale registry is the quietest way Ionic stops working: checks keep passing,
but they are measured against a contract nobody is running any more.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ionic.drift import (
    DriftStatus,
    candidate_roots,
    detect_drift,
    problems,
    resolve_source,
    summarize,
)
from ionic.extract import extract_from_file
from ionic.registry import Registry

AGENT_MD = """\
# Planner Agent

```ionic
id: planner-agent
version: 1.0.0
```

## Tools

- `search_web` — Scoping search.
- `decompose` — Split a brief.

## Constraints

- [source-required] Every step flags whether it needs a source.
"""


@pytest.fixture
def project(tmp_path: Path):
    """A registry with one contract extracted from a real file on disk."""
    source = tmp_path / "planner" / "AGENTS.md"
    source.parent.mkdir(parents=True)
    source.write_text(AGENT_MD, encoding="utf-8")

    registry = Registry(tmp_path / ".ionic" / "registry.db")
    registry.register(extract_from_file(source))
    yield registry, source
    registry.close()


def only(reports, contract_id="planner-agent"):
    matches = [r for r in reports if r.contract_id == contract_id]
    assert len(matches) == 1
    return matches[0]


def test_unchanged_source_is_in_sync(project):
    registry, _ = project
    report = only(detect_drift(registry))
    assert report.status is DriftStatus.IN_SYNC
    assert report.is_problem is False
    assert report.source_fingerprint == report.registered_fingerprint


def test_editing_the_source_is_detected(project):
    registry, source = project
    source.write_text(AGENT_MD.replace("- `search_web` — Scoping search.\n", ""), encoding="utf-8")

    report = only(detect_drift(registry))
    assert report.status is DriftStatus.DRIFTED
    assert report.is_problem is True
    assert report.source_fingerprint != report.registered_fingerprint
    assert "changed on disk" in report.headline()


def test_a_version_only_bump_is_not_treated_as_behavioural_drift(project):
    registry, source = project
    source.write_text(AGENT_MD.replace("version: 1.0.0", "version: 1.1.0"), encoding="utf-8")

    report = only(detect_drift(registry))
    assert report.status is DriftStatus.VERSION_ONLY
    assert report.is_problem is False
    assert report.source_version == "1.1.0"
    assert report.registered_version == "1.0.0"


def test_cosmetic_edits_are_not_drift(project):
    """Whitespace and prose that carry no contract meaning must stay quiet."""
    registry, source = project
    source.write_text(AGENT_MD + "\n\nSome extra prose under no heading.\n", encoding="utf-8")
    assert only(detect_drift(registry)).status is DriftStatus.IN_SYNC


def test_a_deleted_source_is_reported(project):
    registry, source = project
    source.unlink()
    report = only(detect_drift(registry))
    assert report.status is DriftStatus.SOURCE_MISSING
    assert report.is_problem is True


def test_a_contract_registered_without_a_source_is_not_a_problem(tmp_path):
    from ionic.models import Contract

    registry = Registry(tmp_path / "registry.db")
    registry.register(Contract(id="inline-agent"))
    report = only(detect_drift(registry), "inline-agent")
    assert report.status is DriftStatus.NO_SOURCE
    assert report.is_problem is False
    registry.close()


def test_detect_drift_can_target_one_contract(project):
    registry, _ = project
    from ionic.models import Contract

    registry.register(Contract(id="other"))
    reports = detect_drift(registry, contract_id="planner-agent")
    assert [r.contract_id for r in reports] == ["planner-agent"]


def test_relative_sources_resolve_against_the_project_root(tmp_path, monkeypatch):
    """Contracts store whatever path they were registered with, which is usually
    relative to wherever the user was standing."""
    project_root = tmp_path / "repo"
    source = project_root / "agents" / "AGENTS.md"
    source.parent.mkdir(parents=True)
    source.write_text(AGENT_MD, encoding="utf-8")

    monkeypatch.chdir(project_root)
    registry = Registry(project_root / ".ionic" / "registry.db")
    registry.register(extract_from_file(Path("agents/AGENTS.md")))
    assert registry.get("planner-agent").source == "agents/AGENTS.md"

    # Same registry, but the user is now standing somewhere else entirely.
    monkeypatch.chdir(tmp_path)
    report = only(detect_drift(registry))
    assert report.status is DriftStatus.IN_SYNC, report.detail
    assert report.resolved_source == str(source.resolve())
    registry.close()


def test_resolve_source_returns_none_when_nothing_matches(tmp_path):
    assert resolve_source("nope/AGENTS.md", [tmp_path]) is None


def test_absolute_sources_are_used_as_is(tmp_path):
    target = tmp_path / "AGENTS.md"
    target.write_text("# x", encoding="utf-8")
    assert resolve_source(str(target), []) == target


def test_candidate_roots_are_unique_and_include_the_project_root(project):
    registry, _ = project
    roots = candidate_roots(registry)
    assert len(roots) == len(set(roots))
    # <project>/.ionic/registry.db -> <project> must be searched
    assert registry.path.parent.parent.resolve() in roots


def test_summarize_and_problems(project):
    registry, source = project
    source.write_text(AGENT_MD.replace("- `decompose` — Split a brief.\n", ""), encoding="utf-8")
    reports = detect_drift(registry)

    counts = summarize(reports)
    assert counts["drifted"] == 1
    assert counts["in_sync"] == 0
    assert [r.contract_id for r in problems(reports)] == ["planner-agent"]


def test_an_unreadable_source_is_reported_not_raised(project):
    registry, source = project
    source.write_bytes(b"\xff\xfe\x00 invalid utf-8 \xc3\x28")
    report = only(detect_drift(registry))
    assert report.status is DriftStatus.SOURCE_UNREADABLE
    assert report.is_problem is True
    assert report.detail
