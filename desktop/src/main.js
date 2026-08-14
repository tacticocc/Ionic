"use strict";

/**
 * Ionic Desktop -- main process.
 *
 * Security posture: context isolation on, node integration off, a narrow
 * preload bridge, and no remote content. The renderer can only reach the
 * handlers registered below, and every one of them ends in a CLI call.
 */

const {
  app,
  BrowserWindow,
  clipboard,
  dialog,
  ipcMain,
  shell,
  Menu,
  nativeTheme,
  safeStorage,
} = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const { EDITION, configureAppIdentity } = require("./edition");
const ionic = require("./ionic-cli");
const legal = require("./legal");
const preferences = require("./preferences");
const customThemeFile = require("./custom-theme-file");
const { createSubscriptionRuntimeService } = require("./subscription-runtime-service");
const { createCodexSubscriptionService } = require("./codex-subscription");
const { createGrokSubscriptionService } = require("./grok-subscription");
const { isAllowedExternalUrl } = require("./external-url-policy");
const {
  requireSubscriptionConsent,
} = require("./subscription-consent");
const { composeDesktopStatus } = require("./desktop-status");

// Establish the Essential profile before taking the single-instance lock or
// resolving any userData-backed settings so secrets, registries, and legal
// acceptance always use the immutable product profile.
configureAppIdentity(app);

const SETTINGS_FILE = () => path.join(app.getPath("userData"), "settings.json");
const CREDENTIALS_FILE = () => path.join(app.getPath("userData"), "credentials.json");
const RENDERER_FILE = path.join(__dirname, "renderer", "index.html");
const RENDERER_URL = pathToFileURL(RENDERER_FILE).href;
const APPEARANCE_BACKGROUNDS = Object.freeze({
  light: "#F8F8F6",
  dark: "#111418",
  oled: "#000000",
});

let mainWindow = null;
let settings = { ...preferences.DEFAULT_SETTINGS };
let isQuitting = false;
let subscriptions = null;
let runtimeStatusCache = null;
let runtimeStatusCachedAt = 0;

/* ------------------------------------------------------------------ */
/* settings                                                            */
/* ------------------------------------------------------------------ */

function loadSettings() {
  settings = preferences.loadSettings(SETTINGS_FILE(), {
    onError: (error) => console.error("could not read settings:", error.message),
  });
  return settings;
}

function saveSettings(patch) {
  settings = preferences.saveSettings(SETTINGS_FILE(), settings, patch);
  return settings;
}

function normalizedAppearanceTheme(theme) {
  return theme === "custom" || Object.hasOwn(APPEARANCE_BACKGROUNDS, theme)
    ? theme
    : preferences.DEFAULT_SETTINGS.appearanceTheme;
}

function normalizedCustomTheme(customTheme) {
  try {
    return preferences.validateCustomTheme(customTheme);
  } catch {
    return preferences.DEFAULT_SETTINGS.customTheme;
  }
}

function appearanceBase(theme, customTheme) {
  const normalized = normalizedAppearanceTheme(theme);
  return normalized === "custom" ? normalizedCustomTheme(customTheme).base : normalized;
}

function appearanceBackground(theme, customTheme) {
  const normalized = normalizedAppearanceTheme(theme);
  if (normalized === "custom") return normalizedCustomTheme(customTheme).colors.canvas;
  return APPEARANCE_BACKGROUNDS[normalized];
}

function applyNativeAppearance(theme, customTheme = settings.customTheme) {
  const base = appearanceBase(theme, customTheme);
  nativeTheme.themeSource = base === "light" ? "light" : "dark";
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.setBackgroundColor(appearanceBackground(theme, customTheme));
  }
  return normalizedAppearanceTheme(theme);
}

function encodedCustomTheme(customTheme) {
  return encodeURIComponent(JSON.stringify(normalizedCustomTheme(customTheme)));
}

