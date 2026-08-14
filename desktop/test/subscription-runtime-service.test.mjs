import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { describe, it } from "node:test";

const require = createRequire(import.meta.url);
const { createSubscriptionRuntimeService } = require("../src/subscription-runtime-service.js");
const { subscriptionDisclosure } = require("../src/subscription-consent.js");

function fakeRuntime() {
  return {
    status: async ({ probeAuthentication }) => ({ installed: true, probeAuthentication }),
    models: async () => ({ models: ["grok-code-fast-1"] }),
    beginLogin: async (mode) => ({ state: "awaiting_user", mode }),
    pollLogin: async (loginId) => ({ state: "connected", loginId }),
    cancelLogin: async (loginId) => loginId === "pending",
    logout: async () => ({ connected: false }),
  };
}

describe("Essential subscription runtime service", () => {
  it("delegates consented model and login actions without storing credentials", async () => {
    const runtime = fakeRuntime();
    const service = createSubscriptionRuntimeService({
      services: { "xai-grok-build": runtime },
    });
    const disclosure = subscriptionDisclosure("xai-grok-build");
    const consent = {
      accepted: true,
      provider: disclosure.provider,
      version: disclosure.version,
    };

    assert.deepEqual(await service.models("xai-grok-build", consent), {
      models: ["grok-code-fast-1"],
    });
    assert.equal((await service.beginLogin("xai-grok-build", "browser", consent)).mode, "browser");
    assert.equal((await service.pollLogin("xai-grok-build", "pending")).state, "connected");
    assert.equal(await service.cancelLogin("xai-grok-build", "pending"), true);
    assert.deepEqual(await service.logout("xai-grok-build"), { connected: false });
  });

  it("rejects unconsented and unsupported subscription providers", async () => {
    const service = createSubscriptionRuntimeService({
      services: { "xai-grok-build": fakeRuntime() },
    });
    await assert.rejects(() => service.models("xai-grok-build", null), /disclosure|accept/i);
    await assert.rejects(() => service.status("anthropic"), /not supported/i);
  });
});
