from __future__ import annotations

import atexit
import shutil
import tempfile
from pathlib import Path

import pytest

from ionic.models import Contract
from ionic.registry import Registry

DEMO_REPOS = Path(tempfile.mkdtemp(prefix="ionic-test-contracts-"))


def _write_demo_contract(relative_path: str, text: str) -> None:
    target = DEMO_REPOS / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text.strip() + "\n", encoding="utf-8")


_write_demo_contract(
    "planner-agent/AGENTS.md",
    """
# Planner Agent

```ionic
id: planner-agent
version: 1.4.0
tags: [core, upstream]
```

Turns a research brief into an ordered plan.

## Outputs

- `plan` (json) — An ordered execution plan.

## Tools

- `decompose_task` — Split a brief into steps.
- `search_web` — Run a scoping search to confirm a step is answerable before committing to it.
- `estimate_effort` — Estimate execution effort.

## Capabilities

- task decomposition
- scope estimation

## Constraints

- [source-required] Every factual step requires a source.
- [no-execution] The planner never executes a step.
""",
)

_write_demo_contract(
    "planner-agent/AGENTS.proposed.md",
    """
# Planner Agent

```ionic
id: planner-agent
version: 1.5.0
tags: [core, upstream]
```

Turns a research brief into a human-readable plan.

## Outputs

- `plan` (markdown) — A readable execution brief.

## Tools

- `decompose_task` — Split a brief into steps.
- `research` — Run a unified scoping call.
- `estimate_effort` — Estimate execution effort.

## Capabilities

- task decomposition
- scope estimation
- budget enforcement

## Constraints

- [no-execution] The planner never executes a step.
""",
)

_write_demo_contract(
    "researcher-agent/CLAUDE.md",
    """
# Research Agent

```ionic
id: researcher-agent
version: 2.1.0
tags: [core]
```

Executes a plan and returns sourced findings.

## Outputs

- `findings` (json) — Sourced findings for every plan step.

## Tools

- `fetch_page` — Retrieve a URL.
- `summarize_source` — Condense a source.

## Capabilities

- evidence gathering
- source attribution

## Constraints

- [cite-everything] Every factual claim carries a source.

## Depends On

- `planner-agent` — tools: search_web; outputs: plan; format: json; constraints: source-required
""",
)

_write_demo_contract(
    "publisher-agent/AGENTS.md",
    """
# Publisher Agent

```ionic
id: publisher-agent
version: 0.9.2
tags: [downstream]
depends_on:
  - contract_id: researcher-agent
    requires_capabilities: [source attribution]
    expects_outputs: [findings]
    expects_format: json
    requires_constraints: [cite-everything]
  - contract_id: planner-agent
    expects_outputs: [plan]
    expects_format: json
```

Assembles a citable document from research findings.

## Outputs

- `document` (markdown) — A citable report.

## Capabilities

- document assembly
- citation rendering

## Constraints

- [no-uncited-claims] Every claim has a citation.
""",
)

atexit.register(shutil.rmtree, DEMO_REPOS, ignore_errors=True)


@pytest.fixture
def registry(tmp_path: Path) -> Registry:
    reg = Registry(tmp_path / "registry.db")
    yield reg
    reg.close()


@pytest.fixture
def planner() -> Contract:
    return Contract.model_validate(
        {
            "id": "planner",
            "name": "Planner",
            "version": "1.0.0",
            "identity": "Plans work for other agents.",
            "inputs": [{"name": "brief", "format": "text"}],
            "outputs": [
                {
                    "name": "plan",
                    "format": "json",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "steps": {"type": "array"},
                            "cost": {"type": "number"},
                        },
                        "required": ["steps"],
                    },
                }
            ],
            "tools": [
                {"name": "search_web", "description": "Search."},
                {"name": "decompose", "description": "Split a brief."},
            ],
            "capabilities": ["task decomposition", "scope estimation"],
            "constraints": [
                {"id": "source-required", "statement": "Every step flags sources."},
                {"id": "no-execution", "statement": "Never executes a step."},
            ],
            "persona_rules": ["Terse."],
        }
    )


@pytest.fixture
def researcher() -> Contract:
    return Contract.model_validate(
        {
            "id": "researcher",
            "name": "Researcher",
            "version": "2.0.0",
            "outputs": [{"name": "findings", "format": "json"}],
            "depends_on": [
                {
                    "contract_id": "planner",
                    "requires_tools": ["search_web"],
                    "requires_capabilities": ["task decomposition"],
                    "expects_outputs": ["plan"],
                    "expects_format": "json",
                    "requires_constraints": ["source-required"],
                }
            ],
        }
    )
