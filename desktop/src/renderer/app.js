"use strict";

/* Ionic Desktop -- renderer.
 *
 * No Node, no filesystem, no network. Everything crosses the narrow preload
 * bridge and ends in the Ionic CLI.
 */

const SEVERITIES = ["critical", "high", "medium", "low", "info"];
const SEVERITY_RANK = { info: 0, low: 1, medium: 2, high: 3, critical: 4 };
const LEGAL_DOCUMENTS = {
  eula: "Desktop EULA",
  mit: "MIT License",
  "third-party": "Open Source Licenses",
};
const APPEARANCE_THEMES = new Set(["light", "dark", "oled", "custom"]);
const APPEARANCE_CACHE_KEY = "ionic.appearanceTheme";
const CUSTOM_THEME_CACHE_KEY = "ionic.customTheme";
const PANE_LAYOUT_CACHE_KEY = "ionic.layout.panes.v1";
const WORKSPACE_REPOSITORIES_CACHE_KEY = "ionic.workspace.repositories.v1";
const LEGAL_STATUS_TIMEOUT_MS = 12_000;
const MAX_WORKSPACE_REPOSITORIES = 64;
const WORKSPACE_REPOSITORY_ID = /^[a-z0-9][a-z0-9._-]{0,63}$/;
const PANE_LAYOUTS = {
  workspaceSidebar: {
    handle: "#workspace-resizer",
    container: "#app",
    property: "--workspace-sidebar-width",
    defaultValue: 224,
    min: 192,
    max: 360,
    reserve: 600,
    separator: 7,
  },
  settingsSidebar: {
    handle: "#settings-resizer",
    container: "#settings",
    property: "--settings-sidebar-width",
    defaultValue: 272,
    min: 216,
    max: 360,
    reserve: 520,
    separator: 7,
  },
  contractRail: {
    handle: "#contract-resizer",
    container: ".split",
    property: "--contract-rail-width",
    defaultValue: 300,
    min: 220,
    max: 480,
    reserve: 320,
    separator: 15,
  },
  repositoryRail: {
    handle: "#repository-resizer",
    container: "#repositories-split",
    property: "--repository-rail-width",
    defaultValue: 300,
    min: 220,
    max: 480,
    reserve: 360,
    separator: 15,
  },
};
const CUSTOM_THEME_COLOR_KEYS = [
  "canvas",
  "sidebar",
  "surface",
  "border",
  "text",
  "muted",
  "accent",
];
const CUSTOM_THEME_DEFAULTS = {
  light: {
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
  },
  dark: {
    base: "dark",
    colors: {
      canvas: "#111418",
      sidebar: "#0D1014",
      surface: "#181C22",
      border: "#303842",
      text: "#F4F7FA",
      muted: "#929DA8",
      accent: "#26DBFF",
    },
  },
  oled: {
    base: "oled",
    colors: {
      canvas: "#000000",
      sidebar: "#030507",
      surface: "#06090F",
      border: "#172333",
      text: "#F7FAFC",
      muted: "#8F9CAA",
      accent: "#26DBFF",
    },
  },
};
const CUSTOM_THEME_SEMANTIC_COLORS = {
  light: {
    critical: "#B42335",
    high: "#9C3E11",
    medium: "#735B00",
    low: "#285D86",
    info: "#5B6470",
    ok: "#227044",
  },
  dark: {
    critical: "#FF7185",
    high: "#FFA066",
    medium: "#F0CC57",
    low: "#7DC4F5",
    info: "#AEB6C0",
    ok: "#5FDD94",
  },
  oled: {
    critical: "#FF7185",
    high: "#FFA066",
    medium: "#F0CC57",
    low: "#7DC4F5",
    info: "#AEB6C0",
    ok: "#5FDD94",
  },
};
const HIGH_CONTRAST_CONTROL_FILL = "#007B91";
const PROVIDER_ORDER = ["anthropic", "openai", "google", "xai", "local"];
const PROVIDERS = Object.freeze({
  anthropic: Object.freeze({
    label: "Anthropic",
    credentialLabel: "Anthropic API key",
    description: "Claude models through the Anthropic API.",
    icon: "psychology",
    modelSetting: "anthropicModel",
    models: ["claude-sonnet-5", "claude-opus-5", "claude-fable-5", "claude-haiku-4-5"],
  }),
  openai: Object.freeze({
    label: "OpenAI",
    credentialLabel: "OpenAI API key",
    description: "GPT models through the OpenAI API.",
    icon: "deployed_code",
    modelSetting: "openaiModel",
    models: ["gpt-5.2", "gpt-5", "gpt-4.1"],
  }),
  google: Object.freeze({
    label: "Google Gemini",
    credentialLabel: "Gemini API key",
    description: "Gemini models through Google AI Studio.",
    icon: "auto_awesome",
    modelSetting: "googleModel",
    models: ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"],
  }),
  xai: Object.freeze({
    label: "SpaceXAI · Grok",
    credentialLabel: "SpaceXAI API key",
    description: "Grok models through the SpaceXAI API.",
    icon: "orbit",
    modelSetting: "xaiModel",
    models: ["grok-4.5", "grok-4.3", "grok-4"],
  }),
  local: Object.freeze({
    label: "OpenAI-compatible",
    credentialLabel: "Endpoint API key",
    description: "Ollama, LM Studio, vLLM, or another compatible endpoint.",
    icon: "dns",
    modelSetting: "openaiCompatibleModel",
    models: ["qwen2.5-coder", "llama3.3", "deepseek-r1"],
  }),
  none: Object.freeze({
    label: "None",
    description: "Keep reviews structural and fully local.",
    icon: "shield_lock",
    models: [],
  }),
});
const SUBSCRIPTION_RUNTIME_IDS = Object.freeze([
  "openai-codex",
  "xai-grok-build",
]);
const MODEL_ACCESS_MODES = new Set(["api", "subscription"]);
const SELECTABLE_SUBSCRIPTION_RUNTIMES = new Set(["openai-codex", "xai-grok-build"]);

const state = {
  contracts: [],
  selected: null,
  settings: {},
  registryPath: null,
  view: "contracts",
  graphRequest: 0,
  engine: null,
  settingsOpen: false,
  settingsCategory: "ai",
  settingsReturnFocus: null,
  settingsDraftsReady: false,
  customThemeDirty: false,
  activeProvider: "anthropic",
  providerModels: {
    anthropic: "claude-sonnet-5",
    openai: "gpt-5.2",
    google: "gemini-3.6-flash",
    xai: "grok-4.5",
    local: "qwen2.5-coder",
  },
  credentials: null,
  credentialBusy: false,
  runtimeDiscovery: {
    runtimes: {},
    runtimeError: "",
  },
  runtimeDiscoveryRequest: 0,
  subscriptionAuth: {},
  subscriptionModels: {},
  subscriptionBusy: new Set(),
  subscriptionLogin: {},
  subscriptionPollTimers: {},
  paneLayout: {},
  repositories: [],
  selectedRepository: "all",
  workspaceScan: null,
  workspaceSelection: new Set(),
  workspaceSyncPlan: null,
  workspaceBusy: false,
  workspaceRequest: 0,
  launchStructuralScanAttempted: false,
  legal: {
    accepted: false,
    agreement: null,
    required: false,
    document: "eula",
    request: 0,
    initializationRequest: 0,
    returnFocus: null,
    licenses: {
      loaded: false,
      busy: false,
      detailBusy: false,
      request: 0,
      detailRequest: 0,
      items: [],
      query: "",
      selectedId: null,
      text: "",
      error: "",
      detailError: "",
    },
  },
};

let paneResizeObserver = null;
let activePaneResize = null;

/* ------------------------------------------------------------------ */
/* helpers                                                             */
/* ------------------------------------------------------------------ */

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function readPaneLayout() {
  try {
    const stored = JSON.parse(localStorage.getItem(PANE_LAYOUT_CACHE_KEY) || "{}");
    return Object.fromEntries(
      Object.entries(PANE_LAYOUTS).map(([name, config]) => {
        const value = stored?.[name];
        const normalized =
          typeof value === "number" && Number.isFinite(value)
            ? Math.max(config.min, Math.min(config.max, Math.round(value)))
            : config.defaultValue;
        return [name, normalized];
      })
    );
  } catch {
    return Object.fromEntries(
      Object.entries(PANE_LAYOUTS).map(([name, config]) => [name, config.defaultValue])
    );
  }
}

function savePaneLayout() {
  try {
    localStorage.setItem(PANE_LAYOUT_CACHE_KEY, JSON.stringify(state.paneLayout));
  } catch {
    // Window geometry is a convenience; restricted storage must not block the app.
  }
}

function paneLimits(name) {
  const config = PANE_LAYOUTS[name];
  const container = $(config.container);
  const available = container?.getBoundingClientRect().width || 0;
  const dynamicMax = available ? available - config.reserve - config.separator : config.max;
  return {
    min: config.min,
    max: Math.max(config.min, Math.min(config.max, Math.floor(dynamicMax))),
  };
}

function applyPaneWidth(name, preferred = state.paneLayout[name]) {
  const config = PANE_LAYOUTS[name];
  const container = $(config.container);
  const handle = $(config.handle);
  if (!config || !container || !handle) return config?.defaultValue;
  const { min, max } = paneLimits(name);
  const value = Math.max(min, Math.min(max, Number(preferred) || config.defaultValue));
  container.style.setProperty(config.property, `${value}px`);
  handle.setAttribute("aria-valuemin", String(min));
  handle.setAttribute("aria-valuemax", String(max));
  handle.setAttribute("aria-valuenow", String(value));
  handle.setAttribute("aria-valuetext", `${value} pixels`);
  return value;
}

function applyVisiblePaneWidths() {
  if (window.innerWidth <= 800) return;
  for (const name of Object.keys(PANE_LAYOUTS)) {
    const container = $(PANE_LAYOUTS[name].container);
    if (container && !container.classList.contains("hidden")) applyPaneWidth(name);
  }
}

function setPreferredPaneWidth(name, value, { persist = false, clampToVisible = true } = {}) {
  const config = PANE_LAYOUTS[name];
  const { min, max } = clampToVisible
    ? paneLimits(name)
    : { min: config.min, max: config.max };
  state.paneLayout[name] = Math.max(min, Math.min(max, Math.round(value)));
  applyPaneWidth(name);
  if (persist) savePaneLayout();
}

function stopPaneResize({ persist = true } = {}) {
  if (!activePaneResize) return;
  activePaneResize.handle.classList.remove("active");
  document.documentElement.classList.remove("pane-resizing");
  if (persist) savePaneLayout();
  activePaneResize = null;
}

function initializePaneResizers() {
  state.paneLayout = readPaneLayout();
  for (const [name, config] of Object.entries(PANE_LAYOUTS)) {
    const handle = $(config.handle);
    handle.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || window.innerWidth <= 800) return;
      event.preventDefault();
      const startValue = applyPaneWidth(name);
      activePaneResize = { name, handle, startX: event.clientX, startValue };
      handle.classList.add("active");
      document.documentElement.classList.add("pane-resizing");
      handle.setPointerCapture(event.pointerId);
    });
    handle.addEventListener("pointermove", (event) => {
      if (activePaneResize?.handle !== handle) return;
      setPreferredPaneWidth(name, activePaneResize.startValue + event.clientX - activePaneResize.startX);
    });
    handle.addEventListener("pointerup", () => stopPaneResize());
    handle.addEventListener("pointercancel", () => stopPaneResize());
    handle.addEventListener("lostpointercapture", () => stopPaneResize());
    handle.addEventListener("dblclick", () =>
      setPreferredPaneWidth(name, config.defaultValue, {
        persist: true,
        clampToVisible: false,
      })
    );
    handle.addEventListener("keydown", (event) => {
      const { min, max } = paneLimits(name);
      const current = applyPaneWidth(name);
      let next = null;
      if (event.key === "ArrowLeft") next = current - (event.shiftKey ? 32 : 8);
      if (event.key === "ArrowRight") next = current + (event.shiftKey ? 32 : 8);
      if (event.key === "Home") next = min;
      if (event.key === "End") next = max;
      if (event.key === "Enter") next = config.defaultValue;
      if (next === null) return;
      event.preventDefault();
      setPreferredPaneWidth(name, next, {
        persist: true,
        clampToVisible: event.key !== "Enter",
      });
    });
  }
  paneResizeObserver = new ResizeObserver(() => applyVisiblePaneWidths());
  for (const config of Object.values(PANE_LAYOUTS)) paneResizeObserver.observe($(config.container));
  window.addEventListener("blur", () => stopPaneResize());
  applyVisiblePaneWidths();
}

function plural(n, one, many) {
  return `${n} ${n === 1 ? one : many || `${one}s`}`;
}

function setText(selector, value) {
  const node = $(selector);
  if (node) node.textContent = value || "";
}

function renderProductIdentity(desktop = null) {
  if (!desktop || typeof desktop !== "object" || Array.isArray(desktop)) return;
  const productName = typeof desktop.productName === "string" ? desktop.productName.trim() : "";
  const edition = desktop.edition === "essential" ? "essential" : "";
  const status = $("#status-version");
  if (productName) status.dataset.productName = productName;
  const badge = $("#product-edition");
  badge.hidden = !edition;
  badge.textContent = edition ? edition[0].toUpperCase() + edition.slice(1) : "";
}

function configuredAnalysisDescription(settings = state.settings) {
  const accessMode = normalizedModelAccessMode(settings?.modelAccessMode);
  if (settings?.useLlm !== true) return "Structural review";
  if (accessMode === "subscription") {
    const runtime = normalizedSubscriptionRuntimeSelection(settings?.subscriptionRuntime);
    return `Semantic review · ${runtime === "xai-grok-build" ? "Grok Build" : "OpenAI Codex"}`;
  }
  const provider = settings?.judgeProvider || "none";
  if (provider === "none") return "Structural review";
  const providerLabel = PROVIDERS[provider]?.label || "Configured provider";
  const model = typeof settings?.judgeModel === "string" ? settings.judgeModel.trim() : "";
  return `Semantic review · ${providerLabel}${model ? ` ${model}` : ""}`;
}

/** Unwrap the IPC envelope and route failures to the active surface. */
async function call(promise, { quiet = false, onError = null } = {}) {
  let result;
  try {
    result = await promise;
  } catch (error) {
    result = { ok: false, error: { message: error?.message || String(error) } };
  }

  if (result?.ok) return result.data;

  const error = result?.error || { message: "The desktop bridge did not respond." };
  if (error.notFound) {
    showSetup(error.searched || [], error.message);
    return null;
  }
  if (onError) onError(error);
  if (!quiet) toast(error.message, "error");
  return null;
}

let toastTimer = null;
function toast(message, tone = "error", duration = 6000) {
  const container = $("#toast");
  setText("#toast-message", message);
  container.classList.remove("hidden", "success", "error", "info");
  container.classList.add(tone);
  container.setAttribute("role", tone === "error" ? "alert" : "status");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(hideToast, duration);
}

function hideToast() {
  clearTimeout(toastTimer);
  $("#toast").classList.add("hidden");
}

function emptyState(title, body, { action = null, actionClass = "" } = {}) {
  const wrapper = el("div", "empty-state");
  wrapper.append(el("strong", null, title), el("p", null, body));
  if (action) {
    const button = el("button", `ghost ${actionClass}`.trim(), action);
    button.type = "button";
    wrapper.append(button);
  }
  return wrapper;
}

function repositoryBasename(sourcePath) {
  const normalized = String(sourcePath || "").replace(/[\\/]+$/, "");
  return normalized.split(/[\\/]/).pop() || "repository";
}

function repositorySlug(value) {
  const normalized = String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, "-")
    .replace(/^[^a-z0-9]+|[^a-z0-9]+$/g, "")
    .slice(0, 64);
  return WORKSPACE_REPOSITORY_ID.test(normalized) ? normalized : "repository";
}

function uniqueRepositoryId(sourcePath, entries = state.repositories) {
  const base = repositorySlug(repositoryBasename(sourcePath));
  const used = new Set(entries.map(({ id }) => id));
  if (!used.has(base)) return base;
  for (let index = 2; index <= MAX_WORKSPACE_REPOSITORIES + 1; index += 1) {
    const suffix = `-${index}`;
    const candidate = `${base.slice(0, 64 - suffix.length)}${suffix}`;
    if (!used.has(candidate)) return candidate;
  }
  return `repo-${Date.now().toString(36)}`.slice(0, 64);
}

function normalizedRepositoryEntries(value) {
  if (!Array.isArray(value)) return [];
  const ids = new Set();
  const paths = new Set();
  const entries = [];
  for (const candidate of value.slice(0, MAX_WORKSPACE_REPOSITORIES)) {
    if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) continue;
    const id = typeof candidate.id === "string" ? candidate.id.trim().toLowerCase() : "";
    const sourcePath = typeof candidate.path === "string" ? candidate.path.trim() : "";
    const pathKey = sourcePath.toLowerCase();
    if (
      !WORKSPACE_REPOSITORY_ID.test(id) ||
      !sourcePath ||
      sourcePath.length > 4096 ||
      ids.has(id) ||
      paths.has(pathKey)
    ) {
      continue;
    }
    ids.add(id);
    paths.add(pathKey);
    entries.push({ id, path: sourcePath });
  }
  return entries;
}

function readWorkspaceRepositories() {
  try {
    return normalizedRepositoryEntries(
      JSON.parse(localStorage.getItem(WORKSPACE_REPOSITORIES_CACHE_KEY) || "[]")
    );
  } catch {
    return [];
  }
}

function saveWorkspaceRepositories() {
  try {
    localStorage.setItem(
      WORKSPACE_REPOSITORIES_CACHE_KEY,
      JSON.stringify(normalizedRepositoryEntries(state.repositories))
    );
  } catch {
    toast("Repositories are available for this window, but could not be saved locally.", "error");
  }
}

function workspaceRepositoriesRequest() {
  return state.repositories.map(({ id, path: sourcePath }) => ({ id, path: sourcePath }));
}

function workspaceAgents(scan = state.workspaceScan) {
  return Array.isArray(scan?.agents) ? scan.agents : [];
}

function workspaceConflicts(scan = state.workspaceScan) {
  return Array.isArray(scan?.conflicts) ? scan.conflicts : [];
}

function workspaceChecks(scan = state.workspaceScan) {
  return Array.isArray(scan?.checks) ? scan.checks : [];
}

function qualifiedWorkspaceRef(value) {
  if (typeof value === "string" && value.trim().includes("/")) {
    return value.trim().toLowerCase();
  }
  if (value && typeof value === "object") {
    const repositoryId = value.repository_id || value.repositoryId || value.repository?.id;
    const contractId = value.contract_id || value.contractId || value.contract?.id;
    if (repositoryId && contractId) return `${repositoryId}/${contractId}`.toLowerCase();
  }
  return "";
}