function cliOptions({ withJudgeCredential = false } = {}) {
  let env = preferences.applyCliEnvironment(settings, process.env);
  if (
    withJudgeCredential &&
    settings.modelAccessMode !== "subscription" &&
    settings.judgeProvider !== "none"
  ) {
    const credential = preferences.resolveCredential(
      CREDENTIALS_FILE(),
      settings.judgeProvider,
      { safeStorage, env: process.env }
    );
    env = preferences.applyCredentialToEnvironment(env, settings.judgeProvider, credential);
  }
  return {
    registryPath: settings.registryPath || path.join(app.getPath("userData"), "registry.db"),
    explicitBin: settings.ionicBin || null,
    appDir: app.getAppPath(),
    resourcesDir: process.resourcesPath,
    env,
  };
}

function legalOptions() {
  return {
    appDir: app.getAppPath(),
    resourcesDir: process.resourcesPath,
    isPackaged: app.isPackaged,
    recordFile: path.join(app.getPath("userData"), "legal.json"),
    appVersion: app.getVersion(),
    edition: EDITION.id,
  };
}

async function cachedRuntimeStatus() {
  const now = Date.now();
  if (runtimeStatusCache && now - runtimeStatusCachedAt < 60_000) return runtimeStatusCache;
  runtimeStatusCache = await ionic.runtimeStatus(cliOptions());
  runtimeStatusCachedAt = now;
  return runtimeStatusCache;
}

function subscriptionService() {
  if (subscriptions) return subscriptions;
  const openExternal = async (url) => {
    if (!isAllowedExternalUrl(url)) throw new Error("Refused to open an untrusted authorization URL");
    await shell.openExternal(url);
    return true;
  };
  const codexSubscription = createCodexSubscriptionService({
    runtimeStatus: cachedRuntimeStatus,
    env: process.env,
    openExternal,
    version: app.getVersion(),
    clientInfo: {
      name: `ionic_${EDITION.id}`,
      title: EDITION.productName,
      version: app.getVersion(),
    },
  });
  const grokSubscription = createGrokSubscriptionService({
    runtimeStatus: cachedRuntimeStatus,
    env: process.env,
  });
  subscriptions = createSubscriptionRuntimeService({
    services: {
      "openai-codex": codexSubscription,
      "xai-grok-build": grokSubscription,
    },
  });
  return subscriptions;
}

async function requireAcceptedLegal() {
  const current = await legal.legalStatus(legalOptions());
  if (!current?.accepted) {
    throw new Error("Accept the current desktop terms before using this operation");
  }
}

