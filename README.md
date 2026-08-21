<a id="readme-top"></a>

[![Contributors][contributors-shield]][contributors-url]
[![Forks][forks-shield]][forks-url]
[![Stargazers][stars-shield]][stars-url]
[![Issues][issues-shield]][issues-url]
[![Release][release-shield]][release-url]
[![MIT License][license-shield]][license-url]

<br />
<div align="center">
  <a href="https://github.com/tacticocc/Ionic">
    <img src="https://raw.githubusercontent.com/tacticocc/Ionic/main/brand/Ionic%20Icon%20BG.png" alt="Ionic logo" width="128" height="128">
  </a>

  <h3 align="center">Ionic Essential</h3>

  <p align="center">
    The local-first compatibility layer for multi-agent systems.
    <br />
    Register agent contracts, map dependencies, and catch breakages before they ship.
    <br />
    <br />
    <a href="https://github.com/tacticocc/Ionic/releases/latest"><strong>Download the latest release »</strong></a>
    <br />
    <br />
    <a href="https://github.com/tacticocc/Ionic">Browse Source</a>
    &middot;
    <a href="https://github.com/tacticocc/Ionic/issues/new?template=bug-report.yml">Report Bug</a>
    &middot;
    <a href="https://github.com/tacticocc/Ionic/issues/new?template=feature-request.yml">Request Feature</a>
  </p>
</div>

<details>
  <summary>Table of Contents</summary>
  <ol>
    <li>
      <a href="#about-the-project">About the Project</a>
      <ul>
        <li><a href="#core-capabilities">Core Capabilities</a></li>
        <li><a href="#built-with">Built With</a></li>
      </ul>
    </li>
    <li>
      <a href="#getting-started">Getting Started</a>
      <ul>
        <li><a href="#prerequisites">Prerequisites</a></li>
        <li><a href="#windows-desktop">Windows Desktop</a></li>
        <li><a href="#install-the-cli-from-pypi">Install the CLI from PyPI</a></li>
        <li><a href="#install-from-source">Install From Source</a></li>
        <li><a href="#build-the-desktop-app">Build the Desktop App</a></li>
      </ul>
    </li>
    <li><a href="#usage">Usage</a></li>
    <li><a href="#privacy-and-security">Privacy and Security</a></li>
    <li><a href="#roadmap">Roadmap</a></li>
    <li><a href="#contributing">Contributing</a></li>
    <li><a href="#license-and-trademarks">License and Trademarks</a></li>
    <li><a href="#acknowledgments">Acknowledgments</a></li>
  </ol>
</details>

## About the Project

Agent systems often depend on instruction files whose behavioral contracts are
implicit. A small change to one agent's tools, outputs, constraints, or persona
can silently break every agent that depends on it.

Ionic Essential turns those instructions into versioned contracts. It keeps a
local registry, builds the dependency graph, and checks proposed changes against
their consumers before rollout. The same core is available through a desktop
app, command-line interface, Python library, and MCP server.

Ionic Essential is open source under the MIT License and has no Ionic account
requirement or Ionic telemetry.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

### Core Capabilities

- Discover agent instruction files such as `AGENTS.md` and `CLAUDE.md`.
- Extract contracts for tools, inputs, outputs, capabilities, constraints,
  persona rules, and dependencies.
- Register and version contracts in a local SQLite registry.
- Visualize direct and transitive dependencies.
- Detect structural compatibility problems locally by default.
- Opt in to semantic review with a configured provider or supported
  subscription runtime.
- Use Ionic through the desktop app, CLI, Python API, or MCP.
- Work interactively from a bounded terminal cockpit with completion and
  in-session history.

### Built With

