import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { describe, it } from "node:test";

const require = createRequire(import.meta.url);

const {
  CONSENT_VERSION,
  requireSubscriptionConsent,
  subscriptionDisclosure,
} = require("../src/subscription-consent.js");

describe("subscription disclosures", () => {
  it("describes the fail-closed Codex app-server boundary", () => {
    const disclosure = subscriptionDisclosure("openai-codex");
    const boundary = disclosure.localBoundary.join("\n");

    assert.equal(CONSENT_VERSION, "2026-08-14.3");
    assert.equal(disclosure.version, CONSENT_VERSION);
    assert.match(boundary, /dedicated Codex profile.*separate/);
    assert.match(boundary, /user-added skills.*built-in system-skill/);
    assert.match(boundary, /restricted read-only roots/);
    assert.match(boundary, /zero loaded instruction sources/);
    assert.match(boundary, /Tool-network access is disabled/);
    assert.match(boundary, /approvals are declined/);
    assert.match(boundary, /Any tool item aborts/);
    assert.match(boundary, /older app-server versions fail closed/);
    assert.doesNotMatch(boundary, /may still apply its user-level configuration/);
  });

  it("invalidates consent captured before the hardened disclosure", () => {
    assert.throws(
      () =>
        requireSubscriptionConsent("openai-codex", {
          accepted: true,
          provider: "openai-codex",
          version: "2026-08-14.2",
        }),
      /Review and accept/
    );
  });
});
