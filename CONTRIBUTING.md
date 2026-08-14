# Contributing to Ionic Essential

Thank you for helping improve Ionic Essential.

## Collaboration principles

- Be specific, constructive, and respectful in issues, reviews, and design
  discussions.
- Start from the user problem and document relevant privacy, security, and
  compatibility tradeoffs.
- Prefer small, reviewable changes over unrelated rewrites.
- Assume good intent, critique the change rather than the contributor, and
  resolve disagreements with reproducible evidence.
- Never post credentials, customer data, private contracts, or unpublished
  vulnerability details.

## Scope

This repository contains the MIT-licensed Essential source. Keep changes
focused on Essential; commercial-only control-plane features do
not belong in this repository. Do not commit credentials, customer data,
generated installers, build directories, local registries, or dependency
caches.

Before starting a substantial feature or behavior change, search existing
issues and open a feature request. This prevents duplicate work and gives
maintainers a chance to confirm that the proposal fits Ionic Essential.

## Development setup

Ionic requires Python 3.11 or newer. Install the Python development environment
and run its tests with:

```text
python -m pip install -e ".[dev]"
python -m pytest -q
```

For the desktop app, use Node.js 22 or newer:

```text
cd desktop
npm ci --no-audit --no-fund
npm test
```

Build an unsigned local Windows installer with `npm run dist:win`. Official
release signing credentials are not stored in this repository.

## Issues

Use the repository's structured issue forms:

- Bug reports should include the affected version, operating system, surface
  (desktop, CLI, MCP, or library), minimal reproduction steps, expected and
  actual behavior, and sanitized diagnostics.
- Feature requests should explain the underlying problem, intended users,
  proposed behavior, alternatives considered, and privacy or compatibility
  implications.
- Security vulnerabilities must follow [SECURITY.md](SECURITY.md) and must not
  be disclosed in a public issue.

Keep one actionable problem per issue. Use a minimal reproduction repository
when practical, but remove secrets and proprietary content first.

## Development workflow

1. Fork the repository and create a focused branch.
2. Make the smallest coherent change that solves the issue.
3. Add or update automated tests.
4. Run the relevant Python and desktop test suites.
5. Open a pull request using the provided template.

Commit messages should be short, imperative summaries such as
`Harden contract import validation`. Avoid generated files and unrelated
formatting changes.

## Pull requests

- Keep each change focused and explain its user-visible effect.
- Link the relevant issue when one exists.
- Add or update tests for behavioral changes.
- Preserve local-first operation, zero Ionic telemetry, and structural scans by
  default.
- Keep provider credentials out of logs, command arguments, and committed
  fixtures.
- Retain applicable copyright, license, and third-party notices.
- Call out breaking changes, migrations, new dependencies, network behavior,
  and changes to persisted data.

Draft pull requests are welcome for early design feedback. Mark a pull request
ready only when its description and checklist are complete and its relevant
tests pass. Reviewers may request changes for correctness, scope, security,
privacy, maintainability, accessibility, or release compliance.

Maintainers may close inactive or out-of-scope proposals with an explanation.
Opening an issue or pull request does not guarantee inclusion or a particular
release timeline.

By contributing, you agree that your contribution is provided under this
repository's MIT License.
