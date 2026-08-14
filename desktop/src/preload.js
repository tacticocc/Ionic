"use strict";

/**
 * The only bridge between the renderer and the outside world.
 *
 * Every method here maps to one registered IPC handler. The renderer gets no
 * filesystem access, no child_process, and no way to reach a channel that is
 * not on this list.
 */

const { contextBridge, ipcRenderer } = require("electron");

// Sandboxed Electron preloads cannot load local CommonJS modules. Keep this
// small display-only sanitizer self-contained so a missing preload bridge can
// never strand startup before the legal status request. The main process still
// applies the authoritative external-url-policy before opening any URL.
const SUBSCRIPTION_AUTHORIZATION_HOSTS = Object.freeze({
  "openai-codex": Object.freeze([
    "auth.openai.com",
    "chatgpt.com",
    "www.chatgpt.com",
  ]),
  "xai-grok-build": Object.freeze([
    "accounts.x.ai",
    "auth.x.ai",
    "grok.com",
    "www.grok.com",
  ]),
});

function sanitizeSubscriptionAuthorizationUrl(
  provider,
  raw,
  { stripQueryAndHash = false } = {}
) {
  const hosts = SUBSCRIPTION_AUTHORIZATION_HOSTS[provider];
  if (!hosts || typeof raw !== "string" || !raw) return "";
  try {
    const url = new URL(raw);
    if (
      url.protocol !== "https:" ||
      url.username ||
      url.password ||
      (url.port && url.port !== "443") ||
      !hosts.includes(url.hostname.toLowerCase())
    ) {
      return "";
    }
    if (stripQueryAndHash) {
      url.search = "";
      url.hash = "";
    }
    return url.toString();
  } catch {
    return "";
  }
}

const invoke = (channel, ...args) => ipcRenderer.invoke(channel, ...args);
const APPEARANCE_THEMES = new Set(["light", "dark", "oled", "custom"]);
const APPEARANCE_THEME_ARGUMENT = "--ionic-appearance-theme=";
const CUSTOM_THEME_ARGUMENT = "--ionic-custom-theme=";
const EDITION_ARGUMENT = "--ionic-edition=";
const PRODUCT_NAME_ARGUMENT = "--ionic-product-name=";
const CUSTOM_THEME_BASES = new Set(["light", "dark", "oled"]);
const CUSTOM_THEME_COLOR_KEYS = [
  "canvas",
  "sidebar",
  "surface",
  "border",
  "text",
  "muted",
  "accent",
];
const DEFAULT_CUSTOM_THEME = {
  base: "light",
  colors: {
    canvas: "#F8F8F6",
    sidebar: "#F3F4F2",
    surface: "#FFFFFF",
    border: "#D8DDDE",
    text: "#111820",
    muted: "#5E6A72",
    accent: "#006D82",
  },
};
const ESSENTIAL_PRODUCT = Object.freeze({
  edition: "essential",
  productName: "Ionic Essential",
});

function initialProduct() {
  const argumentsList = Array.isArray(process.argv) ? process.argv : [];
  const editionArgument = argumentsList.find(
    (value) => typeof value === "string" && value.startsWith(EDITION_ARGUMENT)
  );
  const productArgument = argumentsList.find(
    (value) => typeof value === "string" && value.startsWith(PRODUCT_NAME_ARGUMENT)
  );
  const edition = editionArgument?.slice(EDITION_ARGUMENT.length);
  let productName = null;
  try {
    productName = productArgument
      ? decodeURIComponent(productArgument.slice(PRODUCT_NAME_ARGUMENT.length))
      : null;
  } catch {
    productName = null;
  }
  if (edition !== ESSENTIAL_PRODUCT.edition || productName !== ESSENTIAL_PRODUCT.productName) {
    return { ...ESSENTIAL_PRODUCT };
  }
  return { edition, productName };
}

function initialAppearanceTheme() {
  const argument = Array.isArray(process.argv)
    ? process.argv.find(
        (value) => typeof value === "string" && value.startsWith(APPEARANCE_THEME_ARGUMENT)
      )
    : null;
  const value = argument?.slice(APPEARANCE_THEME_ARGUMENT.length);
  return APPEARANCE_THEMES.has(value) ? value : "light";
}

