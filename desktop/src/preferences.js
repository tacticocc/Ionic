"use strict";

/**
 * Validated desktop preferences and encrypted credential persistence.
 *
 * This module deliberately has no Electron imports. The main process injects
 * Electron's safeStorage implementation, while the validation and persistence
 * behavior stays testable with plain Node.
 */

const fs = require("node:fs");
const path = require("node:path");

const CUSTOM_THEME_COLOR_KEYS = Object.freeze([
  "canvas",
  "sidebar",
  "surface",
  "border",
  "text",
  "muted",
  "accent",
]);
const CUSTOM_THEME_BASES = new Set(["light", "dark", "oled"]);
const CUSTOM_THEME_DEFAULTS = Object.freeze({
  light: Object.freeze({
    base: "light",
    colors: Object.freeze({
      canvas: "#F8F8F6",
      sidebar: "#F3F4F2",
      surface: "#FFFFFF",
      border: "#D8DDDE",
      text: "#111820",
      muted: "#5E6A72",
      accent: "#006D82",
    }),
  }),
  dark: Object.freeze({
    base: "dark",
    colors: Object.freeze({
      canvas: "#111418",
      sidebar: "#0D1014",
      surface: "#181C22",
      border: "#303842",
      text: "#F4F7FA",
      muted: "#929DA8",
      accent: "#26DBFF",
    }),
  }),
  oled: Object.freeze({
    base: "oled",
    colors: Object.freeze({
      canvas: "#000000",
      sidebar: "#030507",
      surface: "#06090F",
      border: "#172333",
      text: "#F7FAFC",
      muted: "#8F9CAA",
      accent: "#26DBFF",
    }),
  }),
});

const DEFAULT_SETTINGS = Object.freeze({
  registryPath: null,
  ionicBin: null,
  modelAccessMode: "api",
  subscriptionRuntime: "openai-codex",
  codexSubscriptionModel: "",
  codexSubscriptionEffort: null,
  codexSubscriptionConsentVersion: null,
  grokSubscriptionModel: "",
  grokSubscriptionEffort: null,
  grokSubscriptionConsentVersion: null,
  appearanceTheme: "light",
  customTheme: CUSTOM_THEME_DEFAULTS.light,
  useLlm: false,
  failOn: "high",
  transitive: false,
  judgeProvider: "anthropic",
  judgeModel: "claude-sonnet-5",
  anthropicModel: "claude-sonnet-5",
  openaiModel: "gpt-5.2",
  googleModel: "gemini-3.6-flash",
  xaiModel: "grok-4.5",
  openaiCompatibleModel: "qwen2.5-coder",
  judgeEffort: null,
  judgeMaxTokens: 32000,
  openaiCompatibleBaseUrl: "http://localhost:11434/v1",
});

const FAIL_ON_VALUES = new Set(["critical", "high", "medium", "low", "info"]);
const APPEARANCE_THEMES = new Set(["light", "dark", "oled", "custom"]);
const JUDGE_PROVIDERS = new Set(["anthropic", "openai", "google", "xai", "local", "none"]);
const JUDGE_EFFORTS = new Set(["low", "medium", "high", "xhigh", "max"]);
const CREDENTIAL_PROVIDERS = new Set(["anthropic", "openai", "google", "xai", "local"]);
const PROVIDER_MODEL_SETTINGS = Object.freeze({
  anthropic: "anthropicModel",
  openai: "openaiModel",
  google: "googleModel",
  xai: "xaiModel",
  local: "openaiCompatibleModel",
});
const MODEL_SETTING_KEYS = new Set(Object.values(PROVIDER_MODEL_SETTINGS));
const SETTING_KEYS = new Set(Object.keys(DEFAULT_SETTINGS));
const CREDENTIAL_FILE_VERSION = 1;

