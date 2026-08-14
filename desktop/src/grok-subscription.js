"use strict";

const childProcess = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const {
  SUBSCRIPTION_AUTHORIZATION_HOSTS,
  sanitizeSubscriptionAuthorizationUrl,
} = require("./external-url-policy");

const GROK_RUNTIME_ID = "xai-grok-build";
const GROK_EXECUTABLES = new Set(["grok", "grok.exe"]);
const GROK_AUTH_HOSTS = new Set(SUBSCRIPTION_AUTHORIZATION_HOSTS["xai-grok-build"]);
const SAFE_ENVIRONMENT_NAMES = new Set([
  "APPDATA",
  "COLORTERM",
  "COMSPEC",
  "GROK_HOME",
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
  "XDG_CONFIG_HOME",
]);
const DEFAULT_LOGIN_TIMEOUT_MS = 5 * 60 * 1000;
const DEFAULT_OPERATION_TIMEOUT_MS = 15 * 1000;
const DEFAULT_PROBE_TIMEOUT_MS = 10 * 1000;
const DEFAULT_MAX_OUTPUT_BYTES = 64 * 1024;
const DEFAULT_MAX_INPUT_BYTES = 16 * 1024;
const MAX_GROK_MODELS = 200;
const GROK_REASONING_EFFORTS = new Set(["low", "medium", "high", "xhigh"]);
const GROK_ACCOUNT_AUTH_METHODS = ["grok.com"];
const TERMINAL_LOGIN_STATES = new Set([
  "cancelled",
  "connected",
  "failed",
  "timed_out",
]);

class GrokSubscriptionError extends Error {
  constructor(message, code = "GROK_SUBSCRIPTION_ERROR") {
    super(message);
    this.name = "GrokSubscriptionError";
    this.code = code;
  }
}

class GrokRuntimeUnavailable extends GrokSubscriptionError {
  constructor(message) {
    super(message, "GROK_RUNTIME_UNAVAILABLE");
    this.name = "GrokRuntimeUnavailable";
  }
}

/**
 * The official CLI owns its saved login. Ionic forwards only ordinary OS
 * paths needed to locate that profile. API keys, bearer tokens, cloud
 * credentials, proxy credentials, and arbitrary GROK_* overrides are never
 * inherited by the subscription process.
 */
function cleanGrokEnvironment(source = process.env) {
  const output = {};
  for (const [name, value] of Object.entries(source || {})) {
    if (!SAFE_ENVIRONMENT_NAMES.has(name.toUpperCase())) continue;
    if (typeof value === "string" && value) output[name] = value;
  }
  output.NO_COLOR = "1";
  output.IONIC_RUNTIME_BOUNDARY = "1";
  return output;
}

function validateNativeGrokExecutable(candidate, { fsImpl = fs } = {}) {
  if (typeof candidate !== "string" || !candidate.trim() || !path.isAbsolute(candidate)) {
    throw new GrokRuntimeUnavailable(
      "Ionic requires the absolute path reported by an installed native Grok Build runtime"
    );
  }
  if (/\0|\r|\n/.test(candidate)) {
    throw new GrokRuntimeUnavailable("The Grok Build executable path is invalid");
  }
  const resolved = path.resolve(candidate);
  const name = path.basename(resolved).toLowerCase();
  if (!GROK_EXECUTABLES.has(name) || [".cmd", ".bat", ".ps1"].includes(path.extname(name))) {
    throw new GrokRuntimeUnavailable(
      "Shell wrappers are not accepted; install the official native Grok Build executable"
    );
  }
  let stat;
  try {
    stat = fsImpl.statSync(resolved);
  } catch {
    throw new GrokRuntimeUnavailable("The native Grok Build executable is no longer available");
  }
  if (!stat.isFile()) {
    throw new GrokRuntimeUnavailable("The configured Grok Build runtime is not an executable file");
  }
  return resolved;
}

