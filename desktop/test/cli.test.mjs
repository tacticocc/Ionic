/**
 * Integration tests for the CLI bridge.
 *
 * These run the *real* ionic CLI against a throwaway registry -- no Electron,
 * no mocks. If the CLI's JSON contract ever drifts, these fail here rather
 * than in the UI.
 *
 *   IONIC_BIN=/path/to/ionic npm test        (from desktop/)
 */

import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { after, describe, it } from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const ionic = require("../src/ionic-cli.js");

const workdir = fs.mkdtempSync(path.join(os.tmpdir(), "ionic-desktop-test-"));
const registryPath = path.join(workdir, "registry.db");
const DEMO_REPOS = path.join(workdir, "demo-repos");
const PROPOSED = path.join(DEMO_REPOS, "planner-agent", "AGENTS.proposed.md");

function writeDemoContract(relativePath, lines) {
  const target = path.join(DEMO_REPOS, ...relativePath.split("/"));
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, `${lines.join("\n")}\n`, "utf8");
}

writeDemoContract("planner-agent/AGENTS.md", [
  "# Planner Agent", "", "```ionic", "id: planner-agent", "version: 1.4.0",
  "tags: [core, upstream]", "```", "", "Turns a research brief into an ordered plan.",
  "", "## Outputs", "", "- `plan` (json) — An ordered execution plan.",
  "", "## Tools", "", "- `decompose_task` — Split a brief into steps.",
  "- `search_web` — Run a scoping search to confirm a step is answerable before committing to it.",
  "- `estimate_effort` — Estimate execution effort.", "", "## Capabilities", "",
  "- task decomposition", "- scope estimation", "", "## Constraints", "",
  "- [source-required] Every factual step requires a source.",
  "- [no-execution] The planner never executes a step.",
]);
writeDemoContract("planner-agent/AGENTS.proposed.md", [
  "# Planner Agent", "", "```ionic", "id: planner-agent", "version: 1.5.0",
  "tags: [core, upstream]", "```", "", "Turns a research brief into a readable plan.",
  "", "## Outputs", "", "- `plan` (markdown) — A readable execution brief.",
  "", "## Tools", "", "- `decompose_task` — Split a brief into steps.",
  "- `research` — Run a unified scoping call.",
  "- `estimate_effort` — Estimate execution effort.", "", "## Capabilities", "",
  "- task decomposition", "- scope estimation", "- budget enforcement", "",
  "## Constraints", "", "- [no-execution] The planner never executes a step.",
]);
writeDemoContract("researcher-agent/CLAUDE.md", [
  "# Research Agent", "", "```ionic", "id: researcher-agent", "version: 2.1.0",
  "tags: [core]", "```", "", "Executes a plan and returns sourced findings.",
  "", "## Outputs", "", "- `findings` (json) — Sourced findings for each step.",
  "", "## Tools", "", "- `fetch_page` — Retrieve a URL.",
  "- `summarize_source` — Condense a source.", "", "## Capabilities", "",
  "- evidence gathering", "- source attribution", "", "## Constraints", "",
  "- [cite-everything] Every factual claim carries a source.", "", "## Depends On", "",
  "- `planner-agent` — tools: search_web; outputs: plan; format: json; constraints: source-required",
]);
writeDemoContract("publisher-agent/AGENTS.md", [
  "# Publisher Agent", "", "```ionic", "id: publisher-agent", "version: 0.9.2",
  "tags: [downstream]", "depends_on:", "  - contract_id: researcher-agent",
  "    requires_capabilities: [source attribution]", "    expects_outputs: [findings]",
  "    expects_format: json", "    requires_constraints: [cite-everything]",
  "  - contract_id: planner-agent", "    expects_outputs: [plan]",
  "    expects_format: json", "```", "", "Assembles a citable document.",
  "", "## Outputs", "", "- `document` (markdown) — A citable report.",
  "", "## Constraints", "", "- [no-uncited-claims] Every claim has a citation.",
]);

const PLATFORM_NAMES = { win32: "win", darwin: "mac", linux: "linux" };
const CLI_EXE = process.platform === "win32" ? "ionic.exe" : "ionic";