function validatedCustomTheme(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  if (
    Object.keys(value).length !== 2 ||
    !Object.hasOwn(value, "base") ||
    !Object.hasOwn(value, "colors") ||
    !CUSTOM_THEME_BASES.has(value.base) ||
    !value.colors ||
    typeof value.colors !== "object" ||
    Array.isArray(value.colors)
  ) {
    return null;
  }
  const colorKeys = Object.keys(value.colors);
  if (
    colorKeys.length !== CUSTOM_THEME_COLOR_KEYS.length ||
    !CUSTOM_THEME_COLOR_KEYS.every((key) => colorKeys.includes(key))
  ) {
    return null;
  }
  const colors = {};
  for (const key of CUSTOM_THEME_COLOR_KEYS) {
    const color = value.colors[key];
    if (typeof color !== "string" || !/^#[0-9a-fA-F]{6}$/.test(color)) return null;
    colors[key] = color.toUpperCase();
  }
  return { base: value.base, colors };
}

function initialCustomTheme() {
  const argument = Array.isArray(process.argv)
    ? process.argv.find(
        (value) => typeof value === "string" && value.startsWith(CUSTOM_THEME_ARGUMENT)
      )
    : null;
  if (!argument) return DEFAULT_CUSTOM_THEME;
  try {
    const parsed = JSON.parse(decodeURIComponent(argument.slice(CUSTOM_THEME_ARGUMENT.length)));
    return validatedCustomTheme(parsed) || DEFAULT_CUSTOM_THEME;
  } catch {
    return DEFAULT_CUSTOM_THEME;
  }
}

contextBridge.exposeInMainWorld("ionic", {
  product: initialProduct(),
  initialAppearanceTheme: initialAppearanceTheme(),
  initialCustomTheme: initialCustomTheme(),
  safeSubscriptionVerificationUrl: (provider, raw) =>
    sanitizeSubscriptionAuthorizationUrl(provider, raw, {
      stripQueryAndHash: provider === "xai-grok-build",
    }),
  settings: () => invoke("app:settings"),
  saveSettings: (patch) => invoke("app:save-settings", patch),
  useManagedCli: () => invoke("app:use-managed-cli"),
  credentialStatus: () => invoke("app:credential-status"),
  saveCredential: (provider, secret) => invoke("app:save-credential", provider, secret),
  clearCredential: (provider) => invoke("app:clear-credential", provider),
  resetCredentials: () => invoke("app:reset-credentials"),

  subscriptionRuntimes: () => invoke("subscription:runtimes"),
  subscriptionStatus: (provider, inspect = false) =>
    invoke("subscription:status", provider, inspect === true),
  subscriptionModels: (provider, consent) =>
    invoke("subscription:models", provider, consent),
  beginSubscriptionLogin: (provider, mode, consent) =>
    invoke("subscription:login", provider, mode, consent),
  pollSubscriptionLogin: (provider, loginId) =>
    invoke("subscription:poll", provider, loginId),
  cancelSubscriptionLogin: (provider, loginId) =>
    invoke("subscription:cancel", provider, loginId),
  logoutSubscription: (provider) => invoke("subscription:logout", provider),

  legalStatus: () => invoke("legal:status"),
  acceptLegal: (agreement) => invoke("legal:accept", agreement),
  declineLegal: () => invoke("legal:decline"),
  readLegal: (name) => invoke("legal:document", name),
  listOpenSourceLicenses: () => invoke("legal:oss:list"),
  readOpenSourceLicense: (id) => invoke("legal:oss:read", id),

  locate: () => invoke("ionic:locate"),
  status: () => invoke("ionic:status"),
  list: () => invoke("ionic:list"),
  graph: (rootId) => invoke("ionic:graph", rootId),
  register: (target) => invoke("ionic:register", target),
  workspaceScan: (request) => invoke("ionic:workspace-scan", request),
  workspaceCheck: (request) => invoke("ionic:workspace-check", request),
  workspaceSync: (request) => invoke("ionic:workspace-sync", request),
  check: (request) => invoke("ionic:check", request),
  copyText: (value) => invoke("app:copy-text", value),

  pickFile: () => invoke("dialog:pick-file"),
  pickDirectory: () => invoke("dialog:pick-directory"),
  pickWorkspaceDirectories: () => invoke("dialog:pick-workspace-directories"),
  pickRegistry: () => invoke("dialog:pick-registry"),
  pickCli: () => invoke("dialog:pick-cli"),
  importCustomTheme: () => invoke("appearance:custom-theme:import"),
  exportCustomTheme: (theme) => invoke("appearance:custom-theme:export", theme),
  reveal: (target) => invoke("shell:reveal", target),

  onMenu: (event, handler) => {
    const allowed = [
      "menu:open-registry",
      "menu:register",
      "menu:scan-workspace",
      "menu:choose-cli",
      "menu:use-managed-cli",
      "menu:settings",
      "menu:refresh",
      "menu:show-eula",
      "menu:show-mit",
      "menu:show-third-party",
    ];
    if (!allowed.includes(event) || typeof handler !== "function") return () => {};
    const listener = () => handler();
    ipcRenderer.on(event, listener);
    return () => ipcRenderer.removeListener(event, listener);
  },
});