function applySettingsPatch(base, patch) {
  if (!patch || typeof patch !== "object" || Array.isArray(patch)) {
    throw new TypeError("settings must be an object");
  }

  const next = { ...base };
  for (const [key, value] of Object.entries(patch)) {
    if (!SETTING_KEYS.has(key)) throw new TypeError(`unknown setting ${key}`);

    if (key === "registryPath" || key === "ionicBin") {
      if (value !== null && typeof value !== "string") {
        throw new TypeError(`${key} must be a path or null`);
      }
      next[key] = typeof value === "string" && value.trim() ? path.resolve(value.trim()) : null;
      continue;
    }

    if (key === "modelAccessMode") {
      if (!new Set(["api", "subscription"]).has(value)) {
        throw new TypeError("modelAccessMode must be api or subscription");
      }
      next[key] = value;
      if (value === "subscription") next.useLlm = false;
      continue;
    }

    if (key === "subscriptionRuntime") {
      if (!new Set(["openai-codex", "xai-grok-build"]).has(value)) {
        throw new TypeError("subscriptionRuntime must be openai-codex or xai-grok-build");
      }
      next[key] = value;
      continue;
    }

    if (key === "codexSubscriptionModel" || key === "grokSubscriptionModel") {
      if (typeof value !== "string" || value.trim().length > 200) {
        throw new TypeError(`${key} must be a model identifier of at most 200 characters`);
      }
      next[key] = value.trim();
      continue;
    }

    if (key === "codexSubscriptionEffort" || key === "grokSubscriptionEffort") {
      const allowed = key === "grokSubscriptionEffort"
        ? new Set(["low", "medium", "high", "xhigh"])
        : JUDGE_EFFORTS;
      if (value !== null && !allowed.has(value)) {
        throw new TypeError(
          key === "grokSubscriptionEffort"
            ? `${key} must be low, medium, high, xhigh, or null`
            : `${key} must be low, medium, high, xhigh, max, or null`
        );
      }
      next[key] = value;
      continue;
    }

    if (
      key === "codexSubscriptionConsentVersion" ||
      key === "grokSubscriptionConsentVersion"
    ) {
      if (value !== null && (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}\.\d+$/.test(value))) {
        throw new TypeError(`${key} must be a consent version or null`);
      }
      next[key] = value;
      continue;
    }

    if (key === "appearanceTheme") {
      if (!APPEARANCE_THEMES.has(value)) {
        throw new TypeError("appearanceTheme must be light, dark, oled, or custom");
      }
      next[key] = value;
      continue;
    }

    if (key === "customTheme") {
      next[key] = validateCustomTheme(value);
      continue;
    }

    if (key === "useLlm" || key === "transitive") {
      if (typeof value !== "boolean") throw new TypeError(`${key} must be true or false`);
      next[key] = value;
      continue;
    }

    if (key === "failOn") {
      if (!FAIL_ON_VALUES.has(value)) throw new TypeError("failOn is not a known severity");
      next[key] = value;
      continue;
    }

    if (key === "judgeProvider") {
      if (!JUDGE_PROVIDERS.has(value)) {
        throw new TypeError(
          "judgeProvider must be anthropic, openai, google, xai, local, or none"
        );
      }
      next[key] = value;
      continue;
    }

    if (key === "judgeModel" || MODEL_SETTING_KEYS.has(key)) {
      if (typeof value !== "string" || !value.trim() || value.trim().length > 200) {
        throw new TypeError(`${key} must be between 1 and 200 characters`);
      }
      next[key] = value.trim();
      continue;
    }

    if (key === "judgeEffort") {
      if (value !== null && !JUDGE_EFFORTS.has(value)) {
        throw new TypeError("judgeEffort must be low, medium, high, xhigh, max, or null");
      }
      next[key] = value;
      continue;
    }

    if (key === "judgeMaxTokens") {
      if (!Number.isInteger(value) || value < 256 || value > 200000) {
        throw new TypeError("judgeMaxTokens must be an integer from 256 to 200000");
      }
      next[key] = value;
      continue;
    }

    if (key === "openaiCompatibleBaseUrl") {
      next[key] = validateOpenAiCompatibleBaseUrl(value);
    }
  }

  const provider = next.judgeProvider;
  const changedProvider = Object.hasOwn(patch, "judgeProvider");
  const changedCurrentModel = Object.hasOwn(patch, "judgeModel");
  if (changedProvider && !changedCurrentModel) {
    const modelSetting = PROVIDER_MODEL_SETTINGS[provider];
    if (modelSetting) next.judgeModel = next[modelSetting];
  }
  if (changedCurrentModel) {
    const modelSetting = PROVIDER_MODEL_SETTINGS[provider];
    if (modelSetting) next[modelSetting] = next.judgeModel;
  }
  const selectedModelSetting = PROVIDER_MODEL_SETTINGS[provider];
  if (selectedModelSetting && Object.hasOwn(patch, selectedModelSetting) && !changedCurrentModel) {
    next.judgeModel = next[selectedModelSetting];
  }
  return next;
}