function managedFixture({ protocol = ionic.DESKTOP_PROTOCOL, contents = "ionic-sidecar" } = {}) {
  const resourcesDir = fs.mkdtempSync(path.join(workdir, "resources-"));
  const bundleDir = path.join(resourcesDir, "ionic");
  const executable = path.join(bundleDir, CLI_EXE);
  fs.mkdirSync(bundleDir);
  fs.writeFileSync(executable, contents);
  fs.chmodSync(executable, 0o755);
  const bytes = fs.readFileSync(executable);
  const manifest = {
    edition: ionic.DESKTOP_EDITION,
    version: "0.1.0",
    desktop_protocol: protocol,
    platform: PLATFORM_NAMES[process.platform] || process.platform,
    arch: process.arch,
    executable: {
      name: CLI_EXE,
      sha256: crypto.createHash("sha256").update(bytes).digest("hex"),
      size: bytes.length,
    },
  };
  fs.writeFileSync(path.join(bundleDir, "manifest.json"), JSON.stringify(manifest));
  return { resourcesDir, executable, manifest };
}

// Resolved at module load, before the suites below are defined: node:test
// evaluates suite options eagerly, and a function passed as `skip` is simply
// truthy -- which would silently skip everything.
let skipReason = false;
try {
  await ionic.locate({});
  await ionic.register(DEMO_REPOS, { registryPath });
} catch (err) {
  if (err.name !== "IonicNotFound") throw err;
  skipReason = `ionic CLI unavailable (${err.message}). Build or reinstall the managed engine, or set IONIC_BIN.`;
  console.error(`\n  ${skipReason}\n`);
}

after(() => {
  fs.rmSync(workdir, { recursive: true, force: true });
});