function workspaceConflictRefs(conflict) {
  const direct = conflict?.agent_refs || conflict?.agentRefs || conflict?.agents;
  const refs = Array.isArray(direct) ? direct.map(qualifiedWorkspaceRef).filter(Boolean) : [];
  for (const value of [
    conflict?.changed_contract,
    conflict?.changedContract,
    conflict?.affected_contract,
    conflict?.affectedContract,
  ]) {
    const ref = qualifiedWorkspaceRef(value);
    if (ref) refs.push(ref);
  }
  for (const evidence of Array.isArray(conflict?.evidence) ? conflict.evidence : []) {
    const ref = qualifiedWorkspaceRef(
      typeof evidence === "object"
        ? evidence?.agent_ref || evidence?.agentRef || evidence?.ref || evidence
        : evidence
    );
    if (ref) refs.push(ref);

    const parsed = workspaceEvidenceEntry(evidence);
    if (parsed.repositoryId && parsed.relativePath) {
      for (const documentEntry of Array.isArray(state.workspaceScan?.documents)
        ? state.workspaceScan.documents
        : []) {
        if (
          documentEntry?.repository_id === parsed.repositoryId &&
          documentEntry?.path === parsed.relativePath
        ) {
          for (const documentRef of Array.isArray(documentEntry.agent_refs)
            ? documentEntry.agent_refs
            : []) {
            const normalized = qualifiedWorkspaceRef(documentRef);
            if (normalized) refs.push(normalized);
          }
        }
      }
    }
  }
  for (const match of String(conflict?.message || "").matchAll(/`([^`]+\/[a-z0-9._-]+)`/gi)) {
    const ref = qualifiedWorkspaceRef(match[1]);
    if (ref) refs.push(ref);
  }
  return [...new Set(refs)];
}

function workspaceAgentRef(agent) {
  const direct = qualifiedWorkspaceRef(agent?.ref);
  if (direct) return direct;
  const repositoryId =
    agent?.repository_id || agent?.repositoryId || agent?.repository?.id || "unknown";
  const contractId = agent?.contract?.id || agent?.contract_id || agent?.contractId || "agent";
  return `${repositoryId}/${contractId}`.toLowerCase();
}

function workspaceAgentRepositoryId(agent) {
  const ref = workspaceAgentRef(agent);
  return ref.includes("/") ? ref.slice(0, ref.indexOf("/")) : "unknown";
}

function workspaceAgentContract(agent) {
  return agent?.contract && typeof agent.contract === "object" ? agent.contract : agent || {};
}

function workspaceSource(agent) {
  if (typeof agent?.source === "string") return { path: agent.source };
  return agent?.source && typeof agent.source === "object" ? agent.source : {};
}

function workspaceSourcePath(agent) {
  const source = workspaceSource(agent);
  const raw = source.path || source.absolute_path || source.absolutePath || source.relative_path || source.relativePath || "";
  return workspaceEvidenceEntry(raw).displayPath || raw;
}

function workspaceAbsolutePath(repositoryId, relativePath) {
  const repository = workspaceRepositoryFor(repositoryId);
  if (!repository || !relativePath) return "";
  const separator = repository.path.includes("\\") ? "\\" : "/";
  return `${repository.path.replace(/[\\/]+$/, "")}${separator}${String(relativePath).replace(/^[\\/]+/, "")}`;
}

function workspaceEvidenceEntry(entry) {
  if (entry && typeof entry === "object") {
    const repositoryId = String(entry.repository_id || entry.repositoryId || entry.repo || "").toLowerCase();
    const relativePath = entry.path || entry.source || entry.file || entry.relative_path || entry.relativePath || "";
    const absolutePath = entry.absolute_path || entry.absolutePath || workspaceAbsolutePath(repositoryId, relativePath);
    return {
      repositoryId,
      relativePath,
      absolutePath,
      displayPath: relativePath || absolutePath || "",
      line: entry.start_line ?? entry.startLine ?? entry.line ?? null,
      endLine: entry.end_line ?? entry.endLine ?? null,
      excerpt: entry.excerpt || entry.text || entry.message || entry.detail || "",
    };
  }

  const text = String(entry || "").trim();
  const sourceMatch = text.match(/^([a-z0-9][a-z0-9._-]{0,63}):(.+)$/i);
  if (sourceMatch && workspaceRepositoryFor(sourceMatch[1].toLowerCase())) {
    const repositoryId = sourceMatch[1].toLowerCase();
    const locationMatch = sourceMatch[2].match(/^(.*):(\d+)$/);
    const relativePath = locationMatch ? locationMatch[1] : sourceMatch[2];
    return {
      repositoryId,
      relativePath,
      absolutePath: workspaceAbsolutePath(repositoryId, relativePath),
      displayPath: `${repositoryId}:${relativePath}`,
      line: locationMatch ? Number(locationMatch[2]) : null,
      endLine: null,
      excerpt: "",
    };
  }
  return {
    repositoryId: "",
    relativePath: "",
    absolutePath: "",
    displayPath: "",
    line: null,
    endLine: null,
    excerpt: text,
  };
}

function workspaceRepositoryFor(id) {
  return state.repositories.find((repository) => repository.id === id) || null;
}

function workspaceNetworkLabel(report) {
  const network = report?.network;
  if (network && typeof network === "object") return network.used ? "network used" : "offline";
  return network === "used" ? "network used" : "offline";
}

function uniqueWorkspaceRecords(...collections) {
  const records = [];
  const seen = new Set();
  for (const collection of collections) {
    for (const record of Array.isArray(collection) ? collection : []) {
      const key = JSON.stringify(record);
      if (seen.has(key)) continue;
      seen.add(key);
      records.push(record);
    }
  }
  return records;
}

function clearWorkspaceResult(message = "Repositories changed. Run Scan workspace to refresh results.") {
  state.workspaceScan = null;
  state.workspaceSelection = new Set();
  state.workspaceSyncPlan = null;
  hideWorkspaceSyncReview();
  setText("#workspace-scan-status", message);
  renderWorkspace();
}

function surfaceError(title, message, retry) {
  const wrapper = el("div", "surface-error");
  wrapper.append(el("strong", null, title), el("p", null, message));
  if (retry) {
    const button = el("button", "ghost compact-action", "Try again");
    button.type = "button";
    button.addEventListener("click", retry);
    wrapper.append(button);
  }
  return wrapper;
}

function setButtonBusy(button, busy, busyLabel = "Working…") {
  if (!button) return;
  const label = button.querySelector(
    ":scope > .button-label, :scope > span:not(.material-symbol):not(.spinner)"
  );
  const labelTarget = label || button;
  if (!labelTarget.dataset.label) labelTarget.dataset.label = labelTarget.textContent.trim();
  button.disabled = busy;
  labelTarget.textContent = busy ? busyLabel : labelTarget.dataset.label;
  button.setAttribute("aria-busy", String(busy));
}

/* ------------------------------------------------------------------ */
/* legal, setup + boot                                                 */
/* ------------------------------------------------------------------ */

async function bridgeEnvelope(promise, { timeoutMs = 0, timeoutMessage = "" } = {}) {
  let timeout = null;
  try {
    const result = timeoutMs > 0
      ? await Promise.race([
          Promise.resolve(promise),
          new Promise((resolve) => {
            timeout = setTimeout(() => resolve({
              ok: false,
              error: {
                code: "BRIDGE_TIMEOUT",
                message: timeoutMessage || "The desktop bridge took too long to respond.",
              },
            }), timeoutMs);
          }),
        ])
      : await promise;
    if (result?.ok) return result;
    return {
      ok: false,
      error: result?.error || { message: "The desktop bridge did not respond." },
    };
  } catch (error) {
    return {
      ok: false,
      error: { message: error?.message || String(error) },
    };
  } finally {
    if (timeout !== null) clearTimeout(timeout);
  }
}

function legalPayload(data, name) {
  if (typeof data === "string") return data;
  if (typeof data?.text === "string") return data.text;
  if (typeof data?.content === "string") return data.content;
  return `${LEGAL_DOCUMENTS[name]} is unavailable.`;
}

function safeLicenseField(value, maxLength = 512) {
  if (typeof value !== "string") return "";
  const text = value.trim().slice(0, maxLength);
  return /[\u0000-\u001f\u007f]/.test(text) ? "" : text;
}

function normalizedOpenSourceLicense(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const id = safeLicenseField(value.id, 256);
  const name = safeLicenseField(value.name, 256);
  if (!id || !name) return null;
  return {
    id,
    name,
    version: safeLicenseField(value.version, 160),
    license: safeLicenseField(value.license, 256),
    source: safeLicenseField(value.source),
    document: safeLicenseField(value.document, 512),
  };
}

function openSourceLicenseText(data) {
  if (typeof data === "string") return data;
  if (typeof data?.text === "string") return data.text;
  return "";
}

function openSourceMatches(item, query) {
  if (!query) return true;
  return [item.name, item.version, item.license, item.source, item.document]
    .join(" ")
    .toLocaleLowerCase()
    .includes(query);
}

function selectedOpenSourceLicense() {
  return state.legal.licenses.items.find(
    (item) => item.id === state.legal.licenses.selectedId
  ) || null;
}

function renderOpenSourceLicenses() {
  const licenses = state.legal.licenses;
  const focusedLicenseId = document.activeElement?.dataset?.licenseId || null;
  const listMethodAvailable = typeof window.ionic?.listOpenSourceLicenses === "function";
  const readMethodAvailable = typeof window.ionic?.readOpenSourceLicense === "function";
  const refresh = $("#open-source-refresh");
  refresh.disabled = licenses.busy || !listMethodAvailable;
  refresh.title = listMethodAvailable
    ? "Reload the license inventory packaged with this build"
    : "The packaged license inventory is unavailable in this desktop build.";

  const filter = $("#open-source-filter");
  filter.disabled = !licenses.loaded;
  if (filter.value !== licenses.query) filter.value = licenses.query;
  const query = licenses.query.trim().toLocaleLowerCase();
  const visible = licenses.items.filter((item) => openSourceMatches(item, query));
  const status = $("#open-source-list-status");
  if (licenses.busy) {
    status.textContent = "Loading packaged licenses…";
  } else if (licenses.error) {
    status.textContent = "Packaged license inventory unavailable.";
  } else if (!licenses.loaded) {
    status.textContent = "Packaged license inventory has not been loaded.";
  } else if (query) {
    status.textContent = `${visible.length} of ${licenses.items.length} components match`;
  } else {
    status.textContent = `${licenses.items.length} packaged ${licenses.items.length === 1 ? "component" : "components"}`;
  }

  const list = $("#open-source-list");
  list.replaceChildren();
  const rovingId = visible.some((item) => item.id === licenses.selectedId)
    ? licenses.selectedId
    : visible[0]?.id;
  visible.forEach((item) => {
    const entry = el("div", "open-source-list-entry");
    entry.setAttribute("role", "listitem");
    const button = el("button", "open-source-list-item");
    button.type = "button";
    button.disabled = !readMethodAvailable;
    button.dataset.licenseId = item.id;
    button.tabIndex = item.id === rovingId ? 0 : -1;
    if (item.id === licenses.selectedId) button.setAttribute("aria-current", "true");
    const name = el("strong", null, item.name);
    const detail = el(
      "span",
      null,
      `${item.version || "Version not reported"} · ${item.license || "License not reported"}`
    );
    button.append(name, detail);
    if (item.document) button.append(el("span", "open-source-list-document", item.document));
    button.addEventListener("click", () => void loadOpenSourceLicense(item.id));
    button.addEventListener("keydown", handleOpenSourceListKeydown);
    entry.append(button);
    list.append(entry);
  });
  if (focusedLicenseId) {
    list.querySelector(`[data-license-id="${CSS.escape(focusedLicenseId)}"]`)?.focus({ preventScroll: true });
  }

  const item = selectedOpenSourceLicense();
  const empty = $("#open-source-detail-empty");
  const content = $("#open-source-detail-content");
  const detail = $("#open-source-detail");
  const emptyTitle = $("#open-source-detail-title");
  const emptyCopy = empty.querySelector(":scope > span:last-child");
  if (!item) {
    empty.classList.remove("hidden");
    content.classList.add("hidden");
    detail.removeAttribute("aria-busy");
    detail.setAttribute("aria-label", "Packaged license text");
    if (licenses.loaded && !visible.length) {
      emptyTitle.textContent = query ? "No matching components" : "No packaged licenses reported";
      emptyCopy.textContent = query
        ? "Try a different component, version, license, or source."
        : "This build did not report any packaged open source components.";
    } else {
      emptyTitle.textContent = "Choose a component";
      emptyCopy.textContent = "Select a packaged component to read its exact included license text.";
    }
  } else {
    empty.classList.add("hidden");
    content.classList.remove("hidden");
    detail.toggleAttribute("aria-busy", licenses.detailBusy);
    detail.setAttribute("aria-label", `${item.name} packaged license text`);
    $("#open-source-license-name").textContent = item.name;
    $("#open-source-license-version").textContent = item.version || "Version not reported";
    $("#open-source-license-type").textContent = item.license || "License not reported";
    $("#open-source-license-source").textContent = item.source || "Source not reported";
    $("#open-source-license-document-row").classList.toggle("hidden", !item.document);
    $("#open-source-license-document").textContent = item.document || "Document not reported";
    $("#open-source-license-text").textContent = licenses.detailBusy
      ? "Loading exact packaged license text…"
      : licenses.detailError
        ? "The exact packaged license text could not be loaded. Select the component to retry."
        : licenses.text;
  }

  const error = $("#open-source-error");
  const errorMessage = licenses.error || licenses.detailError;
  error.textContent = errorMessage;
  error.classList.toggle("hidden", !errorMessage);
}

function handleOpenSourceListKeydown(event) {
  if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
  const items = $$("#open-source-list .open-source-list-item:not(:disabled)");
  if (!items.length) return;
  event.preventDefault();
  const current = Math.max(0, items.indexOf(event.currentTarget));
  const nextIndex = event.key === "Home"
    ? 0
    : event.key === "End"
      ? items.length - 1
      : event.key === "ArrowDown"
        ? Math.min(current + 1, items.length - 1)
        : Math.max(current - 1, 0);
  items.forEach((item, index) => { item.tabIndex = index === nextIndex ? 0 : -1; });
  items[nextIndex].focus({ preventScroll: false });
}

async function loadOpenSourceLicenses({ force = false } = {}) {
  const licenses = state.legal.licenses;
  if (licenses.loaded && !force) {
    renderOpenSourceLicenses();
    return true;
  }
  if (typeof window.ionic?.listOpenSourceLicenses !== "function") {
    licenses.error = "The packaged license inventory is unavailable in this desktop build.";
    renderOpenSourceLicenses();
    return false;
  }
  const request = ++licenses.request;
  licenses.busy = true;
  licenses.error = "";
  renderOpenSourceLicenses();
  const result = await bridgeEnvelope(window.ionic.listOpenSourceLicenses());
  if (request !== licenses.request) return false;
  licenses.busy = false;
  if (!result.ok || !Array.isArray(result.data?.licenses)) {
    licenses.error = result.ok
      ? "The packaged license inventory response was invalid."
      : result.error.message;
    renderOpenSourceLicenses();
    return false;
  }
  const seen = new Set();
  licenses.items = result.data.licenses
    .map(normalizedOpenSourceLicense)
    .filter((item) => item && !seen.has(item.id) && seen.add(item.id))
    .sort((left, right) => left.name.localeCompare(right.name) || left.version.localeCompare(right.version));
  licenses.loaded = true;
  if (!licenses.items.some((item) => item.id === licenses.selectedId)) {
    licenses.selectedId = null;
    licenses.text = "";
    licenses.detailError = "";
  }
  renderOpenSourceLicenses();
  return true;
}

async function loadOpenSourceLicense(id) {
  const licenses = state.legal.licenses;
  const item = licenses.items.find((candidate) => candidate.id === id);
  if (!item || typeof window.ionic?.readOpenSourceLicense !== "function") return;
  const request = ++licenses.detailRequest;
  licenses.selectedId = item.id;
  licenses.detailBusy = true;
  licenses.detailError = "";
  licenses.text = "";
  renderOpenSourceLicenses();
  const result = await bridgeEnvelope(window.ionic.readOpenSourceLicense(item.id));
  if (request !== licenses.detailRequest || licenses.selectedId !== item.id) return;
  licenses.detailBusy = false;
  const text = result.ok ? openSourceLicenseText(result.data) : "";
  if (!result.ok || !text) {
    licenses.detailError = result.ok
      ? "The packaged license text response was empty or invalid."
      : result.error.message;
  } else {
    licenses.text = text;
    $("#open-source-live").textContent = `${item.name} license text loaded.`;
  }
  renderOpenSourceLicenses();
}

function setLegalMode(required) {
  state.legal.required = required;
  $("#legal-close").classList.toggle("hidden", required);
  $("#legal-consent").classList.toggle("hidden", !required);
  $("#legal-recovery").classList.add("hidden");
  $("#legal-kicker").textContent = required ? "First launch" : "Legal";
  $("#legal-title").textContent = required ? "Before you continue" : "Legal documents";
  $("#legal-summary").textContent = required
    ? "Review the terms for the official Ionic Desktop distribution and related services published by Tactico Technologies (Publishers)."
    : "Review the terms, open-source license, and notices included with Ionic Desktop.";
}

function showLegalSurface(required) {
  setLegalMode(required);
  $("#legal").classList.remove("hidden");
  $("#boot").classList.add("hidden");
  $("#setup").classList.add("hidden");
  $("#app").classList.add("hidden");
  $("#settings").classList.add("hidden");
  $("#statusbar").classList.add("hidden");
}

async function loadLegalDocument(name, { focusDocument = false } = {}) {
  if (!LEGAL_DOCUMENTS[name]) return;

  const request = ++state.legal.request;
  state.legal.document = name;
  $$(".legal-tab").forEach((button) => {
    const active = button.dataset.legalDocument === name;
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });

  const surface = $("#legal-document");
  const licenseSurface = $("#open-source-licenses");
  const text = $("#legal-text");
  const error = $("#legal-error");
  const isLicenseBrowser = name === "third-party";
  surface.classList.toggle("hidden", isLicenseBrowser);
  licenseSurface.classList.toggle("hidden", !isLicenseBrowser);
  error.classList.add("hidden");
  error.textContent = "";
  if (isLicenseBrowser) {
    await loadOpenSourceLicenses();
    if (request !== state.legal.request) return;
    if (focusDocument) $("#open-source-filter").focus({ preventScroll: true });
    return;
  }
  surface.setAttribute("aria-busy", "true");
  surface.setAttribute("aria-label", `${LEGAL_DOCUMENTS[name]} text`);
  text.textContent = `Loading ${LEGAL_DOCUMENTS[name].toLowerCase()}…`;

  const result = await bridgeEnvelope(window.ionic.readLegal(name));
  if (request !== state.legal.request) return;
  surface.removeAttribute("aria-busy");
  if (!result.ok) {
    text.textContent = `${LEGAL_DOCUMENTS[name]} could not be loaded.`;
    error.textContent = `${result.error.message} Select the document again to retry.`;
    error.classList.remove("hidden");
    return;
  }

  text.textContent = legalPayload(result.data, name);
  surface.scrollTop = 0;
  if (focusDocument) surface.focus({ preventScroll: true });
}

async function openLegalDocument(name, { required = state.legal.required } = {}) {
  if (!required && !$("#legal").classList.contains("hidden")) {
    state.legal.returnFocus ||= document.activeElement;
  } else if (!required) {
    state.legal.returnFocus = document.activeElement;
  }

  showLegalSurface(required);
  (required ? $("#legal-title") : $("#legal-close")).focus({ preventScroll: true });
  await loadLegalDocument(name);
}

function closeLegalDocuments() {
  if (state.legal.required || !state.legal.accepted) return;
  $("#legal").classList.add("hidden");
  if (state.settingsOpen) showSettingsSurface();
  else showApp();
  const returnFocus = state.legal.returnFocus;
  state.legal.returnFocus = null;
  if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true });
}

function showLegalStatusError(error) {
  state.legal.required = true;
  showLegalSurface(true);
  $("#legal-title").textContent = "Could not verify desktop terms";
  $("#legal-summary").textContent = error.message || "The legal settings could not be read.";
  $("#legal-text").textContent =
    "Ionic Desktop stays closed until it can confirm whether the current agreement has been accepted.";
  $("#legal-document").classList.remove("hidden");
  $("#open-source-licenses").classList.add("hidden");
  $("#legal-document").removeAttribute("aria-busy");
  $("#legal-consent").classList.add("hidden");
  $("#legal-recovery").classList.remove("hidden");
  $$(".legal-tab").forEach((button) => { button.disabled = true; });
  $("#legal-retry").focus({ preventScroll: true });
}

async function initializeLegal() {
  showBoot("Checking desktop terms…");
  const request = ++state.legal.initializationRequest;
  let statusRequest;
  try {
    if (typeof window.ionic?.legalStatus !== "function") {
      throw new Error(
        "Ionic's secure desktop bridge did not start. Restart Ionic Desktop; if this continues, reinstall the current build."
      );
    }
    statusRequest = window.ionic.legalStatus();
  } catch (error) {
    if (request === state.legal.initializationRequest) {
      showLegalStatusError(error);
    }
    return;
  }

  const result = await bridgeEnvelope(statusRequest, {
    timeoutMs: LEGAL_STATUS_TIMEOUT_MS,
    timeoutMessage:
      "The desktop terms check took too long. Try again; if this continues, restart Ionic Desktop.",
  });
  if (request !== state.legal.initializationRequest) return;
  if (!result.ok) {
    showLegalStatusError(result.error);
    return;
  }

  $$(".legal-tab").forEach((button) => { button.disabled = false; });
  state.legal.accepted = Boolean(result.data?.accepted);
  state.legal.agreement = result.data
    ? {
        agreementId: result.data.agreementId,
        termsVersion: result.data.termsVersion,
        sha256: result.data.sha256,
        edition: result.data.edition,
      }
    : null;
  if (!state.legal.accepted) {
    await openLegalDocument("eula", { required: true });
    return;
  }
  await boot();
}

async function acceptLegal() {
  const button = $("#legal-accept");
  if (!$("#legal-agree").checked || button.disabled) return;
  setButtonBusy(button, true, "Accepting…");
  $("#legal-error").classList.add("hidden");

  const result = await bridgeEnvelope(window.ionic.acceptLegal(state.legal.agreement));
  if (!result.ok) {
    setButtonBusy(button, false);
    $("#legal-error").textContent = `${result.error.message} Try accepting again.`;
    $("#legal-error").classList.remove("hidden");
    button.focus();
    return;
  }

  state.legal.accepted = true;
  state.legal.required = false;
  await boot();
}

async function declineLegal() {
  const button = $("#legal-decline");
  setButtonBusy(button, true, "Closing…");
  const result = await bridgeEnvelope(window.ionic.declineLegal());
  if (!result.ok) {
    setButtonBusy(button, false);
    $("#legal-error").textContent = `${result.error.message} Try declining again or close the window.`;
    $("#legal-error").classList.remove("hidden");
  }
}

function handleLegalKeydown(event) {
  const legal = $("#legal");
  if (legal.classList.contains("hidden")) return;
  if (event.key === "Escape") {
    if (!state.legal.required) {
      event.preventDefault();
      closeLegalDocuments();
    }
    return;
  }
  if (event.key !== "Tab") return;

  const focusable = $$(
    "#legal button:not(:disabled):not(.hidden), #legal input:not(:disabled), #legal [tabindex='0']"
  )
    .filter((node) => !node.closest(".hidden"));
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function showBoot(message = "Connecting to the local engine…") {
  $("#legal").classList.add("hidden");
  $("#boot").classList.remove("hidden");
  const label = $("#boot > div:last-child > span");
  if (label) label.textContent = message;
  $("#setup").classList.add("hidden");
  $("#app").classList.add("hidden");
  $("#settings").classList.add("hidden");
  state.settingsOpen = false;
  $("#statusbar").classList.add("hidden");
}

function showSetup(searched, message = "") {
  $("#legal").classList.add("hidden");
  $("#boot").classList.add("hidden");
  $("#setup").classList.remove("hidden");
  $("#app").classList.add("hidden");
  $("#settings").classList.add("hidden");
  state.settingsOpen = false;
  $("#statusbar").classList.add("hidden");
  $("#setup-message").textContent =
    "Ionic Desktop could not verify its included managed engine. Restore that connection, choose another Ionic executable, or retry the check.";
  $("#setup-searched").textContent = (searched || []).join("\n") || "No locations were reported.";
  $("#setup-managed").focus();
}

function showApp() {
  $("#legal").classList.add("hidden");
  $("#boot").classList.add("hidden");
  $("#setup").classList.add("hidden");
  $("#app").classList.remove("hidden");
  $("#settings").classList.add("hidden");
  state.settingsOpen = false;
  $("#statusbar").classList.remove("hidden");
  requestAnimationFrame(applyVisiblePaneWidths);
}

async function chooseCli() {
  const picked = await call(window.ionic.pickCli());
  if (!picked) return;
  const saved = await call(window.ionic.saveSettings({ ionicBin: picked }));
  if (!saved) return;
  state.settings = saved;
  showBoot("Verifying the selected engine…");
  await boot();
}

async function useManagedCli() {
  const button = $("#setup-managed");
  setButtonBusy(button, true, "Restoring…");
  const result = await bridgeEnvelope(window.ionic.useManagedCli());
  if (!result.ok) {
    setButtonBusy(button, false);
    showSetup(result.error.searched || [], result.error.message);
    toast(result.error.message, "error");
    return;
  }
  await boot();
}

/* ------------------------------------------------------------------ */
/* navigation                                                          */
/* ------------------------------------------------------------------ */

function showView(name, { focus = true } = {}) {
  state.view = name;
  $$(".nav-item").forEach((button) => {
    const active = button.dataset.view === name;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });

  $$(".view").forEach((view) => {
    const active = view.id === `view-${name}`;
    view.classList.toggle("active", active);
    view.hidden = !active;
  });

  const view = $(`#view-${name}`);
  if (focus) view?.focus({ preventScroll: true });
  if (name === "contracts" || name === "repositories") {
    requestAnimationFrame(applyVisiblePaneWidths);
  }
  if (name === "graph") renderGraph();
}

/* ------------------------------------------------------------------ */
/* repositories                                                       */
/* ------------------------------------------------------------------ */

function repositoryAgentCounts() {
  const counts = new Map(state.repositories.map(({ id }) => [id, 0]));
  for (const agent of workspaceAgents()) {
    const repositoryId = workspaceAgentRepositoryId(agent);
    counts.set(repositoryId, (counts.get(repositoryId) || 0) + 1);
  }
  return counts;
}

function renderRepositoryList() {
  const list = $("#repository-list");
  list.replaceChildren();
  const query = $("#repository-filter").value.trim().toLowerCase();
  const counts = repositoryAgentCounts();
  const matching = state.repositories.filter((repository) =>
    `${repository.id} ${repository.path}`.toLowerCase().includes(query)
  );

  if (!state.repositories.length) {
    const item = el("li");
    item.append(emptyState("No repositories", "Add one or more local repository folders."));
    list.append(item);
    return;
  }

  const entries = query
    ? matching
    : [{ id: "all", path: "Every repository in this workspace" }, ...matching];
  if (!entries.length) {
    const item = el("li");
    item.append(emptyState("No matches", "Try a repository id or path."));
    list.append(item);
    return;
  }

  for (const repository of entries) {
    const item = el("li");
    const button = el("button", "repository-row");
    const isAll = repository.id === "all";
    button.type = "button";
    button.dataset.repositoryId = repository.id;
    button.setAttribute("aria-current", String(repository.id === state.selectedRepository));
    button.append(
      el("span", "repository-row-name", isAll ? "All repositories" : repository.id),
      el(
        "span",
        "repository-row-count",
        isAll ? plural(workspaceAgents().length, "agent") : plural(counts.get(repository.id) || 0, "agent")
      ),
      el("span", "repository-row-path", repository.path)
    );
    button.title = repository.path;
    button.addEventListener("click", () => {
      state.selectedRepository = repository.id;
      renderRepositoryList();
      renderWorkspaceResults();
      $("#workspace-results-title").focus({ preventScroll: true });
    });
    item.append(button);
    list.append(item);
  }
}