async function resolveGrokExecutable(
  runtimeStatus,
  { allowlistedExecutablePaths = [], fsImpl = fs } = {}
) {
  if (typeof runtimeStatus !== "function") {
    throw new GrokRuntimeUnavailable("Grok Build runtime discovery is unavailable");
  }
  let payload;
  try {
    payload = await runtimeStatus();
  } catch {
    throw new GrokRuntimeUnavailable("Grok Build runtime discovery failed");
  }
  const runtimes = Array.isArray(payload?.runtimes) ? payload.runtimes : [];
  const runtime = runtimes.find((entry) => entry?.id === GROK_RUNTIME_ID);
  if (runtime?.installed && typeof runtime.executable === "string") {
    return {
      executable: validateNativeGrokExecutable(runtime.executable, { fsImpl }),
      version: safeText(runtime.version, 80),
    };
  }

  for (const candidate of allowlistedExecutablePaths) {
    try {
      return {
        executable: validateNativeGrokExecutable(candidate, { fsImpl }),
        version: null,
      };
    } catch {
      // Try the next exact path supplied by the trusted embedding host.
    }
  }
  throw new GrokRuntimeUnavailable(
    "Install the official native Grok Build CLI before linking an xAI subscription"
  );
}

function safeText(value, maxLength = 256) {
  if (typeof value !== "string") return null;
  const output = value.replace(/[\0\r\n]/g, " ").trim();
  return output ? output.slice(0, maxLength) : null;
}

function safeLoginId(value) {
  const loginId = safeText(value, 100);
  if (!loginId || !/^[A-Za-z0-9._:-]{4,100}$/.test(loginId)) {
    throw new GrokSubscriptionError("The Grok Build login identifier is invalid");
  }
  return loginId;
}

function sanitizeVerificationUrl(raw) {
  // OAuth query parameters can contain state or PKCE material. The official
  // CLI owns browser OAuth, and only a non-secret device verification page is
  // ever displayed by Ionic.
  return sanitizeSubscriptionAuthorizationUrl("xai-grok-build", raw, {
    stripQueryAndHash: true,
  }) || null;
}

function parseSafeDeviceLoginMetadata(text) {
  if (typeof text !== "string" || !text) return {};
  let verificationUrl = null;
  for (const match of text.matchAll(/https:\/\/[^\s<>"']+/gi)) {
    const trimmed = match[0].replace(/[),.;!?]+$/, "");
    verificationUrl = sanitizeVerificationUrl(trimmed);
    if (verificationUrl) break;
  }

  const codeMatch = text.match(
    /(?:user\s+|device\s+)?code\s*(?::|is)\s*([A-Z0-9]{3,12}(?:-[A-Z0-9]{3,12}){0,2})\b/i
  );
  const userCode = codeMatch ? codeMatch[1].toUpperCase() : null;
  return {
    ...(verificationUrl ? { verificationUrl } : {}),
    ...(userCode ? { userCode } : {}),
  };
}

function safeModelId(value) {
  const model = safeText(value, 200);
  return model && /^[A-Za-z0-9._:/-]{1,200}$/.test(model) ? model : null;
}

function parseGrokModelState(initializeResult) {
  const modelState = initializeResult?._meta?.modelState;
  if (!modelState || typeof modelState !== "object" || Array.isArray(modelState)) {
    return { models: [], truncated: false };
  }
  const currentModelId = safeModelId(modelState.currentModelId);
  const availableModels = Array.isArray(modelState.availableModels)
    ? modelState.availableModels
    : [];
  const models = [];
  const seen = new Set();
  let truncated = false;
  for (let index = 0; index < availableModels.length; index += 1) {
    const raw = availableModels[index];
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) continue;
    const id = safeModelId(raw.modelId || raw.id);
    if (!id || seen.has(id)) continue;
    seen.add(id);
    const meta = raw._meta && typeof raw._meta === "object" && !Array.isArray(raw._meta)
      ? raw._meta
      : {};
    const rawEfforts = meta.supportsReasoningEffort === false
      ? []
      : Array.isArray(meta.reasoningEfforts)
        ? meta.reasoningEfforts
        : [];
    const supportedEfforts = [];
    let markedDefault = null;
    for (const effort of rawEfforts) {
      const candidate = safeText(effort?.value || effort?.id, 20)?.toLowerCase();
      if (!GROK_REASONING_EFFORTS.has(candidate) || supportedEfforts.includes(candidate)) {
        continue;
      }
      supportedEfforts.push(candidate);
      if (effort?.default === true) markedDefault = candidate;
    }
    const reportedDefault = safeText(meta.reasoningEffort, 20)?.toLowerCase();
    const defaultEffort = supportedEfforts.includes(markedDefault)
      ? markedDefault
      : supportedEfforts.includes(reportedDefault)
        ? reportedDefault
        : null;
    models.push({
      id,
      displayName: safeText(raw.name, 120) || id,
      description: safeText(raw.description, 500),
      isDefault: id === currentModelId,
      supportedEfforts,
      defaultEffort,
      agentType: safeText(meta.agentType, 80),
    });
    if (models.length >= MAX_GROK_MODELS) {
      truncated = index < availableModels.length - 1;
      break;
    }
  }
  return {
    models,
    truncated,
  };
}

