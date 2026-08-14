"use strict";

/**
 * The bridge to the Ionic CLI.
 *
 * The desktop app is a thin client, exactly like the GitHub Action: it never
 * reimplements contract logic, it shells out to `ionic` and renders what comes
 * back. That keeps one source of truth for what a breaking change is.
 *
 * Deliberately free of Electron imports so it can be tested with plain node.
 */

const { spawn } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { DESKTOP_EDITION } = require("./edition");

const IS_WINDOWS = process.platform === "win32";
const EXE = IS_WINDOWS ? "ionic.exe" : "ionic";
const DESKTOP_PROTOCOL = 4;

const PLATFORM_NAMES = {
  win32: "win",
  darwin: "mac",
  linux: "linux",
};

class IonicError extends Error {
  constructor(message, { code = null, stderr = "" } = {}) {
    super(message);
    this.name = "IonicError";
    this.code = code;
    this.stderr = stderr;
  }
}

class IonicNotFound extends IonicError {
  constructor(searched, message = null) {
    super(
      message ||
        "Could not start Ionic Desktop's managed engine. Repair or reinstall Ionic Desktop, " +
          "or choose a compatible Ionic executable from the repair screen."
    );
    this.name = "IonicNotFound";
    this.searched = searched;
  }
}

/**
 * Candidate locations for the CLI, most explicit first.
 * Returns paths only -- existence is checked by the caller.
 */
function candidatePaths(
  { explicitBin = null, env = process.env, appDir = null, resourcesDir = null } = {}
) {
  const candidates = [];

  if (explicitBin) candidates.push(explicitBin);

  // The packaged, checksum-verified sidecar is the zero-setup default. An
  // explicit IONIC_BIN remains first so developers can intentionally test a
  // local CLI build.
  if (resourcesDir) {
    candidates.push(path.join(resourcesDir, "ionic", EXE));
  }

  // IONIC_BIN inherited from the launch environment is useful to developers,
  // but must not unexpectedly outrank the app's bundled managed engine.
  if (env.IONIC_BIN) candidates.push(env.IONIC_BIN);

  // A virtualenv the user has already activated.
  if (env.VIRTUAL_ENV) {
    candidates.push(path.join(env.VIRTUAL_ENV, IS_WINDOWS ? "Scripts" : "bin", EXE));
  }

  // A venv sitting next to the app (how a bundled build would ship it).
  if (appDir) {
    candidates.push(path.join(appDir, ".venv", IS_WINDOWS ? "Scripts" : "bin", EXE));
  }

  // Conventional user installs.
  const home = os.homedir();
  if (home) {
    candidates.push(path.join(home, ".local", "bin", EXE));
    if (IS_WINDOWS) {
      candidates.push(path.join(home, "AppData", "Roaming", "Python", "Scripts", EXE));
    }
  }
  candidates.push("/usr/local/bin/" + EXE);
  candidates.push("/opt/homebrew/bin/" + EXE);

  return candidates;
}

function runtimePlatform(platformName = process.platform) {
  return PLATFORM_NAMES[platformName] || platformName;
}

function managedCandidatePath(resourcesDir) {
  return resourcesDir ? path.join(resourcesDir, "ionic", EXE) : null;
}

function fileSha256(candidate) {
  return crypto.createHash("sha256").update(fs.readFileSync(candidate)).digest("hex");
}

/**
 * Validate the build manifest before executing the packaged CLI sidecar.
 *
 * The manifest lives inside the same read-only app resources directory as the
 * executable. Size and SHA-256 checks catch incomplete packaging, accidental
 * replacement, and local tampering before the process is launched.
 */