describe("resolution", () => {
  it("proposes candidate paths without touching the disk", () => {
    const candidates = ionic.candidatePaths({
      explicitBin: "/custom/ionic",
      env: { IONIC_BIN: "/inherited/ionic", PATH: "" },
      appDir: "/app",
      resourcesDir: "/resources",
    });
    assert.equal(candidates[0], "/custom/ionic");
    assert.equal(candidates[1], path.join("/resources", "ionic", CLI_EXE));
    assert.ok(candidates.length > 1);
  });

  it("orders the managed sidecar before virtualenv and app-local fallbacks", () => {
    const candidates = ionic.candidatePaths({
      explicitBin: "/explicit/ionic",
      env: { IONIC_BIN: "/inherited/ionic", VIRTUAL_ENV: "/active-venv", PATH: "" },
      appDir: "/app",
      resourcesDir: "/resources",
    });
    assert.deepEqual(candidates.slice(0, 5), [
      "/explicit/ionic",
      path.join("/resources", "ionic", CLI_EXE),
      "/inherited/ionic",
      path.join("/active-venv", process.platform === "win32" ? "Scripts" : "bin", CLI_EXE),
      path.join("/app", ".venv", process.platform === "win32" ? "Scripts" : "bin", CLI_EXE),
    ]);
  });

  it("accepts a managed sidecar only when its manifest matches its bytes and host", () => {
    const fixture = managedFixture();
    const manifest = ionic.verifyManagedCandidate(fixture.executable, {
      resourcesDir: fixture.resourcesDir,
    });
    assert.deepEqual(manifest, fixture.manifest);

    const resolved = ionic.resolveIonic({ env: { PATH: "" }, resourcesDir: fixture.resourcesDir });
    assert.equal(resolved.kind, "managed");
    assert.equal(resolved.command, fixture.executable);
  });

  it("rejects a managed sidecar whose executable was changed after packaging", () => {
    const fixture = managedFixture();
    fs.appendFileSync(fixture.executable, "tampered");
    assert.throws(
      () =>
        ionic.verifyManagedCandidate(fixture.executable, {
          resourcesDir: fixture.resourcesDir,
        }),
      (err) => {
        assert.equal(err.name, "IonicError");
        assert.match(err.message, /integrity check/);
        return true;
      }
    );
  });

  it("requires desktop protocol 4 in both the manifest and status handshake", () => {
    const fixture = managedFixture({ protocol: 1 });
    assert.throws(
      () =>
        ionic.verifyManagedCandidate(fixture.executable, {
          resourcesDir: fixture.resourcesDir,
        }),
      /manifest is incompatible/
    );

    const validStatus = {
      version: "0.1.0",
      desktop_protocol: ionic.DESKTOP_PROTOCOL,
      telemetry: "none",
      registry: { path: "/registry.db" },
    };
    assert.equal(ionic.validateStatusHandshake(validStatus), validStatus);
    assert.throws(
      () => ionic.validateStatusHandshake({ ...validStatus, desktop_protocol: 1 }),
      /requires CLI protocol 4/
    );
    assert.throws(
      () => ionic.validateStatusHandshake({ ...validStatus, desktop_protocol: undefined }),
      /reported none/
    );
  });

  it("rejects a managed sidecar staged for another desktop edition", () => {
    const fixture = managedFixture();
    fs.writeFileSync(
      path.join(fixture.resourcesDir, "ionic", "manifest.json"),
      JSON.stringify({ ...fixture.manifest, edition: "other" })
    );
    assert.throws(
      () => ionic.verifyManagedCandidate(fixture.executable, { resourcesDir: fixture.resourcesDir }),
      /manifest is incompatible/
    );
  });

  it("validates workspace identities and builds the exact structural CLI contract", () => {
    const repositories = [
      { id: "alpha", path: path.join(workdir, "alpha") },
      { id: "beta", path: path.join(workdir, "beta") },
    ];
    assert.deepEqual(ionic.workspaceScanArgs(repositories), [
      "workspace", "scan", "--json",
      "--repo", `alpha=${path.resolve(repositories[0].path)}`,
      "--repo", `beta=${path.resolve(repositories[1].path)}`,
    ]);

    const direct = ionic.workspaceCheckArgs({ repositories, failOn: "high" });
    assert.deepEqual(direct.slice(0, 3), ["workspace", "check", "--json"]);
    assert.equal(direct.includes("--transitive"), false);
    assert.equal(direct.includes("--direct"), false);
    assert.equal(direct.includes("--llm"), false);
    assert.equal(direct.includes("--no-llm"), false);
    assert.equal(
      ionic.workspaceCheckArgs({ repositories, failOn: "medium", transitive: true }).at(-1),
      "--transitive"
    );

    const plan = ionic.workspaceSyncArgs({
      repositories,
      agents: ["alpha/planner", "beta/planner"],
      expectedScanId: "scan-ignored-on-plan",
    });
    assert.deepEqual(plan.slice(0, 3), ["workspace", "sync", "--json"]);
    assert.deepEqual(
      plan.flatMap((value, index) => value === "--agent" ? [plan[index + 1]] : []),
      ["alpha/planner", "beta/planner"]
    );
    assert.equal(plan.includes("--apply"), false);
    assert.equal(plan.includes("--expected-scan"), false);

    const apply = ionic.workspaceSyncArgs({
      repositories,
      agents: ["alpha/planner"],
      apply: true,
      expectedScanId: "reviewed-plan-token-123",
    });
    assert.deepEqual(
      apply.slice(-3),
      ["--apply", "--expected-scan", "reviewed-plan-token-123"]
    );
  });

  it("rejects ambiguous workspace inputs before spawning the CLI", () => {
    const sourcePath = path.join(workdir, "alpha");
    assert.throws(
      () => ionic.normalizeWorkspaceRepositories([
        { id: "alpha", path: sourcePath },
        { id: "alpha", path: path.join(workdir, "other") },
      ]),
      /already in this workspace/
    );
    assert.throws(
      () => ionic.normalizeWorkspaceRepositories([{ id: "Alpha Space", path: sourcePath }]),
      /repository id/
    );
    assert.throws(
      () => ionic.normalizeWorkspaceRepositories([{ id: "alpha", path: "relative" }]),
      /absolute directory path/
    );
    assert.throws(
      () => ionic.normalizeWorkspaceAgentSelectors(
        ["unknown/planner"],
        [{ id: "alpha", path: sourcePath }]
      ),
      /invalid workspace agent selector/
    );
    assert.throws(
      () => ionic.workspaceSyncArgs({
        repositories: [{ id: "alpha", path: sourcePath }],
        apply: true,
        expectedScanId: null,
      }),
      /reviewed sync plan token returned by the matching workspace sync preview/
    );
  });

  it("turns stale apply exit 3 payloads into actionable errors", () => {
    assert.throws(
      () => ionic.workspaceSyncResult({
        code: 3,
        data: { error: "The reviewed sync plan is stale; review the new preview." },
      }),
      (error) => {
        assert.equal(error.name, "IonicError");
        assert.equal(error.code, 3);
        assert.match(error.message, /reviewed sync plan is stale/i);
        return true;
      }
    );
    assert.throws(
      () => ionic.workspaceSyncResult({
        code: 3,
        data: {
          conflicts: [{
            kind: "stale_registry",
            message: "The registry changed after this plan was reviewed.",
          }],
        },
      }),
      /registry changed after this plan/i
    );
    assert.deepEqual(
      ionic.workspaceSyncResult({ code: 1, data: { status: "blocked" } }),
      { status: "blocked" }
    );
  });

  it("throws a helpful error when nothing is installed anywhere", () => {
    // Empty PATH and a HOME holding nothing: every candidate, including the
    // python fallbacks, must be verified to exist before it is accepted.
    assert.throws(
      () => ionic.resolveIonic({ env: { PATH: "", HOME: "/nonexistent-xyz" } }),
      (err) => {
        assert.equal(err.name, "IonicNotFound");
        assert.match(err.message, /Repair or reinstall Ionic Desktop/);
        assert.ok(Array.isArray(err.searched) && err.searched.length > 0);
        return true;
      }
    );
  });

  it("prefers IONIC_BIN over everything else", () => {
    if (!process.env.IONIC_BIN) return;
    const resolved = ionic.resolveIonic({ env: { IONIC_BIN: process.env.IONIC_BIN } });
    assert.equal(resolved.command, process.env.IONIC_BIN);
    assert.equal(resolved.kind, "executable");
  });

  it("prefers the saved executable over an inherited IONIC_BIN", () => {
    const resolved = ionic.resolveIonic({
      explicitBin: process.execPath,
      env: { IONIC_BIN: "/not-the-saved-cli", PATH: "" },
    });
    assert.equal(resolved.command, process.execPath);
  });

  it("rejects an executable named ionic when it is not Ionic Contracts", async () => {
    await assert.rejects(
      () =>
        ionic.locate({
          env: { IONIC_BIN: process.execPath, PATH: "" },
        }),
      (err) => {
        assert.equal(err.name, "IonicNotFound");
        assert.match(err.message, /did not identify as Ionic Contracts/);
        return true;
      }
    );
  });

  it("stops a command whose output exceeds the desktop safety limit", async () => {
    await assert.rejects(
      () =>
        ionic.run(["-e", "process.stdout.write('x'.repeat(2048))"], {
          env: { IONIC_BIN: process.execPath, PATH: "" },
          maxOutputBytes: 128,
        }),
      (err) => {
        assert.equal(err.name, "IonicError");
        assert.match(err.message, /too much output/);
        return true;
      }
    );
  });
});