async function requireSubscriptionReviewAccess(request) {
  if (
    request?.useLlm !== true ||
    settings.modelAccessMode !== "subscription"
  ) {
    return null;
  }

  const provider = settings.subscriptionRuntime;
  const version = provider === "openai-codex"
    ? settings.codexSubscriptionConsentVersion
    : settings.grokSubscriptionConsentVersion;
  const disclosure = requireSubscriptionConsent(provider, {
    accepted: true,
    provider,
    version,
  });
  const consent = {
    accepted: true,
    provider,
    version: disclosure.version,
  };

  // Codex can report its exact account mode without exposing a token. A
  // ChatGPT-backed review must fail closed when Codex is using API-key or
  // custom authentication, otherwise "Subscription" could incur API billing.
  if (provider === "openai-codex") {
    const status = await subscriptionService().status(provider, {
      probeAuthentication: true,
    });
    if (status?.connected !== true || status?.authMode !== "chatgpt") {
      const error = new Error(
        "Sign in to the official Codex runtime with ChatGPT before running a subscription review"
      );
      error.code = "SUBSCRIPTION_AUTH_REQUIRED";
      throw error;
    }
    if (status?.semanticReviewCapable !== true) {
      const error = new Error(
        status?.semanticReviewMessage ||
        "Semantic review is unavailable because the installed Codex app-server does not prove Ionic's restricted read-only root boundary. Linking and model inspection remain available."
      );
      error.code = "SUBSCRIPTION_RUNTIME_BOUNDARY_UNAVAILABLE";
      throw error;
    }
  } else {
    const status = await subscriptionService().status(provider, {
      probeAuthentication: false,
    });
    if (status?.installed !== true) {
      const error = new Error(
        "Install the official Grok Build runtime before running a subscription review"
      );
      error.code = "SUBSCRIPTION_RUNTIME_REQUIRED";
      throw error;
    }
  }

  // Settings files are local but user-editable. Re-read the runtime-owned
  // catalog immediately before a paid/subscription review so stale or
  // tampered model and effort values cannot be forwarded to a different
  // provider route. An empty value deliberately means the runtime default.
  const catalog = await subscriptionService().models(provider, consent);
  const models = Array.isArray(catalog?.models) ? catalog.models : [];
  const modelSetting = provider === "openai-codex"
    ? "codexSubscriptionModel"
    : "grokSubscriptionModel";
  const effortSetting = provider === "openai-codex"
    ? "codexSubscriptionEffort"
    : "grokSubscriptionEffort";
  const selectedModelId = settings[modelSetting] || "";
  const selectedEffort = settings[effortSetting] || null;
  const selectedModel = selectedModelId
    ? models.find((model) => model?.id === selectedModelId)
    : models.find((model) => model?.isDefault === true) || models[0] || null;
  if (selectedModelId && !selectedModel) {
    const error = new Error(
      "The selected subscription model is no longer advertised by the official runtime. Inspect models again."
    );
    error.code = "SUBSCRIPTION_MODEL_STALE";
    throw error;
  }
  if (
    selectedEffort &&
    (!selectedModel ||
      !Array.isArray(selectedModel.supportedEfforts) ||
      !selectedModel.supportedEfforts.includes(selectedEffort))
  ) {
    const error = new Error(
      "The selected reasoning effort is not available for this runtime model. Inspect models again."
    );
    error.code = "SUBSCRIPTION_EFFORT_STALE";
    throw error;
  }

  return disclosure;
}

/* ------------------------------------------------------------------ */
/* window                                                              */
/* ------------------------------------------------------------------ */

function createWindow() {
  const appearanceTheme = normalizedAppearanceTheme(settings.appearanceTheme);
  const customTheme = normalizedCustomTheme(settings.customTheme);
  mainWindow = new BrowserWindow({
    width: 1180,
    height: 800,
    minWidth: 760,
    minHeight: 600,
    backgroundColor: appearanceBackground(appearanceTheme, customTheme),
    title: EDITION.productName,
    show: false,
    autoHideMenuBar: process.platform !== "darwin",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      additionalArguments: [
        `--ionic-appearance-theme=${appearanceTheme}`,
        `--ionic-custom-theme=${encodedCustomTheme(customTheme)}`,
        `--ionic-edition=${EDITION.id}`,
        `--ionic-product-name=${encodeURIComponent(EDITION.productName)}`,
      ],
    },
  });

  let rendererFailureShown = false;
  const reportRendererFailure = (message) => {
    if (rendererFailureShown) return;
    rendererFailureShown = true;
    mainWindow?.show();
    dialog.showErrorBox(
      `${EDITION.productName} could not start`,
      `${message}\n\nReinstall the desktop app or run it from a fresh checkout.`
    );
  };

  mainWindow.once("ready-to-show", () => mainWindow.show());
  mainWindow.webContents.on("did-fail-load", (_event, code, description) => {
    reportRendererFailure(`${description} (${code})`);
  });
  mainWindow.webContents.on("render-process-gone", (_event, details) => {
    if (!isQuitting && details.reason !== "clean-exit") {
      reportRendererFailure(`The renderer stopped unexpectedly: ${details.reason}`);
    }
  });
  mainWindow.loadFile(RENDERER_FILE).catch((error) => reportRendererFailure(error.message));

  // Nothing in this app should ever open a new window or navigate away.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (isAllowedExternalUrl(url)) shell.openExternal(url);
    return { action: "deny" };
  });
  mainWindow.webContents.on("will-navigate", (event) => event.preventDefault());

  mainWindow.on("closed", () => (mainWindow = null));
}

