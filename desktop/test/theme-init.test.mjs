import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SOURCE = fs.readFileSync(
  path.resolve(HERE, "..", "src", "renderer", "theme-init.js"),
  "utf8"
);
const CACHE_KEY = "ionic.appearanceTheme";
const CUSTOM_CACHE_KEY = "ionic.customTheme";
const SESSION_KEY = "ionic.appearanceTheme.initialized";
const LIGHT_CUSTOM_THEME = {
  base: "light",
  colors: {
    canvas: "#F8F8F6",
    sidebar: "#F3F4F2",
    surface: "#FFFFFF",
    border: "#D8DDDE",
    text: "#111820",
    muted: "#5E6A72",
    accent: "#006D82",
  },
};
const DARK_CUSTOM_THEME = {
  base: "dark",
  colors: {
    canvas: "#111418",
    sidebar: "#0D1014",
    surface: "#181C22",
    border: "#303842",
    text: "#F4F7FA",
    muted: "#929DA8",
    accent: "#26DBFF",
  },
};

function storage(entries = {}, { failReads = false, failWrites = false } = {}) {
  const values = new Map(Object.entries(entries));
  return {
    getItem(key) {
      if (failReads) throw new Error("storage read failed");
      return values.has(key) ? values.get(key) : null;
    },
    setItem(key, value) {
      if (failWrites) throw new Error("storage write failed");
      values.set(key, String(value));
    },
    value(key) {
      return values.get(key);
    },
  };
}

function runThemeInit({
  initialAppearanceTheme,
  initialCustomTheme,
  cachedTheme,
  cachedCustomTheme,
  initialized = false,
  localOptions,
  sessionOptions,
} = {}) {
  const localEntries = {};
  if (cachedTheme !== undefined) localEntries[CACHE_KEY] = cachedTheme;
  if (cachedCustomTheme !== undefined) {
    localEntries[CUSTOM_CACHE_KEY] =
      typeof cachedCustomTheme === "string"
        ? cachedCustomTheme
        : JSON.stringify(cachedCustomTheme);
  }
  const localStorage = storage(localEntries, localOptions);
  const sessionStorage = storage(
    initialized ? { [SESSION_KEY]: "true" } : {},
    sessionOptions
  );
  const properties = new Map();
  const style = {
    colorScheme: "",
    setProperty(name, value) {
      properties.set(name, value);
    },
    value(name) {
      return properties.get(name);
    },
  };
  const document = { documentElement: { dataset: {}, style } };
  const window = {
    ionic: { initialAppearanceTheme, initialCustomTheme },
    localStorage,
    sessionStorage,
  };

  vm.runInNewContext(SOURCE, { document, window });
  return { document, localStorage, sessionStorage };
}

describe("early appearance bootstrap", () => {
  it("uses the main-process theme for a fresh window and repairs a stale cache", () => {
    const result = runThemeInit({
      initialAppearanceTheme: "dark",
      cachedTheme: "oled",
    });

    assert.equal(result.document.documentElement.dataset.theme, "dark");
    assert.equal(result.localStorage.value(CACHE_KEY), "dark");
    assert.equal(result.sessionStorage.value(SESSION_KEY), "true");
  });

  it("uses the successfully saved cache on a same-window reload", () => {
    const result = runThemeInit({
      initialAppearanceTheme: "dark",
      cachedTheme: "oled",
      initialized: true,
    });

    assert.equal(result.document.documentElement.dataset.theme, "oled");
    assert.equal(result.localStorage.value(CACHE_KEY), "oled");
  });

  it("falls back to light when both preload and cached values are invalid", () => {
    const result = runThemeInit({
      initialAppearanceTheme: "system",
      cachedTheme: "blue",
      initialized: true,
    });

    assert.equal(result.document.documentElement.dataset.theme, "light");
    assert.equal(result.localStorage.value(CACHE_KEY), "light");
  });

  it("still applies the validated preload theme when storage is unavailable", () => {
    const result = runThemeInit({
      initialAppearanceTheme: "oled",
      localOptions: { failReads: true, failWrites: true },
      sessionOptions: { failReads: true, failWrites: true },
    });

    assert.equal(result.document.documentElement.dataset.theme, "oled");
  });

  it("applies a validated custom palette before styles load and caches it", () => {
    const result = runThemeInit({
      initialAppearanceTheme: "custom",
      initialCustomTheme: DARK_CUSTOM_THEME,
    });
    const root = result.document.documentElement;

    assert.equal(root.dataset.theme, "custom");
    assert.equal(root.dataset.themeBase, "dark");
    assert.equal(root.style.colorScheme, "dark");
    for (const [key, color] of Object.entries(DARK_CUSTOM_THEME.colors)) {
      assert.equal(root.style.value(`--${key}`), color);
    }
    assert.deepEqual(
      JSON.parse(result.localStorage.value(CUSTOM_CACHE_KEY)),
      DARK_CUSTOM_THEME
    );
  });

  it("uses the cached custom palette only on same-window reloads", () => {
    const fresh = runThemeInit({
      initialAppearanceTheme: "custom",
      initialCustomTheme: LIGHT_CUSTOM_THEME,
      cachedTheme: "custom",
      cachedCustomTheme: DARK_CUSTOM_THEME,
    });
    assert.equal(fresh.document.documentElement.dataset.themeBase, "light");

    const reload = runThemeInit({
      initialAppearanceTheme: "custom",
      initialCustomTheme: LIGHT_CUSTOM_THEME,
      cachedTheme: "custom",
      cachedCustomTheme: DARK_CUSTOM_THEME,
      initialized: true,
    });
    assert.equal(reload.document.documentElement.dataset.themeBase, "dark");
    assert.equal(reload.document.documentElement.style.value("--accent"), "#26DBFF");
  });

  it("rejects malformed cached custom data without losing the main-process palette", () => {
    const result = runThemeInit({
      initialAppearanceTheme: "custom",
      initialCustomTheme: LIGHT_CUSTOM_THEME,
      cachedTheme: "custom",
      cachedCustomTheme: {
        ...DARK_CUSTOM_THEME,
        colors: { ...DARK_CUSTOM_THEME.colors, accent: "cyan" },
      },
      initialized: true,
    });

    assert.equal(result.document.documentElement.dataset.themeBase, "light");
    assert.equal(result.document.documentElement.style.value("--accent"), "#006D82");
  });
});
