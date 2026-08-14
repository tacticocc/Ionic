import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { PassThrough, Writable } from "node:stream";
import { afterEach, describe, it } from "node:test";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const {
  CODEX_APP_SERVER_CONFIG,
  CodexAppServerClient,
  assertCodexProfileBoundary,
  cleanCodexEnvironment,
  createCodexSubscriptionService,
  ionicCodexProfileDirectory,
  isAllowedCodexAuthUrl,
  probeCodexSemanticBoundary,
  resolveCodexExecutable,
  schemaProvesEphemeralThread,
  schemaProvesRestrictedReadRoots,
} = require("../src/codex-subscription.js");
const temporary = [];

function executable(name = process.platform === "win32" ? "codex.exe" : "codex") {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "ionic-codex-test-"));
  temporary.push(directory);
  const target = path.join(directory, name);
  fs.writeFileSync(target, "native-placeholder");
  return target;
}

function fakeAppServer(handler) {
  const child = new EventEmitter();
  child.stdout = new PassThrough();
  child.stderr = new PassThrough();
  child.killed = false;
  let buffered = "";
  child.stdin = new Writable({
    write(chunk, _encoding, callback) {
      buffered += chunk.toString();
      while (buffered.includes("\n")) {
        const newline = buffered.indexOf("\n");
        const line = buffered.slice(0, newline);
        buffered = buffered.slice(newline + 1);
        if (line) handler(JSON.parse(line), child);
      }
      callback();
    },
  });
  child.kill = () => {
    child.killed = true;
    return true;
  };
  child.respond = (message) => queueMicrotask(() => child.stdout.write(`${JSON.stringify(message)}\n`));
  return child;
}