function validateCustomTheme(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError("customTheme must be an object");
  }
  const keys = Object.keys(value);
  if (keys.length !== 2 || !keys.includes("base") || !keys.includes("colors")) {
    throw new TypeError("customTheme must contain only base and colors");
  }
  if (!CUSTOM_THEME_BASES.has(value.base)) {
    throw new TypeError("customTheme.base must be light, dark, or oled");
  }
  if (!value.colors || typeof value.colors !== "object" || Array.isArray(value.colors)) {
    throw new TypeError("customTheme.colors must be an object");
  }
  const colorKeys = Object.keys(value.colors);
  if (
    colorKeys.length !== CUSTOM_THEME_COLOR_KEYS.length ||
    !CUSTOM_THEME_COLOR_KEYS.every((key) => colorKeys.includes(key))
  ) {
    throw new TypeError(`customTheme.colors must contain exactly ${CUSTOM_THEME_COLOR_KEYS.join(", ")}`);
  }

  const colors = {};
  for (const key of CUSTOM_THEME_COLOR_KEYS) {
    const color = value.colors[key];
    if (typeof color !== "string" || !/^#[0-9a-fA-F]{6}$/.test(color)) {
      throw new TypeError(`customTheme.colors.${key} must be a #RRGGBB hex color`);
    }
    colors[key] = color.toUpperCase();
  }
  return { base: value.base, colors };
}

function validateOpenAiCompatibleBaseUrl(value) {
  if (typeof value !== "string" || !value.trim()) {
    throw new TypeError("openaiCompatibleBaseUrl must be an HTTP or HTTPS URL");
  }
  const trimmed = value.trim();
  if (/[\u0000-\u001f\u007f]/.test(trimmed)) {
    throw new TypeError("openaiCompatibleBaseUrl must not contain control characters");
  }
  let parsed;
  try {
    parsed = new URL(trimmed);
  } catch {
    throw new TypeError("openaiCompatibleBaseUrl must be an HTTP or HTTPS URL");
  }
  if (!["http:", "https:"].includes(parsed.protocol)) {
    throw new TypeError("openaiCompatibleBaseUrl must use HTTP or HTTPS");
  }
  if (parsed.username || parsed.password) {
    throw new TypeError("openaiCompatibleBaseUrl must not contain embedded credentials");
  }
  if (parsed.search || parsed.hash) {
    throw new TypeError("openaiCompatibleBaseUrl must not contain a query string or fragment");
  }
  return trimmed;
}

function loadSettings(file, { onError = null } = {}) {
  try {
    const raw = fs.readFileSync(file, "utf8");
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new TypeError("settings must be an object");
    }

    // Migrate older settings files field-by-field. A future or malformed field
    // must not erase unrelated valid preferences from an existing install.
    let migrated = { ...DEFAULT_SETTINGS };
    for (const key of SETTING_KEYS) {
      if (!Object.hasOwn(parsed, key)) continue;
      try {
        migrated = applySettingsPatch(migrated, { [key]: parsed[key] });
      } catch (error) {
        if (typeof onError === "function") onError(error);
      }
    }
    // Ionic Desktop 0.2 used "local" storage names for generic OpenAI-compatible
    // endpoints. Preserve those values without keeping the ambiguous names in
    // the public settings contract.
    if (!Object.hasOwn(parsed, "openaiCompatibleModel") && Object.hasOwn(parsed, "localModel")) {
      try {
        migrated = applySettingsPatch(migrated, { openaiCompatibleModel: parsed.localModel });
      } catch (error) {
        if (typeof onError === "function") onError(error);
      }
    }
    if (
      !Object.hasOwn(parsed, "openaiCompatibleBaseUrl") &&
      Object.hasOwn(parsed, "localBaseUrl")
    ) {
      try {
        migrated = applySettingsPatch(migrated, {
          openaiCompatibleBaseUrl: parsed.localBaseUrl,
        });
      } catch (error) {
        if (typeof onError === "function") onError(error);
      }
    }
    return migrated;
  } catch (error) {
    if (error.code !== "ENOENT" && typeof onError === "function") onError(error);
    return { ...DEFAULT_SETTINGS };
  }
}