function validateProcessArguments(args) {
  if (!Array.isArray(args) || !args.length) {
    throw new TypeError("Grok Build process arguments are required");
  }
  for (const arg of args) {
    if (typeof arg !== "string" || /[\0\r\n]/.test(arg) || Buffer.byteLength(arg) > 4096) {
      throw new GrokSubscriptionError("A Grok Build process argument is invalid");
    }
  }
  return [...args];
}

class BoundedGrokProcess {
  constructor({
    executable,
    args,
    spawnImpl = childProcess.spawn,
    env = process.env,
    timeoutMs,
    maxOutputBytes,
    maxInputBytes = DEFAULT_MAX_INPUT_BYTES,
    onOutput = () => {},
  }) {
    this.executable = executable;
    this.args = validateProcessArguments(args);
    this.spawnImpl = spawnImpl;
    this.environment = cleanGrokEnvironment(env);
    this.timeoutMs = timeoutMs;
    this.maxOutputBytes = maxOutputBytes;
    this.maxInputBytes = maxInputBytes;
    this.onOutput = onOutput;
    this.child = null;
    this.stdout = Buffer.alloc(0);
    this.stderr = Buffer.alloc(0);
    this.totalOutputBytes = 0;
    this.finished = false;
    this.timer = null;
    this.result = new Promise((resolve) => {
      this.resolveResult = resolve;
    });
  }

  start() {
    if (this.child) return this;
    let child;
    try {
      child = this.spawnImpl(this.executable, this.args, {
        cwd: path.dirname(this.executable),
        env: this.environment,
        shell: false,
        windowsHide: true,
        stdio: ["pipe", "pipe", "pipe"],
      });
    } catch {
      throw new GrokRuntimeUnavailable("The native Grok Build CLI could not be started");
    }
    if (!child?.stdin || !child?.stdout || !child?.stderr) {
      try { child?.kill?.(); } catch { /* best effort */ }
      throw new GrokRuntimeUnavailable("Grok Build did not provide a bounded stdio transport");
    }
    this.child = child;
    child.stdout.on("data", (chunk) => this._record("stdout", chunk));
    child.stderr.on("data", (chunk) => this._record("stderr", chunk));
    child.once("error", () => this.terminate("process_error"));
    child.once("exit", (code, signal) => {
      this._finish("exit", {
        exitCode: Number.isInteger(code) ? code : null,
        signal: safeText(signal, 30),
      });
    });
    this.timer = setTimeout(() => this.terminate("timeout"), this.timeoutMs);
    return this;
  }

  _record(channel, chunk) {
    if (this.finished) return;
    const incoming = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    this.totalOutputBytes += incoming.length;
    if (this.totalOutputBytes > this.maxOutputBytes) {
      this.terminate("output_limit");
      return;
    }
    if (channel === "stdout") {
      this.stdout = Buffer.concat([this.stdout, incoming]);
    } else {
      this.stderr = Buffer.concat([this.stderr, incoming]);
    }
    this.onOutput(
      this.stdout.toString("utf8"),
      this.stderr.toString("utf8")
    );
  }

