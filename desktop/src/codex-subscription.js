"use strict";

const childProcess = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const CODEX_RUNTIME_ID = "openai-codex";
const CODEX_EXECUTABLES = new Set(["codex", "codex.exe"]);
const AUTH_URL_HOSTS = new Set(["auth.openai.com", "chatgpt.com", "www.chatgpt.com"]);
const SAFE_ENVIRONMENT_NAMES = new Set([
  "APPDATA",
  "COLORTERM",
  "COMSPEC",
  "HOME",
  "LANG",
  "LC_ALL",
  "LOCALAPPDATA",
  "NO_COLOR",
  "OS",
  "PATH",
  "PATHEXT",
  "PROGRAMDATA",
  "PROGRAMFILES",
  "PROGRAMFILES(X86)",
  "PROGRAMW6432",
  "SYSTEMDRIVE",
  "SYSTEMROOT",
  "TEMP",
  "TERM",
  "TMP",
  "USERPROFILE",
  "WINDIR",
  "XDG_DATA_HOME",
]);
const DEFAULT_REQUEST_TIMEOUT_MS = 10_000;
const DEFAULT_MAX_LINE_BYTES = 256 * 1024;
const DEFAULT_MAX_STDERR_BYTES = 64 * 1024;
const MAX_MODEL_PAGES = 10;
const MAX_MODELS = 500;
const DEFAULT_BOUNDARY_PROBE_TIMEOUT_MS = 30_000;
const DEFAULT_BOUNDARY_PROBE_OUTPUT_BYTES = 64 * 1024;

const CODEX_APP_SERVER_CONFIG = Object.freeze([
  "analytics.enabled=false",
  "feedback.enabled=false",
  'history.persistence="none"',
  "project_doc_max_bytes=0",
  'shell_environment_policy.inherit="none"',
  "shell_environment_policy.ignore_default_excludes=false",
  'web_search="disabled"',
]);
const CODEX_FORBIDDEN_PROFILE_ENTRIES = new Set([
  "agents.md",
  "config.toml",
  "hooks",
  "memories",
  "plugins",
  "rules",
]);

class CodexSubscriptionError extends Error {
  constructor(message, code = "CODEX_SUBSCRIPTION_ERROR") {
    super(message);
    this.name = "CodexSubscriptionError";
    this.code = code;
  }
}

class CodexRuntimeUnavailable extends CodexSubscriptionError {
  constructor(message) {
    super(message, "CODEX_RUNTIME_UNAVAILABLE");
    this.name = "CodexRuntimeUnavailable";
  }
}

/**
 * Auth is owned by Codex, so the child receives only the small set of ordinary
 * OS variables it needs to find its own profile. Provider/API credentials are
 * deliberately never inherited.
 */
function ionicCodexProfileDirectory(
  source = process.env,
  { platform = process.platform, tempDirectory = os.tmpdir() } = {}
) {
  const value = (name) => {
    const match = Object.entries(source || {}).find(([key, candidate]) =>
      key.toUpperCase() === name && typeof candidate === "string" && candidate
    );
    return match?.[1] || null;
  };
  let base;
  if (platform === "win32") {
    base = value("LOCALAPPDATA") || value("APPDATA") || tempDirectory;
  } else if (platform === "darwin") {
    base = value("HOME")
      ? path.join(value("HOME"), "Library", "Application Support")
      : tempDirectory;
  } else {
    base = value("XDG_DATA_HOME") || (value("HOME")
      ? path.join(value("HOME"), ".local", "share")
      : tempDirectory);
  }
  return path.resolve(base, "Tactico Technologies", "Ionic", "CodexSubscription");
}