function saveSettings(file, base, patch) {
  const next = applySettingsPatch(base, patch);
  atomicWriteJson(file, next);
  return next;
}

function assertCredentialProvider(provider) {
  if (!CREDENTIAL_PROVIDERS.has(provider)) {
    throw new TypeError("credential provider must be anthropic, openai, google, xai, or local");
  }
}

function environmentCredential(provider, env = process.env) {
  assertCredentialProvider(provider);
  if (provider === "anthropic") {
    const apiKey = readEnvironmentValue(env, "ANTHROPIC_API_KEY");
    if (apiKey) return { name: "ANTHROPIC_API_KEY", value: apiKey };
    const authToken = readEnvironmentValue(env, "ANTHROPIC_AUTH_TOKEN");
    return authToken ? { name: "ANTHROPIC_AUTH_TOKEN", value: authToken } : null;
  }
  if (provider === "openai") {
    const apiKey = readEnvironmentValue(env, "OPENAI_API_KEY");
    return apiKey ? { name: "OPENAI_API_KEY", value: apiKey } : null;
  }
  if (provider === "google") {
    const geminiKey = readEnvironmentValue(env, "GEMINI_API_KEY");
    if (geminiKey) return { name: "GEMINI_API_KEY", value: geminiKey };
    const googleKey = readEnvironmentValue(env, "GOOGLE_API_KEY");
    return googleKey ? { name: "GOOGLE_API_KEY", value: googleKey } : null;
  }
  if (provider === "xai") {
    const apiKey = readEnvironmentValue(env, "XAI_API_KEY");
    return apiKey ? { name: "XAI_API_KEY", value: apiKey } : null;
  }
  const localKey = readEnvironmentValue(env, "IONIC_LOCAL_API_KEY");
  return localKey ? { name: "IONIC_LOCAL_API_KEY", value: localKey } : null;
}

function readEnvironmentValue(env, expectedName) {
  const match = Object.keys(env).find((key) => key.toUpperCase() === expectedName);
  const value = match ? env[match] : "";
  return typeof value === "string" ? value.trim() : "";
}

function readCredentialFile(file) {
  try {
    const parsed = JSON.parse(fs.readFileSync(file, "utf8"));
    if (
      !parsed ||
      typeof parsed !== "object" ||
      Array.isArray(parsed) ||
      parsed.version !== CREDENTIAL_FILE_VERSION ||
      !parsed.credentials ||
      typeof parsed.credentials !== "object" ||
      Array.isArray(parsed.credentials)
    ) {
      throw new Error("unsupported credential file format");
    }
    return parsed;
  } catch (error) {
    if (error.code === "ENOENT") {
      return { version: CREDENTIAL_FILE_VERSION, credentials: {} };
    }
    throw new Error(`Could not read encrypted credentials: ${error.message}`);
  }
}

function canUseSafeStorage(safeStorage) {
  try {
    return (
      Boolean(safeStorage?.isEncryptionAvailable?.()) &&
      selectedStorageBackend(safeStorage) !== "basic_text"
    );
  } catch {
    return false;
  }
}

function selectedStorageBackend(safeStorage) {
  try {
    const backend = safeStorage?.getSelectedStorageBackend?.();
    return typeof backend === "string" && backend ? backend : null;
  } catch {
    return null;
  }
}

function credentialStatus(file, { safeStorage, env = process.env } = {}) {
  const encryptionAvailable = canUseSafeStorage(safeStorage);
  const backend = selectedStorageBackend(safeStorage);
  const stored = readCredentialFile(file).credentials;
  const status = { encryptionAvailable, backend };

  for (const provider of CREDENTIAL_PROVIDERS) {
    const hasStoredCredential = typeof stored[provider] === "string" && Boolean(stored[provider]);
    if (environmentCredential(provider, env)) {
      status[provider] = {
        configured: true,
        source: "environment",
        stored: hasStoredCredential,
      };
    } else if (hasStoredCredential) {
      status[provider] = { configured: true, source: "secure", stored: true };
    } else {
      status[provider] = { configured: false, source: "none", stored: false };
    }
  }
  return status;
}