function buildMenu() {
  const isMac = process.platform === "darwin";
  const template = [
    ...(isMac ? [{ role: "appMenu" }] : []),
    {
      label: "File",
      submenu: [
        {
          label: "Open Registry…",
          accelerator: "CmdOrCtrl+O",
          click: () => mainWindow?.webContents.send("menu:open-registry"),
        },
        {
          label: "Register Contracts…",
          accelerator: "CmdOrCtrl+R",
          click: () => mainWindow?.webContents.send("menu:register"),
        },
        {
          label: "Scan Workspace",
          accelerator: "CmdOrCtrl+Shift+S",
          click: () => mainWindow?.webContents.send("menu:scan-workspace"),
        },
        {
          label: "Choose Ionic Executable…",
          click: () => mainWindow?.webContents.send("menu:choose-cli"),
        },
        {
          label: "Use Managed Engine",
          click: () => mainWindow?.webContents.send("menu:use-managed-cli"),
        },
        { type: "separator" },
        {
          label: "Settings",
          accelerator: "CmdOrCtrl+,",
          click: () => mainWindow?.webContents.send("menu:settings"),
        },
        { type: "separator" },
        {
          label: "Refresh Workspace",
          accelerator: "CmdOrCtrl+Shift+R",
          click: () => mainWindow?.webContents.send("menu:refresh"),
        },
        { type: "separator" },
        isMac ? { role: "close" } : { role: "quit" },
      ],
    },
    {
      label: "View",
      submenu: [
        { role: "reload" },
        { role: "toggleDevTools" },
        { type: "separator" },
        { role: "resetZoom" },
        { role: "zoomIn" },
        { role: "zoomOut" },
        { type: "separator" },
        { role: "togglefullscreen" },
      ],
    },
    {
      role: "help",
      submenu: [
        {
          label: "Ionic on GitHub",
          click: () => shell.openExternal("https://github.com/tacticocc/Ionic"),
        },
        { type: "separator" },
        {
          label: "End User License Agreement",
          click: () => mainWindow?.webContents.send("menu:show-eula"),
        },
        {
          label: "Ionic MIT License",
          click: () => mainWindow?.webContents.send("menu:show-mit"),
        },
        {
          label: "Third-Party Notices",
          click: () => mainWindow?.webContents.send("menu:show-third-party"),
        },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

/* ------------------------------------------------------------------ */
/* IPC                                                                 */
/* ------------------------------------------------------------------ */

/** Wrap a handler so the renderer always gets {ok, data} | {ok:false, error}. */
const LEGAL_GATE_EXEMPT_CHANNELS = new Set([
  "app:clear-credential",
  "app:reset-credentials",
  "subscription:cancel",
  "subscription:logout",
  "legal:status",
  "legal:accept",
  "legal:document",
  "legal:oss:list",
  "legal:oss:read",
  "legal:decline",
]);

function handle(channel, fn) {
  ipcMain.handle(channel, async (event, ...args) => {
    try {
      if (!isTrustedSender(event)) throw new Error("Rejected a request from an unknown renderer");
      if (!LEGAL_GATE_EXEMPT_CHANNELS.has(channel)) await requireAcceptedLegal();
      return { ok: true, data: await fn(...args) };
    } catch (err) {
      return {
        ok: false,
        error: {
          name: err.name || "Error",
          message: err.message || String(err),
          notFound: err.name === "IonicNotFound",
          searched: err.searched || null,
          code:
            typeof err.code === "number" || typeof err.code === "string"
              ? err.code
              : null,
        },
      };
    }
  });
}

handle("app:settings", async () => settings);
handle("app:save-settings", async (patch) => {
  const previousBin = settings.ionicBin;
  const previousAppearanceTheme = settings.appearanceTheme;
  const previousCustomTheme = settings.customTheme;
  const next = saveSettings(patch);
  if (next.ionicBin !== previousBin) ionic.resetResolution?.();
  if (
    next.appearanceTheme !== previousAppearanceTheme ||
    (next.appearanceTheme === "custom" && next.customTheme !== previousCustomTheme)
  ) {
    applyNativeAppearance(next.appearanceTheme, next.customTheme);
  }
  return next;
});
handle("app:use-managed-cli", async () => {
  const next = saveSettings({ ionicBin: null });
  ionic.resetResolution?.();
  return next;
});
handle("app:credential-status", async () =>
  preferences.credentialStatus(CREDENTIALS_FILE(), { safeStorage, env: process.env })
);
handle("app:save-credential", async (provider, secret) =>
  preferences.saveCredential(CREDENTIALS_FILE(), provider, secret, {
    safeStorage,
    env: process.env,
  })
);
handle("app:clear-credential", async (provider) =>
  preferences.clearCredential(CREDENTIALS_FILE(), provider, {
    safeStorage,
    env: process.env,
  })
);
handle("app:reset-credentials", async () =>
  preferences.resetCredentials(CREDENTIALS_FILE(), {
    safeStorage,
    env: process.env,
  })
);

handle("subscription:runtimes", async () => cachedRuntimeStatus());
handle("subscription:status", async (provider, inspect = false) => {
  await requireAcceptedLegal();
  return subscriptionService().status(provider, {
    probeAuthentication: inspect === true,
  });
});
handle("subscription:models", async (provider, consent) => {
  await requireAcceptedLegal();
  return subscriptionService().models(provider, consent);
});
handle("subscription:login", async (provider, mode, consent) => {
  await requireAcceptedLegal();
  return subscriptionService().beginLogin(provider, mode, consent);
});
handle("subscription:poll", async (provider, loginId) => {
  await requireAcceptedLegal();
  return subscriptionService().pollLogin(provider, loginId);
});
// Cancellation and logout remain available after terms change, so a pending
// login can always be stopped and Codex can always erase its managed session.
handle("subscription:cancel", async (provider, loginId) =>
  subscriptionService().cancelLogin(provider, loginId)
);
handle("subscription:logout", async (provider) =>
  subscriptionService().logout(provider)
);

handle("legal:status", async () => legal.legalStatus(legalOptions()));
handle("legal:accept", async (agreement) => legal.acceptAgreement(agreement, legalOptions()));
handle("legal:document", async (name) => legal.readDocument(name, legalOptions()));
handle("legal:oss:list", async () => legal.listOpenSourceLicenses(legalOptions()));
handle("legal:oss:read", async (id) => legal.readOpenSourceLicense(id, legalOptions()));
handle("legal:decline", async () => {
  setImmediate(() => app.quit());
  return true;
});

handle("ionic:locate", async () => ionic.locate(cliOptions()));

handle("ionic:status", async () =>
  composeDesktopStatus(await ionic.status(cliOptions()), settings, {
    version: app.getVersion(),
    edition: EDITION.id,
    productName: EDITION.productName,
  })
);
handle("ionic:list", async () => ionic.list(cliOptions()));
handle("ionic:graph", async (rootId) => ionic.graph(rootId || null, cliOptions()));
handle("ionic:register", async (target) => ionic.register(target, cliOptions()));
handle("ionic:workspace-scan", async (request) =>
  ionic.workspaceScan(request, cliOptions())
);
handle("ionic:workspace-check", async (request) =>
  ionic.workspaceCheck(request, cliOptions())
);
handle("ionic:workspace-sync", async (request) =>
  ionic.workspaceSync(request, cliOptions())
);
handle("ionic:check", async (request) => {
  await requireSubscriptionReviewAccess(request);
  return ionic.check(
    request,
    cliOptions({
      withJudgeCredential: preferences.shouldAttachJudgeCredential("check", request),
    })
  );
});
handle("app:copy-text", async (value) => {
  if (typeof value !== "string" || value.length > 1_000_000) {
    throw new TypeError("clipboard content must be text under 1 MB");
  }
  clipboard.writeText(value);
  return true;
});

handle("dialog:pick-file", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "Choose a proposed contract",
    properties: ["openFile"],
    filters: [
      { name: "Agent instructions", extensions: ["md", "markdown"] },
      { name: "Contracts", extensions: ["json", "yaml", "yml"] },
      { name: "All files", extensions: ["*"] },
    ],
  });
  return result.canceled ? null : result.filePaths[0];
});

handle("dialog:pick-directory", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "Choose a directory to scan for AGENTS.md / CLAUDE.md",
    properties: ["openDirectory"],
  });
  return result.canceled ? null : result.filePaths[0];
});

