import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { PassThrough } from "node:stream";
import { afterEach, describe, it } from "node:test";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const {
  GROK_RUNTIME_ID,
  cleanGrokEnvironment,
  createGrokSubscriptionService,
  parseSafeDeviceLoginMetadata,
  validateNativeGrokExecutable,
} = require("../src/grok-subscription.js");

const temporaryDirectories = [];

function nativeGrokFixture() {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "ionic-grok-subscription-"));
  temporaryDirectories.push(directory);
  const executable = path.join(directory, process.platform === "win32" ? "grok.exe" : "grok");
  fs.writeFileSync(executable, "fixture");
  return executable;
}

class FakeProcess extends EventEmitter {
  constructor() {
    super();
    this.stdin = new PassThrough();
    this.stdout = new PassThrough();
    this.stderr = new PassThrough();
    this.killed = false;
  }

  kill() {
    this.killed = true;
    return true;
  }

  exit(code = 0, signal = null) {
    this.emit("exit", code, signal);
  }
}

function spawnHarness(onSpawn = () => {}) {
  const calls = [];
  function spawn(executable, args, options) {
    const child = new FakeProcess();
    calls.push({ executable, args, options, child });
    onSpawn(child, args, options);
    return child;
  }
  return { calls, spawn };
}

function runtimeStatus(executable, version = null) {
  return async () => ({
    runtimes: [
      {
        id: GROK_RUNTIME_ID,
        installed: true,
        available: true,
        executable,
        version,
      },
    ],
  });
}

function tick() {
  return new Promise((resolve) => setImmediate(resolve));
}

afterEach(() => {
  while (temporaryDirectories.length) {
    fs.rmSync(temporaryDirectories.pop(), { recursive: true, force: true });
  }
});