describe("registry operations", { skip: skipReason }, () => {
  it("status reports the registry and declares no telemetry", async () => {
    const status = await ionic.status({ registryPath });
    assert.equal(status.telemetry, "none");
    assert.equal(status.desktop_protocol, ionic.DESKTOP_PROTOCOL);
    assert.equal(status.registry.contracts, 3);
    assert.ok(status.version);
  });

  it("list returns the demo contracts", async () => {
    const contracts = await ionic.list({ registryPath });
    const ids = contracts.map((c) => c.id).sort();
    assert.deepEqual(ids, ["planner-agent", "publisher-agent", "researcher-agent"]);
  });

  it("show returns a full contract", async () => {
    const contract = await ionic.show("planner-agent", { registryPath });
    assert.equal(contract.id, "planner-agent");
    assert.ok(contract.tools.some((t) => t.name === "search_web"));
    assert.ok(contract.constraints.some((c) => c.id === "source-required"));
  });

  it("graph returns nodes and edges the UI can lay out", async () => {
    const graph = await ionic.graph(null, { registryPath });
    assert.equal(graph.nodes.length, 3);
    assert.ok(graph.edges.length >= 3);
    for (const edge of graph.edges) {
      assert.ok(typeof edge.source === "string");
      assert.ok(typeof edge.target === "string");
      assert.equal(typeof edge.resolved, "boolean");
    }
  });
});

