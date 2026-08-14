import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, it } from "node:test";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const preferences = require("../src/preferences.js");
const temporaryDirectories = [];

const safeStorage = {
  isEncryptionAvailable: () => true,
  encryptString: (secret) => Buffer.from(`encrypted:${secret}`, "utf8"),
  decryptString: (encrypted) => {
    const value = encrypted.toString("utf8");
    if (!value.startsWith("encrypted:")) throw new Error("invalid ciphertext");
    return value.slice("encrypted:".length);
  },
};

function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "ionic-preferences-test-"));
  temporaryDirectories.push(root);
  return {
    root,
    settingsFile: path.join(root, "user-data", "settings.json"),
    credentialsFile: path.join(root, "user-data", "credentials.json"),
  };
}

afterEach(() => {
  while (temporaryDirectories.length) {
    fs.rmSync(temporaryDirectories.pop(), { recursive: true, force: true });
  }
});

describe("desktop preferences", () => {
  it("defaults to light and accepts the built-in and custom appearance themes", () => {
    assert.equal(preferences.DEFAULT_SETTINGS.appearanceTheme, "light");
    assert.equal(preferences.DEFAULT_SETTINGS.useLlm, false);
    assert.equal(preferences.DEFAULT_SETTINGS.modelAccessMode, "api");
    assert.equal(preferences.DEFAULT_SETTINGS.subscriptionRuntime, "openai-codex");
    assert.equal(preferences.CUSTOM_THEME_DEFAULTS.dark.colors.sidebar, "#0D1014");
    assert.equal(preferences.CUSTOM_THEME_DEFAULTS.oled.colors.sidebar, "#030507");

    for (const appearanceTheme of ["light", "dark", "oled", "custom"]) {
      const settings = preferences.applySettingsPatch(preferences.DEFAULT_SETTINGS, {
        appearanceTheme,
      });
      assert.equal(settings.appearanceTheme, appearanceTheme);
    }

    for (const appearanceTheme of ["system", "blue", "", null, 1]) {
      assert.throws(
        () => preferences.applySettingsPatch(preferences.DEFAULT_SETTINGS, { appearanceTheme }),
        /appearanceTheme must be light, dark, oled, or custom/
      );
    }
  });

  it("validates supported subscription runtimes", () => {
    const configured = preferences.applySettingsPatch(preferences.DEFAULT_SETTINGS, {
      modelAccessMode: "subscription",
      subscriptionRuntime: "xai-grok-build",
    });
    assert.equal(configured.subscriptionRuntime, "xai-grok-build");
    assert.equal(configured.useLlm, false);
    assert.throws(
      () => preferences.applySettingsPatch(configured, { subscriptionRuntime: "anthropic-claude-code" }),
      /subscriptionRuntime/
    );
  });

  it("validates the API/subscription mode and keeps subscription mode structural", () => {
    const subscription = preferences.applySettingsPatch(preferences.DEFAULT_SETTINGS, {
      useLlm: true,
      modelAccessMode: "subscription",
    });
    assert.equal(subscription.modelAccessMode, "subscription");
    assert.equal(subscription.useLlm, false);
    const explicitlyEnabled = preferences.applySettingsPatch(subscription, { useLlm: true });
    assert.equal(explicitlyEnabled.useLlm, true);
    assert.throws(
      () => preferences.applySettingsPatch(preferences.DEFAULT_SETTINGS, { modelAccessMode: "cookies" }),
      /api or subscription/
    );
  });

  it("validates and normalizes the developer custom theme contract", () => {
    const customTheme = {
      base: "dark",
      colors: {
        canvas: "#020a1f",
        sidebar: "#061126",
        surface: "#0b172b",
        border: "#243552",
        text: "#f4f8fc",
        muted: "#9aa9bc",
        accent: "#26dbff",
      },
    };
    const settings = preferences.applySettingsPatch(preferences.DEFAULT_SETTINGS, {
      appearanceTheme: "custom",
      customTheme,
    });

    assert.equal(settings.appearanceTheme, "custom");
    assert.deepEqual(settings.customTheme, {
      base: "dark",
      colors: Object.fromEntries(
        Object.entries(customTheme.colors).map(([key, value]) => [key, value.toUpperCase()])
      ),
    });
    assert.notEqual(settings.customTheme, customTheme);
    assert.notEqual(settings.customTheme.colors, customTheme.colors);

    const invalid = (customThemePatch, pattern) =>
      assert.throws(
        () =>
          preferences.applySettingsPatch(preferences.DEFAULT_SETTINGS, {
            customTheme: customThemePatch,
          }),
        pattern
      );
    invalid(null, /must be an object/);
    invalid({ ...customTheme, future: true }, /only base and colors/);
    invalid({ ...customTheme, base: "system" }, /base must be light, dark, or oled/);
    invalid(
      { ...customTheme, colors: { ...customTheme.colors, accent: "#1234" } },
      /accent must be a #RRGGBB/
    );
    const { accent: _accent, ...missingAccent } = customTheme.colors;
    invalid({ ...customTheme, colors: missingAccent }, /must contain exactly/);
  });

  it("persists appearance and migrates an invalid stored theme without losing other settings", () => {
    const { settingsFile } = fixture();
    const saved = preferences.saveSettings(settingsFile, preferences.DEFAULT_SETTINGS, {
      appearanceTheme: "oled",
    });
    assert.equal(preferences.loadSettings(settingsFile).appearanceTheme, "oled");
    assert.equal(saved.appearanceTheme, "oled");

    fs.writeFileSync(
      settingsFile,
      JSON.stringify({ appearanceTheme: "system", useLlm: true, failOn: "medium" })
    );
    const errors = [];
    const migrated = preferences.loadSettings(settingsFile, {
      onError: (error) => errors.push(error),
    });
    assert.equal(migrated.appearanceTheme, "light");
    assert.equal(migrated.useLlm, true);
    assert.equal(migrated.failOn, "medium");
    assert.equal(errors.length, 1);
  });

  it("migrates an invalid custom palette independently from the selected theme", () => {
    const { settingsFile } = fixture();
    fs.mkdirSync(path.dirname(settingsFile), { recursive: true });
    fs.writeFileSync(
      settingsFile,
      JSON.stringify({
        appearanceTheme: "custom",
        customTheme: {
          base: "dark",
          colors: { ...preferences.CUSTOM_THEME_DEFAULTS.dark.colors, accent: "cyan" },
        },
        failOn: "low",
      })
    );
    const errors = [];

    const migrated = preferences.loadSettings(settingsFile, {
      onError: (error) => errors.push(error),
    });

    assert.equal(migrated.appearanceTheme, "custom");
    assert.deepEqual(migrated.customTheme, preferences.CUSTOM_THEME_DEFAULTS.light);
    assert.equal(migrated.failOn, "low");
    assert.equal(errors.length, 1);
  });

  it("persists every judge setting and leaves no temporary file", () => {
    const { settingsFile } = fixture();
    const saved = preferences.saveSettings(settingsFile, preferences.DEFAULT_SETTINGS, {
      judgeProvider: "local",
      judgeModel: "qwen3:32b",
      judgeEffort: "high",
      judgeMaxTokens: 64000,
      openaiCompatibleBaseUrl: "https://llm.example.test/v1",
      failOn: "medium",
    });

    assert.deepEqual(preferences.loadSettings(settingsFile), saved);
    assert.equal(saved.openaiCompatibleModel, "qwen3:32b");
    assert.equal(saved.anthropicModel, "claude-sonnet-5");
    assert.equal(fs.existsSync(`${settingsFile}.tmp`), false);
  });

  it("persists a separate model identifier for each provider", () => {
    let settings = preferences.applySettingsPatch(preferences.DEFAULT_SETTINGS, {
      judgeModel: "claude-sonnet-custom",
    });
    settings = preferences.applySettingsPatch(settings, { judgeProvider: "local" });
    assert.equal(settings.judgeModel, "qwen2.5-coder");
    settings = preferences.applySettingsPatch(settings, { judgeModel: "qwen3:32b" });
    settings = preferences.applySettingsPatch(settings, { judgeProvider: "openai" });
    assert.equal(settings.judgeModel, "gpt-5.2");
    settings = preferences.applySettingsPatch(settings, { judgeModel: "gpt-custom" });
    settings = preferences.applySettingsPatch(settings, { judgeProvider: "google" });
    assert.equal(settings.judgeModel, "gemini-3.6-flash");
    settings = preferences.applySettingsPatch(settings, { judgeModel: "gemini-custom" });
    settings = preferences.applySettingsPatch(settings, { judgeProvider: "xai" });
    assert.equal(settings.judgeModel, "grok-4.5");
    settings = preferences.applySettingsPatch(settings, { judgeModel: "grok-custom" });
    settings = preferences.applySettingsPatch(settings, { judgeProvider: "anthropic" });

    assert.equal(settings.judgeModel, "claude-sonnet-custom");
    assert.equal(settings.anthropicModel, "claude-sonnet-custom");
    assert.equal(settings.openaiModel, "gpt-custom");
    assert.equal(settings.googleModel, "gemini-custom");
    assert.equal(settings.xaiModel, "grok-custom");
    assert.equal(settings.openaiCompatibleModel, "qwen3:32b");
  });

  it("validates provider, model, effort, token count, and local URL", () => {
    const patch = (value) => preferences.applySettingsPatch(preferences.DEFAULT_SETTINGS, value);
    assert.throws(() => patch({ judgeProvider: "cohere" }), /anthropic, openai, google, xai, local/);
    assert.throws(() => patch({ judgeModel: "   " }), /1 and 200/);
    assert.throws(() => patch({ judgeModel: "x".repeat(201) }), /1 and 200/);
    assert.throws(() => patch({ judgeEffort: "extreme" }), /low, medium, high/);
    assert.throws(() => patch({ judgeMaxTokens: 255 }), /256 to 200000/);
    assert.throws(() => patch({ judgeMaxTokens: 1000.5 }), /integer/);
    assert.throws(() => patch({ openaiCompatibleBaseUrl: "ftp://localhost/model" }), /HTTP or HTTPS/);
    assert.throws(
      () => patch({ openaiCompatibleBaseUrl: "https://user:secret@example.test/v1" }),
      /embedded credentials/
    );
    assert.throws(
      () => patch({ openaiCompatibleBaseUrl: "https://example.test/v1?key=value" }),
      /query string or fragment/
    );
    assert.throws(
      () => patch({ openaiCompatibleBaseUrl: "https://example.test/v1#fragment" }),
      /query string or fragment/
    );
  });

  it("migrates known settings without letting an invalid field erase the rest", () => {
    const { settingsFile } = fixture();
    fs.mkdirSync(path.dirname(settingsFile), { recursive: true });
    fs.writeFileSync(
      settingsFile,
      JSON.stringify({ useLlm: true, failOn: "medium", judgeMaxTokens: -1, futureField: true })
    );
    const errors = [];

    const loaded = preferences.loadSettings(settingsFile, { onError: (error) => errors.push(error) });

    assert.equal(loaded.useLlm, true);
    assert.equal(loaded.failOn, "medium");
    assert.equal(loaded.judgeMaxTokens, preferences.DEFAULT_SETTINGS.judgeMaxTokens);
    assert.equal(Object.hasOwn(loaded, "futureField"), false);
    assert.equal(errors.length, 1);
  });

  it("migrates legacy local model and endpoint names", () => {
    const { settingsFile } = fixture();
    fs.mkdirSync(path.dirname(settingsFile), { recursive: true });
    fs.writeFileSync(
      settingsFile,
      JSON.stringify({
        judgeProvider: "local",
        localModel: "legacy-model",
        localBaseUrl: "http://localhost:1234/v1",
      })
    );

    const loaded = preferences.loadSettings(settingsFile);
    assert.equal(loaded.judgeModel, "legacy-model");
    assert.equal(loaded.openaiCompatibleModel, "legacy-model");
    assert.equal(loaded.openaiCompatibleBaseUrl, "http://localhost:1234/v1");
    assert.equal(Object.hasOwn(loaded, "localModel"), false);
    assert.equal(Object.hasOwn(loaded, "localBaseUrl"), false);
  });

  it("maps settings to deterministic CLI environment overrides", () => {
    const settings = preferences.applySettingsPatch(preferences.DEFAULT_SETTINGS, {
      judgeProvider: "none",
      judgeModel: "test-model",
      judgeEffort: null,
      judgeMaxTokens: 1234,
      openaiCompatibleBaseUrl: "http://localhost:8080/v1",
      failOn: "low",
    });
    const env = preferences.applyCliEnvironment(settings, {
      KEEP_ME: "yes",
      IONIC_JUDGE_EFFORT: "max",
      ionic_judge_model: "environment-model",
      ANTHROPIC_API_KEY: "must-not-reach-status",
      OPENAI_API_KEY: "must-not-reach-status",
      GEMINI_API_KEY: "must-not-reach-status",
      GOOGLE_API_KEY: "must-not-reach-status",
      XAI_API_KEY: "must-not-reach-status",
      IONIC_LOCAL_API_KEY: "must-not-reach-status",
    });

    assert.deepEqual(env, {
      KEEP_ME: "yes",
      IONIC_JUDGE_PROVIDER: "none",
      IONIC_MODEL_ACCESS: "api",
      IONIC_JUDGE_MODEL: "test-model",
      IONIC_JUDGE_MAX_TOKENS: "1234",
      IONIC_LOCAL_BASE_URL: "http://localhost:8080/v1",
      IONIC_FAIL_ON: "low",
    });

    const openai = preferences.applyCliEnvironment(
      preferences.applySettingsPatch(preferences.DEFAULT_SETTINGS, {
        judgeProvider: "openai",
        judgeEffort: "max",
      }),
      {}
    );
    assert.equal(openai.IONIC_JUDGE_EFFORT, undefined);
  });

  it("sanitizes inherited Anthropic routing only for an app-stored key", () => {
    const inherited = {
      ANTHROPIC_AUTH_TOKEN: "old-token",
      ANTHROPIC_BASE_URL: "https://proxy.example.test",
      ANTHROPIC_CUSTOM_HEADERS: "x-secret: inherited",
      Anthropic_Extra_Headers: "x-other: inherited",
      ANTHROPIC_PROFILE: "inherited-profile",
      KEEP_ME: "yes",
    };
    const secure = preferences.applyCredentialToEnvironment(inherited, "anthropic", {
      source: "secure",
      name: "ANTHROPIC_API_KEY",
      value: "stored-key",
    });

    assert.equal(secure.ANTHROPIC_API_KEY, "stored-key");
    assert.equal(secure.ANTHROPIC_AUTH_TOKEN, undefined);
    assert.equal(secure.ANTHROPIC_BASE_URL, undefined);
    assert.equal(secure.ANTHROPIC_CUSTOM_HEADERS, undefined);
    assert.equal(secure.Anthropic_Extra_Headers, undefined);
    assert.equal(secure.ANTHROPIC_PROFILE, undefined);
    assert.equal(secure.KEEP_ME, "yes");

    const environment = preferences.applyCredentialToEnvironment(inherited, "anthropic", {
      source: "environment",
      name: "ANTHROPIC_AUTH_TOKEN",
      value: "environment-token",
    });
    assert.equal(environment.ANTHROPIC_AUTH_TOKEN, "environment-token");
    assert.equal(environment.ANTHROPIC_BASE_URL, "https://proxy.example.test");
    assert.equal(environment.ANTHROPIC_CUSTOM_HEADERS, "x-secret: inherited");
  });

  it("attaches a judge credential only to semantic check commands", () => {
    assert.equal(preferences.shouldAttachJudgeCredential("check", { useLlm: true }), true);
    assert.equal(preferences.shouldAttachJudgeCredential("check", { useLlm: false }), false);
    for (const command of ["locate", "status", "list", "register", "show", "graph"]) {
      assert.equal(preferences.shouldAttachJudgeCredential(command, { useLlm: true }), false);
    }
  });
});