function repositoryEditor(repository) {
  const wrapper = el("div", "repository-editor");
  const copy = el("div", "repository-editor-copy");
  const label = el("label", null, "Stable repository id");
  const row = el("div", "repository-id-row");
  const input = el("input");
  input.type = "text";
  input.value = repository.id;
  input.maxLength = 64;
  input.autocomplete = "off";
  input.spellcheck = false;
  input.setAttribute("aria-label", `Stable id for ${repository.path}`);
  const save = el("button", "ghost compact-action", "Save id");
  save.type = "button";
  save.addEventListener("click", () => {
    const requested = input.value.trim().toLowerCase();
    const duplicate = state.repositories.some(
      (entry) => entry !== repository && entry.id === requested
    );
    if (!WORKSPACE_REPOSITORY_ID.test(requested) || duplicate) {
      input.setAttribute("aria-invalid", "true");
      toast(
        duplicate
          ? `Repository id ${requested} is already used.`
          : "Use 1–64 lowercase letters, numbers, dots, underscores, or dashes.",
        "error"
      );
      input.focus();
      return;
    }
    input.removeAttribute("aria-invalid");
    const previous = repository.id;
    repository.id = requested;
    if (state.selectedRepository === previous) state.selectedRepository = requested;
    saveWorkspaceRepositories();
    clearWorkspaceResult("Repository id saved. Scan workspace to refresh its identity.");
  });
  row.append(input, save);
  copy.append(label, row, el("p", "repository-path", repository.path));

  const actions = el("div", "repository-editor-actions");
  const reveal = el("button", "quiet-action", "Show folder");
  reveal.type = "button";
  reveal.addEventListener("click", () => void call(window.ionic.reveal(repository.path)));
  const remove = el("button", "quiet-action danger-action", "Remove");
  remove.type = "button";
  remove.setAttribute("aria-label", `Remove repository ${repository.id} from this workspace`);
  remove.addEventListener("click", () => {
    state.repositories = state.repositories.filter((entry) => entry !== repository);
    state.selectedRepository = "all";
    saveWorkspaceRepositories();
    clearWorkspaceResult("Repository removed. Scan workspace to refresh results.");
    $("#repository-add").focus({ preventScroll: true });
  });
  actions.append(reveal, remove);
  wrapper.append(copy, actions);
  return wrapper;
}

function workspaceConflictEvidence(conflict) {
  const evidence = Array.isArray(conflict?.evidence) ? conflict.evidence : [];
  if (!evidence.length) return null;
  const details = el("details", "workspace-evidence");
  details.append(el("summary", null, `Evidence (${evidence.length})`));
  const list = el("ul", "workspace-evidence-list");
  for (const entry of evidence) {
    const parsed = workspaceEvidenceEntry(entry);
    const item = el("li", "workspace-evidence-item");
    const source = el("div", "workspace-evidence-source");
    if (parsed.absolutePath) {
      const button = el("button", "workspace-source-button", parsed.displayPath);
      button.type = "button";
      button.title = `Show ${parsed.absolutePath}`;
      button.addEventListener("click", () => void call(window.ionic.reveal(parsed.absolutePath)));
      source.append(button);
    } else if (parsed.displayPath) {
      source.append(el("span", "workspace-source-button", parsed.displayPath));
    } else {
      source.append(el("span", "workspace-source-button", "Evidence"));
    }
    const location = parsed.line
      ? `line ${parsed.line}${parsed.endLine && parsed.endLine !== parsed.line ? `–${parsed.endLine}` : ""}`
      : "";
    if (location) source.append(el("span", "workspace-source-location", location));
    item.append(source);
    if (parsed.excerpt) item.append(el("pre", null, parsed.excerpt));
    list.append(item);
  }
  details.append(list);
  return details;
}

function workspaceConflictRow(conflict) {
  const item = el("li", "workspace-conflict");
  const severity = String(conflict?.severity || (conflict?.blocking ? "high" : "info")).toLowerCase();
  const head = el("div", "workspace-conflict-head");
  head.append(
    el("span", `sev ${SEVERITIES.includes(severity) ? severity : "info"}`, severity),
    el("span", "workspace-conflict-title", conflict?.message || conflict?.summary || "Instruction conflict"),
    el("span", "workspace-conflict-kind", conflict?.kind || "conflict")
  );
  item.append(head);
  const refs = workspaceConflictRefs(conflict);
  if (refs.length) {
    item.append(el("div", "workspace-conflict-agents", refs.join(" · ")));
  }
  if (conflict?.detail) item.append(el("p", null, conflict.detail));
  const evidence = workspaceConflictEvidence(conflict);
  if (evidence) item.append(evidence);
  if (conflict?.recommendation) {
    const recommendation = el("div", "workspace-recommendation");
    recommendation.append(
      el("strong", null, "Recommendation"),
      document.createTextNode(conflict.recommendation)
    );
    item.append(recommendation);
  }
  return item;
}

function visibleWorkspaceAgents() {
  const agents = workspaceAgents();
  if (state.selectedRepository === "all") return agents;
  return agents.filter((agent) => workspaceAgentRepositoryId(agent) === state.selectedRepository);
}

function visibleWorkspaceConflicts() {
  const conflicts = workspaceConflicts();
  if (state.selectedRepository === "all") return conflicts;
  return conflicts.filter((conflict) => {
    const refs = workspaceConflictRefs(conflict);
    const evidence = Array.isArray(conflict?.evidence) ? conflict.evidence : [];
    return refs.some((ref) => String(ref).startsWith(`${state.selectedRepository}/`)) ||
      evidence.some((entry) =>
        [entry?.repository_id, entry?.repositoryId, entry?.repo].includes(state.selectedRepository)
      );
  });
}

function visibleWorkspaceChecks() {
  const checks = workspaceChecks();
  if (state.selectedRepository === "all") return checks;
  return checks.filter((check) => {
    const contractRef = qualifiedWorkspaceRef(check?.contract_id || check?.contractId);
    if (contractRef.startsWith(`${state.selectedRepository}/`)) return true;
    return (Array.isArray(check?.findings) ? check.findings : []).some((finding) =>
      workspaceConflictRefs(finding).some((ref) => ref.startsWith(`${state.selectedRepository}/`))
    );
  });
}

function workspaceCheckBlockedRefs(scan = state.workspaceScan) {
  const refs = new Set();
  for (const check of workspaceChecks(scan)) {
    if (String(check?.verdict || "").toUpperCase() !== "REQUEST_CHANGES") continue;
    const changed = qualifiedWorkspaceRef(check?.contract_id || check?.contractId);
    if (changed) refs.add(changed);
  }
  return refs;
}

function renderWorkspaceAgents(container, agents) {
  const sectionNode = el("fieldset", "workspace-section workspace-agent-selection");
  sectionNode.append(el("legend", null, "Agents to sync"));
  const selectionMeta = el(
    "p",
    "workspace-selection-meta",
    `${plural(agents.length, "agent")} discovered · ${plural(state.workspaceSelection.size, "selected")}`
  );
  selectionMeta.id = "workspace-agent-selection-meta";
  sectionNode.append(selectionMeta);

  if (!agents.length) {
    sectionNode.append(emptyState("No agents found", "No supported instruction files were discovered in this scope."));
    container.append(sectionNode);
    return;
  }

  const groups = new Map();
  for (const agent of agents) {
    const repositoryId = workspaceAgentRepositoryId(agent);
    if (!groups.has(repositoryId)) groups.set(repositoryId, []);
    groups.get(repositoryId).push(agent);
  }
  const wrapper = el("div", "workspace-agent-groups");
  for (const [repositoryId, groupedAgents] of groups) {
    const group = el("section", "workspace-agent-group");
    group.append(el("h4", "workspace-agent-group-title", repositoryId));
    const list = el("ul", "workspace-agent-list");
    for (const agent of groupedAgents) {
      const contract = workspaceAgentContract(agent);
      const ref = workspaceAgentRef(agent);
      const sourcePath = workspaceSourcePath(agent);
      const item = el("li", "workspace-agent-row");
      const checkbox = el("input");
      checkbox.type = "checkbox";
      checkbox.checked = state.workspaceSelection.has(ref);
      const blockedByConflict = workspaceConflicts().some(
        (conflict) => conflict?.blocking && workspaceConflictRefs(conflict).includes(ref)
      );
      const unknownBlockingConflict = workspaceConflicts().some(
        (conflict) => conflict?.blocking && workspaceConflictRefs(conflict).length === 0
      );
      const blockedByCheck = workspaceCheckBlockedRefs().has(ref);
      const blockedByScanError = Array.isArray(state.workspaceScan?.errors) && state.workspaceScan.errors.length > 0;
      checkbox.disabled = Boolean(
        agent?.blocking ||
        agent?.status === "blocked" ||
        agent?.status === "invalid" ||
        blockedByConflict ||
        blockedByCheck ||
        blockedByScanError ||
        unknownBlockingConflict
      );
      checkbox.dataset.agentRef = ref;
      checkbox.setAttribute("aria-label", `Select ${ref} for registry sync`);
      if (blockedByCheck) {
        checkbox.title = "Resolve this agent's blocking compatibility findings before syncing.";
      } else if (blockedByScanError) {
        checkbox.title = "Resolve workspace scan errors before syncing agents.";
      } else if (blockedByConflict || unknownBlockingConflict) {
        checkbox.title = "Resolve blocking instruction conflicts before syncing this agent.";
      }
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) state.workspaceSelection.add(ref);
        else state.workspaceSelection.delete(ref);
        hideWorkspaceSyncReview();
        updateWorkspaceSyncButton();
        const meta = $("#workspace-agent-selection-meta");
        if (meta) {
          meta.textContent = `${plural(agents.length, "agent")} discovered · ${plural(state.workspaceSelection.size, "selected")}`;
        }
      });
      const copy = el("div", "workspace-agent-copy");
      copy.append(
        el("span", "workspace-agent-name", contract.name || contract.id || ref),
        el("span", "workspace-agent-identity", `${ref}${contract.version ? ` · v${contract.version}` : ""}`),
        el("span", "workspace-agent-source", sourcePath || "Source path unavailable")
      );
      const status = String(agent?.status || "ready").toLowerCase();
      const displayStatus = blockedByCheck
        ? "compatibility blocked"
        : blockedByScanError
          ? "scan error blocked"
        : blockedByConflict || unknownBlockingConflict
          ? "conflict blocked"
          : status.replaceAll("_", " ");
      const statusClass = blockedByCheck || blockedByScanError || blockedByConflict || unknownBlockingConflict ? "blocked" : status;
      const statusNode = el("span", `workspace-agent-status ${statusClass}`, displayStatus);
      statusNode.id = `workspace-agent-status-${ref.replace(/[^a-z0-9_-]+/g, "-")}`;
      checkbox.setAttribute("aria-describedby", statusNode.id);
      item.append(checkbox, copy, statusNode);
      list.append(item);
    }
    group.append(list);
    wrapper.append(group);
  }
  sectionNode.append(wrapper);
  container.append(sectionNode);
}

function renderWorkspaceChecks(container, checks) {
  const sectionNode = el("section", "workspace-section");
  const head = el("header", "workspace-section-head");
  const blocked = checks.filter(
    (check) => String(check?.verdict || "").toUpperCase() === "REQUEST_CHANGES"
  ).length;
  head.append(
    el("h3", null, "Compatibility findings"),
    el("p", null, checks.length ? `${plural(checks.length, "check")} · ${plural(blocked, "blocked")}` : "none reported")
  );
  sectionNode.append(head);

  if (!checks.length) {
    sectionNode.append(el("p", "dim", "No registry compatibility checks were reported for this scope."));
    container.append(sectionNode);
    return;
  }

  const list = el("ul", "workspace-check-list");
  for (const check of checks) {
    const item = el("li", "workspace-check");
    const verdict = String(check?.verdict || "UNKNOWN").toUpperCase();
    const contractRef = qualifiedWorkspaceRef(check?.contract_id || check?.contractId) || "Unknown agent";
    const header = el("div", "workspace-check-head");
    header.append(
      el("span", `workspace-verdict ${verdict === "APPROVED" ? "approved" : "blocked"}`, verdict.replace("_", " ")),
      el("strong", null, contractRef),
      el("span", "workspace-check-range", `${check?.from_version || "new"} → ${check?.to_version || "current"}`)
    );
    item.append(header);
    const findings = Array.isArray(check?.findings) ? check.findings : [];
    if (!findings.length) {
      item.append(el("p", "dim", "No compatibility impact detected."));
    } else {
      const findingList = el("ul", "workspace-finding-list");
      for (const finding of findings) {
        findingList.append(
          workspaceConflictRow({
            ...finding,
            message: finding?.summary || finding?.message || "Compatibility finding",
            agent_refs: [
              contractRef,
              finding?.changed_contract,
              finding?.affected_contract,
            ].filter(Boolean),
          })
        );
      }
      item.append(findingList);
    }
    list.append(item);
  }
  sectionNode.append(list);
  container.append(sectionNode);
}

function renderWorkspaceReportErrors(container) {
  const errors = Array.isArray(state.workspaceScan?.errors) ? state.workspaceScan.errors : [];
  if (!errors.length) return;
  const sectionNode = el("section", "workspace-section workspace-report-errors");
  const head = el("header", "workspace-section-head");
  head.append(el("h3", null, "Scan errors"), el("p", null, plural(errors.length, "error")));
  sectionNode.append(head);
  const list = el("ul", "workspace-error-list");
  for (const error of errors) {
    const repositoryId = error?.repository_id || error?.repositoryId || "workspace";
    const location = [repositoryId, error?.path].filter(Boolean).join(":");
    const item = el("li", "workspace-error-row");
    item.append(el("strong", null, location), el("p", null, error?.message || "Unknown scan error"));
    list.append(item);
  }
  sectionNode.append(list);
  container.append(sectionNode);
}

function renderWorkspaceConflicts(container, conflicts) {
  const sectionNode = el("section", "workspace-section");
  const head = el("header", "workspace-section-head");
  const blocking = conflicts.filter((conflict) => conflict?.blocking).length;
  head.append(
    el("h3", null, "Instruction conflicts"),
    el("p", null, conflicts.length ? `${plural(conflicts.length, "conflict")} · ${plural(blocking, "blocking")}` : "none found")
  );
  sectionNode.append(head);
  if (!conflicts.length) {
    sectionNode.append(el("p", "dim", "No instruction conflicts were reported for this scope."));
  } else {
    const list = el("ul", "workspace-conflict-list");
    conflicts.forEach((conflict) => list.append(workspaceConflictRow(conflict)));
    sectionNode.append(list);
  }
  container.append(sectionNode);
}

function renderWorkspaceResults() {
  const container = $("#workspace-results");
  container.replaceChildren();
  const repository = workspaceRepositoryFor(state.selectedRepository);
  $("#workspace-results-title").textContent = repository ? repository.id : "Local workspace";

  if (!state.repositories.length) {
    $("#workspace-summary").textContent = "Add repositories to begin. Saved repositories scan structurally at launch.";
    container.append(
      emptyState(
        "No repositories yet",
        "Add local repositories, then scan now or let Ionic run a local structural scan at the next launch. No files are watched or uploaded.",
        { action: "Add repositories…", actionClass: "empty-add-repositories" }
      )
    );
    updateWorkspaceSyncButton();
    return;
  }

  if (repository) container.append(repositoryEditor(repository));
  if (!state.workspaceScan) {
    $("#workspace-summary").textContent = `${plural(state.repositories.length, "repository", "repositories")} configured · not scanned`;
    container.append(
      emptyState(
        "Ready to scan",
        "Saved repositories are scanned locally when Ionic launches. Choose Scan workspace any time to refresh; structural analysis makes no network call."
      )
    );
    updateWorkspaceSyncButton();
    return;
  }

  const agents = visibleWorkspaceAgents();
  const conflicts = visibleWorkspaceConflicts();
  const checks = visibleWorkspaceChecks();
  const summary = state.workspaceScan.summary || {};
  const reportDocuments = state.workspaceScan.documents;
  const documents = repository && Array.isArray(reportDocuments)
    ? reportDocuments.filter((documentEntry) => documentEntry?.repository_id === repository.id).length
    : Number(summary.documents ?? (Array.isArray(reportDocuments) ? reportDocuments.length : reportDocuments) ?? 0);
  $("#workspace-summary").textContent = `${plural(agents.length, "agent")} · ${plural(documents, "instruction file")} · ${plural(conflicts.length, "conflict")} · ${workspaceNetworkLabel(state.workspaceScan)}`;
  renderWorkspaceReportErrors(container);
  renderWorkspaceAgents(container, agents);
  renderWorkspaceChecks(container, checks);
  renderWorkspaceConflicts(container, conflicts);
  updateWorkspaceSyncButton();
}

function renderWorkspace() {
  renderRepositoryList();
  renderWorkspaceResults();
}

function setWorkspaceError(message = "", { focus = false, preserved = true } = {}) {
  const error = $("#workspace-error");
  error.replaceChildren();
  error.classList.toggle("hidden", !message);
  if (message) {
    error.append(
      el("strong", null, "Workspace operation failed"),
      el(
        "p",
        null,
        preserved ? `${message} Your repositories and last successful scan are unchanged.` : message
      )
    );
    if (focus) error.focus({ preventScroll: false });
  }
}

function workspaceBlockedReason(report, fallback = "The current workspace has blocking findings.") {
  const conflict = workspaceConflicts(report).find((entry) => entry?.blocking) || workspaceConflicts(report)[0];
  if (conflict?.message) return conflict.message;
  const blockedCheck = workspaceChecks(report).find(
    (check) => String(check?.verdict || "").toUpperCase() === "REQUEST_CHANGES"
  );
  const finding = Array.isArray(blockedCheck?.findings) ? blockedCheck.findings[0] : null;
  if (finding?.summary || finding?.message) return finding.summary || finding.message;
  if (blockedCheck?.contract_id) return `Compatibility check blocked ${blockedCheck.contract_id}.`;
  const reportError = Array.isArray(report?.errors) ? report.errors[0] : null;
  return reportError?.message || fallback;
}

function adoptBlockedWorkspaceReport(report) {
  state.workspaceScan = report;
  const allowed = defaultWorkspaceSelection(report);
  state.workspaceSelection = new Set(
    [...state.workspaceSelection].filter((ref) => allowed.has(ref))
  );
  hideWorkspaceSyncReview();
  renderWorkspace();
}

function setWorkspaceBusy(busy, message = "") {
  state.workspaceBusy = busy;
  $("#repository-results").setAttribute("aria-busy", String(busy));
  $("#repository-add").disabled = busy;
  $("#workspace-scan").disabled = busy || state.repositories.length === 0;
  $("#repository-filter").disabled = busy || state.repositories.length === 0;
  $$("#repository-results input, #repository-results button").forEach((control) => {
    if (busy) {
      if (!control.disabled) control.dataset.enabledBeforeWorkspaceBusy = "true";
      control.disabled = true;
    } else if (control.dataset.enabledBeforeWorkspaceBusy === "true") {
      control.disabled = false;
      delete control.dataset.enabledBeforeWorkspaceBusy;
    }
  });
  $("#workspace-scan .spinner").classList.toggle("hidden", !busy);
  if (message) setText("#workspace-scan-status", message);
  updateWorkspaceSyncButton();
}

function updateWorkspaceSyncButton() {
  const button = $("#workspace-sync");
  button.disabled =
    state.workspaceBusy ||
    !state.workspaceScan?.scan_id ||
    state.workspaceSelection.size === 0;
  const label = button.querySelector(".button-label");
  if (label) label.textContent = state.workspaceSelection.size
    ? `Sync selected (${state.workspaceSelection.size})`
    : "Sync selected";
  $("#workspace-scan").disabled = state.workspaceBusy || state.repositories.length === 0;
  $("#repository-filter").disabled = state.workspaceBusy || !state.repositories.length;
}

async function addWorkspaceRepositories() {
  const result = await bridgeEnvelope(window.ionic.pickWorkspaceDirectories());
  if (!result.ok) {
    setWorkspaceError(result.error.message);
    return;
  }
  const paths = Array.isArray(result.data) ? result.data : [];
  if (!paths.length) return;
  const known = new Set(state.repositories.map(({ path: sourcePath }) => sourcePath.toLowerCase()));
  let added = 0;
  for (const sourcePath of paths) {
    if (
      typeof sourcePath !== "string" ||
      !sourcePath.trim() ||
      known.has(sourcePath.trim().toLowerCase()) ||
      state.repositories.length >= MAX_WORKSPACE_REPOSITORIES
    ) {
      continue;
    }
    const repository = { id: uniqueRepositoryId(sourcePath), path: sourcePath.trim() };
    state.repositories.push(repository);
    known.add(repository.path.toLowerCase());
    added += 1;
  }
  if (!added) {
    toast("Those repositories are already in this workspace, or the 64-repository limit was reached.", "info");
    return;
  }
  saveWorkspaceRepositories();
  state.selectedRepository = "all";
  clearWorkspaceResult(`${plural(added, "repository", "repositories")} added. Run Scan workspace when ready.`);
  $("#workspace-scan").focus({ preventScroll: true });
}

function defaultWorkspaceSelection(scan) {
  const blockingRefs = new Set();
  let hasUnscopedBlockingConflict = false;
  for (const conflict of workspaceConflicts(scan)) {
    if (!conflict?.blocking) continue;
    const refs = workspaceConflictRefs(conflict);
    if (!refs.length) hasUnscopedBlockingConflict = true;
    refs.forEach((ref) => blockingRefs.add(String(ref).toLowerCase()));
  }
  for (const ref of workspaceCheckBlockedRefs(scan)) blockingRefs.add(ref);
  if (Array.isArray(scan?.errors) && scan.errors.length) return new Set();
  return new Set(
    workspaceAgents(scan)
      .filter((agent) => {
        const ref = workspaceAgentRef(agent);
        return !hasUnscopedBlockingConflict && !agent?.blocking && agent?.status !== "blocked" && !blockingRefs.has(ref);
      })
      .map(workspaceAgentRef)
  );
}

