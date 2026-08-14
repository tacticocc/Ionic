import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { afterEach, describe, it } from "node:test";

const require = createRequire(import.meta.url);
const { EDITION, configureAppIdentity } = require("../src/edition.js");
const temporaryDirectories = [];

afterEach(() => {
  while (temporaryDirectories.length) {
    fs.rmSync(temporaryDirectories.pop(), { recursive: true, force: true });
  }
});

describe("Essential desktop identity", () => {
  it("uses an edition-specific package, profile, and Windows identity", () => {
    const appData = fs.mkdtempSync(path.join(os.tmpdir(), "ionic-edition-"));
    temporaryDirectories.push(appData);
    const calls = [];
    const app = {
      getPath(name) {
        assert.equal(name, "appData");
        return appData;
      },
      setName(value) {
        calls.push(["name", value]);
      },
      setPath(name, value) {
        calls.push([name, value]);
      },
      setAppUserModelId(value) {
        calls.push(["appId", value]);
      },
    };

    const paths = configureAppIdentity(app);
    assert.equal(EDITION.id, "essential");
    assert.equal(EDITION.productName, "Ionic Essential");
    assert.equal(EDITION.appId, "com.tactico.ionic.essential");
    assert.equal(paths.userData, path.join(appData, "Tactico Technologies", "Ionic Essential"));
    assert.equal(paths.sessionData, path.join(paths.userData, "Session"));
    assert.equal(fs.statSync(paths.sessionData).isDirectory(), true);
    assert.deepEqual(calls, [
      ["name", "Ionic Essential"],
      ["userData", paths.userData],
      ["sessionData", paths.sessionData],
      ["appId", "com.tactico.ionic.essential"],
    ]);
  });
});