describe("encrypted credentials", () => {
  it("stores ciphertext separately and never returns a secret in status", () => {
    const { credentialsFile } = fixture();
    const status = preferences.saveCredential(credentialsFile, "anthropic", "sk-test-secret", {
      safeStorage,
      env: {},
    });

    assert.deepEqual(status, {
      encryptionAvailable: true,
      backend: null,
      anthropic: { configured: true, source: "secure", stored: true },
      openai: { configured: false, source: "none", stored: false },
      google: { configured: false, source: "none", stored: false },
      xai: { configured: false, source: "none", stored: false },
      local: { configured: false, source: "none", stored: false },
    });
    const raw = fs.readFileSync(credentialsFile, "utf8");
    assert.equal(raw.includes("sk-test-secret"), false);
    assert.equal(JSON.stringify(status).includes("sk-test-secret"), false);
    assert.equal(fs.existsSync(`${credentialsFile}.tmp`), false);

    assert.deepEqual(
      preferences.resolveCredential(credentialsFile, "anthropic", { safeStorage, env: {} }),
      {
        source: "secure",
        name: "ANTHROPIC_API_KEY",
        value: "sk-test-secret",
      }
    );
  });

  it("gives environment credentials precedence without exposing their values", () => {
    const { credentialsFile } = fixture();
    preferences.saveCredential(credentialsFile, "anthropic", "stored-secret", {
      safeStorage,
      env: {},
    });
    const env = { ANTHROPIC_AUTH_TOKEN: "environment-secret" };

    const status = preferences.credentialStatus(credentialsFile, { safeStorage, env });
    assert.deepEqual(status.anthropic, {
      configured: true,
      source: "environment",
      stored: true,
    });
    assert.equal(JSON.stringify(status).includes("environment-secret"), false);
    assert.deepEqual(
      preferences.resolveCredential(credentialsFile, "anthropic", { safeStorage, env }),
      {
        source: "environment",
        name: "ANTHROPIC_AUTH_TOKEN",
        value: "environment-secret",
      }
    );
  });

  it("reports local environment credentials without reading them back", () => {
    const { credentialsFile } = fixture();
    const status = preferences.credentialStatus(credentialsFile, {
      safeStorage,
      env: { IONIC_LOCAL_API_KEY: "local-environment-secret" },
    });
    assert.deepEqual(status.local, {
      configured: true,
      source: "environment",
      stored: false,
    });
    assert.equal(JSON.stringify(status).includes("local-environment-secret"), false);
  });

  it("recognizes every vendor environment key without exposing values", () => {
    const { credentialsFile } = fixture();
    const status = preferences.credentialStatus(credentialsFile, {
      safeStorage,
      env: {
        OPENAI_API_KEY: "openai-secret",
        GOOGLE_API_KEY: "google-secret",
        XAI_API_KEY: "xai-secret",
      },
    });
    assert.equal(status.openai.source, "environment");
    assert.equal(status.google.source, "environment");
    assert.equal(status.xai.source, "environment");
    assert.doesNotMatch(JSON.stringify(status), /openai-secret|google-secret|xai-secret/);
  });

  it("clears only the selected stored provider", () => {
    const { credentialsFile } = fixture();
    preferences.saveCredential(credentialsFile, "anthropic", "anthropic-secret", {
      safeStorage,
      env: {},
    });
    preferences.saveCredential(credentialsFile, "local", "local-secret", {
      safeStorage,
      env: {},
    });
    for (const provider of ["openai", "google", "xai"]) {
      preferences.saveCredential(credentialsFile, provider, `${provider}-secret`, {
        safeStorage,
        env: {},
      });
    }

    const status = preferences.clearCredential(credentialsFile, "anthropic", {
      safeStorage,
      env: {},
    });
    assert.deepEqual(status.anthropic, { configured: false, source: "none", stored: false });
    assert.deepEqual(status.local, { configured: true, source: "secure", stored: true });
    for (const provider of ["openai", "google", "xai"]) {
      assert.deepEqual(status[provider], { configured: true, source: "secure", stored: true });
    }
  });

  it("quarantines an unreadable credential file before resetting saved keys", () => {
    const { root, credentialsFile } = fixture();
    fs.mkdirSync(path.dirname(credentialsFile), { recursive: true });
    fs.writeFileSync(credentialsFile, "not json", "utf8");

    const status = preferences.resetCredentials(credentialsFile, {
      safeStorage,
      env: {},
      now: () => 12345,
    });

    assert.deepEqual(status.anthropic, { configured: false, source: "none", stored: false });
    assert.equal(fs.existsSync(`${credentialsFile}.unreadable-12345`), true);
    assert.equal(fs.readFileSync(`${credentialsFile}.unreadable-12345`, "utf8"), "not json");
    assert.equal(JSON.parse(fs.readFileSync(credentialsFile, "utf8")).version, 1);
    assert.equal(root.length > 0, true);
  });

  it("refuses plaintext fallback when OS encryption is unavailable", () => {
    const { credentialsFile } = fixture();
    const unavailable = { isEncryptionAvailable: () => false };
    assert.throws(
      () =>
        preferences.saveCredential(credentialsFile, "anthropic", "secret", {
          safeStorage: unavailable,
          env: {},
        }),
      /Secure credential storage is unavailable/
    );
    assert.equal(fs.existsSync(credentialsFile), false);
  });

  it("rejects Electron's insecure basic_text fallback and reports the backend", () => {
    const { credentialsFile } = fixture();
    const basicText = {
      isEncryptionAvailable: () => true,
      getSelectedStorageBackend: () => "basic_text",
      encryptString: (secret) => Buffer.from(secret),
    };
    const status = preferences.credentialStatus(credentialsFile, {
      safeStorage: basicText,
      env: {},
    });
    assert.equal(status.encryptionAvailable, false);
    assert.equal(status.backend, "basic_text");
    assert.throws(
      () =>
        preferences.saveCredential(credentialsFile, "local", "secret", {
          safeStorage: basicText,
          env: {},
        }),
      /Secure credential storage is unavailable/
    );
  });
});
