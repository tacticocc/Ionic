import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { describe, it } from "node:test";
import { prepareNsisScript } from "../scripts/prepare-nsis-script.mjs";

describe("NSIS Setup toolchain", () => {
  it("pins reviewed electron-builder templates and removes every WinShell plug-in call", () => {
    const templates = prepareNsisScript();
    const generated = templates
      .map((name) => fs.readFileSync(name, "utf8"))
      .join("\n");

    assert.equal(templates.length, 2);
    assert.doesNotMatch(generated, /WinShell::/u);
    assert.match(generated, /TacticoSetShortcutAppId/u);
    assert.match(generated, /TacticoUnpinShortcut/u);
    assert.match(generated, /TacticoClearAppDestinations/u);
  });

  it("implements shortcut metadata and cleanup through NSIS core COM/System calls", () => {
    const source = fs.readFileSync(
      path.resolve("nsis", "native-shell.nsh"),
      "utf8"
    );
    assert.match(source, /InitPropVariantFromString/u);
    assert.match(source, /PKEY_AppUserModel_ID/u);
    assert.match(source, /IStartMenuPinnedList::RemoveFromList/u);
    assert.match(source, /IApplicationDestinations::RemoveAllDestinations/u);
    assert.doesNotMatch(source, /WinShell::/u);
  });

  it("ships the reviewed corresponding-source archives byte-for-byte", () => {
    const expected = {
      "7z1900-src.7z": "9ba70a5e8485cf9061b30a2a84fe741de5aeb8dd271aab8889da0e9b3bf1868e",
      "nsis-3.04-src.tar.bz2": "609536046c50f35cfd909dd7df2ab38f2e835d0da3c1048aa0d48c59c5a4f4f5",
      "Nsis7z-19.00-source-and-binaries.7z": "6f2f3730049926f40442ee0c8b7d3e3dee7ace544d82467ff8059ea3f4201c58",
      "StdUtils-1.14-sources.tar": "db9f98d7a947d5a6b7cd341e01edd412ea04510c5faee19a23b1e84582d86121",
      "UAC-0.2.4c-source-and-binaries.zip": "20e3192af5598568887c16d88de59a52c2ce4a26e42c5fb8bee8105dcbbd1760",
    };
    const sourceDir = path.resolve("src", "legal", "sources", "NSIS-plugins");

    for (const [name, digest] of Object.entries(expected)) {
      const actual = crypto
        .createHash("sha256")
        .update(fs.readFileSync(path.join(sourceDir, name)))
        .digest("hex");
      assert.equal(actual, digest, name);
    }
  });

  it("fail-closes release verification on unexpected compiled plug-ins or sources", () => {
    const verifier = fs.readFileSync(
      path.resolve("scripts", "verify-nsis-contents.ps1"),
      "utf8"
    );
    const packageJson = JSON.parse(fs.readFileSync("package.json", "utf8"));

    assert.match(verifier, /Compiled Setup unexpectedly embeds WinShell\.dll/u);
    assert.match(verifier, /B72E9013A6204E9F01076DC38DABBF30870D44DFC66962ADBF73619D4331601E/u);
    assert.match(verifier, /nsDialogs\.dll/u);
    assert.match(verifier, /1E40211AF65923C2F4FD02CE021458A7745D28E2F383835E3015E96575632172/u);
    assert.match(verifier, /nsExec\.dll/u);
    assert.match(verifier, /5D9CEB1CE5F35AEA5F9E5A0C0EDEEEC04DFEFE0C77890C80C70E98209B58B962/u);
    assert.match(verifier, /Compare-Object -ReferenceObject \$expectedPluginNames/u);
    assert.match(verifier, /exact reviewed allowlist/u);
    assert.match(verifier, /Packaged corresponding source is missing/u);
    assert.match(packageJson.scripts["release:verify:nsis"], /verify-nsis-contents\.ps1/u);
    assert.match(packageJson.scripts["release:verify:win"], /&& npm run release:verify:nsis$/u);
  });
});
