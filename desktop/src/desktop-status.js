"use strict";

/**
 * Add desktop-owned runtime facts to the CLI status handshake.
 *
 * The CLI version and the Electron app version are separate release surfaces.
 * Keep the CLI's original `version` field for protocol compatibility while
 * exposing both versions with unambiguous names to the renderer.
 */
function composeDesktopStatus(cliStatus, settings, desktop) {
  if (!cliStatus || typeof cliStatus !== "object" || Array.isArray(cliStatus)) {
    throw new TypeError("cliStatus must be an object");
  }
  if (typeof cliStatus.version !== "string" || !cliStatus.version.trim()) {
    throw new TypeError("cliStatus.version must be a non-empty string");
  }
  const desktopVersion = typeof desktop === "string" ? desktop : desktop?.version;
  const edition = typeof desktop === "object" ? desktop?.edition : "essential";
  const productName = typeof desktop === "object" ? desktop?.productName : "Ionic Desktop";
  if (typeof desktopVersion !== "string" || !desktopVersion.trim()) {
    throw new TypeError("desktop.version must be a non-empty string");
  }
  if (edition !== "essential") {
    throw new TypeError("desktop.edition must be essential");
  }
  if (typeof productName !== "string" || !productName.trim()) {
    throw new TypeError("desktop.productName must be a non-empty string");
  }

  const accessMode = settings?.modelAccessMode === "subscription" ? "subscription" : "api";
  const provider = settings?.judgeProvider || "none";
  const subscriptionRuntime = settings?.subscriptionRuntime === "xai-grok-build"
    ? "xai-grok-build"
    : "openai-codex";
  const semanticEnabled = settings?.useLlm === true &&
    (accessMode === "subscription" || provider !== "none");
  const model = typeof settings?.judgeModel === "string" ? settings.judgeModel.trim() : "";
  const providerLabel = {
    anthropic: "Anthropic",
    openai: "OpenAI",
    google: "Google Gemini",
    xai: "SpaceXAI · Grok",
    local: "OpenAI-compatible",
  }[provider] || "";
  const semanticDetail = accessMode === "subscription"
    ? subscriptionRuntime === "xai-grok-build" ? "Grok Build" : "OpenAI Codex"
    : [providerLabel, model].filter(Boolean).join(" ");

  return {
    ...cliStatus,
    analysis: {
      mode: semanticEnabled ? "semantic" : "structural",
      description: semanticEnabled
        ? `Semantic review${semanticDetail ? ` · ${semanticDetail}` : ""}`
        : "Structural review",
    },
    desktop: {
      version: desktopVersion.trim(),
      edition,
      productName: productName.trim(),
    },
    engine: { version: cliStatus.version.trim() },
  };
}

module.exports = { composeDesktopStatus };