handle("dialog:pick-workspace-directories", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "Add repositories to this workspace",
    buttonLabel: "Add repositories",
    properties: ["openDirectory", "multiSelections"],
  });
  return result.canceled ? [] : result.filePaths;
});

handle("dialog:pick-cli", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "Choose the Ionic Contracts executable",
    properties: ["openFile"],
    filters: [
      { name: "Executables", extensions: process.platform === "win32" ? ["exe", "cmd", "bat"] : ["*"] },
      { name: "All files", extensions: ["*"] },
    ],
  });
  return result.canceled ? null : result.filePaths[0];
});

handle("dialog:pick-registry", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "Open an Ionic registry",
    properties: ["openFile"],
    filters: [{ name: "Ionic registry", extensions: ["db"] }],
  });
  if (result.canceled) return null;
  return result.filePaths[0];
});

handle("appearance:custom-theme:import", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "Import an Ionic custom theme",
    buttonLabel: "Import theme",
    properties: ["openFile", "dontAddToRecent"],
    filters: [{ name: "Ionic custom theme", extensions: ["json"] }],
  });
  if (result.canceled) return { canceled: true };
  const file = result.filePaths[0];
  return {
    canceled: false,
    fileName: path.basename(file),
    customTheme: customThemeFile.readCustomThemeFile(file),
  };
});