function assertCodexProfileBoundary(profileDirectory, { fsImpl = fs } = {}) {
  const profile = path.resolve(profileDirectory);
  fsImpl.mkdirSync(profile, { recursive: true, mode: 0o700 });
  try { fsImpl.chmodSync(profile, 0o700); } catch { /* Windows and inherited ACLs */ }
  const present = fsImpl.readdirSync(profile)
    .map((name) => String(name).toLowerCase())
    .filter((name) => CODEX_FORBIDDEN_PROFILE_ENTRIES.has(name));
  if (present.length) {
    throw new CodexRuntimeUnavailable(
      `The dedicated Ionic Codex profile contains configuration or instruction sources (${[...new Set(present)].sort().join(", ")}). Remove them before linking.`
    );
  }
  const skills = path.join(profile, "skills");
  if (fsImpl.existsSync(skills)) {
    const samePath = (left, right) => process.platform === "win32"
      ? left.toLowerCase() === right.toLowerCase()
      : left === right;
    if (
      !fsImpl.statSync(skills).isDirectory() ||
      !samePath(fsImpl.realpathSync(skills), path.resolve(skills))
    ) {
      throw new CodexRuntimeUnavailable(
        "The dedicated Ionic Codex profile contains an invalid skills entry."
      );
    }
    const userSkills = fsImpl.readdirSync(skills).filter((name) => name !== ".system");
    if (userSkills.length) {
      throw new CodexRuntimeUnavailable(
        "The dedicated Ionic Codex profile contains user-added skills. Remove them before linking."
      );
    }
    const systemSkills = path.join(skills, ".system");
    if (
      fsImpl.existsSync(systemSkills) &&
      (!fsImpl.statSync(systemSkills).isDirectory() ||
        !samePath(fsImpl.realpathSync(systemSkills), path.resolve(systemSkills)))
    ) {
      throw new CodexRuntimeUnavailable(
        "The dedicated Ionic Codex profile contains an invalid system-skills entry."
      );
    }
  }
  return profile;
}

function cleanCodexEnvironment(source = process.env, { profileDirectory } = {}) {
  const output = {};
  for (const [name, value] of Object.entries(source || {})) {
    const normalized = name.toUpperCase();
    if (!SAFE_ENVIRONMENT_NAMES.has(normalized)) continue;
    if (typeof value === "string" && value) output[normalized] = value;
  }
  output.NO_COLOR = "1";
  output.IONIC_RUNTIME_BOUNDARY = "1";
  output.CODEX_HOME = path.resolve(
    profileDirectory || ionicCodexProfileDirectory(source)
  );
  return output;
}

function codexAppServerArgs() {
  const args = ["app-server", "--strict-config"];
  for (const setting of CODEX_APP_SERVER_CONFIG) args.push("--config", setting);
  return args;
}

function validateNativeCodexExecutable(candidate, { fsImpl = fs } = {}) {
  if (typeof candidate !== "string" || !candidate.trim() || !path.isAbsolute(candidate)) {
    throw new CodexRuntimeUnavailable(
      "Ionic requires the absolute path reported by an installed native Codex runtime"
    );
  }
  if (/\0|\r|\n/.test(candidate)) {
    throw new CodexRuntimeUnavailable("The Codex executable path is invalid");
  }
  const resolved = path.resolve(candidate);
  const name = path.basename(resolved).toLowerCase();
  if (!CODEX_EXECUTABLES.has(name) || [".cmd", ".bat", ".ps1"].includes(path.extname(name))) {
    throw new CodexRuntimeUnavailable(
      "Shell wrappers are not accepted; install the official native Codex executable"
    );
  }
  let stat;
  try {
    stat = fsImpl.statSync(resolved);
  } catch {
    throw new CodexRuntimeUnavailable("The native Codex executable is no longer available");
  }
  if (!stat.isFile()) {
    throw new CodexRuntimeUnavailable("The configured Codex runtime is not an executable file");
  }
  return resolved;
}

function schemaProvesRestrictedReadRoots(schema) {
  const definitionRef = (value) => {
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    if (typeof value.$ref === "string") return value.$ref;
    for (const key of ["allOf", "anyOf", "oneOf"]) {
      if (!Array.isArray(value[key])) continue;
      for (const child of value[key]) {
        const found = definitionRef(child);
        if (found) return found;
      }
    }
    return null;
  };
  const rootProperties = schema?.properties;
  const requiredTurnFields = [
    "approvalPolicy",
    "cwd",
    "effort",
    "input",
    "model",
    "outputSchema",
    "sandboxPolicy",
    "threadId",
  ];
  if (!rootProperties || !requiredTurnFields.every((key) => Object.hasOwn(rootProperties, key))) {
    return false;
  }
  const definitions = schema?.definitions;
  const branches = definitions?.SandboxPolicy?.oneOf;
  if (!definitions || !Array.isArray(branches)) return false;
  for (const branch of branches) {
    const properties = branch?.properties;
    if (!properties || !JSON.stringify(properties.type).includes("readOnly")) continue;
    const access = properties.access;
    if (!access || typeof access !== "object") return false;
    const reference = definitionRef(access);
    const accessSchema = reference?.startsWith("#/definitions/")
      ? definitions[reference.slice("#/definitions/".length)]
      : access;
    const encoded = JSON.stringify(accessSchema || {});
    return ["restricted", "readableRoots", "includePlatformDefaults"]
      .every((marker) => encoded.includes(marker));
  }
  return false;
}

