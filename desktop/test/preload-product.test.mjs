import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SOURCE = fs.readFileSync(path.resolve(HERE, "..", "src", "preload.js"), "utf8");

function load(argv) {
  let api;
  vm.runInNewContext(SOURCE, {
    process: { argv },
    decodeURIComponent,
    URL,
    require(name) {
      assert.equal(name, "electron");
      return {
        contextBridge: { exposeInMainWorld(_name, value) { api = value; } },
        ipcRenderer: { invoke() {}, on() {}, removeListener() {} },
      };
    },
  });
  return api;
}

describe("Essential preload identity", () => {
  it("remains loadable inside Electron's restricted sandbox", () => {
    const imports = [...SOURCE.matchAll(/\brequire\((['"])([^'"]+)\1\)/g)]
      .map((match) => match[2]);
    assert.deepEqual(imports, ["electron"]);
    assert.ok(load([]));
  });

  it("exposes validated Essential product metadata", () => {
    const api = load([
      "--ionic-edition=essential",
      `--ionic-product-name=${encodeURIComponent("Ionic Essential")}`,
    ]);
    assert.equal(api.product.edition, "essential");
    assert.equal(api.product.productName, "Ionic Essential");
  });

  it("fails closed when metadata is malformed or names another edition", () => {
    for (const argv of [
      [],
      ["--ionic-edition=other", "--ionic-product-name=Other%20Product"],
      ["--ionic-edition=essential", "--ionic-product-name=%E0%A4%A"],
    ]) {
      const api = load(argv);
      assert.equal(api.product.edition, "essential");
      assert.equal(api.product.productName, "Ionic Essential");
    }
  });

  it("sanitizes subscription verification URLs without a local module import", () => {
    const api = load([]);
    assert.equal(
      api.safeSubscriptionVerificationUrl(
        "xai-grok-build",
        "https://accounts.x.ai/device?state=secret#fragment"
      ),
      "https://accounts.x.ai/device"
    );
    assert.equal(
      api.safeSubscriptionVerificationUrl(
        "openai-codex",
        "https://chatgpt.com/device?state=kept#fragment"
      ),
      "https://chatgpt.com/device?state=kept#fragment"
    );
    assert.equal(
      api.safeSubscriptionVerificationUrl(
        "xai-grok-build",
        "https://accounts.x.ai.evil.example/device"
      ),
      ""
    );
  });
});
