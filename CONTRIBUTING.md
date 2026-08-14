# Contributing to Ionic Essential

Thank you for helping improve Ionic Essential.

## Scope

This repository contains the MIT-licensed Essential source. Keep changes
focused on Essential; commercial-only control-plane features do
not belong in this repository. Do not commit credentials, customer data,
generated installers, build directories, local registries, or dependency
caches.

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

## Pull requests

- Keep each change focused and explain its user-visible effect.
- Add or update tests for behavioral changes.
- Preserve local-first operation, zero Ionic telemetry, and structural scans by
  default.
- Keep provider credentials out of logs, command arguments, and committed
  fixtures.
- Retain applicable copyright, license, and third-party notices.

By contributing, you agree that your contribution is provided under this
repository's MIT License.