function schemaProvesEphemeralThread(schema) {
  const properties = schema?.properties;
  return Boolean(properties) && [
    "approvalPolicy",
    "baseInstructions",
    "cwd",
    "developerInstructions",
    "ephemeral",
    "model",
    "sandbox",
  ].every((key) => Object.hasOwn(properties, key));
}

function boundaryStatus(capable, unsupportedControls = [], message = "") {
  return {
    semanticReviewCapable: capable,
    unsupportedControls: [...unsupportedControls],
    semanticReviewMessage: cleanText(message, 500),
  };
}

/** Generate the installed version's official protocol schema locally. */
function probeCodexSemanticBoundary(
  executable,
  {
    spawnImpl = childProcess.spawn,
    fsImpl = fs,
    env = process.env,
    profileDirectory,
    timeoutMs = DEFAULT_BOUNDARY_PROBE_TIMEOUT_MS,
    maxOutputBytes = DEFAULT_BOUNDARY_PROBE_OUTPUT_BYTES,
  } = {}
) {
  const target = validateNativeCodexExecutable(executable, { fsImpl });
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 100 || timeoutMs > 60_000) {
    throw new TypeError("Codex boundary probe timeout is invalid");
  }
  if (!Number.isSafeInteger(maxOutputBytes) || maxOutputBytes < 1024 || maxOutputBytes > 1024 * 1024) {
    throw new TypeError("Codex boundary probe output limit is invalid");
  }
  const directory = fsImpl.mkdtempSync(path.join(os.tmpdir(), "ionic-codex-boundary-"));
  const outputDirectory = path.join(directory, "schema");
  const profile = assertCodexProfileBoundary(
    profileDirectory || ionicCodexProfileDirectory(env),
    { fsImpl }
  );
  let child;
  try {
    child = spawnImpl(target, ["app-server", "generate-json-schema", "--out", outputDirectory], {
      cwd: directory,
      env: cleanCodexEnvironment(env, { profileDirectory: profile }),
      shell: false,
      windowsHide: true,
      stdio: ["pipe", "pipe", "pipe"],
    });
  } catch {
    fsImpl.rmSync(directory, { recursive: true, force: true });
    return Promise.resolve(boundaryStatus(
      false,
      [],
      "Semantic review is unavailable because Ionic could not start the installed Codex CLI for a local boundary check. Sign-in and model catalog remain available."
    ));
  }

  return new Promise((resolve) => {
    let settled = false;
    let output = Buffer.alloc(0);
    const finish = (result) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      try { child?.kill?.(); } catch { /* best effort */ }
      try { fsImpl.rmSync(directory, { recursive: true, force: true }); } catch { /* best effort */ }
      resolve(result);
    };
    const append = (chunk) => {
      if (settled) return;
      const next = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
      if (output.length + next.length > maxOutputBytes) {
        finish(boundaryStatus(
          false,
          [],
          "Semantic review is unavailable because the installed Codex CLI exceeded the local boundary-check output limit. Sign-in and model catalog remain available."
        ));
        return;
      }
      output = Buffer.concat([output, next]);
    };
    const complete = (exitCode) => {
      if (settled) return;
      if (typeof exitCode === "number" && exitCode !== 0) {
        finish(boundaryStatus(
          false,
          ["app-server.generate-json-schema"],
          "Semantic review is unavailable because the installed Codex CLI could not generate its version-matched app-server schema. Sign-in and model catalog remain available."
        ));
        return;
      }
      try {
        const readSchema = (name) => {
          const schemaPath = path.join(outputDirectory, "v2", name);
          const stat = fsImpl.statSync(schemaPath);
          if (!stat.isFile() || stat.size > 2 * 1024 * 1024) {
            throw new Error("invalid schema file");
          }
          return JSON.parse(fsImpl.readFileSync(schemaPath, "utf8"));
        };
        const turnSchema = readSchema("TurnStartParams.json");
        const threadSchema = readSchema("ThreadStartParams.json");
        if (
          !schemaProvesRestrictedReadRoots(turnSchema) ||
          !schemaProvesEphemeralThread(threadSchema)
        ) {
          finish(boundaryStatus(
            false,
            ["sandboxPolicy.readOnly.access.restricted.readableRoots"],
            "Semantic review is unavailable because this Codex app-server version does not prove restricted readable roots. Sign-in and model catalog remain available."
          ));
          return;
        }
        finish(boundaryStatus(
          true,
          [],
          "The installed Codex app-server advertises restricted read-only roots, structured output, and explicit approval controls required by Ionic."
        ));
        return;
      } catch {
        finish(boundaryStatus(
          false,
          ["sandboxPolicy.readOnly.access.restricted.readableRoots"],
          "Semantic review is unavailable because Ionic could not verify the installed Codex app-server schema. Sign-in and model catalog remain available."
        ));
      }
    };
    const timer = setTimeout(() => finish(boundaryStatus(
      false,
      [],
      "Semantic review is unavailable because the installed Codex CLI did not finish the local boundary check. Sign-in and model catalog remain available."
    )), timeoutMs);

    if (!child?.stdin || !child?.stdout || !child?.stderr) {
      finish(boundaryStatus(
        false,
        [],
        "Semantic review is unavailable because the installed Codex CLI did not provide a bounded process transport. Sign-in and model catalog remain available."
      ));
      return;
    }
    child.stdout.on("data", append);
    child.stderr.on("data", append);
    child.once("error", () => finish(boundaryStatus(
      false,
      [],
      "Semantic review is unavailable because the installed Codex CLI failed during the local boundary check. Sign-in and model catalog remain available."
    )));
    child.once("close", complete);
    child.stdin.end();
  });
}

