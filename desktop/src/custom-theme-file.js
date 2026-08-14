"use strict";

const fs = require("node:fs");
const { TextDecoder } = require("node:util");

const preferences = require("./preferences");

const CUSTOM_THEME_FILE_FORMAT = "ionic.custom-theme";
const CUSTOM_THEME_FILE_VERSION = 1;
const CUSTOM_THEME_FILE_MAX_BYTES = 64 * 1024;
const CUSTOM_THEME_FILE_KEYS = Object.freeze(["format", "version", "base", "colors"]);

function themeFileError(message, code) {
  const error = new Error(message);
  error.name = "CustomThemeFileError";
  error.code = code;
  return error;
}

function hasExactKeys(value, expected) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const keys = Object.keys(value);
  return keys.length === expected.length && expected.every((key) => keys.includes(key));
}

function decodeUtf8(buffer) {
  if (!Buffer.isBuffer(buffer)) {
    throw new TypeError("Theme file content must be a Buffer");
  }
  if (buffer.length === 0) {
    throw themeFileError("The selected theme file is empty.", "THEME_FILE_EMPTY");
  }
  if (buffer.length > CUSTOM_THEME_FILE_MAX_BYTES) {
    throw themeFileError(
      `Theme files must be ${CUSTOM_THEME_FILE_MAX_BYTES / 1024} KB or smaller.`,
      "THEME_FILE_TOO_LARGE"
    );
  }
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(buffer);
  } catch {
    throw themeFileError(
      "The selected theme file is not valid UTF-8 text.",
      "THEME_FILE_INVALID_UTF8"
    );
  }
}

function parseCustomThemeFile(buffer) {
  const source = decodeUtf8(buffer);
  let document;
  try {
    document = JSON.parse(source);
  } catch {
    throw themeFileError(
      "The selected file does not contain valid theme JSON.",
      "THEME_FILE_INVALID_JSON"
    );
  }

  if (!hasExactKeys(document, CUSTOM_THEME_FILE_KEYS)) {
    throw themeFileError(
      "Theme JSON must contain exactly format, version, base, and colors.",
      "THEME_FILE_INVALID_SCHEMA"
    );
  }
  if (document.format !== CUSTOM_THEME_FILE_FORMAT) {
    throw themeFileError(
      "The selected JSON is not an Ionic custom theme file.",
      "THEME_FILE_INVALID_FORMAT"
    );
  }
  if (document.version !== CUSTOM_THEME_FILE_VERSION) {
    throw themeFileError(
      `Theme file version ${String(document.version)} is not supported.`,
      "THEME_FILE_UNSUPPORTED_VERSION"
    );
  }

  try {
    return preferences.validateCustomTheme({
      base: document.base,
      colors: document.colors,
    });
  } catch (error) {
    throw themeFileError(
      error?.message || "The selected theme has an invalid base or color palette.",
      "THEME_FILE_INVALID_SCHEMA"
    );
  }
}

function serializeCustomThemeFile(theme) {
  const normalized = preferences.validateCustomTheme(theme);
  return `${JSON.stringify(
    {
      format: CUSTOM_THEME_FILE_FORMAT,
      version: CUSTOM_THEME_FILE_VERSION,
      base: normalized.base,
      colors: normalized.colors,
    },
    null,
    2
  )}\n`;
}

function readCustomThemeFile(file, { fsImpl = fs } = {}) {
  const handle = fsImpl.openSync(file, "r");
  try {
    const status = fsImpl.fstatSync(handle);
    if (!status.isFile()) {
      throw themeFileError("Select a JSON theme file, not a folder.", "THEME_FILE_NOT_FILE");
    }
    if (status.size > CUSTOM_THEME_FILE_MAX_BYTES) {
      throw themeFileError(
        `Theme files must be ${CUSTOM_THEME_FILE_MAX_BYTES / 1024} KB or smaller.`,
        "THEME_FILE_TOO_LARGE"
      );
    }

    const chunks = [];
    let total = 0;
    while (true) {
      const chunk = Buffer.allocUnsafe(
        Math.min(8192, CUSTOM_THEME_FILE_MAX_BYTES + 1 - total)
      );
      const count = fsImpl.readSync(handle, chunk, 0, chunk.length, null);
      if (count === 0) break;
      total += count;
      if (total > CUSTOM_THEME_FILE_MAX_BYTES) {
        throw themeFileError(
          `Theme files must be ${CUSTOM_THEME_FILE_MAX_BYTES / 1024} KB or smaller.`,
          "THEME_FILE_TOO_LARGE"
        );
      }
      chunks.push(chunk.subarray(0, count));
    }
    return parseCustomThemeFile(Buffer.concat(chunks, total));
  } finally {
    fsImpl.closeSync(handle);
  }
}

function writeCustomThemeFile(file, theme) {
  fs.writeFileSync(file, serializeCustomThemeFile(theme), {
    encoding: "utf8",
    mode: 0o600,
  });
  return true;
}

module.exports = {
  CUSTOM_THEME_FILE_FORMAT,
  CUSTOM_THEME_FILE_VERSION,
  CUSTOM_THEME_FILE_MAX_BYTES,
  parseCustomThemeFile,
  serializeCustomThemeFile,
  readCustomThemeFile,
  writeCustomThemeFile,
};
