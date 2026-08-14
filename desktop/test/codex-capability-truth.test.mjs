import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DESKTOP = path.resolve(HERE, "..");
describe("Codex capability truth", () => {
  it("describes official app-server support as conditional in the UI", () => {
    const html = fs.readFileSync(path.join(DESKTOP, "src", "renderer", "index.html"), "utf8");
    const app = fs.readFileSync(path.join(DESKTOP, "src", "renderer", "app.js"), "utf8");

    assert.match(html, /Official app-server support is conditional/);
    assert.match(html, /sign-in and the model catalog may work/);
    assert.match(app, /Sign-in and the model catalog can be checked/);
    assert.match(app, /Connected for sign-in and model catalog only/);
  });

  it("keeps the runtime-facing semantic review gate fail-closed", () => {
    const main = fs.readFileSync(path.join(DESKTOP, "src", "main.js"), "utf8");
    assert.match(main, /status\?\.semanticReviewCapable !== true/);
    assert.match(main, /throw new Error\([\s\S]+?Semantic review is unavailable/);
  });
});