/**
 * The normal source is Ionic's passive runtime-status handshake. An embedding
 * host may alternatively provide exact, trusted absolute paths; arbitrary
 * renderer paths are never accepted.
 */
async function resolveCodexExecutable(
  runtimeStatus,
  { allowlistedExecutablePaths = [], fsImpl = fs } = {}
) {
  if (typeof runtimeStatus !== "function") {
    throw new CodexRuntimeUnavailable("Codex runtime discovery is unavailable");
  }
  let payload;
  try {
    payload = await runtimeStatus();
  } catch {
    throw new CodexRuntimeUnavailable("Codex runtime discovery failed");
  }
  const runtimes = Array.isArray(payload?.runtimes) ? payload.runtimes : [];
  const runtime = runtimes.find((entry) => entry?.id === CODEX_RUNTIME_ID);
  if (runtime?.installed && runtime?.available && typeof runtime.executable === "string") {
    return validateNativeCodexExecutable(runtime.executable, { fsImpl });
  }

  for (const candidate of allowlistedExecutablePaths) {
    try {
      return validateNativeCodexExecutable(candidate, { fsImpl });
    } catch {
      // Try the next exact path supplied by the trusted embedding host.
    }
  }
  throw new CodexRuntimeUnavailable(
    "Install the official native Codex CLI before linking a ChatGPT subscription"
  );
}

function isAllowedCodexAuthUrl(raw) {
  try {
    const url = new URL(raw);
    return (
      url.protocol === "https:" &&
      !url.username &&
      !url.password &&
      (!url.port || url.port === "443") &&
      AUTH_URL_HOSTS.has(url.hostname.toLowerCase())
    );
  } catch {
    return false;
  }
}

function cleanText(value, maxLength = 256) {
  if (typeof value !== "string") return null;
  const output = value.replace(/[\0\r\n]/g, " ").trim();
  return output ? output.slice(0, maxLength) : null;
}

function safeLoginId(value) {
  const loginId = cleanText(value, 200);
  if (!loginId || !/^[A-Za-z0-9._:-]{4,200}$/.test(loginId)) {
    throw new CodexSubscriptionError("Codex returned an invalid login identifier");
  }
  return loginId;
}

function safePlanType(value) {
  const plan = cleanText(value, 64);
  return plan && /^[A-Za-z0-9._ -]{1,64}$/.test(plan) ? plan : null;
}

const CODEX_REASONING_EFFORTS = new Set(["low", "medium", "high", "xhigh", "max"]);

function safeModelId(value) {
  const model = cleanText(value, 200);
  return model && /^[A-Za-z0-9._:/-]{1,200}$/.test(model) ? model : null;
}

function publicClientInfo(raw, version = "0.0.0") {
  const name = cleanText(raw?.name, 64);
  const title = cleanText(raw?.title, 120);
  const clientVersion = cleanText(raw?.version, 64) || cleanText(version, 64) || "0.0.0";
  return {
    name: name && /^[A-Za-z0-9._-]{1,64}$/.test(name) ? name : "ionic",
    title: title || "Ionic",
    version: clientVersion,
  };
}

