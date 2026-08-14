import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, it } from "node:test";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const themeFile = require("../src/custom-theme-file.js");
const temporaryDirectories = [];

const THEME = {
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

function document(patch = {}) {
  return {
    format: themeFile.CUSTOM_THEME_FILE_FORMAT,
    version: themeFile.CUSTOM_THEME_FILE_VERSION,
    ...THEME,
    ...patch,
  };
}

function encoded(value) {
  return Buffer.from(JSON.stringify(value), "utf8");
}

afterEach(() => {
  while (temporaryDirectories.length) {
    fs.rmSync(temporaryDirectories.pop(), { recursive: true, force: true });
  }
});

describe("custom theme JSON files", () => {
  it("round-trips only the portable format metadata, base, and seven normalized colors", () => {
    const output = themeFile.serializeCustomThemeFile(THEME);
    const parsedDocument = JSON.parse(output);

    assert.deepEqual(Object.keys(parsedDocument), ["format", "version", "base", "colors"]);
    assert.equal(parsedDocument.format, "ionic.custom-theme");
    assert.deepEqual(Object.keys(parsedDocument.colors), [
      "canvas",
      "sidebar",
      "surface",
      "border",
      "text",
      "muted",
      "accent",
    ]);
    assert.equal(parsedDocument.colors.canvas, "#020A1F");
    assert.equal(parsedDocument.colors.accent, "#26DBFF");
    assert.deepEqual(themeFile.parseCustomThemeFile(Buffer.from(output, "utf8")), {
      base: "dark",
      colors: parsedDocument.colors,
    });
  });

  it("rejects extra, missing, future, and malformed schema fields", () => {
    assert.throws(
      () => themeFile.parseCustomThemeFile(encoded({ ...document(), apiKey: "secret" })),
      /exactly format, version, base, and colors/
    );
    assert.throws(
      () => themeFile.parseCustomThemeFile(encoded({
        format: themeFile.CUSTOM_THEME_FILE_FORMAT,
        base: THEME.base,
        colors: THEME.colors,
      })),
      /exactly format, version, base, and colors/
    );
    assert.throws(
      () => themeFile.parseCustomThemeFile(encoded(document({ format: "other.theme" }))),
      /not an Ionic custom theme/
    );
    assert.throws(
      () => themeFile.parseCustomThemeFile(encoded(document({ version: 2 }))),
      /version 2 is not supported/
    );
    assert.throws(
      () => themeFile.parseCustomThemeFile(encoded(document({ base: "system" }))),
      /base must be light, dark, or oled/
    );
    assert.throws(
      () => themeFile.parseCustomThemeFile(encoded(document({
        colors: { ...THEME.colors, future: "#000000" },
      }))),
      /must contain exactly/
    );
    assert.throws(
      () => themeFile.parseCustomThemeFile(encoded(document({
        colors: { ...THEME.colors, accent: "cyan" },
      }))),
      /accent must be a #RRGGBB/
    );
  });

  it("rejects empty, oversized, non-UTF-8, and malformed JSON input", () => {
    assert.throws(() => themeFile.parseCustomThemeFile(Buffer.alloc(0)), /empty/);
    assert.throws(
      () => themeFile.parseCustomThemeFile(Buffer.alloc(themeFile.CUSTOM_THEME_FILE_MAX_BYTES + 1)),
      /64 KB or smaller/
    );
    assert.throws(
      () => themeFile.parseCustomThemeFile(Buffer.from([0xc3, 0x28])),
      /not valid UTF-8/
    );
    assert.throws(
      () => themeFile.parseCustomThemeFile(Buffer.from("{not-json}", "utf8")),
      /valid theme JSON/
    );
  });

  it("reads and writes the same bounded file contract", () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "ionic-theme-file-test-"));
    temporaryDirectories.push(root);
    const file = path.join(root, "theme.json");

    themeFile.writeCustomThemeFile(file, THEME);
    assert.deepEqual(themeFile.readCustomThemeFile(file), {
      base: "dark",
      colors: Object.fromEntries(
        Object.entries(THEME.colors).map(([key, value]) => [key, value.toUpperCase()])
      ),
    });

    fs.writeFileSync(file, Buffer.alloc(themeFile.CUSTOM_THEME_FILE_MAX_BYTES + 1));
    assert.throws(() => themeFile.readCustomThemeFile(file), /64 KB or smaller/);
  });

  it("keeps the byte cap when a selected file grows after it is opened", () => {
    let emitted = 0;
    let closed = false;
    const fsImpl = {
      openSync() {
        return 7;
      },
      fstatSync(handle) {
        assert.equal(handle, 7);
        return { isFile: () => true, size: 1 };
      },
      readSync(handle, buffer, offset, length) {
        assert.equal(handle, 7);
        const remaining = themeFile.CUSTOM_THEME_FILE_MAX_BYTES + 1 - emitted;
        if (remaining <= 0) return 0;
        const count = Math.min(length, remaining);
        buffer.fill(0x20, offset, offset + count);
        emitted += count;
        return count;
      },
      closeSync(handle) {
        assert.equal(handle, 7);
        closed = true;
      },
    };

    assert.throws(
      () => themeFile.readCustomThemeFile("growing.json", { fsImpl }),
      /64 KB or smaller/
    );
    assert.equal(emitted, themeFile.CUSTOM_THEME_FILE_MAX_BYTES + 1);
    assert.equal(closed, true);
  });
});