  write(value) {
    if (!this.child || this.finished || this.child.stdin.destroyed) {
      throw new GrokSubscriptionError("The Grok Build process is not accepting input");
    }
    const input = Buffer.isBuffer(value) ? value : Buffer.from(String(value), "utf8");
    if (input.length > this.maxInputBytes) {
      throw new GrokSubscriptionError(
        "The Grok Build process input exceeded its size limit",
        "GROK_INPUT_LIMIT"
      );
    }
    this.child.stdin.write(input);
  }

  endInput() {
    if (this.child && !this.child.stdin.destroyed) this.child.stdin.end();
  }

  terminate(reason = "cancelled") {
    if (this.finished) return false;
    try { this.child?.kill?.(); } catch { /* best effort */ }
    this._finish(reason);
    return true;
  }

  _finish(reason, extra = {}) {
    if (this.finished) return;
    this.finished = true;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.resolveResult({ reason, ...extra });
  }

  wait() {
    return this.result;
  }
}

function processFailure(result, operation) {
  if (result.reason === "timeout") {
    return new GrokSubscriptionError(
      `The Grok Build ${operation} timed out`,
      "GROK_TIMEOUT"
    );
  }
  if (result.reason === "output_limit") {
    return new GrokSubscriptionError(
      `The Grok Build ${operation} exceeded its output limit`,
      "GROK_OUTPUT_LIMIT"
    );
  }
  if (result.reason === "process_error") {
    return new GrokSubscriptionError(
      `The Grok Build ${operation} stopped unexpectedly`,
      "GROK_PROCESS_ERROR"
    );
  }
  return new GrokSubscriptionError(
    `The official Grok Build CLI could not complete ${operation}`,
    "GROK_COMMAND_FAILED"
  );
}

function selectGrokAccountAuthMethod(initializeResult) {
  const advertised = new Set(
    (Array.isArray(initializeResult?.authMethods) ? initializeResult.authMethods : [])
      .map((method) => safeText(method?.id, 100))
      .filter(Boolean)
  );
  return GROK_ACCOUNT_AUTH_METHODS.find((method) => advertised.has(method)) || null;
}

async function inspectGrokAcp({
  executable,
  spawnImpl,
  env,
  timeoutMs,
  maxOutputBytes,
  maxInputBytes,
  authenticate = false,
}) {
  let process = null;
  let observedLength = 0;
  let lineBuffer = "";
  let initializeResult = null;
  let initializeRejected = false;
  let authMethod = null;
  let authAttempted = false;
  let authSucceeded = null;
  let writeFailed = false;

  function writeRequest(id, method, params) {
    process.write(`${JSON.stringify({ jsonrpc: "2.0", id, method, params })}\n`);
  }

  function complete(reason = "inspection_complete") {
    process?.terminate(reason);
  }

  function handleMessage(message) {
    if (!message || typeof message !== "object" || Array.isArray(message)) return;
    if (message.id === 1) {
      if (message.error || !message.result || typeof message.result !== "object") {
        initializeRejected = true;
        complete();
        return;
      }
      initializeResult = message.result;
      if (!authenticate) {
        complete();
        return;
      }
      // ACP authMethods is a capability list, never authentication state.
      authMethod = selectGrokAccountAuthMethod(initializeResult);
      if (!authMethod) {
        complete();
        return;
      }
      authAttempted = true;
      try {
        writeRequest(2, "authenticate", {
          methodId: authMethod,
          _meta: { headless: true },
        });
      } catch {
        writeFailed = true;
        complete();
      }
      return;
    }
    if (message.id === 2 && authAttempted) {
      authSucceeded = !message.error;
      complete();
    }
  }

  process = new BoundedGrokProcess({
    executable,
    args: ["--no-auto-update", "agent", "stdio"],
    spawnImpl,
    env,
    timeoutMs,
    maxOutputBytes,
    maxInputBytes,
    onOutput: (stdout) => {
      const delta = stdout.slice(observedLength);
      observedLength = stdout.length;
      lineBuffer += delta;
      while (lineBuffer.includes("\n")) {
        const newline = lineBuffer.indexOf("\n");
        const line = lineBuffer.slice(0, newline).replace(/\r$/, "");
        lineBuffer = lineBuffer.slice(newline + 1);
        if (!line.trim()) continue;
        try {
          handleMessage(JSON.parse(line));
        } catch {
          // Ignore diagnostics that are not ACP JSON-RPC messages. The process
          // remains bounded and a missing response is reported as unknown.
        }
      }
    },
  });
  process.start();
  try {
    writeRequest(1, "initialize", {
      protocolVersion: 1,
      clientCapabilities: {
        fs: { readTextFile: false, writeTextFile: false },
        terminal: false,
      },
    });
  } catch {
    writeFailed = true;
    complete("process_error");
  }
  const outcome = await process.wait();
  return {
    outcome,
    initializeResult,
    initializeRejected,
    authMethod,
    authAttempted,
    authSucceeded,
    writeFailed,
  };
}