function verifyManagedCandidate(
  candidate,
  {
    resourcesDir,
    platformName = process.platform,
    arch = process.arch,
  } = {}
) {
  const expectedCandidate = managedCandidatePath(resourcesDir);
  if (!expectedCandidate || path.resolve(candidate) !== path.resolve(expectedCandidate)) {
    throw new IonicError("Refused to verify a CLI outside Ionic Desktop's managed resources.");
  }

  const manifestPath = path.join(resourcesDir, "ionic", "manifest.json");
  let manifest;
  try {
    manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  } catch (error) {
    throw new IonicError(`The bundled Ionic CLI manifest is missing or invalid: ${error.message}`);
  }

  const executable = manifest?.executable;
  if (
    !manifest ||
    typeof manifest.version !== "string" ||
    !manifest.version ||
    manifest.edition !== DESKTOP_EDITION ||
    manifest.desktop_protocol !== DESKTOP_PROTOCOL ||
    manifest.platform !== runtimePlatform(platformName) ||
    manifest.arch !== arch ||
    !executable ||
    executable.name !== path.basename(candidate) ||
    !Number.isSafeInteger(executable.size) ||
    executable.size <= 0 ||
    typeof executable.sha256 !== "string" ||
    !/^[a-f0-9]{64}$/i.test(executable.sha256)
  ) {
    throw new IonicError("The bundled Ionic CLI manifest is incompatible with this app or computer.");
  }

  let stat;
  try {
    stat = fs.statSync(candidate);
  } catch (error) {
    throw new IonicError(`The bundled Ionic CLI executable is unavailable: ${error.message}`);
  }
  if (!stat.isFile() || stat.size !== executable.size) {
    throw new IonicError("The bundled Ionic CLI failed its size integrity check.");
  }
  if (fileSha256(candidate) !== executable.sha256.toLowerCase()) {
    throw new IonicError("The bundled Ionic CLI failed its SHA-256 integrity check.");
  }
  return manifest;
}

function validateStatusHandshake(data) {
  if (
    !data ||
    typeof data.version !== "string" ||
    data.telemetry !== "none" ||
    !data.registry ||
    typeof data.registry.path !== "string"
  ) {
    throw new IonicError("the status handshake returned an unexpected payload");
  }
  if (data.desktop_protocol !== DESKTOP_PROTOCOL) {
    const found = data.desktop_protocol === undefined ? "none" : String(data.desktop_protocol);
    throw new IonicError(
      `Ionic Desktop requires CLI protocol ${DESKTOP_PROTOCOL}, but the CLI reported ${found}`
    );
  }
  return data;
}

/** Look through PATH for any of `names`. Returns the full path, or null. */
function which(names, env = process.env) {
  const raw = env.PATH || env.Path || "";
  const parts = raw.split(IS_WINDOWS ? ";" : ":").filter(Boolean);
  for (const dir of parts) {
    for (const name of names) {
      const full = path.join(dir, name);
      try {
        if (fs.statSync(full).isFile()) return full;
      } catch {
        /* not there; keep looking */
      }
    }
  }
  return null;
}

/** Look through PATH for the ionic executable. */
function searchPath(env = process.env) {
  return which(IS_WINDOWS ? ["ionic.exe", "ionic.cmd", "ionic.bat"] : ["ionic"], env);
}

/**
 * Resolve the CLI once and remember it.
 * Falls back to `python -m ionic.cli` so a `pip install` without a scripts
 * directory on PATH still works.
 */
function resolveIonic(
  { explicitBin = null, env = process.env, appDir = null, resourcesDir = null } = {}
) {
  const searched = [];
  const managedPath = managedCandidatePath(resourcesDir);

  for (const candidate of candidatePaths({ explicitBin, env, appDir, resourcesDir })) {
    searched.push(candidate);
    const isManaged = managedPath && path.resolve(candidate) === path.resolve(managedPath);
    try {
      if (fs.statSync(candidate).isFile()) {
        if (isManaged) {
          const manifest = verifyManagedCandidate(candidate, { resourcesDir });
          return { command: candidate, args: [], kind: "managed", manifest };
        }
        return { command: candidate, args: [], kind: "executable" };
      }
    } catch (error) {
      if (isManaged && error instanceof IonicError) throw error;
      /* keep looking */
    }
  }

  const onPath = searchPath(env);
  if (onPath) return { command: onPath, args: [], kind: "path" };
  searched.push("(PATH)");

  // Last resort: a Python that can run `-m ionic.cli`. Every candidate is
  // verified to exist, so a failure here is a real "nothing is installed"
  // rather than a spawn error surfacing later from inside the UI.
  for (const python of pythonCandidates(env)) {
    searched.push(`${python} -m ionic.cli`);
    const resolved = python.includes(path.sep) ? statFile(python) : which([python], env);
    if (resolved) {
      return { command: resolved, args: ["-m", "ionic.cli"], kind: "module" };
    }
  }

  throw new IonicNotFound(searched);
}