function saveCredential(file, provider, secret, { safeStorage, env = process.env } = {}) {
  assertCredentialProvider(provider);
  if (typeof secret !== "string" || !secret.trim()) {
    throw new TypeError("credential must not be empty");
  }
  if (Buffer.byteLength(secret, "utf8") > 32768) {
    throw new TypeError("credential is too large");
  }
  if (!canUseSafeStorage(safeStorage)) {
    throw new Error("Secure credential storage is unavailable on this computer");
  }

  const document = readCredentialFile(file);
  const encrypted = safeStorage.encryptString(secret.trim());
  if (!Buffer.isBuffer(encrypted) || encrypted.length === 0) {
    throw new Error("Secure credential storage did not return encrypted data");
  }
  document.credentials[provider] = encrypted.toString("base64");
  atomicWriteJson(file, document);
  return credentialStatus(file, { safeStorage, env });
}

function clearCredential(file, provider, { safeStorage, env = process.env } = {}) {
  assertCredentialProvider(provider);
  const document = readCredentialFile(file);
  delete document.credentials[provider];
  atomicWriteJson(file, document);
  return credentialStatus(file, { safeStorage, env });
}

function resetCredentials(file, { safeStorage, env = process.env, now = Date.now } = {}) {
  const directory = path.dirname(file);
  fs.mkdirSync(directory, { recursive: true });
  const quarantine = `${file}.unreadable-${now()}`;
  const existed = fs.existsSync(file);
  if (existed) fs.renameSync(file, quarantine);
  try {
    atomicWriteJson(file, { version: CREDENTIAL_FILE_VERSION, credentials: {} });
  } catch (error) {
    if (existed && fs.existsSync(quarantine) && !fs.existsSync(file)) {
      fs.renameSync(quarantine, file);
    }
    throw error;
  }
  return credentialStatus(file, { safeStorage, env });
}

/** Return a decrypted stored secret only when no environment override exists. */
function resolveCredential(file, provider, { safeStorage, env = process.env } = {}) {
  assertCredentialProvider(provider);
  const inherited = environmentCredential(provider, env);
  if (inherited) return { source: "environment", name: inherited.name, value: inherited.value };

  const encoded = readCredentialFile(file).credentials[provider];
  if (typeof encoded !== "string" || !encoded) return null;
  if (!canUseSafeStorage(safeStorage)) {
    throw new Error("Secure credential storage is unavailable on this computer");
  }
  try {
    const value = safeStorage.decryptString(Buffer.from(encoded, "base64"));
    if (typeof value !== "string" || !value) throw new Error("decrypted credential is empty");
    return { source: "secure", name: credentialEnvironmentName(provider), value };
  } catch (error) {
    throw new Error(`Could not decrypt the saved ${provider} credential: ${error.message}`);
  }
}

function credentialEnvironmentName(provider) {
  assertCredentialProvider(provider);
  return {
    anthropic: "ANTHROPIC_API_KEY",
    openai: "OPENAI_API_KEY",
    google: "GEMINI_API_KEY",
    xai: "XAI_API_KEY",
    local: "IONIC_LOCAL_API_KEY",
  }[provider];
}