class GrokLoginSession {
  constructor({
    loginId,
    mode,
    executable,
    spawnImpl,
    env,
    timeoutMs,
    maxOutputBytes,
    onTerminal,
  }) {
    this.loginId = loginId;
    this.mode = mode;
    this.state = "starting";
    this.verificationUrl = null;
    this.userCode = null;
    this.errorCode = null;
    this.onTerminal = onTerminal;
    this.terminalNotified = false;
    this.process = new BoundedGrokProcess({
      executable,
      args: mode === "device" ? ["login", "--device-auth"] : ["login", "--oauth"],
      spawnImpl,
      env,
      timeoutMs,
      maxOutputBytes,
      onOutput: (stdout, stderr) => this._readOutput(`${stdout}\n${stderr}`),
    });
  }

  start() {
    this.process.start();
    this.state = "awaiting_user";
    this.process.wait().then((result) => this._complete(result));
    return this.snapshot();
  }

  _readOutput(text) {
    if (this.mode !== "device" || TERMINAL_LOGIN_STATES.has(this.state)) return;
    const metadata = parseSafeDeviceLoginMetadata(text);
    if (metadata.verificationUrl) this.verificationUrl = metadata.verificationUrl;
    if (metadata.userCode) this.userCode = metadata.userCode;
  }

  _complete(result) {
    if (result.reason === "exit" && result.exitCode === 0) {
      this.state = "connected";
    } else if (result.reason === "cancelled" || result.reason === "closed") {
      this.state = "cancelled";
    } else if (result.reason === "timeout") {
      this.state = "timed_out";
      this.errorCode = "GROK_TIMEOUT";
    } else if (result.reason === "output_limit") {
      this.state = "failed";
      this.errorCode = "GROK_OUTPUT_LIMIT";
    } else {
      this.state = "failed";
      this.errorCode = result.reason === "process_error"
        ? "GROK_PROCESS_ERROR"
        : "GROK_LOGIN_FAILED";
    }
    this._notifyTerminal();
  }

  _notifyTerminal() {
    if (this.terminalNotified || !TERMINAL_LOGIN_STATES.has(this.state)) return;
    this.terminalNotified = true;
    this.onTerminal(this.snapshot());
  }

  cancel(reason = "cancelled") {
    if (!TERMINAL_LOGIN_STATES.has(this.state)) {
      this.state = reason === "closed" ? "cancelled" : "cancelled";
      this.process.terminate(reason);
      this._notifyTerminal();
    }
    return this.snapshot();
  }

  snapshot() {
    return {
      provider: GROK_RUNTIME_ID,
      loginId: this.loginId,
      mode: this.mode,
      state: this.state,
      ...(this.verificationUrl ? { verificationUrl: this.verificationUrl } : {}),
      ...(this.userCode ? { userCode: this.userCode } : {}),
      ...(this.errorCode ? { errorCode: this.errorCode } : {}),
    };
  }
}