handle("appearance:custom-theme:export", async (theme) => {
  const normalized = preferences.validateCustomTheme(theme);
  const result = await dialog.showSaveDialog(mainWindow, {
    title: "Export Ionic custom theme",
    buttonLabel: "Export theme",
    defaultPath: path.join(app.getPath("documents"), "ionic-custom-theme.json"),
    properties: ["showOverwriteConfirmation", "dontAddToRecent"],
    filters: [{ name: "Ionic custom theme", extensions: ["json"] }],
  });
  if (result.canceled || !result.filePath) return { canceled: true };
  customThemeFile.writeCustomThemeFile(result.filePath, normalized);
  return {
    canceled: false,
    fileName: path.basename(result.filePath),
  };
});

handle("shell:reveal", async (target) => {
  if (typeof target !== "string" || !target.trim()) throw new TypeError("a file path is required");
  const resolved = path.resolve(target);
  if (!fs.existsSync(resolved)) throw new Error(`The file no longer exists: ${resolved}`);
  shell.showItemInFolder(resolved);
  return true;
});

function isTrustedSender(event) {
  const senderUrl = event.senderFrame?.url || event.sender?.getURL?.();
  return senderUrl === RENDERER_URL;
}

/* ------------------------------------------------------------------ */
/* lifecycle                                                           */
/* ------------------------------------------------------------------ */

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("before-quit", () => {
    isQuitting = true;
    subscriptions?.close?.();
  });
  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  app.whenReady().then(() => {
    loadSettings();
    applyNativeAppearance(settings.appearanceTheme, settings.customTheme);
    buildMenu();
    createWindow();

    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
  });

  app.on("window-all-closed", () => {
    if (process.platform !== "darwin") app.quit();
  });
}
