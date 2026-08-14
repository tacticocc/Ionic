# Ionic Essential

Ionic Essential is a local-first compatibility layer for multi-agent systems.
It turns agent instruction files into explicit contracts, maps dependencies
between agents, and identifies structural or semantic breakages before they
ship.

Ionic Essential is open source under the [MIT License](LICENSE).

## Download

The current desktop release is
[Ionic Essential 0.6.1](https://github.com/tacticocc/Ionic/releases/tag/v0.6.1).
The Windows installer is published as a GitHub Release asset rather than
committed to this source repository.

The current Windows build is not Authenticode-signed. Verify its SHA-256 digest
against the value in the release notes before running it.

## What Ionic does

- Discovers agent instruction files such as `AGENTS.md` and `CLAUDE.md`.
- Extracts versioned contracts describing tools, inputs, outputs, constraints,
  capabilities, and dependencies.
- Maintains a local contract registry and dependency graph.
- Compares proposed changes with dependent contracts.
- Runs structural compatibility checks locally by default.
- Exposes the same core through a CLI, desktop app, and MCP server.

## Privacy boundary

Ionic has no Ionic telemetry and does not require an Ionic account. Structural
analysis and the registry remain local. Optional semantic review is explicit;
when enabled, selected contract content is handled by the model provider or
subscription runtime you configure, under that provider's terms.

Do not include credentials, customer source code, or private contracts in
public issues or test fixtures.

## Install from source

Ionic requires Python 3.11 or newer.

```text
python -m pip install -e .
ionic version
```

Install the MCP integration when needed:

```text
python -m pip install -e ".[mcp]"
ionic serve
```

## Quick start

Register one instruction file or scan a directory:

```text
ionic register path/to/AGENTS.md
ionic register path/to/repository
```

Inspect the local registry and dependency graph:

```text
ionic list
ionic graph
ionic status
```

Check a proposed contract change:

```text
ionic check <contract-id> --against path/to/changed/AGENTS.md
```

Structural analysis is the default. Semantic review requires an explicit
`--llm` opt-in and a configured provider or supported subscription runtime.

## Desktop development

The desktop app requires Node.js 22 or newer and a compatible Python build
environment.

```text
cd desktop
npm ci --no-audit --no-fund
npm test
npm run dist:win
```

Local builds are unsigned unless you supply your own signing identity. Tactico
Technologies signing credentials are not stored in this repository.

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening an issue or pull request.
Use the provided issue forms, keep changes focused on Ionic Essential, and add
tests for behavioral changes.

Report vulnerabilities privately by following [SECURITY.md](SECURITY.md).

## License and trademarks

The source code is licensed under the [MIT License](LICENSE). Third-party terms
are listed in [THIRD-PARTY-NOTICES.txt](THIRD-PARTY-NOTICES.txt).

The MIT License does not grant branding rights to the Ionic or Tactico names,
logos, or trade dress. See [TRADEMARKS.md](TRADEMARKS.md).