function publicCodexModel(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw) || raw.hidden === true) return null;
  const id = safeModelId(raw.id || raw.model);
  if (!id) return null;
  const efforts = Array.isArray(raw.supportedReasoningEfforts)
    ? raw.supportedReasoningEfforts
        .map((entry) => cleanText(entry?.reasoningEffort, 20)?.toLowerCase())
        .filter((effort) => CODEX_REASONING_EFFORTS.has(effort))
    : [];
  const supportedEfforts = [...new Set(efforts)];
  const requestedDefault = cleanText(raw.defaultReasoningEffort, 20)?.toLowerCase();
  const defaultEffort = supportedEfforts.includes(requestedDefault) ? requestedDefault : null;
  return {
    id,
    displayName: cleanText(raw.displayName, 120) || id,
    description: cleanText(raw.description, 500),
    isDefault: raw.isDefault === true,
    supportedEfforts,
    defaultEffort,
  };
}

function publicBoundaryStatus(raw) {
  return {
    semanticReviewCapable: raw?.semanticReviewCapable === true
      ? true
      : raw?.semanticReviewCapable === false
        ? false
        : null,
    unsupportedControls: Array.isArray(raw?.unsupportedControls)
      ? raw.unsupportedControls
          .map((value) => cleanText(value, 160))
          .filter((value, index, values) =>
            Boolean(value) && /^(?:--)?[A-Za-z0-9._:-]+$/u.test(value) && values.indexOf(value) === index
          )
          .slice(0, 64)
      : [],
    semanticReviewMessage: cleanText(raw?.semanticReviewMessage, 500),
  };
}

function publicAccountStatus(result, semanticBoundary = null) {
  const account = result?.account;
  const rawType = account && typeof account === "object" ? account.type : null;
  const authMode = {
    chatgpt: "chatgpt",
    apiKey: "api_key",
    amazonBedrock: "amazon_bedrock",
    chatgptAuthTokens: "externally_managed",
  }[rawType] || "none";
  return {
    provider: CODEX_RUNTIME_ID,
    installed: true,
    available: true,
    connected: authMode === "chatgpt",
    authMode,
    planType: authMode === "chatgpt" ? safePlanType(account?.planType) : null,
    requiresOpenaiAuth: result?.requiresOpenaiAuth === true,
    authenticationInspected: true,
    ...publicBoundaryStatus(semanticBoundary),
  };
}

function unavailableStatus(message) {
  return {
    provider: CODEX_RUNTIME_ID,
    installed: false,
    available: false,
    connected: false,
    authMode: "none",
    planType: null,
    requiresOpenaiAuth: true,
    message: cleanText(message, 300) || "Codex subscription linking is unavailable",
    authenticationInspected: false,
    semanticReviewCapable: false,
    unsupportedControls: [],
    semanticReviewMessage: "Semantic review is unavailable because the Codex runtime could not be resolved.",
  };
}

class CodexAppServerClient {
  constructor({
    executable,
    spawnImpl = childProcess.spawn,
    env = process.env,
    version = "0.0.0",
    clientInfo,
    requestTimeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
    maxLineBytes = DEFAULT_MAX_LINE_BYTES,
    maxStderrBytes = DEFAULT_MAX_STDERR_BYTES,
    onNotification = () => {},
    profileDirectory,
    fsImpl = fs,
  }) {
    this.executable = executable;
    this.spawnImpl = spawnImpl;
    this.fsImpl = fsImpl;
    this.profileDirectory = path.resolve(
      profileDirectory || ionicCodexProfileDirectory(env)
    );
    this.environment = cleanCodexEnvironment(env, {
      profileDirectory: this.profileDirectory,
    });
    this.clientInfo = publicClientInfo(clientInfo, version);
    this.requestTimeoutMs = requestTimeoutMs;
    this.maxLineBytes = maxLineBytes;
    this.maxStderrBytes = maxStderrBytes;
    this.onNotification = onNotification;
    this.child = null;
    this.buffer = Buffer.alloc(0);
    this.stderrBytes = 0;
    this.nextId = 1;
    this.pending = new Map();
    this.startPromise = null;
    this.closed = false;
  }

  async start() {
    if (this.closed) throw new CodexSubscriptionError("The Codex session is closed");
    if (this.child) return this;
    if (this.startPromise) return this.startPromise;
    this.startPromise = this._start();
    try {
      return await this.startPromise;
    } finally {
      this.startPromise = null;
    }
  }