async function scanWorkspace(options = {}) {
  const background = options?.background === true;
  if (!state.repositories.length || state.workspaceBusy) return false;
  const request = ++state.workspaceRequest;
  const previousScan = state.workspaceScan;
  const previousSelection = state.workspaceSelection;
  let result = null;
  setWorkspaceError();
  if (!background) {
    hideWorkspaceSyncReview();
    setButtonBusy($("#workspace-scan"), true, "Scanning…");
  }
  setWorkspaceBusy(true, background ? "" : "Scanning local instruction files…");
  try {
    const scanResult = await bridgeEnvelope(
      window.ionic.workspaceScan({ repositories: workspaceRepositoriesRequest() })
    );
    if (request !== state.workspaceRequest) return false;
    result = scanResult;
    if (scanResult.ok) {
      if (!background) {
        setText("#workspace-scan-status", "Checking instruction compatibility across repositories…");
      }
      const checkResult = await bridgeEnvelope(
        window.ionic.workspaceCheck({
          repositories: workspaceRepositoriesRequest(),
          failOn: state.settings.failOn || "high",
          transitive: Boolean(state.settings.transitive),
        })
      );
      if (request !== state.workspaceRequest) return false;
      if (!checkResult.ok) result = checkResult;
      else {
        const scan = scanResult.data || {};
        const check = checkResult.data || {};
        result = {
          ok: true,
          data: {
            ...scan,
            status: check.status || scan.status,
            checks: Array.isArray(check.checks) ? check.checks : scan.checks,
            conflicts: Array.isArray(check.conflicts) ? check.conflicts : scan.conflicts,
            errors: uniqueWorkspaceRecords(scan.errors, check.errors),
            summary: {
              ...(scan.summary || {}),
              checks: check.summary?.checks ?? (Array.isArray(check.checks) ? check.checks.length : 0),
              blocked_checks: check.summary?.blocked_checks ?? 0,
            },
            network: check.network || scan.network || { used: false },
          },
        };
      }
    }
  } catch (error) {
    result = {
      ok: false,
      error: { message: error?.message || "The structural workspace scan could not complete." },
    };
  } finally {
    if (!background) setButtonBusy($("#workspace-scan"), false);
    setWorkspaceBusy(false);
  }
  if (!result?.ok) {
    state.workspaceScan = previousScan;
    state.workspaceSelection = previousSelection;
    setWorkspaceError(result?.error?.message || "The structural workspace scan could not complete.");
    setText(
      "#workspace-scan-status",
      background
        ? "Automatic structural scan failed. Use Scan workspace to retry."
        : "Scan failed. Last successful results remain available."
    );
    renderWorkspace();
    return false;
  }
  state.workspaceScan = result.data;
  state.workspaceSelection = defaultWorkspaceSelection(result.data);
  state.workspaceSyncPlan = null;
  if (!background) {
    setText(
      "#workspace-scan-status",
      `Scan complete · ${plural(workspaceAgents().length, "agent")} · ${workspaceNetworkLabel(result.data)}`
    );
  }
  renderWorkspace();
  return true;
}

async function runLaunchStructuralScan() {
  if (state.launchStructuralScanAttempted) return false;
  state.launchStructuralScanAttempted = true;
  if (!state.legal.accepted || !state.engine || !state.repositories.length) return false;
  try {
    return await scanWorkspace({ background: true });
  } catch {
    return false;
  }
}

function hideWorkspaceSyncReview() {
  state.workspaceSyncPlan = null;
  $("#workspace-sync-review").classList.add("hidden");
  $("#workspace-sync-actions").replaceChildren();
}

function syncPlanActions(plan) {
  const actions = plan?.actions && typeof plan.actions === "object" ? plan.actions : {};
  return ["add", "update", "unchanged", "prune"].map((kind) => ({
    kind,
    ids: Array.isArray(actions[kind]) ? actions[kind].map(String) : [],
  }));
}

function renderSyncPlan(plan) {
  const categories = syncPlanActions(plan);
  const changed = categories
    .filter(({ kind }) => kind !== "unchanged")
    .reduce((total, { ids }) => total + ids.length, 0);
  $("#workspace-sync-review-summary").textContent = `${plural(changed, "registry change")} in this reviewed plan for ${plural(state.workspaceSelection.size, "selected agent")}.`;
  const wrapper = $("#workspace-sync-actions");
  wrapper.replaceChildren();
  for (const { kind, ids } of categories) {
    const group = el("section", "workspace-sync-action-group");
    group.append(el("strong", null, `${kind} (${ids.length})`));
    if (ids.length) {
      const list = el("ul");
      for (const id of ids) list.append(el("li", null, id));
      group.append(list);
    } else {
      group.append(el("span", "dim", "None"));
    }
    wrapper.append(group);
  }
}

async function planWorkspaceSync() {
  if (!state.workspaceScan?.scan_id || !state.workspaceSelection.size || state.workspaceBusy) return;
  setWorkspaceError();
  setButtonBusy($("#workspace-sync"), true, "Planning…");
  setWorkspaceBusy(true, "Preparing a registry sync plan…");
  const result = await bridgeEnvelope(
    window.ionic.workspaceSync({
      repositories: workspaceRepositoriesRequest(),
      agents: [...state.workspaceSelection],
      apply: false,
    })
  );
  setButtonBusy($("#workspace-sync"), false);
  setWorkspaceBusy(false);
  if (!result.ok) {
    setWorkspaceError(result.error.message, { focus: true });
    setText("#workspace-scan-status", "Sync plan failed. No registry changes were made.");
    return;
  }
  if (result.data?.status === "blocked") {
    const reason = workspaceBlockedReason(result.data);
    adoptBlockedWorkspaceReport(result.data);
    setWorkspaceError(`Sync plan blocked: ${reason}`, { focus: true, preserved: false });
    setText("#workspace-scan-status", "Sync plan blocked. No registry changes were made.");
    return;
  }
  const sourceScanId = result.data?.source_scan_id || result.data?.sourceScanId || result.data?.scan_id;
  if (!result.data?.scan_id || sourceScanId !== state.workspaceScan.scan_id) {
    if (result.data?.scan_id) {
      state.workspaceScan = result.data;
      state.workspaceSelection = defaultWorkspaceSelection(result.data);
      hideWorkspaceSyncReview();
      renderWorkspace();
    }
    setWorkspaceError("The workspace changed while the sync plan was being prepared. Review the refreshed results before syncing.", { focus: true, preserved: false });
    setText("#workspace-scan-status", "Workspace changed. Scan again before syncing.");
    return;
  }
  state.workspaceSyncPlan = result.data;
  renderSyncPlan(result.data);
  $("#workspace-sync-review").classList.remove("hidden");
  setText("#workspace-scan-status", "Sync plan ready. Review it before applying.");
  $("#workspace-sync-apply").focus({ preventScroll: true });
}

async function applyWorkspaceSync() {
  if (!state.workspaceSyncPlan || !state.workspaceScan?.scan_id || state.workspaceBusy) return;
  setWorkspaceError();
  setButtonBusy($("#workspace-sync-apply"), true, "Applying…");
  setWorkspaceBusy(true, "Applying the reviewed registry sync…");
  const result = await bridgeEnvelope(
    window.ionic.workspaceSync({
      repositories: workspaceRepositoriesRequest(),
      agents: [...state.workspaceSelection],
      apply: true,
      expectedScanId: state.workspaceSyncPlan.scan_id,
    })
  );
  setButtonBusy($("#workspace-sync-apply"), false);
  setWorkspaceBusy(false);
  if (!result.ok) {
    hideWorkspaceSyncReview();
    if (result.error?.code === 3) {
      state.workspaceScan = null;
      state.workspaceSelection = new Set();
      renderWorkspace();
    }
    setWorkspaceError(result.error.message, {
      focus: true,
      preserved: result.error?.code !== 3,
    });
    setText("#workspace-scan-status", "Sync was not applied. Scan again if the workspace changed.");
    return;
  }
  if (result.data?.status !== "synced" || result.data?.applied !== true) {
    const reason = workspaceBlockedReason(result.data, "Ionic refused the registry update.");
    adoptBlockedWorkspaceReport(result.data);
    setWorkspaceError(`Sync not applied: ${reason}`, { focus: true, preserved: false });
    setText("#workspace-scan-status", "Sync was not applied. Scan and review again.");
    return;
  }
  hideWorkspaceSyncReview();
  setText("#workspace-scan-status", "Registry synced. Scan again before another sync.");
  toast("Selected agents were synced to the local registry.", "success");
  state.workspaceScan = null;
  state.workspaceSelection = new Set();
  await refreshAll();
  renderWorkspace();
  $("#workspace-scan").focus({ preventScroll: true });
}

/* ------------------------------------------------------------------ */
/* settings                                                            */
/* ------------------------------------------------------------------ */

const settingsStatusTimers = new Map();
let settingsSaveQueue = Promise.resolve();
let settingsHydrationRequest = 0;

function setSettingsHydrating(busy) {
  $("#settings").toggleAttribute("aria-busy", busy);
  $$("#settings-content input, #settings-content select, #settings-content button").forEach((control) => {
    if (busy) {
      if (!control.disabled) control.dataset.enabledBeforeHydration = "true";
      control.disabled = true;
    } else if (control.dataset.enabledBeforeHydration === "true") {
      control.disabled = false;
      delete control.dataset.enabledBeforeHydration;
    }
  });
}

function setSettingsUnavailable(unavailable) {
  $$("#settings-content [data-settings-section] input, #settings-content [data-settings-section] select, #settings-content [data-settings-section] button").forEach((control) => {
    if (unavailable) {
      if (!control.disabled) control.dataset.enabledBeforeLoadError = "true";
      control.disabled = true;
    } else if (control.dataset.enabledBeforeLoadError === "true") {
      control.disabled = false;
      delete control.dataset.enabledBeforeLoadError;
    }
  });
}

function showSettingsLoadError(message = "") {
  $("#settings-load-error-message").textContent =
    message || "The latest preferences are unavailable.";
  $("#settings-load-error").classList.toggle("hidden", !message);
}

function setFieldError(inputSelector, messageSelector, message = "") {
  const input = $(inputSelector);
  const target = $(messageSelector);
  input.setAttribute("aria-invalid", String(Boolean(message)));
  target.textContent = message;
  target.classList.toggle("hidden", !message);
}

function showSettingsSurface() {
  $("#legal").classList.add("hidden");
  $("#boot").classList.add("hidden");
  $("#setup").classList.add("hidden");
  $("#app").classList.add("hidden");
  $("#settings").classList.remove("hidden");
  $("#statusbar").classList.add("hidden");
  state.settingsOpen = true;
  requestAnimationFrame(applyVisiblePaneWidths);
}

function setSettingsSaveState(section, message = "", tone = "", { persist = false } = {}) {
  const target = $(`#settings-${section}-status`);
  if (!target) return;
  clearTimeout(settingsStatusTimers.get(section));
  target.textContent = message;
  target.classList.remove("saving", "saved", "error");
  if (tone) target.classList.add(tone);
  if (message && !persist && tone !== "error") {
    settingsStatusTimers.set(
      section,
      setTimeout(() => {
        target.textContent = "";
        target.classList.remove("saving", "saved", "error");
      }, 2600)
    );
  }
}

async function saveSettingsPatch(patch, { section = "ai", savedMessage = "Saved" } = {}) {
  setSettingsSaveState(section, "Saving…", "saving", { persist: true });
  const operation = settingsSaveQueue.then(() =>
    bridgeEnvelope(window.ionic.saveSettings(patch))
  );
  settingsSaveQueue = operation.then(() => undefined, () => undefined);
  const result = await operation;
  if (!result.ok) {
    setSettingsSaveState(section, result.error.message, "error", { persist: true });
    return null;
  }
  state.settings = { ...state.settings, ...(result.data || {}), ...patch };
  if (["useLlm", "judgeProvider", "judgeModel", "modelAccessMode", "subscriptionRuntime"].some((key) => Object.hasOwn(patch, key))) {
    setText("#status-judge", configuredAnalysisDescription(state.settings));
  }
  setSettingsSaveState(section, savedMessage, "saved");
  return state.settings;
}

