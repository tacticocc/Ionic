"use strict";

const {
  requireSubscriptionConsent,
  subscriptionDisclosure,
} = require("./subscription-consent");

const SUPPORTED_RUNTIMES = new Set(["openai-codex", "xai-grok-build"]);

/**
 * Coordinate the official subscription runtimes without owning provider
 * credentials. Essential delegates authentication to Codex or Grok Build and
 * keeps only the user's explicit model, effort, and consent preferences.
 */
function createSubscriptionRuntimeService({ services = {} } = {}) {
  function serviceFor(provider) {
    if (!SUPPORTED_RUNTIMES.has(provider)) {
      throw new Error("This subscription provider is not supported");
    }
    const service = services?.[provider];
    if (!service) throw new Error("This subscription provider is unavailable in this build");
    return service;
  }

  async function status(provider, { probeAuthentication = false } = {}) {
    return {
      ...(await serviceFor(provider).status({ probeAuthentication })),
      disclosure: subscriptionDisclosure(provider),
    };
  }

  async function models(provider, consent) {
    requireSubscriptionConsent(provider, consent);
    const service = serviceFor(provider);
    if (typeof service.models !== "function") {
      throw new Error("This subscription runtime does not expose model discovery");
    }
    return service.models();
  }

  async function beginLogin(provider, mode, consent) {
    requireSubscriptionConsent(provider, consent);
    return serviceFor(provider).beginLogin(mode);
  }

  async function pollLogin(provider, loginId) {
    const service = serviceFor(provider);
    if (typeof service.pollLogin !== "function") {
      return { provider, loginId, state: "awaiting_user" };
    }
    return service.pollLogin(loginId);
  }

  async function cancelLogin(provider, loginId) {
    return serviceFor(provider).cancelLogin(loginId);
  }

  async function logout(provider) {
    return serviceFor(provider).logout();
  }

  function close() {
    for (const service of Object.values(services || {})) service?.close?.();
  }

  return { status, models, beginLogin, pollLogin, cancelLogin, logout, close };
}

module.exports = { createSubscriptionRuntimeService, SUPPORTED_RUNTIMES };