function baseStatus({ installed, version = null, connected = null, inspected = false, message }) {
  return {
    provider: GROK_RUNTIME_ID,
    displayName: "xAI Grok Build",
    installed,
    available: installed,
    connected,
    authenticated: connected,
    authMode: connected === true
      ? "subscription_session"
      : connected === false
        ? "none"
        : "unknown",
    authenticationInspected: inspected,
    version,
    directApiProvider: false,
    message,
  };
}

function createGrokSubscriptionService({
  runtimeStatus,
  allowlistedExecutablePaths = [],
  fsImpl = fs,
  spawnImpl = childProcess.spawn,
  env = process.env,
  randomUUID = crypto.randomUUID,
  loginTimeoutMs = DEFAULT_LOGIN_TIMEOUT_MS,
  operationTimeoutMs = DEFAULT_OPERATION_TIMEOUT_MS,
  probeTimeoutMs = DEFAULT_PROBE_TIMEOUT_MS,
  maxOutputBytes = DEFAULT_MAX_OUTPUT_BYTES,
  maxInputBytes = DEFAULT_MAX_INPUT_BYTES,
} = {}) {
  let knownAuthentication = null;
  let authenticationInspected = false;
  let runtimeVersion = null;
  const sessions = new Map();

  async function runtime() {
    const result = await resolveGrokExecutable(runtimeStatus, {
      allowlistedExecutablePaths,
      fsImpl,
    });
    runtimeVersion = result.version;
    return result;
  }

  function currentSession() {
    for (const session of sessions.values()) {
      if (!TERMINAL_LOGIN_STATES.has(session.state)) return session;
    }
    return null;
  }

  function onLoginTerminal(snapshot) {
    if (snapshot.state === "connected") {
      knownAuthentication = true;
      authenticationInspected = true;
    }
  }

  async function passiveStatus() {
    try {
      await runtime();
      return {
        ...baseStatus({
          installed: true,
          version: runtimeVersion,
          connected: knownAuthentication,
          inspected: authenticationInspected,
          message: authenticationInspected
            ? "Authentication reflects the last explicit Grok Build action."
            : "Installed. Authentication has not been inspected.",
        }),
        activeLogin: currentSession()?.snapshot() || null,
      };
    } catch (error) {
      if (!(error instanceof GrokRuntimeUnavailable)) throw error;
      return {
        ...baseStatus({
          installed: false,
          connected: null,
          inspected: false,
          message: error.message,
        }),
        activeLogin: null,
      };
    }
  }

  async function probeAuthentication() {
    let executable;
    try {
      ({ executable } = await runtime());
    } catch (error) {
      if (error instanceof GrokRuntimeUnavailable) return passiveStatus();
      throw error;
    }

    const inspection = await inspectGrokAcp({
      executable,
      spawnImpl,
      env,
      timeoutMs: probeTimeoutMs,
      maxOutputBytes,
      maxInputBytes,
      authenticate: true,
    });
    if (inspection.authSucceeded === true) {
      knownAuthentication = true;
      authenticationInspected = true;
      return {
        ...baseStatus({
          installed: true,
          version: runtimeVersion,
          connected: true,
          inspected: true,
          message: "Grok Build accepted an explicit account authentication check.",
        }),
        activeLogin: currentSession()?.snapshot() || null,
      };
    }

    knownAuthentication = null;
    authenticationInspected = true;
    return {
      ...baseStatus({
        installed: true,
        version: runtimeVersion,
        connected: null,
        inspected: true,
        message: inspection.outcome.reason === "timeout"
          ? "The Grok Build authentication probe timed out."
          : inspection.outcome.reason === "output_limit"
            ? "The Grok Build authentication probe exceeded its output limit."
            : inspection.authAttempted
              ? "Grok Build did not confirm the account authentication check."
              : "Grok Build authentication state is unknown; advertised methods are not login state.",
      }),
      activeLogin: currentSession()?.snapshot() || null,
    };
  }

  async function status(options = {}) {
    if (options && options.probeAuthentication === true) {
      return probeAuthentication();
    }
    return passiveStatus();
  }

  async function models() {
    const { executable } = await runtime();
    const inspection = await inspectGrokAcp({
      executable,
      spawnImpl,
      env,
      timeoutMs: operationTimeoutMs,
      maxOutputBytes,
      maxInputBytes,
      authenticate: false,
    });
    if (!inspection.initializeResult) {
      if (["timeout", "output_limit", "process_error", "exit"].includes(inspection.outcome.reason)) {
        throw processFailure(inspection.outcome, "model discovery");
      }
      throw new GrokSubscriptionError("Grok Build rejected ACP model discovery");
    }
    const catalog = parseGrokModelState(inspection.initializeResult);
    if (!catalog.models.length) {
      throw new GrokSubscriptionError("Grok Build did not report any available models");
    }
    return {
      provider: GROK_RUNTIME_ID,
      source: "grok_build_acp",
      models: catalog.models,
      truncated: catalog.truncated,
    };
  }

  async function beginLogin(mode = "browser") {
    if (!new Set(["browser", "device"]).has(mode)) {
      throw new TypeError("Grok Build login mode must be browser or device");
    }
    if (currentSession()) {
      throw new GrokSubscriptionError(
        "A Grok Build login is already in progress",
        "GROK_LOGIN_ACTIVE"
      );
    }
    for (const [id, session] of sessions) {
      if (TERMINAL_LOGIN_STATES.has(session.state)) sessions.delete(id);
    }
    const { executable } = await runtime();
    const loginId = safeLoginId(randomUUID());
    const session = new GrokLoginSession({
      loginId,
      mode,
      executable,
      spawnImpl,
      env,
      timeoutMs: loginTimeoutMs,
      maxOutputBytes,
      onTerminal: onLoginTerminal,
    });
    sessions.set(loginId, session);
    try {
      return session.start();
    } catch (error) {
      sessions.delete(loginId);
      throw error;
    }
  }

  async function pollLogin(loginId) {
    const normalized = safeLoginId(loginId);
    const session = sessions.get(normalized);
    if (!session) {
      throw new GrokSubscriptionError(
        "The Grok Build login session was not found",
        "GROK_LOGIN_NOT_FOUND"
      );
    }
    return session.snapshot();
  }

  async function cancelLogin(loginId) {
    const normalized = safeLoginId(loginId);
    const session = sessions.get(normalized);
    if (!session) return false;
    if (TERMINAL_LOGIN_STATES.has(session.state)) return false;
    session.cancel();
    return true;
  }

  async function logout() {
    const active = currentSession();
    if (active) active.cancel();
    const { executable } = await runtime();
    const process = new BoundedGrokProcess({
      executable,
      args: ["logout"],
      spawnImpl,
      env,
      timeoutMs: operationTimeoutMs,
      maxOutputBytes,
      maxInputBytes,
    });
    process.start();
    process.endInput();
    const result = await process.wait();
    if (result.reason !== "exit" || result.exitCode !== 0) {
      throw processFailure(result, "logout");
    }
    knownAuthentication = false;
    authenticationInspected = true;
    return {
      ...baseStatus({
        installed: true,
        version: runtimeVersion,
        connected: false,
        inspected: true,
        message: "Signed out through the official Grok Build CLI.",
      }),
      activeLogin: null,
    };
  }

  function close() {
    for (const session of sessions.values()) session.cancel("closed");
    sessions.clear();
  }

  return {
    status,
    models,
    probeAuthentication,
    beginLogin,
    pollLogin,
    cancelLogin,
    logout,
    close,
  };
}

module.exports = {
  DEFAULT_LOGIN_TIMEOUT_MS,
  DEFAULT_MAX_OUTPUT_BYTES,
  DEFAULT_OPERATION_TIMEOUT_MS,
  DEFAULT_PROBE_TIMEOUT_MS,
  GROK_AUTH_HOSTS,
  GROK_RUNTIME_ID,
  GrokRuntimeUnavailable,
  GrokSubscriptionError,
  cleanGrokEnvironment,
  createGrokSubscriptionService,
  parseGrokModelState,
  parseSafeDeviceLoginMetadata,
  resolveGrokExecutable,
  sanitizeVerificationUrl,
  validateNativeGrokExecutable,
};