describe("multi-repository workspace", { skip: skipReason }, () => {
  const repositories = ["planner-agent", "researcher-agent", "publisher-agent"].map((id) => ({
    id,
    path: path.join(DEMO_REPOS, id),
  }));

  it("discovers distinct qualified agents without network use", async () => {
    const report = await ionic.workspaceScan({ repositories }, { registryPath });
    for (const key of [
      "schema_version", "workspace_id", "operation", "status", "scan_id", "telemetry",
      "network", "repositories", "documents", "agents", "conflicts", "errors", "checks", "summary",
    ]) {
      assert.ok(key in report, `workspace scan is missing ${key}`);
    }
    assert.equal(report.operation, "scan");
    assert.equal(report.telemetry, "none");
    assert.equal(report.network.used, false);
    assert.deepEqual(
      report.agents.map((agent) => agent.ref).sort(),
      ["planner-agent/planner-agent", "publisher-agent/publisher-agent", "researcher-agent/researcher-agent"]
    );
  });

  it("returns compatibility checks and exact selected-agent sync actions", async () => {
    const checked = await ionic.workspaceCheck(
      { repositories, failOn: "high", transitive: false },
      { registryPath }
    );
    assert.equal(checked.operation, "check");
    assert.equal(checked.checks.length, 3);
    const selected = [checked.agents[0].ref];
    const plan = await ionic.workspaceSync(
      { repositories, agents: selected, apply: false },
      { registryPath }
    );
    assert.equal(plan.status, "planned");
    assert.deepEqual(Object.keys(plan.actions).sort(), ["add", "prune", "unchanged", "update"]);
    assert.deepEqual([...plan.actions.add, ...plan.actions.update, ...plan.actions.unchanged], selected);
    assert.equal(plan.applied, false);
  });
});

describe("compatibility checks", { skip: skipReason }, () => {
  it("treats REQUEST_CHANGES (exit 1) as a result, not a crash", async () => {
    const report = await ionic.check(
      { contractId: "planner-agent", against: PROPOSED, useLlm: false },
      { registryPath }
    );
    assert.equal(report.verdict, "REQUEST_CHANGES");
    assert.ok(report.findings.length > 0);
    assert.ok(report.findings.some((f) => f.severity === "critical"));
    assert.ok(report.dependents_checked.includes("researcher-agent"));
  });

  it("returns every field the report view renders", async () => {
    const report = await ionic.check(
      { contractId: "planner-agent", against: PROPOSED, useLlm: false },
      { registryPath }
    );
    for (const key of [
      "verdict",
      "contract_id",
      "from_version",
      "to_version",
      "findings",
      "dependents_checked",
      "fail_on",
      "judge",
    ]) {
      assert.ok(key in report, `report is missing ${key}`);
    }
    const finding = report.findings[0];
    for (const key of ["kind", "severity", "summary", "evidence", "recommendation", "origin"]) {
      assert.ok(key in finding, `finding is missing ${key}`);
    }
  });

  it("approves an unchanged contract", async () => {
    const report = await ionic.check(
      {
        contractId: "planner-agent",
        against: path.join(DEMO_REPOS, "planner-agent", "AGENTS.md"),
        useLlm: false,
      },
      { registryPath }
    );
    assert.equal(report.verdict, "APPROVED");
  });

  it("honours the fail-on threshold", async () => {
    const strict = await ionic.check(
      {
        contractId: "planner-agent",
        against: PROPOSED,
        useLlm: false,
        failOn: "info",
      },
      { registryPath }
    );
    assert.equal(strict.fail_on, "info");
    assert.equal(strict.verdict, "REQUEST_CHANGES");
  });

  it("includes indirect dependents when asked", async () => {
    const report = await ionic.check(
      { contractId: "planner-agent", against: PROPOSED, useLlm: false, transitive: true },
      { registryPath }
    );
    assert.ok(report.dependents_checked.includes("publisher-agent"));
  });

  it("renders markdown for the clipboard / PR comments", async () => {
    const markdown = await ionic.renderMarkdown(
      { contractId: "planner-agent", against: PROPOSED, useLlm: false },
      { registryPath }
    );
    assert.match(markdown, /Ionic compatibility check/);
    assert.match(markdown, /REQUEST_CHANGES/);
  });

  it("surfaces CLI errors instead of hanging", async () => {
    await assert.rejects(
      () => ionic.check({ contractId: "does-not-exist", useLlm: false }, { registryPath }),
      (err) => {
        assert.equal(err.name, "IonicError");
        assert.match(err.message, /does-not-exist|No contract/i);
        return true;
      }
    );
  });
});
