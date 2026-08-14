import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const {
  SUBSCRIPTION_AUTHORIZATION_HOSTS,
  isAllowedExternalUrl,
  sanitizeSubscriptionAuthorizationUrl,
} = require("../src/external-url-policy.js");

describe("central external authorization URL policy", () => {
  it("aligns every supported Grok account and verification host", () => {
    assert.deepEqual(
      [...SUBSCRIPTION_AUTHORIZATION_HOSTS["xai-grok-build"]],
      ["accounts.x.ai", "auth.x.ai", "grok.com", "www.grok.com"]
    );
    for (const host of SUBSCRIPTION_AUTHORIZATION_HOSTS["xai-grok-build"]) {
      const raw = `https://${host}/device?state=secret#fragment`;
      assert.equal(isAllowedExternalUrl(raw), true);
      assert.equal(
        sanitizeSubscriptionAuthorizationUrl("xai-grok-build", raw, {
          stripQueryAndHash: true,
        }),
        `https://${host}/device`
      );
    }
  });

  it("rejects lookalikes, credentials, insecure schemes, and unapproved ports", () => {
    for (const raw of [
      "https://accounts.x.ai.evil.example/device",
      "https://user@grok.com/device",
      "http://auth.x.ai/device",
      "https://grok.com:444/device",
    ]) {
      assert.equal(isAllowedExternalUrl(raw), false);
      assert.equal(sanitizeSubscriptionAuthorizationUrl("xai-grok-build", raw), "");
    }
  });
});