function statFile(candidate) {
  try {
    return fs.statSync(candidate).isFile() ? candidate : null;
  } catch {
    return null;
  }
}

function pythonCandidates(env = process.env) {
  const out = [];
  if (env.IONIC_PYTHON) out.push(env.IONIC_PYTHON);
  if (env.VIRTUAL_ENV) {
    out.push(
      path.join(
        env.VIRTUAL_ENV,
        IS_WINDOWS ? "Scripts" : "bin",
        IS_WINDOWS ? "python.exe" : "python3"
      )
    );
  }
  out.push(...(IS_WINDOWS ? ["python.exe", "python3.exe"] : ["python3", "python"]));
  return out;
}

/**
 * Run the CLI and capture its output.
 *
 * `check` exits 1 on REQUEST_CHANGES by design, so a non-zero exit is not
 * automatically an error -- the caller decides which codes are expected.
 */
function run(
  args,
  {
    registryPath = null,
    timeoutMs = 180000,
    env = process.env,
    explicitBin = null,
    appDir = null,
    resourcesDir = null,
    allowExitCodes = [0],
    maxOutputBytes = 10 * 1024 * 1024,
  } = {}
) {
  const resolved = resolveIonic({ explicitBin, env, appDir, resourcesDir });
  const childEnv = { ...env };
  if (resolved.kind === "managed") {
    for (const key of Object.keys(childEnv)) {
      if (["PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"].includes(key.toUpperCase())) {
        delete childEnv[key];
      }
    }
  }
  if (registryPath) childEnv.IONIC_REGISTRY = registryPath;
  // Rich would otherwise guess a terminal width from the parent process.
  childEnv.COLUMNS = childEnv.COLUMNS || "120";
  childEnv.NO_COLOR = "1";
  // Windows inherits the active ANSI code page (for example CP950), which
  // cannot encode Ionic's arrows and severity glyphs. The desktop protocol is
  // always UTF-8 regardless of the user's terminal settings.
  childEnv.PYTHONUTF8 = childEnv.PYTHONUTF8 || "1";
  childEnv.PYTHONIOENCODING = childEnv.PYTHONIOENCODING || "utf-8";

  return new Promise((resolve, reject) => {
    let child;
    try {
      child = spawn(resolved.command, [...resolved.args, ...args], {
        env: childEnv,
        windowsHide: true,
      });
    } catch (err) {
      reject(new IonicError(`Could not start ${resolved.command}: ${err.message}`));
      return;
    }

    let stdout = "";
    let stderr = "";
    let outputBytes = 0;
    let settled = false;

    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      child.kill();
      reject(new IonicError(`ionic ${args[0]} timed out after ${Math.round(timeoutMs / 1000)}s`));
    }, timeoutMs);

    const append = (stream, chunk) => {
      if (settled) return;
      if (stream === "stdout") stdout += chunk;
      else stderr += chunk;
      outputBytes += chunk.length;
      if (outputBytes > maxOutputBytes) {
        settled = true;
        clearTimeout(timer);
        child.kill();
        reject(new IonicError(`ionic ${args[0]} produced too much output and was stopped`));
      }
    };

    child.stdout.on("data", (chunk) => append("stdout", chunk));
    child.stderr.on("data", (chunk) => append("stderr", chunk));

    child.on("error", (err) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(
        err.code === "ENOENT"
          ? new IonicNotFound([resolved.command])
          : new IonicError(err.message, { stderr })
      );
    });

    child.on("close", (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (!allowExitCodes.includes(code)) {
        reject(
          new IonicError(
            stderr.trim() || `ionic ${args.join(" ")} exited with code ${code}`,
            { code, stderr }
          )
        );
        return;
      }
      resolve({ code, stdout, stderr });
    });
  });
}