function showSettingsCategory(name, { focus = true, clearSearch = true } = {}) {
  if (!$(`#settings-${name}`)) return;
  state.settingsCategory = name;
  if (clearSearch) $("#settings-filter").value = "";
  $$('[data-setting-search]').forEach((node) => {
    node.hidden = false;
    node.classList.remove("hidden-by-search");
  });
  updateProviderVisibility(state.activeProvider);
  renderModelAccessMode(state.settings);

  $$(".settings-nav-item").forEach((button) => {
    button.hidden = false;
    const active = button.dataset.settingsCategory === name;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  $$("[data-settings-section]").forEach((section) => {
    section.classList.remove("hidden-by-search");
    section.hidden = section.dataset.settingsSection !== name;
    section.querySelectorAll(".hidden-by-search").forEach((node) =>
      node.classList.remove("hidden-by-search")
    );
  });
  $("#settings-no-results").classList.add("hidden");

  const section = $(`#settings-${name}`);
  const heading = section.querySelector("h1");
  $("#settings").removeAttribute("aria-label");
  $("#settings").setAttribute("aria-labelledby", heading.id);
  if (focus) {
    heading.tabIndex = -1;
    heading.focus({ preventScroll: true });
  }
  $("#settings-content").scrollTop = 0;
}

function filterSettings() {
  const query = $("#settings-filter").value.trim().toLowerCase();
  if (!query) {
    showSettingsCategory(state.settingsCategory, { focus: false, clearSearch: false });
    return;
  }

  let visibleSections = 0;
  $$(".settings-nav-item").forEach((button) => {
    button.classList.remove("active");
    button.removeAttribute("aria-current");
  });
  $$("[data-settings-section]").forEach((section) => {
    const sectionText = [
      section.dataset.settingsSection,
      section.querySelector("h1")?.textContent,
      section.querySelector(".settings-section-head p")?.textContent,
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    const sectionMatch = sectionText.includes(query);
    let matches = 0;
    section.hidden = false;
    section.querySelectorAll("[data-setting-search]").forEach((node) => {
      const searchable = `${node.dataset.settingSearch || ""} ${node.textContent || ""}`.toLowerCase();
      const match = sectionMatch || searchable.includes(query);
      node.hidden = !match;
      node.classList.toggle("hidden-by-search", !match);
      if (match) matches += 1;
    });
    section.classList.toggle("hidden-by-search", !sectionMatch && matches === 0);
    const nav = $(`.settings-nav-item[data-settings-category="${section.dataset.settingsSection}"]`);
    if (nav) nav.hidden = !sectionMatch && matches === 0;
    if (sectionMatch || matches) visibleSections += 1;
  });
  $("#settings").removeAttribute("aria-labelledby");
  $("#settings").setAttribute("aria-label", "Settings search results");
  $("#settings-no-results").classList.toggle("hidden", visibleSections !== 0);
}

function safeRuntimeText(value, maxLength = 320) {
  return typeof value === "string" ? value.trim().slice(0, maxLength) : "";
}

function normalizedSubscriptionRuntime(raw) {
  const id = safeRuntimeText(raw?.id, 80);
  if (!SUBSCRIPTION_RUNTIME_IDS.includes(id) || !raw || typeof raw !== "object" || Array.isArray(raw)) {
    return null;
  }
  const runtimeState = ["ready", "missing", "unsafe_wrapper", "unavailable"].includes(raw.state)
    ? raw.state
    : "unavailable";
  return {
    id,
    state: runtimeState,
    available: raw.available === true,
    installed: raw.installed === true,
    authenticated: raw.authenticated === true ? true : raw.authenticated === false ? false : null,
    version: safeRuntimeText(raw.version, 120),
    message: safeRuntimeText(raw.message),
    maturity: ["beta", "experimental"].includes(raw.maturity) ? raw.maturity : "",
  };
}

function subscriptionRuntimeRecords(value) {
  if (!Array.isArray(value)) return {};
  return Object.fromEntries(
    value
      .map(normalizedSubscriptionRuntime)
      .filter(Boolean)
      .map((runtime) => [runtime.id, runtime])
  );
}

async function loadRuntimeDiscovery({ announce = false, deferRender = false } = {}) {
  const request = ++state.runtimeDiscoveryRequest;
  if (typeof window.ionic?.subscriptionRuntimes !== "function") {
    state.runtimeDiscovery = {
      runtimes: {},
      runtimeError: "Subscription runtime discovery is unavailable in this build.",
    };
    if (!deferRender) renderSubscriptionRuntimes();
    return false;
  }
  if (announce) setSettingsSaveState("ai", "Refreshing runtimes…", "saving", { persist: true });
  const result = await bridgeEnvelope(window.ionic.subscriptionRuntimes());
  if (request !== state.runtimeDiscoveryRequest) return false;
  state.runtimeDiscovery = result.ok
    ? {
        runtimes: subscriptionRuntimeRecords(result.data?.runtimes),
        runtimeError: safeRuntimeText(result.data?.error),
      }
    : { runtimes: {}, runtimeError: result.error.message };
  if (announce) {
    setSettingsSaveState(
      "ai",
      result.ok ? "Runtime status refreshed" : "Runtime status unavailable",
      result.ok ? "saved" : "error"
    );
  }
  if (!deferRender) renderSubscriptionRuntimes();
  return result.ok;
}

function renderSubscriptionRuntimes() {
  const status = $("#subscription-runtimes-status");
  if (status) {
    status.textContent = state.runtimeDiscovery.runtimeError;
    status.classList.toggle("error", Boolean(state.runtimeDiscovery.runtimeError));
  }
  $$("[data-runtime-id]").forEach((row) => {
    const runtime = state.runtimeDiscovery.runtimes[row.dataset.runtimeId];
    const stateTarget = row.querySelector('[data-runtime-field="state"]');
    const installTarget = row.querySelector('[data-runtime-field="install"]');
    const authenticationTarget = row.querySelector('[data-runtime-field="authentication"]');
    const maturityTarget = row.querySelector('[data-runtime-field="maturity"]');
    const messageTarget = row.querySelector('[data-runtime-field="message"]');
    if (!runtime) {
      stateTarget.textContent = "Unavailable";
      stateTarget.className = "runtime-state unavailable";
      installTarget.textContent = "Status unavailable";
      authenticationTarget.textContent = "Not inspected";
      maturityTarget.textContent = "Status unavailable";
      messageTarget.textContent = "This runtime was not reported by the local desktop service.";
      return;
    }

    const labels = {
      ready: "Installed",
      missing: "Not installed",
      unsafe_wrapper: "Blocked",
      unavailable: "Unavailable",
    };
    stateTarget.textContent = labels[runtime.state];
    stateTarget.className = `runtime-state ${runtime.state}`;
    installTarget.textContent = runtime.installed
      ? runtime.version || "Installed · version unavailable"
      : "Not installed";
    authenticationTarget.textContent = "Not inspected here";
    maturityTarget.textContent = runtime.maturity
      ? runtime.maturity[0].toUpperCase() + runtime.maturity.slice(1)
      : "Status unavailable";
    messageTarget.textContent = runtime.message || (
      runtime.available
        ? "Authentication is owned and verified by the official runtime when first used."
        : "This runtime cannot be delegated to from this device."
    );
  });
  SUBSCRIPTION_RUNTIME_IDS.forEach(renderModelSubscriptionRuntime);
  SUBSCRIPTION_RUNTIME_IDS.forEach(renderSubscriptionAuth);
}

function renderModelSubscriptionRuntime(runtimeId) {
  const row = $(`[data-model-runtime-id="${runtimeId}"]`);
  if (!row) return;
  const runtime = state.runtimeDiscovery.runtimes[runtimeId];
  const stateTarget = row.querySelector('[data-model-runtime-field="state"]');
  const detailTarget = row.querySelector('[data-model-runtime-field="detail"]');
  if (!stateTarget || !detailTarget) return;
  const runtimeName = runtimeId === "xai-grok-build" ? "Grok Build" : "Codex";
  if (!runtime) {
    stateTarget.textContent = "Status unavailable";
    stateTarget.className = "subscription-provider-state unavailable";
    detailTarget.textContent = state.runtimeDiscovery.runtimeError
      || "The local desktop service did not report this runtime.";
    return;
  }

  if (runtime.state === "ready" && runtime.available) {
    stateTarget.textContent = "Installed";
    stateTarget.className = "subscription-provider-state ready";
    const install = runtime.version ? `Installed ${runtime.version}.` : "Installed.";
    detailTarget.textContent = runtimeId === "openai-codex"
      ? `${install} Sign-in and the model catalog can be checked. Semantic review stays locked until this version passes Ionic's local restricted-root boundary check.`
      : `${install} Authentication is not inspected; ${runtimeName} verifies it on first use.`;
    return;
  }
  if (runtime.state === "missing") {
    stateTarget.textContent = "Not installed";
    stateTarget.className = "subscription-provider-state missing";
    detailTarget.textContent = `Install the official ${runtimeName} runtime, then reopen AI & models to refresh local status.`;
    return;
  }
  if (runtime.state === "unsafe_wrapper") {
    stateTarget.textContent = "Blocked";
    stateTarget.className = "subscription-provider-state blocked";
    detailTarget.textContent = runtime.message || "Ionic found a runtime wrapper that cannot be delegated to safely.";
    return;
  }
  stateTarget.textContent = "Unavailable";
  stateTarget.className = "subscription-provider-state unavailable";
  detailTarget.textContent = runtime.message || "This runtime cannot be delegated to from this device.";
}

function normalizedSubscriptionStatus(provider, raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw) || raw.provider !== provider) return null;
  const safeDisclosureList = (value) => Array.isArray(value)
    ? value
        .slice(0, 12)
        .map((entry) => safeRuntimeText(entry, 600))
        .filter(Boolean)
    : [];
  const disclosure = raw.disclosure && typeof raw.disclosure === "object" && !Array.isArray(raw.disclosure)
    ? {
        provider,
        version: safeRuntimeText(raw.disclosure.version, 40),
        product: safeRuntimeText(raw.disclosure.product, 120),
        heading: safeRuntimeText(raw.disclosure.heading, 240),
        purpose: safeRuntimeText(raw.disclosure.purpose, 600),
        authentication: safeRuntimeText(raw.disclosure.authentication, 1_200),
        timing: safeRuntimeText(raw.disclosure.timing, 800),
        sends: safeDisclosureList(raw.disclosure.sends),
        localBoundary: safeDisclosureList(raw.disclosure.localBoundary),
      }
    : null;
  return {
    provider,
    installed: raw.installed === true,
    available: raw.available === true,
    connected: raw.connected === true ? true : raw.connected === false ? false : null,
    authMode: safeRuntimeText(raw.authMode, 80) || "none",
    planType: safeRuntimeText(raw.planType, 80),
    authenticationInspected: raw.authenticationInspected === true,
    requiresOpenaiAuth: raw.requiresOpenaiAuth === true,
    message: safeRuntimeText(raw.message),
    semanticReviewCapable: raw.semanticReviewCapable === true
      ? true
      : raw.semanticReviewCapable === false
        ? false
        : null,
    unsupportedControls: Array.isArray(raw.unsupportedControls)
      ? raw.unsupportedControls.slice(0, 64).map((value) => safeRuntimeText(value, 160)).filter(Boolean)
      : [],
    semanticReviewMessage: safeRuntimeText(raw.semanticReviewMessage, 500),
    disclosure,
  };
}

function renderSubscriptionDisclosure(root, disclosure) {
  const fields = {
    heading: disclosure?.heading || "Review permission unavailable",
    purpose: disclosure?.purpose || "Ionic could not load the current provider disclosure.",
    authentication: disclosure?.authentication || "Sign-in details are unavailable.",
    timing: disclosure?.timing || "No provider action is available until the disclosure loads.",
  };
  for (const [name, value] of Object.entries(fields)) {
    const target = root.querySelector(`[data-subscription-disclosure-field="${name}"]`);
    if (target) target.textContent = value;
  }
  for (const [name, values] of [
    ["sends", disclosure?.sends || []],
    ["boundary", disclosure?.localBoundary || []],
  ]) {
    const target = root.querySelector(`[data-subscription-disclosure-list="${name}"]`);
    if (!target) continue;
    target.replaceChildren(...values.map((value) => el("li", "", value)));
  }
}

function subscriptionConsentSetting(provider) {
  return provider === "xai-grok-build"
    ? "grokSubscriptionConsentVersion"
    : "codexSubscriptionConsentVersion";
}

function subscriptionModelSetting(provider) {
  return provider === "xai-grok-build"
    ? "grokSubscriptionModel"
    : "codexSubscriptionModel";
}

function subscriptionEffortSetting(provider) {
  return provider === "xai-grok-build"
    ? "grokSubscriptionEffort"
    : "codexSubscriptionEffort";
}

function currentSubscriptionConsent(provider) {
  const disclosure = state.subscriptionAuth[provider]?.disclosure;
  if (!disclosure?.version) return null;
  const accepted = state.settings[subscriptionConsentSetting(provider)] === disclosure.version;
  return accepted
    ? { accepted: true, provider, version: disclosure.version }
    : null;
}

function normalizedSubscriptionModels(provider, raw) {
  if (!raw || typeof raw !== "object" || raw.provider !== provider || !Array.isArray(raw.models)) {
    return null;
  }
  const seen = new Set();
  const models = [];
  for (const candidate of raw.models.slice(0, 120)) {
    const id = safeRuntimeText(candidate?.id, 200);
    if (!id || !/^[A-Za-z0-9._:/-]{1,200}$/.test(id) || seen.has(id)) continue;
    seen.add(id);
    const supportedEfforts = Array.isArray(candidate.supportedEfforts)
      ? candidate.supportedEfforts
          .map((value) => safeRuntimeText(value, 20)?.toLowerCase())
          .filter((value, index, values) =>
            ["low", "medium", "high", "xhigh", "max"].includes(value) &&
            values.indexOf(value) === index
          )
      : [];
    models.push({
      id,
      displayName: safeRuntimeText(candidate.displayName, 120) || id,
      isDefault: candidate.isDefault === true,
      supportedEfforts,
      defaultEffort: supportedEfforts.includes(candidate.defaultEffort)
        ? candidate.defaultEffort
        : null,
    });
  }
  return { provider, models, truncated: raw.truncated === true };
}

function normalizedSubscriptionVerificationUrl(provider, raw) {
  try {
    return typeof window.ionic?.safeSubscriptionVerificationUrl === "function"
      ? window.ionic.safeSubscriptionVerificationUrl(provider, raw) || ""
      : "";
  } catch {
    return "";
  }
}

function renderSubscriptionAuth(provider) {
  const root = $(`[data-subscription-provider="${provider}"]`);
  if (!root) return;
  const runtime = state.runtimeDiscovery.runtimes[provider];
  const status = state.subscriptionAuth[provider];
  const login = state.subscriptionLogin[provider];
  const busy = state.subscriptionBusy.has(provider);
  const bridgeAvailable = ["subscriptionStatus", "beginSubscriptionLogin", "cancelSubscriptionLogin", "logoutSubscription"]
    .every((method) => typeof window.ionic?.[method] === "function");
  const pollAvailable = typeof window.ionic?.pollSubscriptionLogin === "function";
  const connectAvailable = bridgeAvailable && (provider !== "xai-grok-build" || pollAvailable);
  const installed = Boolean(runtime?.installed && runtime?.available);
  renderSubscriptionDisclosure(root, status?.disclosure);
  const consent = currentSubscriptionConsent(provider);
  const consentControl = root.querySelector(`[data-subscription-consent="${provider}"]`);
  if (consentControl) {
    consentControl.checked = Boolean(consent);
    consentControl.disabled = busy || !status?.disclosure?.version;
  }
  const message = root.querySelector('[data-subscription-field="status"]');
  if (!connectAvailable) message.textContent = "Subscription linking is unavailable in this desktop build.";
  else if (status?.connected && status?.semanticReviewCapable === false) {
    message.textContent = status.semanticReviewMessage
      || "Connected for sign-in and model catalog only. Semantic review is unavailable because this installed app-server did not prove restricted readable roots.";
  } else if (status?.connected) {
    const plan = status.planType ? ` · ${status.planType}` : "";
    message.textContent = `Connected through the provider runtime${plan}.`;
  } else if (login) message.textContent = "Authorization is waiting for you in the provider sign-in flow.";
  else if (status?.message) message.textContent = status.message;
  else if (status?.authenticationInspected) message.textContent = "The provider runtime did not confirm a subscription sign-in.";
  else if (status) message.textContent = "Authentication has not been inspected. Use Check sign-in when you are ready.";
  else message.textContent = "Local runtime status is loading.";

  const browser = root.querySelector('[data-subscription-action="connect-browser"]');
  const deviceLogin = root.querySelector('[data-subscription-action="connect-device"]');
  const disconnect = root.querySelector('[data-subscription-action="disconnect"]');
  const checkStatus = root.querySelector('[data-subscription-action="check-status"]');
  const inspectModels = root.querySelector('[data-subscription-action="inspect-models"]');
  if (browser) browser.disabled =
    busy || !connectAvailable || !installed || !consent || Boolean(status?.connected) || Boolean(login);
  if (deviceLogin) deviceLogin.disabled =
    busy || !connectAvailable || !installed || !consent || Boolean(status?.connected) || Boolean(login);
  if (disconnect) disconnect.disabled = busy || !bridgeAvailable || !status?.connected;
  if (checkStatus) checkStatus.disabled = busy || !bridgeAvailable || !installed;
  if (inspectModels) inspectModels.disabled =
    busy || !installed || !consent || typeof window.ionic?.subscriptionModels !== "function";
  const device = root.querySelector('[data-subscription-field="device-flow"]');
  device.classList.toggle("hidden", !login?.loginId);
  root.querySelector('[data-subscription-field="device-instruction"]').textContent = login?.userCode
    ? "Enter this code on the authorization page:"
    : "Finish authorization in the provider page opened by Ionic.";
  const deviceCode = root.querySelector('[data-subscription-field="device-code"]');
  deviceCode.textContent = login?.userCode || "—";
  deviceCode.classList.toggle("hidden", !login?.userCode);
  const copyCode = root.querySelector('[data-subscription-action="copy-code"]');
  copyCode.hidden = !login?.userCode;
  copyCode.disabled = !login?.userCode || busy;
  const verificationLink = root.querySelector('[data-subscription-field="verification-link"]');
  verificationLink.hidden = !login?.verificationUrl;
  verificationLink.href = login?.verificationUrl || "#";
  root.querySelector('[data-subscription-action="cancel-login"]').disabled = !login?.loginId || busy;
  root.setAttribute("aria-busy", String(busy));
  renderSubscriptionModelControls(provider, root);
}

function renderSubscriptionModelControls(provider, root = null) {
  const panel = root || $(`[data-subscription-config-provider="${provider}"]`);
  if (!panel) return;
  const modelControl = panel.querySelector('[data-subscription-field="model"]');
  const effortControl = panel.querySelector('[data-subscription-field="effort"]');
  if (!modelControl || !effortControl) return;
  const catalog = state.subscriptionModels[provider];
  const savedModel = state.settings[subscriptionModelSetting(provider)] || "";
  const models = Array.isArray(catalog?.models) ? catalog.models : [];
  modelControl.replaceChildren(
    new Option("Runtime default", ""),
    ...models.map((model) => new Option(
      `${model.displayName}${model.isDefault ? " (default)" : ""}`,
      model.id
    ))
  );
  const modelExists = !savedModel || models.some((model) => model.id === savedModel);
  modelControl.value = modelExists ? savedModel : "";
  modelControl.disabled = models.length === 0 || state.subscriptionBusy.has(provider);

  const selected = models.find((model) => model.id === modelControl.value)
    || models.find((model) => model.isDefault)
    || null;
  const savedEffort = state.settings[subscriptionEffortSetting(provider)] || "";
  const efforts = selected?.supportedEfforts || [];
  effortControl.replaceChildren(
    new Option(selected?.defaultEffort ? `Model default (${selected.defaultEffort})` : "Model default", ""),
    ...efforts.map((effort) => new Option(
      ({ low: "Low", medium: "Medium", high: "High", xhigh: "Extra high", max: "Maximum" })[effort] || effort,
      effort
    ))
  );
  effortControl.value = efforts.includes(savedEffort) ? savedEffort : "";
  effortControl.disabled = !selected || efforts.length === 0 || state.subscriptionBusy.has(provider);
}

function stopSubscriptionPolling(provider) {
  clearTimeout(state.subscriptionPollTimers[provider]);
  delete state.subscriptionPollTimers[provider];
}

function scheduleSubscriptionPolling(provider, delay = 1500) {
  stopSubscriptionPolling(provider);
  if (!state.subscriptionLogin[provider]?.loginId) return;
  state.subscriptionPollTimers[provider] = setTimeout(() => void pollSubscriptionLogin(provider), delay);
}

async function pollSubscriptionLogin(provider) {
  const login = state.subscriptionLogin[provider];
  if (!login?.loginId) return;
  if (typeof window.ionic?.pollSubscriptionLogin !== "function") {
    if (provider === "openai-codex") {
        await loadSubscriptionStatus(provider, { inspect: true });
      if (state.subscriptionAuth[provider]?.connected) {
        delete state.subscriptionLogin[provider];
        stopSubscriptionPolling(provider);
      } else {
        scheduleSubscriptionPolling(provider, 2500);
      }
    }
    return;
  }
  const result = await bridgeEnvelope(window.ionic.pollSubscriptionLogin(provider, login.loginId));
  if (!result.ok) {
    state.subscriptionAuth[provider] = {
      provider, available: false, connected: null, authMode: "unknown", planType: "", message: result.error.message,
    };
    delete state.subscriptionLogin[provider];
    stopSubscriptionPolling(provider);
    renderSubscriptionAuth(provider);
    return;
  }
  const loginState = safeRuntimeText(result.data?.state, 40) || "awaiting_user";
  state.subscriptionLogin[provider] = {
    ...login,
    userCode: safeRuntimeText(result.data?.userCode, 64) || login.userCode,
    verificationUrl: normalizedSubscriptionVerificationUrl(provider, result.data?.verificationUrl)
      || login.verificationUrl,
  };
  if (loginState === "connected") {
    delete state.subscriptionLogin[provider];
    stopSubscriptionPolling(provider);
    await loadSubscriptionStatus(provider, { inspect: true });
  } else if (loginState === "awaiting_user" || loginState === "starting") {
    if (provider === "openai-codex") {
      await loadSubscriptionStatus(provider, { inspect: true });
      if (state.subscriptionAuth[provider]?.connected) {
        delete state.subscriptionLogin[provider];
        stopSubscriptionPolling(provider);
      } else {
        renderSubscriptionAuth(provider);
        scheduleSubscriptionPolling(provider, 2500);
      }
    } else {
      renderSubscriptionAuth(provider);
      scheduleSubscriptionPolling(provider);
    }
  } else {
    delete state.subscriptionLogin[provider];
    stopSubscriptionPolling(provider);
    state.subscriptionAuth[provider] = {
      provider,
      available: true,
      connected: false,
      authMode: "none",
      planType: "",
      message: `Authorization ${loginState.replaceAll("_", " ")}. Start a new sign-in to retry.`,
    };
    renderSubscriptionAuth(provider);
  }
}

async function loadSubscriptionStatus(provider, { announce = false, inspect = false } = {}) {
  if (typeof window.ionic?.subscriptionStatus !== "function") {
    state.subscriptionAuth[provider] = null;
    renderSubscriptionAuth(provider);
    return false;
  }
  if (announce) state.subscriptionBusy.add(provider);
  renderSubscriptionAuth(provider);
  const result = await bridgeEnvelope(window.ionic.subscriptionStatus(provider, inspect));
  state.subscriptionBusy.delete(provider);
  state.subscriptionAuth[provider] = result.ok
    ? normalizedSubscriptionStatus(provider, result.data)
    : { provider, available: false, connected: null, authMode: "unknown", planType: "", message: result.error.message };
  if (state.subscriptionAuth[provider]?.connected) delete state.subscriptionLogin[provider];
  renderSubscriptionAuth(provider);
  return Boolean(result.ok);
}

async function runSubscriptionAction(provider, action, button) {
  if (!SELECTABLE_SUBSCRIPTION_RUNTIMES.has(provider) || state.subscriptionBusy.has(provider)) return;
  if (action === "check-status") {
    setButtonBusy(button, true, "Checking…");
    await loadSubscriptionStatus(provider, { announce: true, inspect: true });
    setButtonBusy(button, false);
    return;
  }
  if (action === "inspect-models") {
    const consent = currentSubscriptionConsent(provider);
    if (!consent) return;
    state.subscriptionBusy.add(provider);
    setButtonBusy(button, true, "Inspecting…");
    renderSubscriptionAuth(provider);
    const result = await bridgeEnvelope(window.ionic.subscriptionModels(provider, consent));
    state.subscriptionBusy.delete(provider);
    setButtonBusy(button, false);
    if (result.ok) {
      state.subscriptionModels[provider] = normalizedSubscriptionModels(provider, result.data);
      const count = state.subscriptionModels[provider]?.models.length || 0;
      state.subscriptionAuth[provider] = {
        ...(state.subscriptionAuth[provider] || { provider }),
        message: count
          ? `${count} available model${count === 1 ? "" : "s"} loaded.`
          : "The runtime did not report an available model.",
      };
    } else {
      state.subscriptionAuth[provider] = {
        ...(state.subscriptionAuth[provider] || { provider }),
        message: result.error.message,
      };
    }
    renderSubscriptionAuth(provider);
    return;
  }
  if (action === "disconnect") {
    const warning = provider === "openai-codex"
      ? "This signs out Ionic's dedicated Codex profile on this device. Your normal Codex CLI and IDE profile is separate. Continue?"
      : "This signs out the shared Grok Build session on this device. Continue?";
    if (!window.confirm(warning)) return;
  }
  state.subscriptionBusy.add(provider);
  setButtonBusy(button, true, action === "disconnect" ? "Signing out…" : "Connecting…");
  renderSubscriptionAuth(provider);
  let result;
  if (action === "connect-browser" || action === "connect-device") {
    const consent = currentSubscriptionConsent(provider);
    if (!consent) {
      state.subscriptionBusy.delete(provider);
      setButtonBusy(button, false);
      renderSubscriptionAuth(provider);
      return;
    }
    result = await bridgeEnvelope(window.ionic.beginSubscriptionLogin(
      provider,
      action === "connect-device" ? "device" : "browser",
      consent
    ));
    if (result.ok) {
      state.subscriptionLogin[provider] = {
        loginId: safeRuntimeText(result.data?.loginId, 200),
        userCode: safeRuntimeText(result.data?.userCode, 64),
        verificationUrl: normalizedSubscriptionVerificationUrl(provider, result.data?.verificationUrl),
      };
      scheduleSubscriptionPolling(provider, provider === "openai-codex" ? 2200 : 900);
    }
  } else if (action === "cancel-login") {
    const loginId = state.subscriptionLogin[provider]?.loginId;
    result = loginId
      ? await bridgeEnvelope(window.ionic.cancelSubscriptionLogin(provider, loginId))
      : { ok: false, error: { message: "No pending authorization to cancel." } };
    if (result.ok) delete state.subscriptionLogin[provider];
    if (result.ok) stopSubscriptionPolling(provider);
  } else if (action === "disconnect") {
    result = await bridgeEnvelope(window.ionic.logoutSubscription(provider));
    if (result.ok) {
      delete state.subscriptionLogin[provider];
      stopSubscriptionPolling(provider);
      state.subscriptionAuth[provider] = normalizedSubscriptionStatus(provider, result.data);
    }
  }
  state.subscriptionBusy.delete(provider);
  setButtonBusy(button, false);
  if (!result?.ok) {
    state.subscriptionAuth[provider] = {
      provider, available: false, connected: false, authMode: "none", planType: "", message: result?.error?.message || "Subscription action failed.",
    };
  }
  renderSubscriptionAuth(provider);
  if (result?.ok && action === "disconnect") await loadSubscriptionStatus(provider);
}

async function changeSubscriptionConsent(event) {
  const control = event.currentTarget;
  const provider = control.dataset.subscriptionConsent;
  if (!SELECTABLE_SUBSCRIPTION_RUNTIMES.has(provider)) return;
  const disclosure = state.subscriptionAuth[provider]?.disclosure;
  if (!disclosure?.version) {
    control.checked = false;
    return;
  }
  const key = subscriptionConsentSetting(provider);
  const previous = state.settings[key] || null;
  const saved = await saveSettingsPatch(
    { [key]: control.checked ? disclosure.version : null },
    {
      section: "ai",
      savedMessage: control.checked ? "Review permission saved" : "Review permission removed",
    }
  );
  if (!saved) state.settings[key] = previous;
  renderSubscriptionAuth(provider);
}

async function changeSubscriptionModel(event) {
  const control = event.currentTarget;
  const provider = control.closest("[data-subscription-config-provider]")?.dataset.subscriptionConfigProvider;
  if (!SELECTABLE_SUBSCRIPTION_RUNTIMES.has(provider)) return;
  const saved = await saveSettingsPatch(
    {
      [subscriptionModelSetting(provider)]: control.value,
      [subscriptionEffortSetting(provider)]: null,
    },
    {
      section: "ai",
      savedMessage: control.value ? "Subscription model saved" : "Runtime default saved",
    }
  );
  if (saved) renderSubscriptionAuth(provider);
}

async function changeSubscriptionEffort(event) {
  const control = event.currentTarget;
  const provider = control.closest("[data-subscription-config-provider]")?.dataset.subscriptionConfigProvider;
  if (!SELECTABLE_SUBSCRIPTION_RUNTIMES.has(provider)) return;
  await saveSettingsPatch(
    { [subscriptionEffortSetting(provider)]: control.value || null },
    {
      section: "ai",
      savedMessage: control.value ? "Reasoning effort saved" : "Model default effort saved",
    }
  );
}

async function copySubscriptionCode(provider) {
  const code = state.subscriptionLogin[provider]?.userCode;
  if (!code || typeof window.ionic?.copyText !== "function") return;
  const result = await bridgeEnvelope(window.ionic.copyText(code));
  if (!result.ok) {
    state.subscriptionAuth[provider] = {
      ...(state.subscriptionAuth[provider] || { provider, available: false, connected: false, authMode: "none", planType: "" }),
      message: result.error.message,
    };
  }
  renderSubscriptionAuth(provider);
}

function syncAnalysisControls(settings = state.settings) {
  const subscriptionMode = normalizedModelAccessMode(settings.modelAccessMode) === "subscription";
  const providerDisabled = !subscriptionMode && (settings.judgeProvider || "anthropic") === "none";
  const useLlm = providerDisabled ? false : Boolean(settings.useLlm);
  $("#setting-use-llm").checked = useLlm;
  $("#setting-transitive").checked = Boolean(settings.transitive);
  $("#setting-fail-on").value = settings.failOn || "high";
  $("#check-llm").checked = useLlm;
  $("#check-transitive").checked = Boolean(settings.transitive);
  $("#check-failon").value = settings.failOn || "high";
  $("#check-llm-access").textContent = subscriptionMode
    ? `Uses the selected ${normalizedSubscriptionRuntimeSelection(settings.subscriptionRuntime) === "xai-grok-build" ? "Grok Build" : "OpenAI Codex"} runtime`
    : "Sends contracts to your configured API provider";
}

function normalizedAppearanceTheme(theme) {
  return APPEARANCE_THEMES.has(theme) ? theme : "light";
}

function cloneCustomTheme(theme) {
  return { base: theme.base, colors: { ...theme.colors } };
}

function normalizedCustomTheme(value, fallbackBase = "light") {
  const fallback = CUSTOM_THEME_DEFAULTS[fallbackBase] || CUSTOM_THEME_DEFAULTS.light;
  if (
    !value ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    !Object.hasOwn(CUSTOM_THEME_DEFAULTS, value.base) ||
    !value.colors ||
    typeof value.colors !== "object" ||
    Array.isArray(value.colors)
  ) {
    return cloneCustomTheme(fallback);
  }
  const colors = {};
  for (const key of CUSTOM_THEME_COLOR_KEYS) {
    const color = value.colors[key];
    if (typeof color !== "string" || !/^#[0-9a-fA-F]{6}$/.test(color)) {
      return cloneCustomTheme(fallback);
    }
    colors[key] = color.toUpperCase();
  }
  return { base: value.base, colors };
}

function validatedImportedCustomTheme(value) {
  if (
    !value ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    Object.keys(value).length !== 2 ||
    !Object.hasOwn(value, "base") ||
    !Object.hasOwn(value, "colors") ||
    !Object.hasOwn(CUSTOM_THEME_DEFAULTS, value.base) ||
    !value.colors ||
    typeof value.colors !== "object" ||
    Array.isArray(value.colors)
  ) {
    return null;
  }
  const keys = Object.keys(value.colors);
  if (
    keys.length !== CUSTOM_THEME_COLOR_KEYS.length ||
    !CUSTOM_THEME_COLOR_KEYS.every((key) => keys.includes(key))
  ) {
    return null;
  }
  for (const key of CUSTOM_THEME_COLOR_KEYS) {
    if (typeof value.colors[key] !== "string" || !/^#[0-9a-fA-F]{6}$/.test(value.colors[key])) {
      return null;
    }
  }
  return normalizedCustomTheme(value);
}

function channelLuminance(channel) {
  const normalized = channel / 255;
  return normalized <= 0.04045
    ? normalized / 12.92
    : ((normalized + 0.055) / 1.055) ** 2.4;
}

function colorLuminance(hex) {
  const channels = hex.slice(1).match(/.{2}/g).map((value) => Number.parseInt(value, 16));
  return (
    0.2126 * channelLuminance(channels[0]) +
    0.7152 * channelLuminance(channels[1]) +
    0.0722 * channelLuminance(channels[2])
  );
}

function contrastRatio(first, second) {
  const one = colorLuminance(first);
  const two = colorLuminance(second);
  return (Math.max(one, two) + 0.05) / (Math.min(one, two) + 0.05);
}

function customAccentInk(accent) {
  return contrastRatio(accent, "#020A1F") >= contrastRatio(accent, "#FFFFFF")
    ? "#020A1F"
    : "#FFFFFF";
}

function setCustomThemeProperties(customTheme) {
  const root = document.documentElement;
  const normalized = normalizedCustomTheme(customTheme);
  root.dataset.themeBase = normalized.base;
  root.style.colorScheme = normalized.base === "light" ? "light" : "dark";
  for (const key of CUSTOM_THEME_COLOR_KEYS) {
    root.style.setProperty(`--${key}`, normalized.colors[key]);
  }
  root.style.setProperty("--accent-ink", customAccentInk(normalized.colors.accent));
  return normalized;
}

function clearCustomThemeProperties() {
  const root = document.documentElement;
  delete root.dataset.themeBase;
  root.style.removeProperty("color-scheme");
  for (const key of [...CUSTOM_THEME_COLOR_KEYS, "accent-ink"]) {
    root.style.removeProperty(`--${key}`);
  }
}

function applyAppearanceTheme(
  theme,
  { cache = false, customTheme = state.settings.customTheme } = {}
) {
  const normalized = normalizedAppearanceTheme(theme);
  document.documentElement.dataset.theme = normalized;
  const custom = normalizedCustomTheme(customTheme);
  if (normalized === "custom") setCustomThemeProperties(custom);
  else clearCustomThemeProperties();
  if (cache) {
    try {
      window.localStorage.setItem(APPEARANCE_CACHE_KEY, normalized);
      window.localStorage.setItem(CUSTOM_THEME_CACHE_KEY, JSON.stringify(custom));
    } catch {
      // The theme remains active for this window when storage is unavailable.
    }
  }
  return normalized;
}

function writeCustomThemeFields(customTheme) {
  const normalized = normalizedCustomTheme(customTheme);
  $("#custom-theme-base").value = normalized.base;
  CUSTOM_THEME_COLOR_KEYS.forEach((key) => {
    const field = $(`[data-custom-token="${key}"]`);
    field.classList.remove("invalid");
    field.querySelector('input[type="color"]').value = normalized.colors[key];
    field.querySelector('input[type="text"]').value = normalized.colors[key];
  });
}

function renderCustomThemeSwatch(customTheme) {
  const colors = normalizedCustomTheme(customTheme).colors;
  const option = $('.theme-option[data-theme-preview="custom"]');
  const roles = {
    canvas: colors.canvas,
    sidebar: colors.sidebar,
    surface: colors.surface,
    border: colors.border,
    text: colors.muted,
    accent: colors.accent,
  };
  Object.entries(roles).forEach(([role, color]) => {
    option.style.setProperty(`--preview-${role}`, color);
  });
}

function renderAppearanceSetting(settings = state.settings, { forceEditor = false } = {}) {
  const theme = normalizedAppearanceTheme(settings.appearanceTheme);
  const control = $(`input[name="appearance-theme"][value="${theme}"]`);
  if (control) control.checked = true;
  const custom = normalizedCustomTheme(settings.customTheme);
  renderCustomThemeSwatch(custom);
  applyAppearanceTheme(theme, { customTheme: custom });
  $("#custom-theme-editor").classList.toggle("hidden", theme !== "custom");
  if (forceEditor || !state.customThemeDirty) writeCustomThemeFields(custom);
  const save = $("#custom-theme-save");
  save.disabled = theme !== "custom";
  if (!state.customThemeDirty) {
    $("#custom-theme-validation").textContent = "Saved palette";
    $("#custom-theme-validation").className = "custom-theme-validation ready";
    save.setAttribute("aria-disabled", "true");
  }
}

function setAppearanceControlsDisabled(disabled) {
  $$('input[name="appearance-theme"]').forEach((control) => {
    control.disabled = disabled;
  });
}

async function persistAppearanceTheme(event) {
  const initiatingControl = event.currentTarget;
  const requested = normalizedAppearanceTheme(event.currentTarget.value);
  const previous = normalizedAppearanceTheme(state.settings.appearanceTheme);
  if (requested === previous) return;

  const customTheme = normalizedCustomTheme(state.settings.customTheme);
  state.customThemeDirty = false;
  applyAppearanceTheme(requested, { customTheme });
  setAppearanceControlsDisabled(true);
  const label = event.currentTarget
    .closest(".theme-option")
    .querySelector("strong")
    .textContent.trim();
  const saved = await saveSettingsPatch(
    { appearanceTheme: requested },
    { section: "appearance", savedMessage: `${label} applied` }
  );
  setAppearanceControlsDisabled(false);

  if (!saved) {
    applyAppearanceTheme(previous, { customTheme });
    renderAppearanceSetting(
      { ...state.settings, appearanceTheme: previous, customTheme },
      { forceEditor: true }
    );
    $(`input[name="appearance-theme"][value="${previous}"]`)?.focus({ preventScroll: true });
    return;
  }
  applyAppearanceTheme(saved.appearanceTheme, { cache: true, customTheme: saved.customTheme });
  renderAppearanceSetting(saved, { forceEditor: true });
  initiatingControl.focus({ preventScroll: true });
}

function readCustomThemeDraft() {
  const base = $("#custom-theme-base").value;
  const colors = {};
  let valid = Object.hasOwn(CUSTOM_THEME_DEFAULTS, base);
  CUSTOM_THEME_COLOR_KEYS.forEach((key) => {
    const field = $(`[data-custom-token="${key}"]`);
    const text = field.querySelector('input[type="text"]');
    const value = text.value.trim().toUpperCase();
    const fieldValid = /^#[0-9A-F]{6}$/.test(value);
    field.classList.toggle("invalid", !fieldValid);
    text.setAttribute("aria-invalid", String(!fieldValid));
    if (fieldValid) colors[key] = value;
    else valid = false;
  });
  return valid ? { base, colors } : null;
}

function customThemeContrastFailures(theme) {
  const failures = [];
  for (const foreground of ["text", "muted"]) {
    for (const background of ["canvas", "sidebar", "surface"]) {
      const ratio = contrastRatio(theme.colors[foreground], theme.colors[background]);
      if (ratio < 4.5) failures.push(`${foreground}/${background} ${ratio.toFixed(1)}:1`);
    }
  }
  for (const background of ["canvas", "sidebar", "surface"]) {
    const ratio = contrastRatio(theme.colors.accent, theme.colors[background]);
    if (ratio < 3) failures.push(`accent/${background} ${ratio.toFixed(1)}:1`);
  }
  for (const background of ["canvas", "sidebar", "surface"]) {
    const ratio = contrastRatio(HIGH_CONTRAST_CONTROL_FILL, theme.colors[background]);
    if (ratio < 3) failures.push(`controls/${background} ${ratio.toFixed(1)}:1`);
  }
  for (const [role, color] of Object.entries(CUSTOM_THEME_SEMANTIC_COLORS[theme.base])) {
    for (const background of ["canvas", "sidebar", "surface"]) {
      const ratio = contrastRatio(color, theme.colors[background]);
      if (ratio < 4.5) failures.push(`${role}/${background} ${ratio.toFixed(1)}:1`);
    }
  }
  return failures;
}

function setCustomThemeFeedback(message, tone = "") {
  const validation = $("#custom-theme-validation");
  validation.textContent = message;
  validation.className = `custom-theme-validation${tone ? ` ${tone}` : ""}`;
}

function previewCustomTheme({ markDirty = true } = {}) {
  if (markDirty) state.customThemeDirty = true;
  const draft = readCustomThemeDraft();
  const validation = $("#custom-theme-validation");
  const save = $("#custom-theme-save");
  if (!draft) {
    validation.textContent = "Enter complete six-digit hex values such as #26DBFF.";
    validation.className = "custom-theme-validation error";
    save.disabled = false;
    save.setAttribute("aria-disabled", "true");
    return null;
  }

  const failures = customThemeContrastFailures(draft);
  if (failures.length) {
    validation.textContent = `Increase contrast: ${failures.slice(0, 3).join(", ")}${failures.length > 3 ? "…" : ""}`;
    validation.className = "custom-theme-validation error";
    save.disabled = false;
    save.setAttribute("aria-disabled", "true");
    return null;
  }
  applyAppearanceTheme("custom", { customTheme: draft });
  renderCustomThemeSwatch(draft);
  validation.textContent = state.customThemeDirty ? "Previewing accessible unsaved changes" : "Saved palette";
  validation.className = "custom-theme-validation ready";
  save.disabled = false;
  save.setAttribute("aria-disabled", String(!state.customThemeDirty));
  return draft;
}

function loadCustomThemeBase(base) {
  const starter = CUSTOM_THEME_DEFAULTS[base] || CUSTOM_THEME_DEFAULTS.light;
  writeCustomThemeFields(starter);
  state.customThemeDirty = true;
  previewCustomTheme({ markDirty: false });
}

async function persistCustomTheme() {
  const draft = previewCustomTheme({ markDirty: false });
  if (!draft || !state.customThemeDirty) return false;
  const button = $("#custom-theme-save");
  setButtonBusy(button, true, "Saving…");
  const saved = await saveSettingsPatch(
    { appearanceTheme: "custom", customTheme: draft },
    { section: "appearance", savedMessage: "Custom theme saved" }
  );
  setButtonBusy(button, false);
  if (!saved) {
    state.customThemeDirty = true;
    previewCustomTheme({ markDirty: false });
    setCustomThemeFeedback("The theme is valid but could not be saved. Try again.", "error");
    button.focus({ preventScroll: true });
    return false;
  }
  state.customThemeDirty = false;
  applyAppearanceTheme(saved.appearanceTheme, { cache: true, customTheme: saved.customTheme });
  renderAppearanceSetting(saved, { forceEditor: true });
  setCustomThemeFeedback("Saved palette", "ready");
  button.focus({ preventScroll: true });
  return true;
}

async function importCustomTheme() {
  const button = $("#custom-theme-import");
  setButtonBusy(button, true, "Importing…");
  const request = Promise.resolve().then(() => {
    if (typeof window.ionic?.importCustomTheme !== "function") {
      throw new Error("Theme import is unavailable in this build.");
    }
    return window.ionic.importCustomTheme();
  });
  const result = await bridgeEnvelope(request);
  setButtonBusy(button, false);

  if (!result.ok) {
    setCustomThemeFeedback(result.error.message, "error");
    button.focus({ preventScroll: true });
    return false;
  }
  if (result.data?.canceled) {
    setCustomThemeFeedback("Import canceled.");
    button.focus({ preventScroll: true });
    return false;
  }

  const importedTheme = validatedImportedCustomTheme(result.data?.customTheme);
  if (!importedTheme) {
    setCustomThemeFeedback("The imported theme did not contain a valid palette.", "error");
    button.focus({ preventScroll: true });
    return false;
  }

  writeCustomThemeFields(importedTheme);
  state.customThemeDirty = true;
  if (!previewCustomTheme({ markDirty: false })) {
    button.focus({ preventScroll: true });
    return false;
  }

  const fileName = result.data?.fileName || "theme file";
  setCustomThemeFeedback(
    `Imported ${fileName}. Review the preview, then save to apply it everywhere.`,
    "ready"
  );
  button.focus({ preventScroll: true });
  return true;
}

async function exportCustomTheme() {
  const button = $("#custom-theme-export");
  const draft = previewCustomTheme({ markDirty: false });
  if (!draft) {
    button.focus({ preventScroll: true });
    return false;
  }

  setButtonBusy(button, true, "Exporting…");
  const request = Promise.resolve().then(() => {
    if (typeof window.ionic?.exportCustomTheme !== "function") {
      throw new Error("Theme export is unavailable in this build.");
    }
    return window.ionic.exportCustomTheme(draft);
  });
  const result = await bridgeEnvelope(request);
  setButtonBusy(button, false);

  if (!result.ok) {
    setCustomThemeFeedback(result.error.message, "error");
    button.focus({ preventScroll: true });
    return false;
  }
  if (result.data?.canceled) {
    setCustomThemeFeedback("Export canceled.");
    button.focus({ preventScroll: true });
    return false;
  }

  setCustomThemeFeedback(`Exported ${result.data?.fileName || "custom theme"}`, "ready");
  button.focus({ preventScroll: true });
  return true;
}

function updateProviderVisibility(provider) {
  $$('[data-provider-only]').forEach((node) => {
    const visible = node.dataset.providerOnly === provider;
    node.dataset.providerVisible = String(visible);
    node.hidden = !visible;
  });
  $$('[data-provider-except]').forEach((node) => {
    const visible = node.dataset.providerExcept !== provider;
    node.dataset.providerVisible = String(visible);
    node.hidden = !visible;
  });
  $$('[data-provider-in]').forEach((node) => {
    const visible = node.dataset.providerIn.split(/\s+/).includes(provider);
    node.dataset.providerVisible = String(visible);
    node.hidden = !visible;
  });

  const disabled = provider === "none" && normalizedModelAccessMode(state.settings.modelAccessMode) !== "subscription";
  $("#setting-use-llm").disabled = disabled;
  $("#check-llm").disabled = disabled;
  $("#setting-use-llm-help").textContent = disabled
    ? "Choose a provider to enable semantic review."
    : "Use the configured provider alongside structural analysis.";
  if (disabled) {
    $("#setting-use-llm").checked = false;
    $("#check-llm").checked = false;
  }
  if ($("#settings-filter").value.trim()) filterSettings();
}

function normalizedModelAccessMode(value) {
  return MODEL_ACCESS_MODES.has(value) ? value : "api";
}

function normalizedSubscriptionRuntimeSelection(value) {
  return SELECTABLE_SUBSCRIPTION_RUNTIMES.has(value) ? value : "openai-codex";
}

function renderModelAccessMode(settings = state.settings) {
  const mode = normalizedModelAccessMode(settings?.modelAccessMode);
  const runtime = normalizedSubscriptionRuntimeSelection(settings?.subscriptionRuntime);
  $$('input[name="model-access-mode"]').forEach((control) => {
    control.checked = control.value === mode;
  });
  $$('input[name="subscription-runtime"]').forEach((control) => {
    control.checked = control.value === runtime;
  });
  $$('[data-model-access-panel]').forEach((panel) => {
    panel.hidden = panel.dataset.modelAccessPanel !== mode;
  });
  $$('[data-subscription-config-provider]').forEach((panel) => {
    panel.hidden = mode !== "subscription" || panel.dataset.subscriptionConfigProvider !== runtime;
  });
  renderModelSubscriptionRuntime("openai-codex");
  renderModelSubscriptionRuntime("xai-grok-build");
  renderSubscriptionAuth("openai-codex");
  renderSubscriptionAuth("xai-grok-build");
  syncAnalysisControls(settings);
}

async function changeModelAccessMode(event) {
  const control = event.currentTarget;
  if (!control.checked) return;
  const mode = normalizedModelAccessMode(control.value);
  const previous = normalizedModelAccessMode(state.settings.modelAccessMode);
  if (mode === previous) return;
  renderModelAccessMode({ ...state.settings, modelAccessMode: mode });
  const saved = await saveSettingsPatch(
    { modelAccessMode: mode },
    { section: "ai", savedMessage: mode === "api" ? "API mode saved" : "Subscription mode saved" }
  );
  renderModelAccessMode(saved || { ...state.settings, modelAccessMode: previous });
}

async function changeSubscriptionRuntime(event) {
  const control = event.currentTarget;
  if (!control.checked) return;
  const runtime = normalizedSubscriptionRuntimeSelection(control.value);
  const previous = normalizedSubscriptionRuntimeSelection(state.settings.subscriptionRuntime);
  if (runtime === previous) return;
  renderModelAccessMode({ ...state.settings, subscriptionRuntime: runtime });
  const saved = await saveSettingsPatch(
    { subscriptionRuntime: runtime },
    { section: "ai", savedMessage: runtime === "openai-codex" ? "Codex selected" : "Grok Build selected" }
  );
  renderModelAccessMode(saved || { ...state.settings, subscriptionRuntime: previous });
}

function renderProviderDetails(provider) {
  const info = PROVIDERS[provider] || PROVIDERS.none;
  $("#setting-provider-icon").textContent = info.icon;
  $("#setting-provider-copy").textContent = info.description;
  const presets = $("#setting-model-presets");
  presets.replaceChildren(
    ...info.models.map((model) => {
      const option = document.createElement("option");
      option.value = model;
      return option;
    })
  );
  $("#setting-model").placeholder = info.models[0] || "Model identifier";

  if (provider !== "none") {
    const credentialName = info.credentialLabel;
    $("#credential-label").textContent =
      provider === "local" ? `${credentialName} (optional)` : credentialName;
    $("#credential-input").setAttribute("aria-label", `New ${credentialName}`);
    $("#credential-save").setAttribute("aria-label", `Save ${credentialName}`);
    $("#credential-clear").setAttribute("aria-label", `Remove saved ${credentialName}`);
  }
}

function renderRegistrySetting() {
  const configured = state.settings.registryPath;
  const active = configured || state.registryPath;
  $("#setting-registry-path").textContent = active || "Using the default registry";
  $("#setting-registry-path").title = active || "Ionic's default local registry";
  $("#setting-registry-default").classList.toggle("hidden", !configured);
}

function renderEngineSetting() {
  const engine = state.engine;
  const managed = engine?.kind === "managed" || (!state.settings.ionicBin && !engine);
  $("#setting-engine-name").textContent = managed ? "Managed engine" : "Custom engine";
  $("#setting-engine-badge").textContent = engine ? "Verified" : "Checking";
  $("#setting-engine-badge").classList.toggle("checking", !engine);
  $("#setting-engine-badge").classList.remove("error");
  const version = engine?.version ? `v${engine.version} · ` : "";
  const command = engine?.command || state.settings.ionicBin || "Included with Ionic Desktop";
  $("#setting-engine-detail").textContent = `${version}${command}`;
  $("#setting-engine-detail").title = command;
  $("#setting-engine-managed").disabled = managed && Boolean(engine);
}

function renderEngineError(message) {
  state.engine = null;
  $("#setting-engine-name").textContent = state.settings.ionicBin ? "Custom engine" : "Managed engine";
  $("#setting-engine-badge").textContent = "Unavailable";
  $("#setting-engine-badge").classList.remove("checking");
  $("#setting-engine-badge").classList.add("error");
  $("#setting-engine-detail").textContent = message || "The engine could not be verified.";
  $("#setting-engine-detail").title = message || "";
  $("#setting-engine-managed").disabled = false;
}

function renderSettingsControls({ initializeDrafts = false } = {}) {
  const settings = state.settings || {};
  const provider = settings.judgeProvider || "anthropic";
  if (initializeDrafts || !state.settingsDraftsReady) {
    for (const providerId of PROVIDER_ORDER) {
      const modelSetting = PROVIDERS[providerId].modelSetting;
      state.providerModels[providerId] =
        settings[modelSetting] ||
        (provider === providerId ? settings.judgeModel : null) ||
        state.providerModels[providerId];
    }
    state.settingsDraftsReady = true;
  }
  state.activeProvider = provider;
  $("#setting-provider").value = provider;
  $("#setting-model").value =
    provider === "none"
      ? state.providerModels.anthropic
      : state.providerModels[provider] || settings.judgeModel || "";
  $("#setting-effort").value = settings.judgeEffort || "";
  $("#setting-max-tokens").value = settings.judgeMaxTokens ?? 32000;
  $("#setting-local-url").value =
    settings.openaiCompatibleBaseUrl || "http://localhost:11434/v1";
  ["#setting-model", "#setting-max-tokens", "#setting-local-url"].forEach((selector) => {
    delete $(selector).dataset.dirty;
  });
  setFieldError("#setting-model", "#setting-model-error");
  setFieldError("#setting-max-tokens", "#setting-max-tokens-error");
  setFieldError("#setting-local-url", "#setting-local-url-error");
  renderLocalUrlWarning($("#setting-local-url").value);
  renderAppearanceSetting(settings);
  syncAnalysisControls(settings);
  renderModelAccessMode(settings);
  renderProviderDetails(provider);
  updateProviderVisibility(provider);
  renderRegistrySetting();
  renderEngineSetting();
}

function credentialEntry(provider) {
  return state.credentials?.[provider] || { configured: false, source: "none", stored: false };
}

function renderCredentialStatus(provider) {
  const entry = credentialEntry(provider);
  const secureStorage = state.credentials?.encryptionAvailable !== false;
  const status = $("#credential-status");
  const input = $("#credential-input");
  const save = $("#credential-save");
  const clear = $("#credential-clear");

  if (entry.source === "environment") {
    status.textContent = entry.stored
      ? "The environment key is active; an encrypted fallback is also saved."
      : "Provided by the environment. Remove it there before replacing it.";
  } else if (entry.source === "secure" && entry.configured) {
    status.textContent = "Saved securely on this device and used only for semantic checks.";
  } else if (!secureStorage) {
    status.textContent = "Secure credential storage is unavailable on this system.";
  } else {
    status.textContent = "No key saved.";
  }
  input.disabled = state.credentialBusy || !secureStorage || entry.source === "environment";
  save.disabled = state.credentialBusy || !secureStorage || entry.source === "environment";
  clear.disabled = state.credentialBusy || !entry.stored;
}

function setCredentialBusy(busy) {
  state.credentialBusy = busy;
  $("#setting-provider").disabled = busy;
  $("#credential-input").disabled = busy;
  $("#credential-save").disabled = busy;
  $("#credential-clear").disabled = busy;
}

function renderCredentialStatuses() {
  if (state.activeProvider !== "none") renderCredentialStatus(state.activeProvider);
}

async function loadCredentialStatus({ deferRender = false } = {}) {
  const result = await bridgeEnvelope(window.ionic.credentialStatus());
  if (!result.ok) {
    $("#credential-reset").classList.remove("hidden");
    $("#credential-status").textContent = "Credential status unavailable.";
    const message = $("#credential-message");
    message.textContent = result.error.message;
    message.className = "credential-message error";
    return false;
  }
  $("#credential-reset").classList.add("hidden");
  state.credentials = result.data;
  if (!deferRender) renderCredentialStatuses();
  return true;
}

async function resetCredentials() {
  const button = $("#credential-reset");
  setButtonBusy(button, true, "Resetting…");
  const result = await bridgeEnvelope(window.ionic.resetCredentials());
  setButtonBusy(button, false);
  if (!result.ok) {
    setSettingsSaveState("ai", result.error.message, "error", { persist: true });
    return;
  }
  state.credentials = result.data;
  $("#credential-reset").classList.add("hidden");
  const message = $("#credential-message");
  message.textContent = "Unreadable saved keys were reset";
  message.className = "credential-message saved";
  renderCredentialStatuses();
}

async function saveCredential(provider) {
  const input = $("#credential-input");
  const secret = input.value.trim();
  const message = $("#credential-message");
  const button = $("#credential-save");
  if (!secret) {
    message.textContent = "Paste a key before saving.";
    message.className = "credential-message error";
    input.focus();
    return;
  }

  setCredentialBusy(true);
  setButtonBusy(button, true, "Saving…");
  message.textContent = "Saving securely…";
  message.className = "credential-message";
  const result = await bridgeEnvelope(window.ionic.saveCredential(provider, secret));
  setButtonBusy(button, false);
  setCredentialBusy(false);
  if (!result.ok) {
    message.textContent = result.error.message;
    message.className = "credential-message error";
    renderCredentialStatuses();
    input.focus();
    return;
  }
  input.value = "";
  state.credentials = result.data;
  renderCredentialStatuses();
  message.textContent = "Key saved";
  message.className = "credential-message saved";
}

async function clearCredential(provider) {
  const button = $("#credential-clear");
  const message = $("#credential-message");
  setCredentialBusy(true);
  setButtonBusy(button, true, "Removing…");
  const result = await bridgeEnvelope(window.ionic.clearCredential(provider));
  setButtonBusy(button, false);
  setCredentialBusy(false);
  if (!result.ok) {
    message.textContent = result.error.message;
    message.className = "credential-message error";
    renderCredentialStatuses();
    return;
  }
  state.credentials = result.data;
  renderCredentialStatuses();
  message.textContent = credentialEntry(provider).source === "environment"
    ? "Saved fallback removed; the environment key remains active"
    : "Key removed";
  message.className = "credential-message saved";
}

async function changeProvider() {
  const previous = state.activeProvider;
  if ($("#credential-input").value) {
    $("#setting-provider").value = previous;
    const message = $("#credential-message");
    message.textContent = "Save or remove the pasted key before changing provider.";
    message.className = "credential-message error";
    $("#credential-input").focus();
    return;
  }
  if (previous !== "none" && $("#setting-model").dataset.dirty === "true") {
    if (!(await saveModelSetting())) {
      $("#setting-provider").value = previous;
      $("#setting-model").value = state.providerModels[previous];
      return;
    }
  }
  const provider = $("#setting-provider").value;
  const nextModel = state.providerModels[provider] || "";
  state.activeProvider = provider;
  $("#credential-message").textContent = "";
  if (provider !== "none") $("#setting-model").value = nextModel;
  renderProviderDetails(provider);
  updateProviderVisibility(provider);
  renderCredentialStatuses();

  const patch = { judgeProvider: provider };
  if (provider !== "none") patch.judgeModel = nextModel;
  if (provider === "none") patch.useLlm = false;
  const saved = await saveSettingsPatch(patch, { section: "ai" });
  if (!saved) {
    state.activeProvider = previous;
    $("#setting-provider").value = previous;
    $("#setting-model").value = state.providerModels[previous] || "";
    renderProviderDetails(previous);
    updateProviderVisibility(previous);
    renderCredentialStatuses();
    syncAnalysisControls(state.settings);
    return;
  }
  syncAnalysisControls(saved);
}

async function saveModelSetting() {
  const input = $("#setting-model");
  const model = input.value.trim();
  if (!model) {
    setFieldError("#setting-model", "#setting-model-error", "Enter a model identifier.");
    setSettingsSaveState("ai", "Enter a model identifier.", "error", { persist: true });
    input.focus();
    return false;
  }
  setFieldError("#setting-model", "#setting-model-error");
  const saved = await saveSettingsPatch({ judgeModel: model }, { section: "ai" });
  if (!saved) {
    input.value = state.providerModels[state.activeProvider] || state.settings.judgeModel || "";
    return false;
  }
  state.providerModels[state.activeProvider] = model;
  delete input.dataset.dirty;
  return true;
}

async function saveMaxTokensSetting() {
  const input = $("#setting-max-tokens");
  const value = Number(input.value);
  if (!Number.isInteger(value) || value < 256 || value > 200000) {
    setFieldError("#setting-max-tokens", "#setting-max-tokens-error", "Use 256–200,000 tokens.");
    setSettingsSaveState("ai", "Use 256–200,000 tokens.", "error", { persist: true });
    input.focus();
    return false;
  }
  setFieldError("#setting-max-tokens", "#setting-max-tokens-error");
  const saved = await saveSettingsPatch({ judgeMaxTokens: value }, { section: "ai" });
  if (saved) delete input.dataset.dirty;
  return Boolean(saved);
}

async function saveLocalUrlSetting() {
  const input = $("#setting-local-url");
  const value = input.value.trim().replace(/\/$/, "");
  try {
    const parsed = new URL(value);
    if (!new Set(["http:", "https:"]).has(parsed.protocol)) throw new Error("protocol");
    if (parsed.username || parsed.password || parsed.search || parsed.hash) throw new Error("unsafe URL");
  } catch {
    setFieldError(
      "#setting-local-url",
      "#setting-local-url-error",
      "Use HTTP(S) without credentials, a query, or a fragment."
    );
    setSettingsSaveState(
      "ai",
      "Use an HTTP(S) URL without credentials, a query, or a fragment.",
      "error",
      { persist: true }
    );
    input.focus();
    return false;
  }
  setFieldError("#setting-local-url", "#setting-local-url-error");
  input.value = value;
  renderLocalUrlWarning(value);
  const saved = await saveSettingsPatch(
    { openaiCompatibleBaseUrl: value },
    { section: "ai" }
  );
  if (saved) delete input.dataset.dirty;
  return Boolean(saved);
}

function renderLocalUrlWarning(value) {
  const warning = $("#setting-local-url-warning");
  let message = "";
  try {
    const parsed = new URL(value);
    const hostname = parsed.hostname.toLowerCase();
    const loopback =
      hostname === "localhost" ||
      hostname === "127.0.0.1" ||
      hostname.startsWith("127.") ||
      hostname === "[::1]" ||
      hostname === "::1";
    if (!loopback && parsed.protocol === "http:") {
      message = "Remote plain HTTP can expose keys and contract content in transit.";
    } else if (!loopback) {
      message = "Remote endpoint: semantic review sends compared contract content off this device.";
    }
  } catch {
    message = "Enter a valid endpoint to review its privacy posture.";
  }
  warning.textContent = message;
  warning.classList.toggle("hidden", !message);
}

async function persistAnalysisFromSettings() {
  const subscriptionMode = normalizedModelAccessMode(state.settings.modelAccessMode) === "subscription";
  const patch = {
    useLlm: !subscriptionMode && state.activeProvider === "none" ? false : $("#setting-use-llm").checked,
    failOn: $("#setting-fail-on").value,
    transitive: $("#setting-transitive").checked,
  };
  syncAnalysisControls({ ...state.settings, ...patch });
  const saved = await saveSettingsPatch(patch, { section: "analysis" });
  if (!saved) syncAnalysisControls(state.settings);
}

async function persistAnalysisFromCheck() {
  const subscriptionMode = normalizedModelAccessMode(state.settings.modelAccessMode) === "subscription";
  const patch = {
    useLlm: !subscriptionMode && state.activeProvider === "none" ? false : $("#check-llm").checked,
    failOn: $("#check-failon").value,
    transitive: $("#check-transitive").checked,
  };
  syncAnalysisControls({ ...state.settings, ...patch });
  const saved = await saveSettingsPatch(patch, { section: "analysis" });
  if (!saved) syncAnalysisControls(state.settings);
}

async function changeSettingsRegistry() {
  const picked = await bridgeEnvelope(window.ionic.pickRegistry());
  if (!picked.ok) {
    setSettingsSaveState("workspace", picked.error.message, "error", { persist: true });
    return;
  }
  if (!picked.data) return;

  const button = $("#setting-registry-change");
  const previous = state.settings.registryPath || null;
  setButtonBusy(button, true, "Opening…");
  const saved = await saveSettingsPatch({ registryPath: picked.data }, { section: "workspace" });
  if (saved) {
    const refreshed = await refreshAll();
    if (!refreshed) {
      await saveSettingsPatch({ registryPath: previous }, { section: "workspace" });
      await refreshAll();
      setSettingsSaveState("workspace", "That registry could not be opened.", "error", { persist: true });
    } else {
      clearWorkspaceResult("Registry changed. Run Scan workspace to review this registry before syncing.");
    }
  }
  renderRegistrySetting();
  setButtonBusy(button, false);
}

async function useDefaultRegistry() {
  const button = $("#setting-registry-default");
  const previous = state.settings.registryPath || null;
  if (!previous) return;
  setButtonBusy(button, true, "Restoring…");
  const saved = await saveSettingsPatch({ registryPath: null }, { section: "workspace" });
  if (saved) {
    const refreshed = await refreshAll();
    if (!refreshed) {
      await saveSettingsPatch({ registryPath: previous }, { section: "workspace" });
      await refreshAll();
      setSettingsSaveState("workspace", "The default registry could not be opened.", "error", { persist: true });
    } else {
      clearWorkspaceResult("Registry changed. Run Scan workspace to review this registry before syncing.");
    }
  }
  renderRegistrySetting();
  setButtonBusy(button, false);
}

async function chooseSettingsEngine() {
  const picked = await bridgeEnvelope(window.ionic.pickCli());
  if (!picked.ok) {
    setSettingsSaveState("engine", picked.error.message, "error", { persist: true });
    return;
  }
  if (!picked.data) return;

  const button = $("#setting-engine-custom");
  const previous = state.settings.ionicBin || null;
  const previousEngine = state.engine;
  setButtonBusy(button, true, "Verifying…");
  const saved = await saveSettingsPatch({ ionicBin: picked.data }, {
    section: "engine",
    savedMessage: "Verifying…",
  });
  if (saved) {
    const located = await bridgeEnvelope(window.ionic.locate());
    if (located.ok) {
      state.engine = located.data;
      setSettingsSaveState("engine", "Verified", "saved");
    } else {
      await saveSettingsPatch({ ionicBin: previous }, { section: "engine" });
      state.engine = previousEngine;
      setSettingsSaveState("engine", located.error.message, "error", { persist: true });
    }
  }
  setButtonBusy(button, false);
  renderEngineSetting();
}

async function useSettingsManagedEngine() {
  const button = $("#setting-engine-managed");
  setButtonBusy(button, true, "Verifying…");
  setSettingsSaveState("engine", "Restoring managed engine…", "saving", { persist: true });
  const restored = await bridgeEnvelope(window.ionic.useManagedCli());
  if (!restored.ok) {
    setSettingsSaveState("engine", restored.error.message, "error", { persist: true });
    setButtonBusy(button, false);
    return;
  }
  state.settings = { ...state.settings, ...(restored.data || {}), ionicBin: null };
  const located = await bridgeEnvelope(window.ionic.locate());
  if (!located.ok) {
    setSettingsSaveState("engine", located.error.message, "error", { persist: true });
  } else {
    state.engine = located.data;
    setSettingsSaveState("engine", "Managed engine verified", "saved");
  }
  setButtonBusy(button, false);
  renderEngineSetting();
}

async function openSettings(category = state.settingsCategory, { force = false } = {}) {
  if (!state.legal.accepted) return;
  if (state.settingsOpen && !force) {
    showSettingsCategory(category, { focus: true });
    return;
  }
  const request = ++settingsHydrationRequest;
  if (!state.settingsOpen) state.settingsReturnFocus = document.activeElement;
  showSettingsSurface();
  showSettingsCategory(category, { focus: true });
  showSettingsLoadError();
  setSettingsUnavailable(false);
  setSettingsHydrating(true);

  const settingsRequest = bridgeEnvelope(window.ionic.settings());
  const engineRequest = bridgeEnvelope(window.ionic.locate());
  const runtimesRequest = loadRuntimeDiscovery({ deferRender: true });
  const subscriptionRequests = ["openai-codex", "xai-grok-build"].map((provider) =>
    loadSubscriptionStatus(provider)
  );
  const [settingsResult, credentialsOk] = await Promise.all([
    settingsRequest,
    loadCredentialStatus({ deferRender: true }),
    runtimesRequest,
    ...subscriptionRequests,
  ]);
  if (request !== settingsHydrationRequest || !state.settingsOpen) return;
  setSettingsHydrating(false);
  if (settingsResult.ok) {
    state.settings = settingsResult.data || state.settings;
    renderSettingsControls({ initializeDrafts: !state.settingsDraftsReady });
  } else {
    showSettingsLoadError(settingsResult.error.message);
    setSettingsUnavailable(true);
  }
  if (credentialsOk) {
    renderCredentialStatuses();
  } else {
    $("#credential-input").disabled = true;
    $("#credential-save").disabled = true;
    $("#credential-clear").disabled = true;
  }
  renderSubscriptionRuntimes();

  const engineResult = await engineRequest;
  if (request !== settingsHydrationRequest || !state.settingsOpen) return;
  if (engineResult.ok) {
    state.engine = engineResult.data;
    renderEngineSetting();
  } else {
    renderEngineError(engineResult.error.message);
  }
}

async function flushSettingsDrafts() {
  const pending = [
    ["#setting-model", saveModelSetting],
    ["#setting-max-tokens", saveMaxTokensSetting],
    ["#setting-local-url", saveLocalUrlSetting],
  ];
  for (const [selector, save] of pending) {
    if ($(selector).dataset.dirty === "true" && !(await save())) return false;
  }
  await settingsSaveQueue;
  return true;
}

async function closeSettings() {
  if (!state.settingsOpen) return;
  if (!(await flushSettingsDrafts())) return;
  if (state.customThemeDirty) {
    state.customThemeDirty = false;
    renderAppearanceSetting(state.settings, { forceEditor: true });
  }
  settingsHydrationRequest += 1;
  setSettingsHydrating(false);
  const returnFocus = state.settingsReturnFocus;
  state.settingsReturnFocus = null;
  showApp();
  if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true });
}

