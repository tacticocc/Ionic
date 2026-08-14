"use strict";

const CONSENT_VERSION = "2026-08-14.3";

const DISCLOSURES = Object.freeze({
  "openai-codex": Object.freeze({
    provider: "openai-codex",
    vendor: "OpenAI",
    product: "ChatGPT / Codex",
    version: CONSENT_VERSION,
    heading: "Allow Ionic to use your ChatGPT / Codex subscription?",
    purpose: "Optional semantic compatibility reviews that you start in Ionic.",
    authentication:
      "OpenAI's official local Codex app-server opens and owns sign-in, tokens, refresh, and logout inside a dedicated Ionic Codex profile. Ionic never receives your password, browser cookies, OAuth token, or API key.",
    sends: Object.freeze([
      "The compared contract text and proposed changes",
      "Relevant dependency context and structural findings",
      "Ionic's review instructions and the required JSON result schema",
    ]),
    localBoundary: Object.freeze([
      "Ionic does not pass a repository or workspace path; each review uses an ephemeral app-server thread in a new temporary folder",
      "The dedicated Codex profile is separate from your normal Codex profile; Ionic refuses config, AGENTS.md, rules, user-added skills, plugins, hooks, or memories while allowing only Codex's own built-in system-skill directory",
      "Before sending contract text, Ionic checks the installed version's official protocol schema for restricted read-only roots and requires Codex to report zero loaded instruction sources",
      "The turn can read only the temporary review folder plus Codex's required platform defaults. Tool-network access is disabled; the app-server itself still connects to OpenAI to perform the review",
      "All command, file-change, MCP, and permission approvals are declined. Any tool item aborts the turn and Ionic discards its output",
      "The output is constrained by Ionic's JSON schema, conversation history is non-persistent, and older app-server versions fail closed instead of receiving a weaker boundary",
      "The official app-server may keep OAuth, installation, local diagnostic, and other runtime state inside this dedicated profile; Ionic does not read those files",
    ]),
    timing:
      "Linking does not enable semantic review. Content leaves the device only when you explicitly run a semantic review.",
  }),
  "xai-grok-build": Object.freeze({
    provider: "xai-grok-build",
    vendor: "xAI",
    product: "Grok Build",
    version: CONSENT_VERSION,
    heading: "Allow Ionic to use your Grok Build subscription?",
    purpose: "Optional semantic compatibility reviews that you start in Ionic.",
    authentication:
      "The official local Grok Build CLI opens and owns sign-in, its cached session, refresh, and logout. Ionic never receives your password, browser cookies, or OAuth token.",
    sends: Object.freeze([
      "The compared contract text and proposed changes",
      "Relevant dependency context and structural findings",
      "Ionic's review instructions and the required JSON result schema",
    ]),
    localBoundary: Object.freeze([
      "Ionic does not pass a repository or workspace path; execution starts in an empty temporary folder",
      "The ACP client advertises no filesystem or terminal capability and supplies no MCP servers",
      "The empty working folder prevents project-level discovery; Grok may still apply user- or administrator-managed runtime configuration",
      "The official Grok CLI may retain its own local session/configuration data under its profile directory",
    ]),
    timing:
      "Linking does not enable semantic review. Content leaves the device only when you explicitly run a semantic review.",
  }),
});

function subscriptionDisclosure(provider) {
  const disclosure = DISCLOSURES[provider];
  if (!disclosure) throw new TypeError("This subscription provider is not supported");
  return {
    ...disclosure,
    sends: [...disclosure.sends],
    localBoundary: [...disclosure.localBoundary],
  };
}

function requireSubscriptionConsent(provider, consent) {
  const disclosure = subscriptionDisclosure(provider);
  if (
    !consent ||
    typeof consent !== "object" ||
    Array.isArray(consent) ||
    consent.accepted !== true ||
    consent.provider !== provider ||
    consent.version !== disclosure.version
  ) {
    const error = new Error(
      `Review and accept the current ${disclosure.product} data-access disclosure before sign-in`
    );
    error.code = "SUBSCRIPTION_CONSENT_REQUIRED";
    throw error;
  }
  return disclosure;
}

module.exports = {
  CONSENT_VERSION,
  requireSubscriptionConsent,
  subscriptionDisclosure,
};