async function runJson(args, options = {}) {
  const { stdout, code } = await run(args, options);
  const trimmed = stdout.trim();
  if (!trimmed) {
    throw new IonicError(`ionic ${args.join(" ")} produced no output`, { code });
  }
  try {
    return { data: JSON.parse(trimmed), code };
  } catch (err) {
    throw new IonicError(
      `Could not parse the output of \`ionic ${args.join(" ")}\`: ${err.message}`,
      { code }
    );
  }
}

const WORKSPACE_REPOSITORY_ID = /^[a-z0-9][a-z0-9._-]{0,63}$/;
const WORKSPACE_AGENT_ID = /^[a-z0-9][a-z0-9._-]{0,127}$/;
const MAX_WORKSPACE_REPOSITORIES = 64;

/**
 * Validate renderer-owned repository entries before any value becomes a CLI
 * argument. Repository paths come from Electron's directory picker, but the
 * check remains here so every workspace operation has the same narrow input.
 */
function normalizeWorkspaceRepositories(repositories) {
  if (!Array.isArray(repositories) || repositories.length === 0) {
    throw new TypeError("workspace repositories must be a non-empty array");
  }
  if (repositories.length > MAX_WORKSPACE_REPOSITORIES) {
    throw new TypeError(`workspace supports at most ${MAX_WORKSPACE_REPOSITORIES} repositories`);
  }

  const ids = new Set();
  const paths = new Set();
  return repositories.map((repository) => {
    if (!repository || typeof repository !== "object" || Array.isArray(repository)) {
      throw new TypeError("each workspace repository must be an object");
    }
    const id = typeof repository.id === "string" ? repository.id.trim().toLowerCase() : "";
    const sourcePath = typeof repository.path === "string" ? repository.path.trim() : "";
    if (!WORKSPACE_REPOSITORY_ID.test(id)) {
      throw new TypeError(
        "repository id must start with a letter or number and use only lowercase letters, numbers, dots, underscores, or dashes"
      );
    }
    if (!sourcePath || sourcePath.length > 4096 || !path.isAbsolute(sourcePath)) {
      throw new TypeError("repository path must be an absolute directory path");
    }
    const resolvedPath = path.resolve(sourcePath);
    const pathKey = process.platform === "win32" ? resolvedPath.toLowerCase() : resolvedPath;
    if (ids.has(id)) throw new TypeError(`repository id ${id} is already in this workspace`);
    if (paths.has(pathKey)) throw new TypeError(`repository path ${resolvedPath} is already in this workspace`);
    ids.add(id);
    paths.add(pathKey);
    return { id, path: resolvedPath };
  });
}

function workspaceRepositoryArgs(repositories) {
  return normalizeWorkspaceRepositories(repositories).flatMap((repository) => [
    "--repo",
    `${repository.id}=${repository.path}`,
  ]);
}

function normalizeWorkspaceAgentSelectors(selectors, repositories) {
  if (selectors === undefined || selectors === null) return [];
  if (!Array.isArray(selectors) || selectors.length > 1000) {
    throw new TypeError("workspace agents must be an array with at most 1000 entries");
  }
  const repositoryIds = new Set(normalizeWorkspaceRepositories(repositories).map(({ id }) => id));
  const seen = new Set();
  return selectors.map((selector) => {
    if (typeof selector !== "string") throw new TypeError("workspace agent selectors must be text");
    const normalized = selector.trim().toLowerCase();
    const slash = normalized.indexOf("/");
    const repositoryId = slash === -1 ? "" : normalized.slice(0, slash);
    const contractId = slash === -1 ? "" : normalized.slice(slash + 1);
    if (
      !repositoryIds.has(repositoryId) ||
      !WORKSPACE_AGENT_ID.test(contractId) ||
      contractId.includes("/")
    ) {
      throw new TypeError(`invalid workspace agent selector ${selector}`);
    }
    if (seen.has(normalized)) throw new TypeError(`workspace agent ${normalized} was selected twice`);
    seen.add(normalized);
    return normalized;
  });
}