/* ------------------------------------------------------------------ */
/* contracts                                                           */
/* ------------------------------------------------------------------ */

async function loadContracts() {
  const contracts = await call(window.ionic.list(), {
    quiet: true,
    onError: (error) => renderContractsError(error.message),
  });
  if (!contracts) return false;

  state.contracts = contracts;
  $("#contract-filter").disabled = contracts.length === 0;
  setText(
    "#contracts-count",
    contracts.length ? `${plural(contracts.length, "contract")} registered` : "Registry is empty"
  );

  renderContractList();
  populateCheckContracts();

  const selectedStillExists = contracts.some((contract) => contract.id === state.selected);
  if (selectedStillExists) selectContract(state.selected, { focus: false });
  else if (contracts.length) selectContract(contracts[0].id, { focus: false });
  else {
    state.selected = null;
    renderContractDetail(null);
  }
  return true;
}

function renderContractsError(message) {
  state.contracts = [];
  state.selected = null;
  $("#contract-filter").disabled = true;
  setText("#contracts-count", "Registry unavailable");
  $("#contract-list").replaceChildren(el("li", "rail-message", "Contract list unavailable."));
  $("#contract-detail").replaceChildren(
    surfaceError("Could not read this registry", message, refreshAll)
  );
  populateCheckContracts();
}

