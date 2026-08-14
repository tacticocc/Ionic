"use strict";

/*
 * Apply the persisted appearance before the main stylesheet is evaluated.
 *
 * The main-process argument is authoritative for a newly created window. A
 * successfully saved renderer choice is cached for reloads because Electron's
 * BrowserWindow additionalArguments cannot change after window creation.
 */
(() => {
  const themes = new Set(["light", "dark", "oled", "custom"]);
  const customBases = new Set(["light", "dark", "oled"]);
  const customColorKeys = [
    "canvas",
    "sidebar",
    "surface",
    "border",
    "text",
    "muted",
    "accent",
  ];
  const defaultCustomTheme = {
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
  const cacheKey = "ionic.appearanceTheme";
  const customCacheKey = "ionic.customTheme";
  const sessionKey = "ionic.appearanceTheme.initialized";
  const initial = themes.has(window.ionic?.initialAppearanceTheme)
    ? window.ionic.initialAppearanceTheme
    : "light";
  const validateCustomTheme = (value) => {
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    if (
      Object.keys(value).length !== 2 ||
      !Object.hasOwn(value, "base") ||
      !Object.hasOwn(value, "colors") ||
      !customBases.has(value.base) ||
      !value.colors ||
      typeof value.colors !== "object" ||
      Array.isArray(value.colors)
    ) {
      return null;
    }
    const keys = Object.keys(value.colors);
    if (
      keys.length !== customColorKeys.length ||
      !customColorKeys.every((key) => keys.includes(key))
    ) {
      return null;
    }
    const colors = {};
    for (const key of customColorKeys) {
      const color = value.colors[key];
      if (typeof color !== "string" || !/^#[0-9a-fA-F]{6}$/.test(color)) return null;
      colors[key] = color.toUpperCase();
    }
    return { base: value.base, colors };
  };
  let theme = initial;
  let customTheme =
    validateCustomTheme(window.ionic?.initialCustomTheme) || defaultCustomTheme;

  try {
    const isReload = window.sessionStorage.getItem(sessionKey) === "true";
    const cached = isReload ? window.localStorage.getItem(cacheKey) : null;
    if (themes.has(cached)) theme = cached;
    if (isReload) {
      const cachedCustomTheme = validateCustomTheme(
        JSON.parse(window.localStorage.getItem(customCacheKey) || "null")
      );
      if (cachedCustomTheme) customTheme = cachedCustomTheme;
    }
  } catch {
    // Storage may be blocked or unavailable. The validated preload value still
    // gives this document a deterministic first-paint theme.
  }

  if (document.documentElement) {
    const root = document.documentElement;
    root.dataset.theme = theme;
    if (theme === "custom") {
      root.dataset.themeBase = customTheme.base;
      root.style.colorScheme = customTheme.base === "light" ? "light" : "dark";
      for (const key of customColorKeys) {
        root.style.setProperty(`--${key}`, customTheme.colors[key]);
      }
      const luminance = (hex) => {
        const channels = hex.slice(1).match(/.{2}/g).map((part) => Number.parseInt(part, 16) / 255);
        const linear = channels.map((channel) =>
          channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
        );
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
      };
      const contrast = (first, second) => {
        const one = luminance(first);
        const two = luminance(second);
        return (Math.max(one, two) + 0.05) / (Math.min(one, two) + 0.05);
      };
      const accent = customTheme.colors.accent;
      const ink = contrast(accent, "#020A1F") >= contrast(accent, "#FFFFFF")
        ? "#020A1F"
        : "#FFFFFF";
      root.style.setProperty("--accent-ink", ink);
    }
  }

  try {
    window.localStorage.setItem(cacheKey, theme);
    window.localStorage.setItem(customCacheKey, JSON.stringify(customTheme));
    window.sessionStorage.setItem(sessionKey, "true");
  } catch {
    // Appearance remains applied for this document even when caching fails.
  }
})();