function validateExpectedScanId(value) {
  if (typeof value !== "string" || !/^[a-zA-Z0-9._:-]{8,200}$/.test(value.trim())) {
    throw new TypeError(
      "expectedScanId must be the reviewed sync plan token returned by the matching workspace sync preview"
    );
  }
  return value.trim();
}

function workspaceScanArgs(repositories) {
  return ["workspace", "scan", "--json", ...workspaceRepositoryArgs(repositories)];
}

function workspaceCheckArgs({ repositories, failOn = "high", transitive = false } = {}) {
  const normalizedFailOn = typeof failOn === "string" ? failOn.trim().toLowerCase() : "";
  if (!["info", "low", "medium", "high", "critical"].includes(normalizedFailOn)) {
    throw new TypeError("failOn must be info, low, medium, high, or critical");
  }
  const args = [
    "workspace",
    "check",
    "--json",
    ...workspaceRepositoryArgs(repositories),
    "--fail-on",
    normalizedFailOn,
  ];
  if (transitive) args.push("--transitive");
  return args;
}

function workspaceSyncArgs(
  { repositories, apply = false, expectedScanId = null, agents = [] } = {}
) {
  const normalizedRepositories = normalizeWorkspaceRepositories(repositories);
  const args = [
    "workspace",
    "sync",
    "--json",
    ...normalizedRepositories.flatMap((repository) => [
      "--repo",
      `${repository.id}=${repository.path}`,
    ]),
  ];
  const normalizedAgents = normalizeWorkspaceAgentSelectors(agents, normalizedRepositories);
  for (const agent of normalizedAgents) args.push("--agent", agent);
  if (apply) {
    args.push("--apply", "--expected-scan", validateExpectedScanId(expectedScanId));
  }
  return args;
}

function workspaceSyncResult({ data, code }) {
  if (code !== 3) return data;
  const staleConflict = Array.isArray(data?.conflicts)
    ? data.conflicts.find((conflict) =>
        ["stale_scan", "stale_plan", "stale_registry"].includes(conflict?.kind)
      )
    : null;
  throw new IonicError(
    data?.error ||
      staleConflict?.message ||
      "The reviewed sync plan is stale. Generate and review a new sync preview before applying.",
    { code }
  );
}

/* -------------------------------------------------------------------------
 * The operations the UI actually needs
 * ------------------------------------------------------------------------- */

