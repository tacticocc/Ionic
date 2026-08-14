"use strict";

/**
 * Single authorization-URL policy shared by the main process, preload bridge,
 * and provider runtime adapters. Renderer code delegates back to this policy
 * instead of maintaining a second hostname list.
 */

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
const GITHUB_AUTHORIZATION_HOSTS = Object.freeze([
  "github.com",
  "www.github.com",
]);
const ALL_EXTERNAL_HOSTS = new Set([
  ...GITHUB_AUTHORIZATION_HOSTS,
  ...Object.values(SUBSCRIPTION_AUTHORIZATION_HOSTS).flat(),
]);

function trustedHttpsUrl(raw, allowedHosts) {
  if (typeof raw !== "string" || !raw) return null;
  try {
    const url = new URL(raw);
    if (
      url.protocol !== "https:"
      || url.username
      || url.password
      || (url.port && url.port !== "443")
      || !allowedHosts.has(url.hostname.toLowerCase())
    ) {
      return null;
    }
    return url;
  } catch {
    return null;
  }
}

function sanitizeSubscriptionAuthorizationUrl(
  provider,
  raw,
  { stripQueryAndHash = false } = {}
) {
  const hosts = SUBSCRIPTION_AUTHORIZATION_HOSTS[provider];
  if (!hosts) return "";
  const url = trustedHttpsUrl(raw, new Set(hosts));
  if (!url) return "";
  if (stripQueryAndHash) {
    url.search = "";
    url.hash = "";
  }
  return url.toString();
}

function isAllowedExternalUrl(raw) {
  return Boolean(trustedHttpsUrl(raw, ALL_EXTERNAL_HOSTS));
}

module.exports = {
  GITHUB_AUTHORIZATION_HOSTS,
  SUBSCRIPTION_AUTHORIZATION_HOSTS,
  isAllowedExternalUrl,
  sanitizeSubscriptionAuthorizationUrl,
};