  async _start() {
    assertCodexProfileBoundary(this.profileDirectory, { fsImpl: this.fsImpl });
    let child;
    try {
      child = this.spawnImpl(this.executable, codexAppServerArgs(), {
        cwd: this.profileDirectory,
        env: this.environment,
        shell: false,
        windowsHide: true,
        stdio: ["pipe", "pipe", "pipe"],
      });
    } catch {
      throw new CodexRuntimeUnavailable("The native Codex app-server could not be started");
    }
    if (!child?.stdin || !child?.stdout || !child?.stderr) {
      try { child?.kill?.(); } catch { /* best effort */ }
      throw new CodexRuntimeUnavailable("The Codex app-server did not provide a stdio transport");
    }
    this.child = child;
    child.stdout.on("data", (chunk) => this._onStdout(chunk));
    child.stderr.on("data", (chunk) => this._onStderr(chunk));
    child.once("error", () => this._fail("The Codex app-server stopped unexpectedly"));
    child.once("exit", () => this._fail("The Codex app-server stopped unexpectedly"));

    await this.request("initialize", {
      clientInfo: this.clientInfo,
    });
    this.notify("initialized", {});
    return this;
  }

  request(method, params = {}) {
    if (!this.child || this.closed) {
      return Promise.reject(new CodexSubscriptionError("The Codex app-server is not running"));
    }
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new CodexSubscriptionError("The Codex app-server request timed out", "CODEX_TIMEOUT"));
        this.close();
      }, this.requestTimeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      try {
        this._write({ method, id, params });
      } catch (error) {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(error);
      }
    });
  }

  notify(method, params = {}) {
    this._write({ method, params });
  }

  _write(message) {
    if (!this.child || this.closed || this.child.stdin.destroyed) {
      throw new CodexSubscriptionError("The Codex app-server is not running");
    }
    const line = `${JSON.stringify(message)}\n`;
    if (Buffer.byteLength(line) > this.maxLineBytes) {
      throw new CodexSubscriptionError("The Codex app-server request exceeded its size limit");
    }
    this.child.stdin.write(line);
  }

  _onStdout(chunk) {
    if (this.closed) return;
    const incoming = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    this.buffer = Buffer.concat([this.buffer, incoming]);
    if (this.buffer.length > this.maxLineBytes && this.buffer.indexOf(0x0a) === -1) {
      this._fail("The Codex app-server exceeded its message limit");
      return;
    }
    while (!this.closed) {
      const newline = this.buffer.indexOf(0x0a);
      if (newline === -1) break;
      const line = this.buffer.subarray(0, newline);
      this.buffer = this.buffer.subarray(newline + 1);
      if (line.length > this.maxLineBytes) {
        this._fail("The Codex app-server exceeded its message limit");
        return;
      }
      if (!line.length) continue;
      let message;
      try {
        message = JSON.parse(line.toString("utf8").replace(/\r$/, ""));
      } catch {
        this._fail("The Codex app-server returned invalid JSONL");
        return;
      }
      this._dispatch(message);
    }
  }

  _onStderr(chunk) {
    this.stderrBytes += Buffer.byteLength(chunk);
    if (this.stderrBytes > this.maxStderrBytes) {
      this._fail("The Codex app-server exceeded its diagnostic output limit");
    }
  }

  _dispatch(message) {
    if (!message || typeof message !== "object" || Array.isArray(message)) {
      this._fail("The Codex app-server returned an invalid message");
      return;
    }
    if (Object.hasOwn(message, "id") && !Object.hasOwn(message, "method")) {
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      clearTimeout(pending.timer);
      if (message.error) {
        const detail = cleanText(message.error?.message, 300) || "Codex rejected the request";
        pending.reject(new CodexSubscriptionError(detail, "CODEX_RPC_ERROR"));
      } else {
        pending.resolve(message.result);
      }
      return;
    }
    if (Object.hasOwn(message, "id") && typeof message.method === "string") {
      // Ionic never enables externally-managed token or attestation
      // capabilities, so no server-initiated request is accepted.
      try {
        this._write({
          id: message.id,
          error: { code: -32601, message: "Unsupported by Ionic's auth-only client" },
        });
      } catch {
        this._fail("The Codex app-server request could not be rejected safely");
      }
      return;
    }
    if (typeof message.method === "string") {
      this.onNotification(message.method, message.params || {});
    }
  }

  _fail(message) {
    if (this.closed) return;
    const error = new CodexSubscriptionError(message);
    this.closed = true;
    const child = this.child;
    this.child = null;
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
    try { child?.kill?.(); } catch { /* best effort */ }
  }

  close() {
    this._fail("The Codex app-server session was closed");
  }
}