afterEach(() => {
  for (const directory of temporary.splice(0)) {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

describe("Codex subscription boundary", () => {
  it("uses passive native-runtime discovery and rejects shell wrappers", async () => {
    const native = executable();
    assert.equal(
      await resolveCodexExecutable(async () => ({
        runtimes: [{ id: "openai-codex", installed: true, available: true, executable: native }],
      })),
      native
    );

    const wrapper = executable("codex.cmd");
    await assert.rejects(
      () => resolveCodexExecutable(async () => ({
        runtimes: [{ id: "openai-codex", installed: true, available: true, executable: wrapper }],
      })),
      /official native Codex executable/
    );
  });

  it("supports only exact trusted fallback executable paths", async () => {
    const native = executable();
    assert.equal(
      await resolveCodexExecutable(async () => ({ runtimes: [] }), {
        allowlistedExecutablePaths: [native],
      }),
      native
    );
    await assert.rejects(
      () => resolveCodexExecutable(async () => ({ runtimes: [] }), {
        allowlistedExecutablePaths: [path.join(path.dirname(native), "missing", "codex.exe")],
      }),
      /Install the official native Codex CLI/
    );
  });

  it("strips API and provider credentials from the app-server environment", () => {
    const profile = path.join(os.tmpdir(), "ionic-codex-test-profile");
    const env = cleanCodexEnvironment({
      Path: "bin",
      USERPROFILE: "profile",
      CODEX_HOME: "user-codex-profile",
      OPENAI_API_KEY: "openai-secret",
      ANTHROPIC_API_KEY: "anthropic-secret",
      XAI_API_KEY: "xai-secret",
      GOOGLE_API_KEY: "google-secret",
      AWS_SECRET_ACCESS_KEY: "aws-secret",
    }, { profileDirectory: profile });
    assert.equal(env.PATH, "bin");
    assert.equal(env.USERPROFILE, "profile");
    assert.equal(env.IONIC_RUNTIME_BOUNDARY, "1");
    assert.equal(env.CODEX_HOME, path.resolve(profile));
    assert.notEqual(env.CODEX_HOME, "user-codex-profile");
    assert.doesNotMatch(JSON.stringify(env), /secret/);
  });

  it("allows only the documented HTTPS ChatGPT authorization hosts", () => {
    assert.equal(isAllowedCodexAuthUrl("https://chatgpt.com/auth/callback"), true);
    assert.equal(isAllowedCodexAuthUrl("https://auth.openai.com/codex/device"), true);
    assert.equal(isAllowedCodexAuthUrl("http://auth.openai.com/codex/device"), false);
    assert.equal(isAllowedCodexAuthUrl("https://auth.openai.com.evil.test/codex/device"), false);
    assert.equal(isAllowedCodexAuthUrl("https://user:password@chatgpt.com/auth"), false);
  });

  it("spawns app-server without a shell and exposes no account secrets", async () => {
    const native = executable();
    const profileDirectory = path.join(path.dirname(native), "profile");
    const calls = [];
    const opened = [];
    const child = fakeAppServer((message, server) => {
      calls.push(message);
      if (message.method === "initialize") server.respond({ id: message.id, result: {} });
      if (message.method === "account/read") {
        server.respond({
          id: message.id,
          result: {
            account: {
              type: "chatgpt",
              email: "private@example.test",
              planType: "pro",
              accessToken: "never-render-this",
            },
            requiresOpenaiAuth: true,
          },
        });
      }
      if (message.method === "account/login/start") {
        server.respond({
          id: message.id,
          result: {
            type: "chatgpt",
            loginId: "login-1234",
            authUrl: "https://chatgpt.com/auth/codex?redirect_uri=http%3A%2F%2Flocalhost%3A8123",
          },
        });
      }
      if (message.method === "account/login/cancel" || message.method === "account/logout") {
        server.respond({ id: message.id, result: {} });
      }
    });
    let spawnCall;
    const service = createCodexSubscriptionService({
      runtimeStatus: async () => ({
        runtimes: [{ id: "openai-codex", installed: true, available: true, executable: native }],
      }),
      spawnImpl(command, args, options) {
        spawnCall = { command, args, options };
        return child;
      },
      env: { PATH: "bin", OPENAI_API_KEY: "api-secret", XAI_API_KEY: "xai-secret" },
      openExternal: async (url) => { opened.push(url); return true; },
      version: "0.4.0",
      clientInfo: { name: "ionic_essential", title: "Ionic Essential" },
      profileDirectory,
      semanticBoundaryProbe: async () => ({
        semanticReviewCapable: true,
        unsupportedControls: [],
        semanticReviewMessage: "The installed Codex CLI accepted every required strict no-tool control.",
      }),
    });

    const status = await service.status();
    assert.deepEqual(status, {
      provider: "openai-codex",
      installed: true,
      available: true,
      connected: true,
      authMode: "chatgpt",
      planType: "pro",
      requiresOpenaiAuth: true,
      authenticationInspected: true,
      semanticReviewCapable: true,
      unsupportedControls: [],
      semanticReviewMessage: "The installed Codex CLI accepted every required strict no-tool control.",
    });
    assert.doesNotMatch(JSON.stringify(status), /private@example|never-render-this/);
    assert.equal(spawnCall.command, native);
    const expectedArgs = ["app-server", "--strict-config"];
    for (const setting of CODEX_APP_SERVER_CONFIG) expectedArgs.push("--config", setting);
    assert.deepEqual(spawnCall.args, expectedArgs);
    assert.equal(spawnCall.options.shell, false);
    assert.deepEqual(spawnCall.options.stdio, ["pipe", "pipe", "pipe"]);
    assert.doesNotMatch(JSON.stringify(spawnCall.options.env), /api-secret|xai-secret/);
    assert.equal(spawnCall.options.env.CODEX_HOME, profileDirectory);
    assert.equal(spawnCall.options.cwd, profileDirectory);
    assert.deepEqual(calls[0].params.clientInfo, {
      name: "ionic_essential",
      title: "Ionic Essential",
      version: "0.4.0",
    });
    assert.equal(calls[1].method, "initialized");
    assert.deepEqual(calls[2].params, { refreshToken: false });

    const login = await service.beginLogin("browser");
    assert.deepEqual(login, {
      provider: "openai-codex",
      state: "awaiting_user",
      mode: "browser",
      loginId: "login-1234",
    });
    assert.equal(opened.length, 1);
    assert.equal(await service.cancelLogin("login-1234"), true);
    assert.equal((await service.logout()).connected, false);
    service.close();
    assert.equal(child.killed, true);
  });

  it("returns a device code without exposing tokens or account identity", async () => {
    const native = executable();
    const child = fakeAppServer((message, server) => {
      if (message.method === "initialize") server.respond({ id: message.id, result: {} });
      if (message.method === "account/login/start") {
        server.respond({
          id: message.id,
          result: {
            type: "chatgptDeviceCode",
            loginId: "device-1234",
            verificationUrl: "https://auth.openai.com/codex/device",
            userCode: "ABCD-1234",
            accessToken: "hidden",
          },
        });
      }
    });
    const service = createCodexSubscriptionService({
      runtimeStatus: async () => ({
        runtimes: [{ id: "openai-codex", installed: true, available: true, executable: native }],
      }),
      spawnImpl: () => child,
      openExternal: async () => true,
      profileDirectory: path.join(path.dirname(native), "profile"),
    });
    const login = await service.beginLogin("device");
    assert.equal(login.verificationUrl, "https://auth.openai.com/codex/device");
    assert.equal(login.userCode, "ABCD-1234");
    assert.doesNotMatch(JSON.stringify(login), /hidden/);
    service.close();
  });

  it("uses an Ionic-owned Codex profile instead of the user's normal profile", () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "ionic-codex-profile-test-"));
    temporary.push(root);
    const profile = ionicCodexProfileDirectory(
      { LOCALAPPDATA: root, CODEX_HOME: path.join(root, "user-codex") },
      { platform: "win32" }
    );
    assert.equal(
      profile,
      path.join(root, "Tactico Technologies", "Ionic", "CodexSubscription")
    );
    assert.notEqual(profile, path.join(root, "user-codex"));
  });

  it("allows only Codex's built-in system skills in the dedicated profile", () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "ionic-codex-skills-test-"));
    temporary.push(root);
    const profile = path.join(root, "profile");
    fs.mkdirSync(path.join(profile, "skills", ".system"), { recursive: true });
    assert.equal(assertCodexProfileBoundary(profile), path.resolve(profile));

    fs.mkdirSync(path.join(profile, "skills", "user-skill"));
    assert.throws(() => assertCodexProfileBoundary(profile), /user-added skills/);
  });

  it("capability-gates app-server versions by their generated restricted-root schema", async () => {
    const native = executable();
    const profileDirectory = path.join(path.dirname(native), "profile");
    const schema = {
      properties: Object.fromEntries([
        "approvalPolicy",
        "cwd",
        "effort",
        "input",
        "model",
        "outputSchema",
        "sandboxPolicy",
        "threadId",
      ].map((key) => [key, {}])),
      definitions: {
        SandboxPolicy: {
          oneOf: [{
            properties: {
              type: { enum: ["readOnly"] },
              access: { $ref: "#/definitions/ReadOnlyAccess" },
            },
          }],
        },
        ReadOnlyAccess: {
          oneOf: [{
            properties: {
              type: { enum: ["restricted"] },
              includePlatformDefaults: { type: "boolean" },
              readableRoots: { type: "array" },
            },
          }],
        },
      },
    };
    assert.equal(schemaProvesRestrictedReadRoots(schema), true);
    assert.equal(schemaProvesRestrictedReadRoots({
      definitions: {
        SandboxPolicy: {
          oneOf: [{ properties: { type: { enum: ["readOnly"] } } }],
        },
      },
    }), false);
    const threadSchema = {
      properties: Object.fromEntries([
        "approvalPolicy",
        "baseInstructions",
        "cwd",
        "developerInstructions",
        "ephemeral",
        "model",
        "sandbox",
      ].map((key) => [key, {}])),
    };
    assert.equal(schemaProvesEphemeralThread(threadSchema), true);

    let spawnCall;
    const spawnImpl = (command, args, options) => {
      spawnCall = { command, args, options };
      const output = args[args.indexOf("--out") + 1];
      fs.mkdirSync(path.join(output, "v2"), { recursive: true });
      fs.writeFileSync(path.join(output, "v2", "TurnStartParams.json"), JSON.stringify(schema));
      fs.writeFileSync(
        path.join(output, "v2", "ThreadStartParams.json"),
        JSON.stringify(threadSchema)
      );
      const child = new EventEmitter();
      child.stdin = new PassThrough();
      child.stdout = new PassThrough();
      child.stderr = new PassThrough();
      child.kill = () => true;
      queueMicrotask(() => child.emit("close", 0));
      return child;
    };
    const result = await probeCodexSemanticBoundary(native, {
      spawnImpl,
      profileDirectory,
      timeoutMs: 2_000,
    });
    assert.equal(result.semanticReviewCapable, true);
    assert.deepEqual(spawnCall.args.slice(0, 2), ["app-server", "generate-json-schema"]);
    assert.equal(spawnCall.options.shell, false);
    assert.equal(spawnCall.options.env.CODEX_HOME, profileDirectory);
  });

  it("keeps sign-in and the model catalog available while semantic review fails closed", async () => {
    const native = executable();
    const profileDirectory = path.join(path.dirname(native), "profile");
    const spawnImpl = (_command, args) => {
      const output = args[args.indexOf("--out") + 1];
      fs.mkdirSync(path.join(output, "v2"), { recursive: true });
      fs.writeFileSync(path.join(output, "v2", "TurnStartParams.json"), JSON.stringify({
        properties: {
          approvalPolicy: {}, cwd: {}, effort: {}, input: {}, model: {}, outputSchema: {},
          sandboxPolicy: {}, threadId: {},
        },
        definitions: {
          SandboxPolicy: {
            oneOf: [{ properties: { type: { enum: ["readOnly"] } } }],
          },
        },
      }));
      fs.writeFileSync(path.join(output, "v2", "ThreadStartParams.json"), JSON.stringify({
        properties: {
          approvalPolicy: {}, baseInstructions: {}, cwd: {}, developerInstructions: {},
          ephemeral: {}, model: {}, sandbox: {},
        },
      }));
      const child = new EventEmitter();
      child.stdin = new PassThrough();
      child.stdout = new PassThrough();
      child.stderr = new PassThrough();
      child.kill = () => true;
      queueMicrotask(() => child.emit("close", 0));
      return child;
    };

    const result = await probeCodexSemanticBoundary(native, {
      spawnImpl,
      profileDirectory,
      timeoutMs: 2_000,
    });
    assert.equal(result.semanticReviewCapable, false);
    assert.deepEqual(result.unsupportedControls, [
      "sandboxPolicy.readOnly.access.restricted.readableRoots",
    ]);
    assert.match(result.semanticReviewMessage, /Sign-in and model catalog remain available/);
    assert.match(result.semanticReviewMessage, /does not prove restricted readable roots/);
  });

  it("paginates and normalizes the account model catalog with model-specific efforts", async () => {
    const native = executable();
    const requests = [];
    const child = fakeAppServer((message, server) => {
      if (message.method === "initialize") server.respond({ id: message.id, result: {} });
      if (message.method !== "model/list") return;
      requests.push(message.params);
      if (!message.params.cursor) {
        server.respond({
          id: message.id,
          result: {
            data: [
              {
                id: "gpt-5.4",
                displayName: "GPT-5.4",
                description: "Frontier coding model",
                isDefault: true,
                defaultReasoningEffort: "high",
                supportedReasoningEfforts: [
                  { reasoningEffort: "low", description: "Fast" },
                  { reasoningEffort: "high", description: "Deep" },
                  { reasoningEffort: "invalid", description: "Ignore" },
                ],
              },
              { id: "hidden-model", hidden: true },
            ],
            nextCursor: "page-2",
          },
        });
      } else {
        server.respond({
          id: message.id,
          result: {
            data: [
              { id: "gpt-5.4", displayName: "duplicate" },
              {
                model: "gpt-5.4-mini",
                displayName: "GPT-5.4 mini",
                defaultReasoningEffort: "medium",
                supportedReasoningEfforts: [{ reasoningEffort: "medium" }],
              },
              { id: "bad model id" },
            ],
            nextCursor: null,
          },
        });
      }
    });
    const service = createCodexSubscriptionService({
      runtimeStatus: async () => ({
        runtimes: [{ id: "openai-codex", installed: true, available: true, executable: native }],
      }),
      spawnImpl: () => child,
      openExternal: async () => true,
      profileDirectory: path.join(path.dirname(native), "profile"),
    });

    const catalog = await service.models();
    assert.equal(catalog.source, "codex_app_server");
    assert.equal(catalog.truncated, false);
    assert.deepEqual(requests, [
      { limit: 100, includeHidden: false },
      { limit: 100, includeHidden: false, cursor: "page-2" },
    ]);
    assert.deepEqual(catalog.models.map((model) => model.id), ["gpt-5.4", "gpt-5.4-mini"]);
    assert.deepEqual(catalog.models[0].supportedEfforts, ["low", "high"]);
    assert.equal(catalog.models[0].defaultEffort, "high");
    assert.equal(catalog.models[0].isDefault, true);
    assert.deepEqual(catalog.models[1].supportedEfforts, ["medium"]);
    assert.equal(catalog.models[1].defaultEffort, "medium");
    service.close();
  });

  it("fails closed when a JSONL response exceeds the configured bound", async () => {
    const native = executable();
    const child = fakeAppServer((message, server) => {
      if (message.method === "initialize") {
        queueMicrotask(() => server.stdout.write(Buffer.alloc(2049, 0x61)));
      }
    });
    const client = new CodexAppServerClient({
      executable: native,
      spawnImpl: () => child,
      maxLineBytes: 2048,
      requestTimeoutMs: 100,
      profileDirectory: path.join(path.dirname(native), "profile"),
    });
    await assert.rejects(() => client.start(), /message limit/);
    assert.equal(child.killed, true);
  });
});