function applyCliEnvironment(settings, env = process.env) {
  const output = { ...env };
  const desktopOwned = new Set([
    "IONIC_JUDGE_PROVIDER",
    "IONIC_MODEL_ACCESS",
    "IONIC_SUBSCRIPTION_RUNTIME",
    "IONIC_SUBSCRIPTION_CONSENT_VERSION",
    "IONIC_JUDGE_MODEL",
    "IONIC_JUDGE_EFFORT",
    "IONIC_JUDGE_MAX_TOKENS",
    "IONIC_LOCAL_BASE_URL",
    "IONIC_FAIL_ON",
  ]);
  const credentials = new Set([
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "XAI_API_KEY",
    "IONIC_LOCAL_API_KEY",
  ]);
  for (const key of Object.keys(output)) {
    const normalized = key.toUpperCase();
    if (desktopOwned.has(normalized) || credentials.has(normalized)) delete output[key];
  }
  output.IONIC_JUDGE_PROVIDER = settings.judgeProvider;
  output.IONIC_MODEL_ACCESS = settings.modelAccessMode;
  if (settings.modelAccessMode === "subscription") {
    output.IONIC_SUBSCRIPTION_RUNTIME = settings.subscriptionRuntime;
  } else {
    delete output.IONIC_SUBSCRIPTION_RUNTIME;
  }
  if (settings.modelAccessMode === "subscription") {
    const codex = settings.subscriptionRuntime === "openai-codex";
    const model = codex ? settings.codexSubscriptionModel : settings.grokSubscriptionModel;
    const effort = codex ? settings.codexSubscriptionEffort : settings.grokSubscriptionEffort;
    if (model) output.IONIC_JUDGE_MODEL = model;
    else delete output.IONIC_JUDGE_MODEL;
    if (effort) output.IONIC_JUDGE_EFFORT = effort;
    else delete output.IONIC_JUDGE_EFFORT;
    const consentVersion = codex
      ? settings.codexSubscriptionConsentVersion
      : settings.grokSubscriptionConsentVersion;
    if (consentVersion) output.IONIC_SUBSCRIPTION_CONSENT_VERSION = consentVersion;
    else delete output.IONIC_SUBSCRIPTION_CONSENT_VERSION;
  } else {
    output.IONIC_JUDGE_MODEL = settings.judgeModel;
    delete output.IONIC_SUBSCRIPTION_CONSENT_VERSION;
  }
  output.IONIC_JUDGE_MAX_TOKENS = String(settings.judgeMaxTokens);
  output.IONIC_LOCAL_BASE_URL = settings.openaiCompatibleBaseUrl;
  output.IONIC_FAIL_ON = settings.failOn;
  if (settings.modelAccessMode !== "subscription") {
    if (settings.judgeProvider === "anthropic" && settings.judgeEffort !== null) {
      output.IONIC_JUDGE_EFFORT = settings.judgeEffort;
    } else {
      delete output.IONIC_JUDGE_EFFORT;
    }
  }
  return output;
}

/**
 * Add a resolved credential to a short-lived child environment.
 *
 * A vendor key saved by the app must never be redirected by inherited
 * provider endpoint/header overrides. Environment-owned credentials retain
 * environment routing because the user configured both outside Ionic.
 */
function applyCredentialToEnvironment(env, provider, credential) {
  const output = { ...env };
  if (!credential) return output;
  assertCredentialProvider(provider);

  if (credential.source === "secure" && provider !== "local") {
    const prefixes = {
      anthropic: ["ANTHROPIC_"],
      openai: ["OPENAI_"],
      google: ["GEMINI_", "GOOGLE_"],
      xai: ["XAI_"],
    }[provider];
    for (const key of Object.keys(output)) {
      if (prefixes.some((prefix) => key.toUpperCase().startsWith(prefix))) delete output[key];
    }
  }

  output[credential.name] = credential.value;
  return output;
}

function shouldAttachJudgeCredential(command, request = null) {
  return command === "check" && request?.useLlm === true;
}

function atomicWriteJson(file, value) {
  const directory = path.dirname(file);
  const temporary = `${file}.tmp`;
  fs.mkdirSync(directory, { recursive: true });
  try {
    fs.writeFileSync(temporary, JSON.stringify(value, null, 2), {
      encoding: "utf8",
      mode: 0o600,
    });
    fs.renameSync(temporary, file);
  } catch (error) {
    try {
      fs.rmSync(temporary, { force: true });
    } catch {
      // Preserve the original write error.
    }
    throw error;
  }
}

module.exports = {
  DEFAULT_SETTINGS,
  CUSTOM_THEME_COLOR_KEYS,
  CUSTOM_THEME_DEFAULTS,
  applySettingsPatch,
  validateCustomTheme,
  validateOpenAiCompatibleBaseUrl,
  loadSettings,
  saveSettings,
  credentialStatus,
  saveCredential,
  clearCredential,
  resetCredentials,
  resolveCredential,
  applyCliEnvironment,
  applyCredentialToEnvironment,
  shouldAttachJudgeCredential,
};
