import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SOURCE = fs.readFileSync(path.resolve(HERE, "..", "src", "preload.js"), "utf8");
const BRAND_DARK = {
  base: "dark",
  colors: {
    canvas: "#020a1f",
    sidebar: "#061126",
    surface: "#0b172b",
    border: "#243552",
    text: "#f4f8fc",
    muted: "#9aa9bc",
    accent: "#26dbff",
  },
};

function expose(args) {
  let api = null;
  const electron = {
    contextBridge: {
      exposeInMainWorld(name, value) {
        assert.equal(name, "ionic");
        api = value;
      },
    },
    ipcRenderer: {
      invoke() {},
      on() {},
      removeListener() {},
    },
  };
  vm.runInNewContext(SOURCE, {
    process: { argv: args },
    URL,
    require(name) {
      assert.equal(name, "electron");
      return electron;
    },
  });
  return api;
}

function customArgument(value) {
  return `--ionic-custom-theme=${encodeURIComponent(JSON.stringify(value))}`;
}

describe("appearance preload payload", () => {
  it("exposes only the immutable Essential product identity", () => {
    const exact = expose([
      "electron",
      "--ionic-edition=essential",
      "--ionic-product-name=Ionic%20Essential",
    ]);
    assert.equal(exact.product.edition, "essential");
    assert.equal(exact.product.productName, "Ionic Essential");

    const spoofed = expose([
      "electron",
      "--ionic-edition=other",
      "--ionic-product-name=Other%20Product",
    ]);
    assert.equal(spoofed.product.edition, "essential");
    assert.equal(spoofed.product.productName, "Ionic Essential");
  });

  it("exposes a validated custom theme from the main-process arguments", () => {
    const api = expose(["electron", "--ionic-appearance-theme=custom", customArgument(BRAND_DARK)]);

    assert.equal(api.initialAppearanceTheme, "custom");
    assert.equal(api.initialCustomTheme.base, "dark");
    assert.equal(api.initialCustomTheme.colors.canvas, "#020A1F");
    assert.equal(api.initialCustomTheme.colors.accent, "#26DBFF");
  });

  it("falls back safely when the custom theme argument is malformed", () => {
    const invalidTheme = {
      ...BRAND_DARK,
      colors: { ...BRAND_DARK.colors, accent: "cyan" },
    };
    const api = expose([
      "electron",
      "--ionic-appearance-theme=custom",
      customArgument(invalidTheme),
    ]);

    assert.equal(api.initialAppearanceTheme, "custom");
    assert.equal(api.initialCustomTheme.base, "light");
    assert.equal(api.initialCustomTheme.colors.canvas, "#F8F8F6");
    assert.equal(api.initialCustomTheme.colors.accent, "#006D82");
  });

  it("rejects unknown appearance modes independently of a valid palette", () => {
    const api = expose(["electron", "--ionic-appearance-theme=system", customArgument(BRAND_DARK)]);

    assert.equal(api.initialAppearanceTheme, "light");
    assert.equal(api.initialCustomTheme.base, "dark");
  });

  it("exposes narrow native theme file operations", () => {
    const calls = [];
    let api = null;
    vm.runInNewContext(SOURCE, {
      process: { argv: [] },
      URL,
      require(name) {
        assert.equal(name, "electron");
        return {
          contextBridge: { exposeInMainWorld(_name, value) { api = value; } },
          ipcRenderer: {
            invoke(...args) { calls.push(args); },
            on() {},
            removeListener() {},
          },
        };
      },
    });

    api.importCustomTheme();
    api.exportCustomTheme(BRAND_DARK);
    assert.deepEqual(calls[0], ["appearance:custom-theme:import"]);
    assert.equal(calls[1][0], "appearance:custom-theme:export");
    assert.equal(calls[1][1].base, "dark");
  });
});