function filteredContracts() {
  const query = $("#contract-filter").value.trim().toLowerCase();
  if (!query) return state.contracts;
  return state.contracts.filter((contract) => {
    const haystack = [contract.id, contract.name, contract.description, ...(contract.tags || [])]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return haystack.includes(query);
  });
}

function renderContractList() {
  const list = $("#contract-list");
  list.replaceChildren();
  const contracts = filteredContracts();

  if (!state.contracts.length) {
    const li = el("li");
    li.append(
      emptyState(
        "No contracts registered",
        "Register a folder containing AGENTS.md or CLAUDE.md to establish a baseline.",
        { action: "Register folder…", actionClass: "empty-register" }
      )
    );
    list.append(li);
    return;
  }

  if (!contracts.length) {
    const li = el("li");
    li.append(emptyState("No matches", "Try a different contract name, id, or tag."));
    list.append(li);
    return;
  }

  for (const contract of contracts) {
    const li = el("li");
    const button = el("button", "contract-row");
    button.type = "button";
    button.dataset.id = contract.id;
    button.setAttribute("aria-current", String(contract.id === state.selected));
    button.append(el("span", "cid", contract.id), el("span", "version", `v${contract.version}`));

    const dependencies = contract.depends_on?.length || 0;
    button.append(
      el(
        "span",
        "cmeta",
        `${plural(contract.tools.length, "tool")} \u00b7 ${plural(contract.outputs.length, "output")} \u00b7 ` +
          `${plural(contract.constraints.length, "constraint")}${dependencies ? ` \u00b7 ${plural(dependencies, "dependency", "dependencies")}` : ""}`
      )
    );
    button.addEventListener("click", () => selectContract(contract.id));
    button.addEventListener("keydown", handleContractKeydown);
    li.append(button);
    list.append(li);
  }
}

function handleContractKeydown(event) {
  if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
  event.preventDefault();
  const rows = $$("#contract-list .contract-row");
  const current = rows.indexOf(event.currentTarget);
  let next = current;
  if (event.key === "ArrowDown") next = Math.min(rows.length - 1, current + 1);
  if (event.key === "ArrowUp") next = Math.max(0, current - 1);
  if (event.key === "Home") next = 0;
  if (event.key === "End") next = rows.length - 1;
  rows[next]?.focus();
  if (rows[next]) selectContract(rows[next].dataset.id, { focus: false });
}

function selectContract(contractId, { focus = false } = {}) {
  state.selected = contractId;
  $$("#contract-list .contract-row").forEach((button) =>
    button.setAttribute("aria-current", String(button.dataset.id === contractId))
  );
  renderContractDetail(state.contracts.find((contract) => contract.id === contractId));
  if (focus) $("#contract-detail h2")?.focus();
}

function renderContractDetail(contract) {
  const detail = $("#contract-detail");
  detail.replaceChildren();
  if (!contract) {
    const empty = emptyState(
      state.contracts.length ? "No contract selected" : "Contract details will appear here",
      state.contracts.length
        ? "Choose a contract to inspect its behavioral promises."
        : "Once a contract is registered, its inputs, outputs, tools, and dependencies will be shown here."
    );
    empty.classList.add("compact");
    detail.append(empty);
    return;
  }

  const head = el("header", "detail-head");
  const identity = el("div");
  const title = el("h2", null, contract.name || contract.id);
  title.tabIndex = -1;
  identity.append(
    title,
    el("div", "subtitle", `${contract.id} \u00b7 v${contract.version} \u00b7 ${contract.fingerprint}`)
  );
  head.append(identity);

  if (contract.source) {
    const reveal = el("button", "ghost compact-action", "Show source");
    reveal.type = "button";
    reveal.addEventListener("click", async () => {
      const shown = await call(window.ionic.reveal(contract.source));
      if (shown) toast("Source revealed in the file manager.", "success", 3200);
    });
    head.append(reveal);
  }
  detail.append(head);

  if (contract.description) detail.append(section("purpose", [el("p", null, contract.description)], true));
  if (contract.identity) detail.append(section("identity", [el("p", null, contract.identity)], true));

  if (contract.inputs?.length) {
    detail.append(section("inputs", contract.inputs.map(ioRow)));
  }
  if (contract.outputs?.length) {
    detail.append(section("outputs", contract.outputs.map(ioRow)));
  }
  if (contract.tools?.length) {
    detail.append(
      section(
        "tools",
        contract.tools.map((tool) => {
          const li = el("li");
          li.append(el("span", "tag fmt", tool.name));
          if (tool.description) li.append(document.createTextNode(` ${tool.description}`));
          if (!tool.required) li.append(el("span", "tag", "optional"));
          return li;
        })
      )
    );
  }
  if (contract.capabilities?.length) {
    detail.append(section("capabilities", contract.capabilities.map((value) => el("li", null, value))));
  }
  if (contract.constraints?.length) {
    detail.append(
      section(
        "constraints",
        contract.constraints.map((constraint) => {
          const li = el("li");
          li.append(el("span", "tag", constraint.id));
          li.append(document.createTextNode(` ${constraint.statement}`));
          return li;
        })
      )
    );
  }
  if (contract.persona_rules?.length) {
    detail.append(section("persona rules", contract.persona_rules.map((value) => el("li", null, value))));
  }
  if (contract.depends_on?.length) {
    detail.append(section("depends on", contract.depends_on.map(dependencyRow)));
  }

  if (contract.source) {
    const source = el("section");
    source.append(el("h3", null, "source"));
    const row = el("div", "source-row");
    const code = el("code", null, contract.source);
    code.title = contract.source;
    row.append(code);
    source.append(row);
    detail.append(source);
  }
}

function ioRow(spec) {
  const li = el("li");
  li.append(el("span", "tag fmt", spec.name), el("span", "tag", spec.format));
  if (spec.required === false) li.append(el("span", "tag", "optional"));
  if (spec.description) li.append(document.createTextNode(` ${spec.description}`));
  if (spec.json_schema) li.append(el("span", "tag", "schema"));
  return li;
}

function dependencyRow(dependency) {
  const li = el("li");
  li.append(el("span", "tag fmt", dependency.contract_id));
  const needs = [];
  if (dependency.requires_tools?.length) needs.push(`tools: ${dependency.requires_tools.join(", ")}`);
  if (dependency.requires_capabilities?.length) {
    needs.push(`capabilities: ${dependency.requires_capabilities.join(", ")}`);
  }
  if (dependency.expects_outputs?.length) needs.push(`outputs: ${dependency.expects_outputs.join(", ")}`);
  if (dependency.expects_format) needs.push(`format: ${dependency.expects_format}`);
  if (dependency.requires_constraints?.length) {
    needs.push(`constraints: ${dependency.requires_constraints.join(", ")}`);
  }
  if (needs.length) li.append(el("span", "dim", ` ${needs.join(" \u00b7 ")}`));
  return li;
}

function section(label, children, prose = false) {
  const wrapper = el("section");
  wrapper.append(el("h3", null, label));
  if (prose) children.forEach((child) => wrapper.append(child));
  else {
    const list = el("ul");
    children.forEach((child) => list.append(child));
    wrapper.append(list);
  }
  return wrapper;
}

/* ------------------------------------------------------------------ */
/* graph                                                               */
/* ------------------------------------------------------------------ */

async function renderGraph() {
  const requestId = ++state.graphRequest;
  const canvas = $("#graph-canvas");
  const refresh = $("#graph-refresh");
  canvas.setAttribute("aria-busy", "true");
  refresh.disabled = true;

  const graph = await call(window.ionic.graph(null), {
    quiet: true,
    onError: (error) => {
      if (requestId !== state.graphRequest) return;
      canvas.replaceChildren(surfaceError("Could not load the dependency graph", error.message, renderGraph));
      setText("#graph-legend", "Graph unavailable");
    },
  });

  if (requestId !== state.graphRequest) return;
  canvas.removeAttribute("aria-busy");
  refresh.disabled = false;
  if (!graph) return;

  canvas.replaceChildren();
  setText("#graph-legend", "");
  setText("#graph-help", "Focus a node or dependency to hear its details.");

  if (!graph.nodes.length) {
    canvas.append(
      emptyState(
        "No contracts registered",
        "Register a folder to map how your agents depend on one another.",
        { action: "Register folder…", actionClass: "empty-register" }
      )
    );
    return;
  }

  const layers = layerNodes(graph);
  const NODE_W = 178;
  const NODE_H = 52;
  const GAP_X = 82;
  const GAP_Y = 26;
  const lastLayer = layers.length - 1;
  const positions = new Map();

  layers.forEach((layer, depth) => {
    layer.forEach((id, index) => {
      positions.set(id, {
        x: 26 + (lastLayer - depth) * (NODE_W + GAP_X),
        y: 30 + index * (NODE_H + GAP_Y),
      });
    });
  });

  const diagramWidth = 52 + layers.length * NODE_W + lastLayer * GAP_X;
  const diagramHeight = 60 + Math.max(...layers.map((layer) => layer.length)) * (NODE_H + GAP_Y);
  const width = Math.max(diagramWidth, canvas.clientWidth - 2);
  const height = Math.max(diagramHeight, 310);

  const svg = svgEl("svg");
  svg.setAttribute("width", String(width));
  svg.setAttribute("height", String(height));
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("role", "group");
  svg.setAttribute("aria-label", `${plural(graph.nodes.length, "contract")} connected by ${plural(graph.edges.length, "dependency", "dependencies")}`);

  const defs = svgEl("defs");
  const marker = svgEl("marker");
  marker.setAttribute("id", "arrow");
  marker.setAttribute("viewBox", "0 0 10 10");
  marker.setAttribute("refX", "9");
  marker.setAttribute("refY", "5");
  marker.setAttribute("markerWidth", "6");
  marker.setAttribute("markerHeight", "6");
  marker.setAttribute("orient", "auto");
  const arrowHead = svgEl("path");
  arrowHead.setAttribute("d", "M 0 0 L 10 5 L 0 10 z");
  arrowHead.setAttribute("class", "arrow-head");
  marker.append(arrowHead);
  defs.append(marker);
  svg.append(defs);

  for (const edge of graph.edges) {
    const from = positions.get(edge.source);
    const to = positions.get(edge.target);
    if (!from || !to) continue;

    const x1 = from.x + NODE_W;
    const y1 = from.y + NODE_H / 2;
    const x2 = to.x;
    const y2 = to.y + NODE_H / 2;
    const mid = (x1 + x2) / 2;
    const path = svgEl("path");
    path.setAttribute("d", `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`);
    path.setAttribute("class", `edge ${edge.resolved ? "resolved" : "unresolved"}`);
    path.setAttribute("marker-end", "url(#arrow)");

    const description = edgeDescription(edge);
    path.setAttribute("tabindex", "0");
    path.setAttribute("role", "img");
    path.setAttribute("aria-label", description);
    path.addEventListener("focus", () => setText("#graph-help", description));
    path.addEventListener("mouseenter", () => setText("#graph-help", description));
    path.addEventListener("blur", resetGraphHelp);
    path.addEventListener("mouseleave", resetGraphHelp);
    svg.append(path);
  }

  for (const node of graph.nodes) {
    const position = positions.get(node.id);
    if (!position) continue;
    const group = svgEl("g");
    group.setAttribute("class", "node-group");
    group.setAttribute("tabindex", "0");
    group.setAttribute("role", "button");
    group.setAttribute("aria-label", `${node.id}, version ${node.version}. Open contract details.`);

    const rect = svgEl("rect");
    rect.setAttribute("x", String(position.x));
    rect.setAttribute("y", String(position.y));
    rect.setAttribute("width", String(NODE_W));
    rect.setAttribute("height", String(NODE_H));
    rect.setAttribute("rx", "7");
    rect.setAttribute("class", "node-box");
    group.append(rect);

    const label = svgEl("text");
    label.setAttribute("x", String(position.x + 14));
    label.setAttribute("y", String(position.y + 23));
    label.setAttribute("class", "node-label");
    label.textContent = node.id.length > 23 ? `${node.id.slice(0, 22)}\u2026` : node.id;
    group.append(label);

    const version = svgEl("text");
    version.setAttribute("x", String(position.x + 14));
    version.setAttribute("y", String(position.y + 41));
    version.setAttribute("class", "node-version");
    version.textContent = `v${node.version}`;
    group.append(version);

    const open = () => {
      showView("contracts");
      selectContract(node.id, { focus: true });
    };
    group.addEventListener("click", open);
    group.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        open();
      }
    });
    svg.append(group);
  }

  canvas.append(svg);
  const unresolved = graph.edges.filter((edge) => !edge.resolved).length;
  setText(
    "#graph-legend",
    unresolved
      ? `${plural(graph.nodes.length, "contract")} \u00b7 ${plural(graph.edges.length, "dependency", "dependencies")} \u00b7 ${plural(unresolved, "unresolved")}`
      : `${plural(graph.nodes.length, "contract")} \u00b7 ${plural(graph.edges.length, "dependency", "dependencies")} \u00b7 all resolved`
  );
}