- [Python 3.11+](https://www.python.org/)
- [Pydantic](https://docs.pydantic.dev/)
- [Typer](https://typer.tiangolo.com/)
- [prompt-toolkit](https://python-prompt-toolkit.readthedocs.io/)
- [Electron](https://www.electronjs.org/)
- [Node.js 22+](https://nodejs.org/)

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Getting Started

Choose the packaged Windows desktop app or install the Python project from
source. Neither path requires an Ionic account.

### Prerequisites

For the Python CLI, library, or MCP server:

- Python 3.11 or newer
- Git

For desktop development:

- Node.js 22 or newer
- npm
- A compatible Python build environment

### Windows Desktop

Download [Ionic Essential 0.6.1][release-url] from GitHub Releases. The installer
is a release asset and is not committed to this source repository.

The current Windows build is not Authenticode-signed. Verify its SHA-256 digest
before running it:

```text
7F4104CEAC355594BDA5DB11B60967262765337A34D8AF1F3EC05D5B36837F35
```

### Install the CLI from PyPI

Install the Ionic 0.7.1 CLI from PyPI with:

```sh
python -m pip install ionic
ionic
```

Install the optional MCP integration with:

```sh
python -m pip install "ionic[mcp]"
ionic serve
```

### Install From Source

1. Clone the repository.

   ```sh
   git clone https://github.com/tacticocc/Ionic.git
   cd Ionic
   ```

2. Install Ionic in editable mode.

   ```sh
   python -m pip install -e .
   ionic version
   ```

3. Install the MCP integration when needed.

   ```sh
   python -m pip install -e ".[mcp]"
   ionic serve
   ```

### Build the Desktop App

```sh
cd desktop
npm ci --no-audit --no-fund
npm test
npm run dist:win
```

Local builds are unsigned unless you supply your own signing identity. Tactico
Technologies signing credentials are not stored in this repository.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Usage

### Open the interactive terminal

Run Ionic without arguments in a real terminal to open the contract operations
cockpit:

```sh
ionic
```

Type `/` to open the described command palette. Use Up/Down and Tab/Shift-Tab
to choose, then Enter to insert a command. `/dashboard` shows local registry
health; `/repo add ID PATH` selects repositories for the current TUI session,
and `/workspace scan`, `check`, or `sync` uses that selection unless you supply
an explicit `--repo` or `--manifest`. Session repositories are not persisted
to Desktop, disk, or the contract registry.

Semantic review remains explicit. `/semantic api PROVIDER [MODEL]` selects a
direct API backend for the current TUI session, `/semantic key set PROVIDER`
opens a masked session-only credential prompt, and `/semantic check ...` runs
the normal Ionic check with `--llm`. `/semantic subscription ...` selects a
supported official runtime without asking for its token; `/semantic consent`
then shows the runtime-specific disclosure and exact versioned acceptance
command. Consent and credentials remain process-local. Configuration alone never
starts a model request, and workspace v1 remains structural and offline.

PageUp/PageDown and the mouse wheel scroll output while the command bar remains
ready. Tab with an empty command focuses scrollback for Up/Down and `g`/`G`, then
Tab returns to the command bar. `/quit` or Ctrl-D exits. The command bar only
runs allowlisted Ionic operations; it does not execute shell commands.

All direct commands remain available for scripts and automation. Redirected
input/output, CI, `IONIC_NO_TUI=1`, and Desktop sidecar calls stay
non-interactive.

### Register agent contracts

Register one instruction file or discover supported files in a directory:

```sh
ionic register path/to/AGENTS.md
ionic register path/to/repository
```

### Inspect dependencies

```sh
ionic list
ionic graph
ionic status
```

### Check a proposed change

```sh
ionic check <contract-id> --against path/to/changed/AGENTS.md
```

Structural checks are the default. Semantic review requires an explicit `--llm`
opt-in and a configured provider or supported subscription runtime:

```sh
ionic check <contract-id> --against path/to/changed/AGENTS.md --llm
```

### Scan a multi-repository workspace

```sh
ionic workspace scan --repo app=path/to/app --repo agent=path/to/agent
ionic workspace check --repo app=path/to/app --repo agent=path/to/agent
```

Workspace v1 checks are structural and local.

### Run the MCP server

```sh
ionic serve
```

Use `ionic --help` or `ionic <command> --help` for the complete command surface.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Privacy and Security

- Ionic emits no Ionic telemetry.
- Structural analysis and the contract registry remain local.
- Semantic review is always opt-in.
- When semantic review is enabled, selected contract content is handled by the
  model provider or subscription runtime you configure, under that provider's
  terms.
- Provider credentials must not be committed, logged, or included in test
  fixtures.

Do not post customer source code, private contracts, personal data, or
credentials in public issues. Report suspected vulnerabilities privately by
following [SECURITY.md](SECURITY.md).

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Roadmap

Ionic Essential is evolving in public. Proposed work and known problems are
tracked through GitHub Issues:

- [Open issues][issues-url]
- [Feature requests](https://github.com/tacticocc/Ionic/issues?q=is%3Aissue+label%3Aenhancement)
- [Current releases][release-url]

Roadmap items are proposals, not release commitments. Please open a feature
request before starting a substantial behavioral change.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Contributing

Contributions that improve Ionic Essential are welcome.

1. Read [CONTRIBUTING.md](CONTRIBUTING.md).
2. Fork the project.
3. Create a focused branch: `git checkout -b feature/short-description`.
4. Add or update tests for behavioral changes.
5. Run the relevant Python and desktop test suites.
6. Commit your changes and open a pull request using the repository template.

By contributing, you agree that your contribution is provided under this
repository's MIT License.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## License and Trademarks

The source code is distributed under the [MIT License](LICENSE). Applicable
third-party terms are listed in
[THIRD-PARTY-NOTICES.txt](THIRD-PARTY-NOTICES.txt).

The MIT License does not grant branding rights to the Ionic or Tactico
Technologies names, logos, icons, or trade dress. See
[TRADEMARKS.md](TRADEMARKS.md) before distributing a modified build.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Acknowledgments

- README structure adapted from
  [Best-README-Template](https://github.com/othneildrew/Best-README-Template).
- [Choose an Open Source License](https://choosealicense.com/)
- [Shields.io](https://shields.io/)
- The open-source projects and contributors listed in
  [THIRD-PARTY-NOTICES.txt](THIRD-PARTY-NOTICES.txt)

Project link: [https://github.com/tacticocc/Ionic](https://github.com/tacticocc/Ionic)

<p align="right">(<a href="#readme-top">back to top</a>)</p>

[contributors-shield]: https://img.shields.io/github/contributors/tacticocc/Ionic.svg?style=for-the-badge
[contributors-url]: https://github.com/tacticocc/Ionic/graphs/contributors
[forks-shield]: https://img.shields.io/github/forks/tacticocc/Ionic.svg?style=for-the-badge
[forks-url]: https://github.com/tacticocc/Ionic/network/members
[stars-shield]: https://img.shields.io/github/stars/tacticocc/Ionic.svg?style=for-the-badge
[stars-url]: https://github.com/tacticocc/Ionic/stargazers
[issues-shield]: https://img.shields.io/github/issues/tacticocc/Ionic.svg?style=for-the-badge
[issues-url]: https://github.com/tacticocc/Ionic/issues
[release-shield]: https://img.shields.io/github/v/release/tacticocc/Ionic.svg?style=for-the-badge
[release-url]: https://github.com/tacticocc/Ionic/releases/tag/v0.6.1
[license-shield]: https://img.shields.io/github/license/tacticocc/Ionic.svg?style=for-the-badge
[license-url]: https://github.com/tacticocc/Ionic/blob/main/LICENSE