const api = {
  IonicError,
  IonicNotFound,
  resolveIonic,
  candidatePaths,
  searchPath,
  which,
  verifyManagedCandidate,
  validateStatusHandshake,
  run,
  runJson,
  normalizeWorkspaceRepositories,
  normalizeWorkspaceAgentSelectors,
  workspaceRepositoryArgs,
  workspaceScanArgs,
  workspaceCheckArgs,
  workspaceSyncArgs,
  workspaceSyncResult,
  DESKTOP_PROTOCOL,
  DESKTOP_EDITION,
  // Resolution is deliberately stateless. Keep this no-op for callers that
  // want to invalidate a future cache when switching executables.
  resetResolution() {},

  /**
   * Verify the resolved command is Ionic Contracts, not another executable
   * named `ionic` (notably the Ionic Framework CLI).
   */
  async locate(options = {}) {
    const searched = [
      ...candidatePaths(options),
      "(PATH)",
      ...pythonCandidates(options.env || process.env).map((python) => `${python} -m ionic.cli`),
    ];
    let resolved;
    try {
      resolved = resolveIonic(options);
      const { data } = await runJson(["status", "--json"], { ...options, timeoutMs: 15000 });
      validateStatusHandshake(data);
      if (resolved.kind === "managed" && resolved.manifest.version !== data.version) {
        throw new IonicError(
          `the bundled CLI manifest says ${resolved.manifest.version}, but the CLI reported ${data.version}`
        );
      }
      return {
        ...resolved,
        version: data.version,
        desktopProtocol: data.desktop_protocol,
        searched,
      };
    } catch (error) {
      if (!resolved && error?.name === "IonicNotFound") throw error;
      const target = resolved?.command ? `Found ${resolved.command}, but it did not identify as Ionic Contracts.` : null;
      throw new IonicNotFound(
        searched,
        [
          target,
          error?.message,
          "Repair or reinstall Ionic Desktop, or choose a compatible executable from the repair screen.",
        ]
          .filter(Boolean)
          .join(" ")
      );
    }
  },

  async status(options = {}) {
    const { data } = await runJson(["status", "--json"], options);
    return data;
  },

  async runtimeStatus(options = {}) {
    const { data } = await runJson(["runtime", "status", "--json"], {
      ...options,
      timeoutMs: 30000,
    });
    return data;
  },

  async list(options = {}) {
    const { data } = await runJson(["list", "--json"], options);
    return data;
  },

  async show(contractId, options = {}) {
    const { data } = await runJson(["show", contractId, "--json"], options);
    return data;
  },

  async graph(rootId = null, options = {}) {
    const args = ["graph", "--format", "json"];
    if (rootId) args.push("--id", rootId);
    const { data } = await runJson(args, options);
    return data;
  },

  async workspaceScan({ repositories } = {}, options = {}) {
    const args = workspaceScanArgs(repositories);
    const { data } = await runJson(args, { ...options, allowExitCodes: [0, 1] });
    return data;
  },

  async workspaceCheck(
    request = {},
    options = {}
  ) {
    const args = workspaceCheckArgs(request);
    const { data } = await runJson(args, { ...options, allowExitCodes: [0, 1] });
    return data;
  },

  async workspaceSync(
    { repositories, apply = false, expectedScanId = null, agents = [] } = {},
    options = {}
  ) {
    const args = workspaceSyncArgs({ repositories, apply, expectedScanId, agents });
    const result = await runJson(args, { ...options, allowExitCodes: [0, 1, 3] });
    return workspaceSyncResult(result);
  },

  async register(target, { force = true, ...options } = {}) {
    const args = ["register", target];
    if (force) args.push("--force");
    const { stdout } = await run(args, { ...options, allowExitCodes: [0, 1] });
    return stdout;
  },

  async remove(contractId, options = {}) {
    await run(["rm", contractId], options);
    return true;
  },

  /**
   * The core operation. Exit code 1 means REQUEST_CHANGES, which is a result,
   * not a failure -- so both 0 and 1 are allowed through.
   */
  async check({ contractId, against = null, useLlm = false, failOn = "high", transitive = false }, options = {}) {
    const args = ["check", contractId, "--format", "json", "--fail-on", failOn];
    args.push(useLlm ? "--llm" : "--no-llm");
    if (against) args.push("--against", against);
    if (transitive) args.push("--transitive");
    const { data } = await runJson(args, { ...options, allowExitCodes: [0, 1] });
    return data;
  },

  async renderMarkdown({ contractId, against = null, useLlm = false, failOn = "high", transitive = false }, options = {}) {
    const args = ["check", contractId, "--format", "markdown", "--fail-on", failOn];
    args.push(useLlm ? "--llm" : "--no-llm");
    if (against) args.push("--against", against);
    if (transitive) args.push("--transitive");
    const { stdout } = await run(args, { ...options, allowExitCodes: [0, 1] });
    return stdout;
  },
};

module.exports = api;
