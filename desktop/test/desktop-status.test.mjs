import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { describe, it } from "node:test";

const require = createRequire(import.meta.url);
const { composeDesktopStatus } = require("../src/desktop-status.js");

const CLI_STATUS = {
  version: "0.1.0",
  judge: {
    provider: "anthropic",
    model: "claude-opus-5",
    description: "anthropic claude-opus-5",
  },
};

describe("desktop status contract", () => {
  it("reports structural review when semantic analysis is switched off", () => {
    const status = composeDesktopStatus(
      CLI_STATUS,
      { useLlm: false, judgeProvider: "anthropic", judgeModel: "claude-opus-5" },
      "0.2.1"
    );

    assert.deepEqual(status.analysis, {
      mode: "structural",
      description: "Structural review",
    });
    assert.equal(status.desktop.version, "0.2.1");
    assert.equal(status.desktop.edition, "essential");
    assert.equal(status.desktop.productName, "Ionic Desktop");
    assert.equal(status.engine.version, "0.1.0");
    assert.equal(status.version, "0.1.0");
  });

  it("names the configured model only when semantic review is active", () => {
    const status = composeDesktopStatus(
      CLI_STATUS,
      { useLlm: true, judgeProvider: "anthropic", judgeModel: "claude-sonnet-custom" },
      "0.2.2"
    );

    assert.deepEqual(status.analysis, {
      mode: "semantic",
      description: "Semantic review · Anthropic claude-sonnet-custom",
    });
  });

  it("labels every configured provider without falling back to Anthropic", () => {
    const providers = {
      openai: "OpenAI",
      google: "Google Gemini",
      xai: "SpaceXAI · Grok",
      local: "OpenAI-compatible",
    };
    for (const [provider, label] of Object.entries(providers)) {
      const status = composeDesktopStatus(
        CLI_STATUS,
        { useLlm: true, judgeProvider: provider, judgeModel: "configured-model" },
        "0.3.0"
      );
      assert.equal(status.analysis.description, `Semantic review · ${label} configured-model`);
      assert.doesNotMatch(status.analysis.description, /Anthropic/);
    }
  });

  it("keeps provider none in structural mode even if a stale toggle is true", () => {
    const status = composeDesktopStatus(
      CLI_STATUS,
      { useLlm: true, judgeProvider: "none", judgeModel: "ignored" },
      "0.2.1"
    );

    assert.equal(status.analysis.mode, "structural");
    assert.equal(status.analysis.description, "Structural review");
  });

  it("rejects missing package versions instead of inventing them", () => {
    assert.throws(() => composeDesktopStatus({}, {}, "0.2.1"), /cliStatus\.version/);
    assert.throws(() => composeDesktopStatus(CLI_STATUS, {}, ""), /desktop\.version/);
  });

  it("rejects a desktop identity from another edition", () => {
    assert.throws(
      () => composeDesktopStatus(CLI_STATUS, { useLlm: false }, {
        version: "0.6.2",
        edition: "other",
        productName: "Other Product",
      }),
      /desktop\.edition must be essential/
    );
  });

  it("labels subscription semantic review with the selected runtime", () => {
    const status = composeDesktopStatus(
      { version: "0.4.0" },
      {
        useLlm: true,
        modelAccessMode: "subscription",
        subscriptionRuntime: "xai-grok-build",
        judgeProvider: "anthropic",
        judgeModel: "claude-sonnet-5",
      },
      { version: "0.6.2", edition: "essential", productName: "Ionic Essential" }
    );
    assert.equal(status.analysis.mode, "semantic");
    assert.match(status.analysis.description, /Grok Build/);
    assert.doesNotMatch(status.analysis.description, /Anthropic|Claude/);
  });
});