function svgEl(tag) {
  return document.createElementNS("http://www.w3.org/2000/svg", tag);
}

function edgeDescription(edge) {
  if (!edge.resolved) return `${edge.source} depends on ${edge.target}, which is not registered.`;
  const needs = [];
  if (edge.requires_tools?.length) needs.push(`tools ${edge.requires_tools.join(", ")}`);
  if (edge.requires_capabilities?.length) needs.push(`capabilities ${edge.requires_capabilities.join(", ")}`);
  if (edge.expects_outputs?.length) needs.push(`outputs ${edge.expects_outputs.join(", ")}`);
  return `${edge.source} needs from ${edge.target}: ${needs.join("; ") || "no exact requirement declared"}.`;
}

function resetGraphHelp() {
  setText("#graph-help", "Focus a node or dependency to hear its details.");
}

function layerNodes(graph) {
  const ids = graph.nodes.map((node) => node.id);
  const outgoing = new Map(ids.map((id) => [id, []]));
  for (const edge of graph.edges) {
    if (outgoing.has(edge.source) && outgoing.has(edge.target)) outgoing.get(edge.source).push(edge.target);
  }

  const depth = new Map();
  const visiting = new Set();
  function resolve(id, guard = 0) {
    if (depth.has(id)) return depth.get(id);
    if (visiting.has(id) || guard > ids.length) return 0;
    visiting.add(id);
    const children = outgoing.get(id) || [];
    const value = children.length
      ? Math.max(...children.map((child) => resolve(child, guard + 1) + 1))
      : 0;
    visiting.delete(id);
    depth.set(id, value);
    return value;
  }

  ids.forEach((id) => resolve(id));
  const maxDepth = Math.max(0, ...depth.values());
  const layers = Array.from({ length: maxDepth + 1 }, () => []);
  ids.forEach((id) => layers[depth.get(id) ?? 0].push(id));
  return layers.filter((layer) => layer.length);
}

/* ------------------------------------------------------------------ */
/* check                                                               */
/* ------------------------------------------------------------------ */

function populateCheckContracts() {
  const select = $("#check-contract");
  const previous = select.value;
  select.replaceChildren();

  if (!state.contracts.length) {
    const option = el("option", null, "Register a contract first");
    option.value = "";
    select.append(option);
    select.disabled = true;
    $("#check-run").disabled = true;
    return;
  }

  select.disabled = false;
  $("#check-run").disabled = false;
  for (const contract of state.contracts) {
    const option = el("option", null, `${contract.id}  (v${contract.version})`);
    option.value = contract.id;
    select.append(option);
  }
  if (previous && state.contracts.some((contract) => contract.id === previous)) select.value = previous;
  else if (state.selected) select.value = state.selected;
}

async function runCheck(event) {
  event.preventDefault();
  const request = {
    contractId: $("#check-contract").value,
    against: $("#check-against").value || null,
    useLlm: $("#check-llm").checked,
    failOn: $("#check-failon").value,
    transitive: $("#check-transitive").checked,
    modelAccessMode: normalizedModelAccessMode(state.settings.modelAccessMode),
    subscriptionRuntime: normalizedSubscriptionRuntimeSelection(state.settings.subscriptionRuntime),
  };
  if (!request.contractId) {
    toast("Register and choose a contract before running a check.", "error");
    return;
  }

  setCheckBusy(true, request.useLlm ? "Running structural and semantic review…" : "Running structural analysis…");
  $("#check-result").replaceChildren();
  $("#check-result").setAttribute("aria-busy", "true");

  const report = await call(window.ionic.check(request), {
    quiet: true,
    onError: (error) => renderCheckError(error.message),
  });

  $("#check-result").removeAttribute("aria-busy");
  setCheckBusy(false);
  if (report) renderReport(report);

  const saved = await call(
    window.ionic.saveSettings({
      useLlm: request.useLlm,
      failOn: request.failOn,
      transitive: request.transitive,
    }),
    { quiet: true }
  );
  if (saved) {
    state.settings = saved;
    syncAnalysisControls(saved);
  }
}

function setCheckBusy(busy, message = "") {
  const button = $("#check-run");
  button.disabled = busy || state.contracts.length === 0;
  button.setAttribute("aria-busy", String(busy));
  $("#check-form").setAttribute("aria-busy", String(busy));
  $("#check-run .button-label").textContent = busy ? "Checking…" : "Run check";
  $("#check-run .spinner").classList.toggle("hidden", !busy);
  setText("#check-progress", message);
}

function renderCheckError(message) {
  const result = $("#check-result");
  result.replaceChildren(surfaceError("The check could not finish", message, () => $("#check-form").requestSubmit()));
  result.querySelector(".surface-error")?.setAttribute("tabindex", "-1");
  result.querySelector(".surface-error")?.focus();
}

function renderReport(report) {
  const container = $("#check-result");
  container.replaceChildren();
  const approved = report.verdict === "APPROVED";
  const banner = el("div", `verdict ${approved ? "approved" : "changes"}`);
  const heading = el("h2", null, report.verdict);
  heading.tabIndex = -1;
  banner.append(
    heading,
    el("div", "meta", `${report.contract_id}  v${report.from_version} \u2192 v${report.to_version}`),
    el(
      "div",
      "meta",
      `dependents: ${report.dependents_checked.length ? report.dependents_checked.join(", ") : "none registered"}`
    )
  );
  container.append(banner);

  if (report.assessment) {
    const note = el("div", "finding info");
    note.append(el("p", null, report.assessment));
    container.append(note);
  }

  const counts = {};
  for (const finding of report.findings) counts[finding.severity] = (counts[finding.severity] || 0) + 1;
  const chips = el("div", "chips");
  for (const severity of SEVERITIES) {
    if (counts[severity]) chips.append(el("span", `sev ${severity}`, `${counts[severity]} ${severity}`));
  }
  if (!report.findings.length) chips.append(el("span", "dim", "No findings"));
  container.append(chips);

  const threshold = SEVERITY_RANK[report.fail_on] ?? 3;
  const sorted = [...report.findings].sort(
    (left, right) => SEVERITY_RANK[right.severity] - SEVERITY_RANK[left.severity]
  );
  const blocking = sorted.filter((finding) => SEVERITY_RANK[finding.severity] >= threshold);
  const observations = sorted.filter((finding) => SEVERITY_RANK[finding.severity] < threshold);

  if (blocking.length) {
    container.append(el("div", "section-label", `Blocking (${blocking.length})`));
    blocking.forEach((finding) => container.append(findingCard(finding)));
  }
  if (observations.length) {
    container.append(el("div", "section-label", `Other observations (${observations.length})`));
    observations.forEach((finding) => container.append(findingCard(finding)));
  }

  const judge = report.judge || {};
  container.append(
    el(
      "p",
      "report-footnote dim",
      judge.enabled
        ? `Semantic review: ${judge.provider} ${judge.model}`
        : judge.error
          ? `Semantic review skipped: ${judge.error}`
          : "Structural analysis only."
    )
  );
  heading.focus({ preventScroll: false });
}

function findingCard(finding) {
  const card = el("article", `finding ${finding.severity}`);
  const head = el("div", "finding-head");
  head.append(el("span", `sev ${finding.severity}`, finding.severity));
  head.append(el("span", "finding-title", finding.summary));
  if (finding.affected_contract) head.append(el("span", "affects", `\u2192 ${finding.affected_contract}`));
  head.append(el("span", "kind", `${finding.kind} \u00b7 ${finding.origin}`));
  card.append(head);

  if (finding.detail) card.append(el("p", null, finding.detail));
  if (finding.evidence?.length) {
    const evidence = el("ul", "evidence");
    finding.evidence.forEach((item) => evidence.append(el("li", null, item)));
    card.append(evidence);
  }
  if (finding.recommendation) {
    const fix = el("div", "fix");
    fix.append(el("strong", null, "Fix"), document.createTextNode(finding.recommendation));
    card.append(fix);
  }
  return card;
}

/* ------------------------------------------------------------------ */
/* status + actions                                                    */
/* ------------------------------------------------------------------ */

async function loadStatus() {
  const status = await call(window.ionic.status(), {
    quiet: true,
    onError: (error) => {
      state.registryPath = null;
      setText("#status-registry", "Registry unavailable");
      $("#status-registry").disabled = true;
      setText("#status-judge", "");
      setText("#status-version", "");
      toast(error.message, "error");
    },
  });
  if (!status) return false;

  state.registryPath = status.registry.path;
  const registry = $("#status-registry");
  registry.textContent = status.registry.path;
  registry.title = `Show ${status.registry.path} in the file manager`;
  registry.disabled = false;
  setText("#status-judge", status.analysis.description);
  renderProductIdentity(status.desktop);
  const productName = status.desktop.productName || $("#status-version").dataset.productName || "Ionic Desktop";
  setText("#status-version", `${productName} v${status.desktop.version}`);
  renderRegistrySetting();
  return true;
}

async function registerContracts() {
  const directory = await call(window.ionic.pickDirectory());
  if (!directory) return;
  const button = $("#btn-register");
  setButtonBusy(button, true, "Registering…");
  const output = await call(window.ionic.register(directory));
  if (output !== null) {
    await refreshAll();
    toast(`Registered contracts from ${directory}`, "success");
  }
  setButtonBusy(button, false);
}

async function openRegistry() {
  const picked = await call(window.ionic.pickRegistry());
  if (!picked) return;
  const previous = state.settings.registryPath || null;
  const button = $("#btn-registry");
  setButtonBusy(button, true, "Opening…");

  const saved = await call(window.ionic.saveSettings({ registryPath: picked }));
  if (!saved) {
    setButtonBusy(button, false);
    return;
  }
  state.settings = saved;
  const refreshed = await refreshAll();
  if (!refreshed) {
    const restored = await call(window.ionic.saveSettings({ registryPath: previous }), { quiet: true });
    if (restored) state.settings = restored;
    await refreshAll();
    toast("That registry could not be opened. The previous workspace was restored.", "error");
  } else {
    clearWorkspaceResult("Registry changed. Run Scan workspace to review this registry before syncing.");
    toast(`Opened registry ${picked}`, "success");
  }
  setButtonBusy(button, false);
}

async function refreshAll() {
  const statusOk = await loadStatus();
  const contractsOk = await loadContracts();
  if (state.view === "graph") await renderGraph();
  return statusOk && contractsOk;
}

/* ------------------------------------------------------------------ */
/* boot                                                                */
/* ------------------------------------------------------------------ */

async function boot() {
  showBoot();
  const located = await call(window.ionic.locate(), {
    quiet: true,
    onError: (error) => showSetup([], error.message),
  });
  if (!located) return;
  state.engine = located;

  state.settings = (await call(window.ionic.settings(), { quiet: true })) || {};
  applyAppearanceTheme(state.settings.appearanceTheme, {
    cache: true,
    customTheme: state.settings.customTheme,
  });
  renderSettingsControls({ initializeDrafts: !state.settingsDraftsReady });
  showApp();
  const refreshed = await refreshAll();
  if (refreshed) await runLaunchStructuralScan();
}

document.addEventListener("DOMContentLoaded", () => {
  renderProductIdentity(window.ionic?.product);
  initializePaneResizers();
  state.repositories = readWorkspaceRepositories();
  renderWorkspace();

  // The legal status check is deliberately the first bridge call at launch.
  const legalInitialization = initializeLegal();

  $$(".nav-item").forEach((button) =>
    button.addEventListener("click", () => showView(button.dataset.view))
  );

  $("#btn-register").addEventListener("click", registerContracts);
  $("#btn-registry").addEventListener("click", openRegistry);
  $("#btn-settings").addEventListener("click", () => openSettings());
  $("#settings-back").addEventListener("click", () => void closeSettings());
  $("#settings-retry").addEventListener("click", () =>
    void openSettings(state.settingsCategory, { force: true })
  );
  $("#settings-filter").addEventListener("input", filterSettings);
  $$(".settings-nav-item").forEach((button) => {
    button.addEventListener("click", () => showSettingsCategory(button.dataset.settingsCategory));
  });
  $$("[data-subscription-action]").forEach((button) => {
    button.addEventListener("click", () => {
      const root = button.closest("[data-subscription-provider]");
      const provider = root?.dataset.subscriptionProvider;
      if (!provider) return;
      if (button.dataset.subscriptionAction === "copy-code") {
        void copySubscriptionCode(provider);
      } else {
        void runSubscriptionAction(provider, button.dataset.subscriptionAction, button);
      }
    });
  });
  $$('input[name="appearance-theme"]').forEach((control) => {
    control.addEventListener("change", persistAppearanceTheme);
  });
  $("#custom-theme-base").addEventListener("change", (event) => {
    loadCustomThemeBase(event.currentTarget.value);
  });
  $("#custom-theme-reset").addEventListener("click", () => {
    loadCustomThemeBase($("#custom-theme-base").value);
  });
  $("#custom-theme-import").addEventListener("click", () => void importCustomTheme());
  $("#custom-theme-export").addEventListener("click", () => void exportCustomTheme());
  $("#custom-theme-save").addEventListener("click", () => void persistCustomTheme());
  $$("[data-custom-token]").forEach((field) => {
    const picker = field.querySelector('input[type="color"]');
    const text = field.querySelector('input[type="text"]');
    picker.addEventListener("input", () => {
      text.value = picker.value.toUpperCase();
      previewCustomTheme();
    });
    text.addEventListener("input", () => {
      const value = text.value.trim();
      if (/^#[0-9a-fA-F]{6}$/.test(value)) picker.value = value;
      previewCustomTheme();
    });
  });
  $("#setting-provider").addEventListener("change", changeProvider);
  $$('input[name="model-access-mode"]').forEach((control) => {
    control.addEventListener("change", changeModelAccessMode);
  });
  $$('input[name="subscription-runtime"]').forEach((control) => {
    control.addEventListener("change", changeSubscriptionRuntime);
  });
  $$('[data-subscription-consent]').forEach((control) => {
    control.addEventListener("change", changeSubscriptionConsent);
  });
  $$('[data-subscription-field="model"]').forEach((control) => {
    control.addEventListener("change", changeSubscriptionModel);
  });
  $$('[data-subscription-field="effort"]').forEach((control) => {
    control.addEventListener("change", changeSubscriptionEffort);
  });
  $("#setting-model").addEventListener("input", () => {
    $("#setting-model").dataset.dirty = "true";
    setFieldError("#setting-model", "#setting-model-error");
    setSettingsSaveState("ai", "Unsaved changes", "", { persist: true });
  });
  $("#setting-model").addEventListener("change", () => {
    if ($("#setting-model").dataset.dirty === "true") void saveModelSetting();
  });
  $("#setting-effort").addEventListener("change", () =>
    saveSettingsPatch({ judgeEffort: $("#setting-effort").value || null }, { section: "ai" })
  );
  $("#setting-max-tokens").addEventListener("input", () => {
    $("#setting-max-tokens").dataset.dirty = "true";
    setFieldError("#setting-max-tokens", "#setting-max-tokens-error");
    setSettingsSaveState("ai", "Unsaved changes", "", { persist: true });
  });
  $("#setting-max-tokens").addEventListener("change", () => {
    if ($("#setting-max-tokens").dataset.dirty === "true") void saveMaxTokensSetting();
  });
  $("#setting-local-url").addEventListener("input", () =>
    {
      $("#setting-local-url").dataset.dirty = "true";
      setFieldError("#setting-local-url", "#setting-local-url-error");
      setSettingsSaveState("ai", "Unsaved changes", "", { persist: true });
      renderLocalUrlWarning($("#setting-local-url").value);
    }
  );
  $("#setting-local-url").addEventListener("change", () => {
    if ($("#setting-local-url").dataset.dirty === "true") void saveLocalUrlSetting();
  });
  $("#credential-save").addEventListener("click", () => saveCredential(state.activeProvider));
  $("#credential-clear").addEventListener("click", () => clearCredential(state.activeProvider));
  $("#credential-reset").addEventListener("click", resetCredentials);
  $("#credential-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      saveCredential(state.activeProvider);
    }
  });
  $("#setting-use-llm").addEventListener("change", persistAnalysisFromSettings);
  $("#setting-fail-on").addEventListener("change", persistAnalysisFromSettings);
  $("#setting-transitive").addEventListener("change", persistAnalysisFromSettings);
  $("#setting-registry-change").addEventListener("click", changeSettingsRegistry);
  $("#setting-registry-default").addEventListener("click", useDefaultRegistry);
  $("#setting-engine-custom").addEventListener("click", chooseSettingsEngine);
  $("#setting-engine-managed").addEventListener("click", useSettingsManagedEngine);
  $$(".settings-legal-open").forEach((button) => {
    button.addEventListener("click", () =>
      openLegalDocument(button.dataset.legalDocument, { required: !state.legal.accepted })
    );
  });
  $("#setup-retry").addEventListener("click", boot);
  $("#setup-managed").addEventListener("click", useManagedCli);
  $("#setup-choose").addEventListener("click", chooseCli);
  $("#legal-close").addEventListener("click", closeLegalDocuments);
  $("#legal-retry").addEventListener("click", initializeLegal);
  $("#legal-accept").addEventListener("click", acceptLegal);
  $("#legal-decline").addEventListener("click", declineLegal);
  $("#legal-agree").addEventListener("change", (event) => {
    $("#legal-accept").disabled = !event.currentTarget.checked;
  });
  $("#open-source-filter").addEventListener("input", (event) => {
    state.legal.licenses.query = event.currentTarget.value;
    renderOpenSourceLicenses();
  });
  $("#open-source-refresh").addEventListener("click", () =>
    void loadOpenSourceLicenses({ force: true })
  );
  $$(".legal-tab").forEach((button) => {
    button.addEventListener("click", () => loadLegalDocument(button.dataset.legalDocument, {
      focusDocument: true,
    }));
  });
  $("#toast-dismiss").addEventListener("click", hideToast);
  $("#graph-refresh").addEventListener("click", renderGraph);
  $("#repository-add").addEventListener("click", addWorkspaceRepositories);
  $("#repository-filter").addEventListener("input", renderRepositoryList);
  $("#workspace-scan").addEventListener("click", scanWorkspace);
  $("#workspace-sync").addEventListener("click", planWorkspaceSync);
  $("#workspace-sync-cancel").addEventListener("click", () => {
    hideWorkspaceSyncReview();
    $("#workspace-sync").focus({ preventScroll: true });
  });
  $("#workspace-sync-apply").addEventListener("click", applyWorkspaceSync);
  $("#check-form").addEventListener("submit", runCheck);
  $("#check-llm").addEventListener("change", persistAnalysisFromCheck);
  $("#check-transitive").addEventListener("change", persistAnalysisFromCheck);
  $("#check-failon").addEventListener("change", persistAnalysisFromCheck);
  $("#contract-filter").addEventListener("input", renderContractList);

  $("#check-browse").addEventListener("click", async () => {
    const file = await call(window.ionic.pickFile());
    if (file) $("#check-against").value = file;
  });
  $("#check-clear").addEventListener("click", () => {
    $("#check-against").value = "";
    $("#check-against").focus();
  });

  $("#status-registry").addEventListener("click", async () => {
    if (state.registryPath) await call(window.ionic.reveal(state.registryPath));
  });

  document.addEventListener("click", (event) => {
    if (event.target.closest(".empty-register")) registerContracts();
    if (event.target.closest(".empty-add-repositories")) addWorkspaceRepositories();
  });
  document.addEventListener("keydown", (event) => {
    handleLegalKeydown(event);
    if (!$("#legal").classList.contains("hidden")) return;
    if (event.key === "," && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      if (!state.settingsOpen) openSettings();
      return;
    }
    if (state.settingsOpen) {
      if (event.key === "Escape") {
        if ($("#settings-filter").value) {
          $("#settings-filter").value = "";
          filterSettings();
          $("#settings-filter").focus();
        } else {
          closeSettings();
        }
      }
      return;
    }
    if (event.key === "Escape") hideToast();
    if (
      event.key === "/" &&
      state.view === "contracts" &&
      !event.ctrlKey &&
      !event.metaKey &&
      !event.altKey &&
      !["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement?.tagName)
    ) {
      event.preventDefault();
      $("#contract-filter").focus();
    }
  });

  window.ionic.onMenu("menu:register", registerContracts);
  window.ionic.onMenu("menu:scan-workspace", () => {
    showView("repositories");
    scanWorkspace();
  });
  window.ionic.onMenu("menu:open-registry", openRegistry);
  window.ionic.onMenu("menu:choose-cli", chooseCli);
  window.ionic.onMenu("menu:use-managed-cli", useManagedCli);
  window.ionic.onMenu("menu:refresh", refreshAll);
  window.ionic.onMenu("menu:settings", () => openSettings());
  window.ionic.onMenu("menu:show-eula", () =>
    openLegalDocument("eula", { required: !state.legal.accepted })
  );
  window.ionic.onMenu("menu:show-mit", () =>
    openLegalDocument("mit", { required: !state.legal.accepted })
  );
  window.ionic.onMenu("menu:show-third-party", () =>
    openLegalDocument("third-party", { required: !state.legal.accepted })
  );

  void legalInitialization;
});