function createCodexSubscriptionService({
  runtimeStatus,
  allowlistedExecutablePaths = [],
  fsImpl = fs,
  spawnImpl = childProcess.spawn,
  env = process.env,
  openExternal,
  version = "0.0.0",
  clientInfo,
  requestTimeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
  maxLineBytes = DEFAULT_MAX_LINE_BYTES,
  maxStderrBytes = DEFAULT_MAX_STDERR_BYTES,
  semanticBoundaryProbe = probeCodexSemanticBoundary,
  profileDirectory,
} = {}) {
  if (typeof openExternal !== "function") {
    throw new TypeError("Codex subscription linking requires a trusted external-browser opener");
  }
  let client = null;
  let clientPromise = null;
  let semanticBoundaryCache = null;
  let semanticBoundaryExecutable = null;
  const activeLogins = new Set();
  const completedLogins = new Set();

  function onNotification(method, params) {
    if (method === "account/login/completed" && typeof params?.loginId === "string") {
      activeLogins.delete(params.loginId);
      completedLogins.add(params.loginId);
    }
  }

  async function getClient() {
    if (client && !client.closed) return client;
    if (clientPromise) return clientPromise;
    clientPromise = (async () => {
      const executable = await resolveCodexExecutable(runtimeStatus, {
        allowlistedExecutablePaths,
        fsImpl,
      });
      const next = new CodexAppServerClient({
        executable,
        spawnImpl,
        env,
        version,
        clientInfo,
        requestTimeoutMs,
        maxLineBytes,
        maxStderrBytes,
        onNotification,
        profileDirectory,
        fsImpl,
      });
      await next.start();
      client = next;
      return next;
    })();
    try {
      return await clientPromise;
    } finally {
      clientPromise = null;
    }
  }

  async function semanticBoundary(executable) {
    if (semanticBoundaryCache && semanticBoundaryExecutable === executable) {
      return semanticBoundaryCache;
    }
    try {
      semanticBoundaryCache = publicBoundaryStatus(await semanticBoundaryProbe(executable, {
        spawnImpl,
        fsImpl,
        env,
        profileDirectory,
      }));
    } catch {
      semanticBoundaryCache = boundaryStatus(
        false,
        [],
        "Semantic review is unavailable because Ionic could not complete the local Codex boundary check. Sign-in and model catalog remain available."
      );
    }
    semanticBoundaryExecutable = executable;
    return semanticBoundaryCache;
  }

  async function status({ probeAuthentication = true } = {}) {
    try {
      if (!probeAuthentication) {
        await resolveCodexExecutable(runtimeStatus, {
          allowlistedExecutablePaths,
          fsImpl,
        });
        return {
          provider: CODEX_RUNTIME_ID,
          installed: true,
          available: true,
          connected: null,
          authMode: "unknown",
          planType: null,
          requiresOpenaiAuth: true,
          authenticationInspected: false,
          message: "Installed. Authentication has not been inspected.",
          semanticReviewCapable: null,
          unsupportedControls: [],
          semanticReviewMessage: "The semantic-review boundary has not been checked.",
        };
      }
      const appServer = await getClient();
      const boundary = await semanticBoundary(appServer.executable);
      return publicAccountStatus(
        await appServer.request("account/read", { refreshToken: false }),
        boundary
      );
    } catch (error) {
      if (error instanceof CodexRuntimeUnavailable) return unavailableStatus(error.message);
      throw error;
    }
  }

  async function models() {
    const appServer = await getClient();
    const models = [];
    const modelIds = new Set();
    const seenCursors = new Set();
    let cursor = null;
    let truncated = false;
    for (let page = 0; page < MAX_MODEL_PAGES; page += 1) {
      const result = await appServer.request("model/list", {
        limit: Math.min(100, MAX_MODELS - models.length),
        includeHidden: false,
        ...(cursor ? { cursor } : {}),
      });
      for (const raw of Array.isArray(result?.data) ? result.data : []) {
        const model = publicCodexModel(raw);
        if (!model || modelIds.has(model.id)) continue;
        modelIds.add(model.id);
        models.push(model);
        if (models.length >= MAX_MODELS) break;
      }
      const nextCursor = cleanText(result?.nextCursor, 500);
      if (!nextCursor) {
        cursor = null;
        break;
      }
      if (models.length >= MAX_MODELS || seenCursors.has(nextCursor)) {
        truncated = true;
        break;
      }
      seenCursors.add(nextCursor);
      cursor = nextCursor;
      if (page === MAX_MODEL_PAGES - 1) truncated = true;
    }
    if (!models.length) {
      throw new CodexSubscriptionError("Codex did not report any models available to this account");
    }
    return {
      provider: CODEX_RUNTIME_ID,
      source: "codex_app_server",
      models,
      truncated,
    };
  }

  async function beginLogin(mode = "browser") {
    if (!new Set(["browser", "device"]).has(mode)) {
      throw new TypeError("Codex login mode must be browser or device");
    }
    const appServer = await getClient();
    const result = await appServer.request(
      "account/login/start",
      mode === "browser"
        ? { type: "chatgpt", useHostedLoginSuccessPage: true, appBrand: "chatgpt" }
        : { type: "chatgptDeviceCode" }
    );
    const expectedType = mode === "browser" ? "chatgpt" : "chatgptDeviceCode";
    if (result?.type !== expectedType) {
      throw new CodexSubscriptionError("Codex returned an unexpected login response");
    }
    const loginId = safeLoginId(result?.loginId);
    const authUrl = mode === "browser" ? result?.authUrl : result?.verificationUrl;
    if (!isAllowedCodexAuthUrl(authUrl)) {
      await appServer.request("account/login/cancel", { loginId }).catch(() => {});
      throw new CodexSubscriptionError("Codex returned an untrusted authorization URL");
    }
    activeLogins.add(loginId);
    if (completedLogins.delete(loginId)) activeLogins.delete(loginId);
    try {
      const opened = await openExternal(authUrl);
      if (opened === false) throw new Error("browser open was declined");
    } catch {
      activeLogins.delete(loginId);
      await appServer.request("account/login/cancel", { loginId }).catch(() => {});
      throw new CodexSubscriptionError("ChatGPT authorization could not be opened safely");
    }
    if (mode === "device") {
      const userCode = cleanText(result?.userCode, 64);
      if (!userCode || !/^[A-Za-z0-9-]{4,64}$/.test(userCode)) {
        activeLogins.delete(loginId);
        await appServer.request("account/login/cancel", { loginId }).catch(() => {});
        throw new CodexSubscriptionError("Codex returned an invalid device code");
      }
      return {
        provider: CODEX_RUNTIME_ID,
        state: "awaiting_user",
        mode,
        loginId,
        verificationUrl: authUrl,
        userCode,
      };
    }
    return {
      provider: CODEX_RUNTIME_ID,
      state: "awaiting_user",
      mode,
      loginId,
    };
  }

  async function cancelLogin(loginId) {
    const normalized = safeLoginId(loginId);
    if (!activeLogins.has(normalized)) return false;
    const appServer = await getClient();
    await appServer.request("account/login/cancel", { loginId: normalized });
    activeLogins.delete(normalized);
    return true;
  }

  async function logout() {
    const appServer = await getClient();
    await appServer.request("account/logout", {});
    activeLogins.clear();
    return {
      provider: CODEX_RUNTIME_ID,
      available: true,
      connected: false,
      authMode: "none",
      planType: null,
      requiresOpenaiAuth: true,
      ...publicBoundaryStatus(semanticBoundaryCache),
    };
  }

  function close() {
    activeLogins.clear();
    completedLogins.clear();
    client?.close();
    client = null;
    semanticBoundaryCache = null;
    semanticBoundaryExecutable = null;
  }

  return { status, models, beginLogin, cancelLogin, logout, close };
}

module.exports = {
  AUTH_URL_HOSTS,
  CODEX_APP_SERVER_CONFIG,
  CODEX_FORBIDDEN_PROFILE_ENTRIES,
  CODEX_RUNTIME_ID,
  CodexAppServerClient,
  CodexRuntimeUnavailable,
  CodexSubscriptionError,
  assertCodexProfileBoundary,
  cleanCodexEnvironment,
  codexAppServerArgs,
  createCodexSubscriptionService,
  isAllowedCodexAuthUrl,
  ionicCodexProfileDirectory,
  publicAccountStatus,
  publicBoundaryStatus,
  publicClientInfo,
  publicCodexModel,
  probeCodexSemanticBoundary,
  resolveCodexExecutable,
  schemaProvesEphemeralThread,
  schemaProvesRestrictedReadRoots,
  validateNativeCodexExecutable,
};