describe("Grok Build subscription boundary", () => {
  it("reports an installed runtime without inspecting authentication", async () => {
    const executable = nativeGrokFixture();
    const harness = spawnHarness(() => {
      throw new Error("passive status must not execute Grok");
    });
    const service = createGrokSubscriptionService({
      runtimeStatus: runtimeStatus(executable, "grok 1.2.3"),
      spawnImpl: harness.spawn,
    });

    const status = await service.status();
    assert.equal(status.provider, GROK_RUNTIME_ID);
    assert.equal(status.installed, true);
    assert.equal(status.connected, null);
    assert.equal(status.authMode, "unknown");
    assert.equal(status.authenticationInspected, false);
    assert.equal(status.version, "grok 1.2.3");
    assert.equal(harness.calls.length, 0);
  });

  it("uses only native absolute grok executables", () => {
    const executable = nativeGrokFixture();
    assert.equal(validateNativeGrokExecutable(executable), path.resolve(executable));
    assert.throws(
      () => validateNativeGrokExecutable("grok"),
      /absolute path/
    );

    const wrapper = path.join(path.dirname(executable), "grok.cmd");
    fs.writeFileSync(wrapper, "@echo off");
    assert.throws(
      () => validateNativeGrokExecutable(wrapper),
      /Shell wrappers/
    );
    const impostor = path.join(path.dirname(executable), "powershell.exe");
    fs.writeFileSync(impostor, "fixture");
    assert.throws(
      () => validateNativeGrokExecutable(impostor),
      /Shell wrappers/
    );
  });

  it("strips API keys, tokens, and arbitrary Grok overrides from child processes", () => {
    const clean = cleanGrokEnvironment({
      PATH: "C:/tools",
      USERPROFILE: "C:/Users/example",
      GROK_HOME: "C:/Users/example/.grok",
      XAI_API_KEY: "xai-secret",
      GROK_API_KEY: "grok-secret",
      GROK_OIDC_CLIENT_SECRET: "oidc-secret",
      OPENAI_API_KEY: "openai-secret",
      GH_TOKEN: "github-secret",
      AWS_SECRET_ACCESS_KEY: "aws-secret",
    });

    assert.equal(clean.PATH, "C:/tools");
    assert.equal(clean.GROK_HOME, "C:/Users/example/.grok");
    assert.equal(clean.NO_COLOR, "1");
    assert.equal(clean.IONIC_RUNTIME_BOUNDARY, "1");
    assert.equal(Object.values(clean).some((value) => /secret/.test(value)), false);
  });

  it("starts browser OAuth through the official CLI and supports cancellation", async () => {
    const executable = nativeGrokFixture();
    const harness = spawnHarness();
    const service = createGrokSubscriptionService({
      runtimeStatus: runtimeStatus(executable),
      spawnImpl: harness.spawn,
      env: { PATH: "C:/tools", XAI_API_KEY: "must-not-leak" },
      randomUUID: () => "login-browser-1",
    });

    const started = await service.beginLogin("browser");
    assert.deepEqual(started, {
      provider: GROK_RUNTIME_ID,
      loginId: "login-browser-1",
      mode: "browser",
      state: "awaiting_user",
    });
    assert.deepEqual(harness.calls[0].args, ["login", "--oauth"]);
    assert.equal(harness.calls[0].options.shell, false);
    assert.equal(harness.calls[0].options.env.XAI_API_KEY, undefined);
    assert.equal(await service.cancelLogin(started.loginId), true);
    assert.equal(harness.calls[0].child.killed, true);
    assert.equal((await service.pollLogin(started.loginId)).state, "cancelled");
    assert.equal(await service.cancelLogin(started.loginId), false);
  });

  it("exposes only a sanitized device verification URL and short user code", async () => {
    const executable = nativeGrokFixture();
    const harness = spawnHarness();
    const service = createGrokSubscriptionService({
      runtimeStatus: runtimeStatus(executable),
      spawnImpl: harness.spawn,
      randomUUID: () => "login-device-1",
    });

    const started = await service.beginLogin("device");
    assert.deepEqual(harness.calls[0].args, ["login", "--device-auth"]);
    harness.calls[0].child.stdout.write(
      "Visit https://auth.x.ai/device?state=do-not-return-this\nUser code: ABCD-EFGH\n"
    );
    harness.calls[0].child.stderr.write("internal-token=never-return-this");
    await tick();
    const polling = await service.pollLogin(started.loginId);
    assert.equal(polling.verificationUrl, "https://auth.x.ai/device");
    assert.equal(polling.userCode, "ABCD-EFGH");
    assert.doesNotMatch(JSON.stringify(polling), /do-not-return|internal-token|never-return/);

    harness.calls[0].child.exit(0);
    await tick();
    assert.equal((await service.pollLogin(started.loginId)).state, "connected");
    assert.equal((await service.status()).connected, true);
  });

  it("does not expose untrusted login URLs or unrelated output as a code", () => {
    const metadata = parseSafeDeviceLoginMetadata(
      "Visit https://evil.example/device?token=secret. device code login required."
    );
    assert.deepEqual(metadata, {});
  });

  it("kills a login that exceeds its wall-clock timeout", async () => {
    const executable = nativeGrokFixture();
    const harness = spawnHarness();
    const service = createGrokSubscriptionService({
      runtimeStatus: runtimeStatus(executable),
      spawnImpl: harness.spawn,
      randomUUID: () => "login-timeout-1",
      loginTimeoutMs: 15,
    });

    const started = await service.beginLogin("device");
    await new Promise((resolve) => setTimeout(resolve, 35));
    const result = await service.pollLogin(started.loginId);
    assert.equal(result.state, "timed_out");
    assert.equal(result.errorCode, "GROK_TIMEOUT");
    assert.equal(harness.calls[0].child.killed, true);
  });

  it("kills a login as soon as combined output exceeds the cap", async () => {
    const executable = nativeGrokFixture();
    const harness = spawnHarness();
    const service = createGrokSubscriptionService({
      runtimeStatus: runtimeStatus(executable),
      spawnImpl: harness.spawn,
      randomUUID: () => "login-output-1",
      maxOutputBytes: 32,
    });

    const started = await service.beginLogin("device");
    harness.calls[0].child.stdout.write("x".repeat(33));
    await tick();
    const result = await service.pollLogin(started.loginId);
    assert.equal(result.state, "failed");
    assert.equal(result.errorCode, "GROK_OUTPUT_LIMIT");
    assert.equal(harness.calls[0].child.killed, true);
  });

  it("confirms account auth only after an explicit bounded ACP authenticate request", async () => {
    const executable = nativeGrokFixture();
    const requests = [];
    const harness = spawnHarness((child, args) => {
      if (!args.includes("stdio")) return;
      child.stdin.on("data", (wire) => {
        const request = JSON.parse(wire.toString("utf8"));
        requests.push(request);
        if (request.method === "initialize") {
          child.stdout.write(
            `${JSON.stringify({
              jsonrpc: "2.0",
              id: request.id,
              result: {
                authMethods: [
                  { id: "grok.com", label: "Grok account" },
                  { id: "xai.api_key", label: "API key" },
                ],
              },
            })}\n`
          );
        }
        if (request.method === "authenticate") {
          assert.deepEqual(request.params, {
            methodId: "grok.com",
            _meta: { headless: true },
          });
          child.stdout.write(`${JSON.stringify({ jsonrpc: "2.0", id: request.id, result: {} })}\n`);
        }
      });
    });
    const service = createGrokSubscriptionService({
      runtimeStatus: runtimeStatus(executable),
      spawnImpl: harness.spawn,
      env: { XAI_API_KEY: "must-not-leak", GROK_HOME: "C:/profile/.grok" },
    });

    const status = await service.status({ probeAuthentication: true });
    assert.equal(status.connected, true);
    assert.equal(status.authMode, "subscription_session");
    assert.equal(status.authenticationInspected, true);
    assert.deepEqual(requests.map((request) => request.method), ["initialize", "authenticate"]);
    assert.deepEqual(harness.calls[0].args, ["--no-auto-update", "agent", "stdio"]);
    assert.equal(harness.calls[0].options.shell, false);
    assert.equal(harness.calls[0].options.env.XAI_API_KEY, undefined);
    assert.equal(harness.calls[0].child.killed, true);
  });

  it("never mistakes advertised ACP methods for authentication state", async () => {
    const executable = nativeGrokFixture();
    const harness = spawnHarness((child, args) => {
      if (!args.includes("stdio")) return;
      child.stdin.once("data", (wire) => {
        const request = JSON.parse(wire.toString("utf8"));
        child.stdout.write(
          `${JSON.stringify({
            jsonrpc: "2.0",
            id: request.id,
            result: {
              authMethods: [
                { id: "xai.api_key" },
                { id: "some.future.account.method" },
              ],
            },
          })}\n`
        );
      });
    });
    const service = createGrokSubscriptionService({
      runtimeStatus: runtimeStatus(executable),
      spawnImpl: harness.spawn,
    });

    const status = await service.probeAuthentication();
    assert.equal(status.connected, null);
    assert.equal(status.authMode, "unknown");
    assert.match(status.message, /advertised methods are not login state/);
  });

  it("accepts every centrally approved Grok verification host and strips OAuth state", () => {
    for (const host of ["accounts.x.ai", "auth.x.ai", "grok.com", "www.grok.com"]) {
      const metadata = parseSafeDeviceLoginMetadata(
        `Visit https://${host}/device?state=never-expose#fragment. Code: ABCD-EFGH`
      );
      assert.equal(metadata.verificationUrl, `https://${host}/device`);
      assert.equal(metadata.userCode, "ABCD-EFGH");
      assert.doesNotMatch(JSON.stringify(metadata), /never-expose|fragment/);
    }
  });

  it("reads model-specific effort options from bounded ACP modelState metadata", async () => {
    const executable = nativeGrokFixture();
    const harness = spawnHarness((child, args) => {
      if (!args.includes("stdio")) return;
      child.stdin.once("data", (wire) => {
        const request = JSON.parse(wire.toString("utf8"));
        child.stdout.write(
          `${JSON.stringify({
            jsonrpc: "2.0",
            id: request.id,
            result: {
              authMethods: [{ id: "grok.com" }],
              _meta: {
                modelState: {
                  currentModelId: "grok-4.5",
                  availableModels: [
                    {
                      modelId: "grok-4.5",
                      name: "Grok 4.5",
                      description: "Primary Grok Build model",
                      _meta: {
                        agentType: "built-in",
                        supportsReasoningEffort: true,
                        reasoningEffort: "medium",
                        reasoningEfforts: [
                          { id: "low", value: "low", default: false },
                          { id: "medium", value: "medium", default: true },
                          { id: "high", value: "high", default: false },
                          { id: "bogus", value: "bogus", default: false },
                        ],
                      },
                    },
                    {
                      modelId: "custom-route/model",
                      name: "Team route",
                      _meta: {
                        agentType: "custom",
                        supportsReasoningEffort: false,
                        reasoningEfforts: [{ id: "xhigh", value: "xhigh" }],
                      },
                    },
                  ],
                },
              },
            },
          })}\n`
        );
      });
    });
    const service = createGrokSubscriptionService({
      runtimeStatus: runtimeStatus(executable),
      spawnImpl: harness.spawn,
    });

    const catalog = await service.models();
    assert.deepEqual(harness.calls[0].args, ["--no-auto-update", "agent", "stdio"]);
    assert.equal(catalog.source, "grok_build_acp");
    assert.deepEqual(catalog.models.map((model) => model.id), [
      "grok-4.5",
      "custom-route/model",
    ]);
    assert.equal(catalog.models[0].isDefault, true);
    assert.deepEqual(catalog.models[0].supportedEfforts, ["low", "medium", "high"]);
    assert.equal(catalog.models[0].defaultEffort, "medium");
    assert.deepEqual(catalog.models[1].supportedEfforts, []);
    assert.equal(catalog.models[1].agentType, "custom");
  });

  it("returns safe unknown status when the explicit ACP probe times out", async () => {
    const executable = nativeGrokFixture();
    const harness = spawnHarness();
    const service = createGrokSubscriptionService({
      runtimeStatus: runtimeStatus(executable),
      spawnImpl: harness.spawn,
      probeTimeoutMs: 15,
    });

    const status = await service.probeAuthentication();
    assert.equal(status.connected, null);
    assert.equal(status.authenticationInspected, true);
    assert.match(status.message, /timed out/);
    assert.equal(harness.calls[0].child.killed, true);
  });

  it("logs out only through the official CLI and returns no command output", async () => {
    const executable = nativeGrokFixture();
    const harness = spawnHarness((child, args) => {
      if (args[0] !== "logout") return;
      queueMicrotask(() => {
        child.stdout.write("secret-session-value-that-must-stay-in-process");
        child.exit(0);
      });
    });
    const service = createGrokSubscriptionService({
      runtimeStatus: runtimeStatus(executable),
      spawnImpl: harness.spawn,
      env: { XAI_API_KEY: "must-not-leak" },
    });

    const status = await service.logout();
    assert.deepEqual(harness.calls[0].args, ["logout"]);
    assert.equal(status.connected, false);
    assert.equal(status.authMode, "none");
    assert.doesNotMatch(JSON.stringify(status), /secret-session|must-not-leak/);
  });
});
