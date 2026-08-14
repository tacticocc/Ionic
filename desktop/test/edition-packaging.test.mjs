import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const HERE = path.dirname(fileURLToPath(import.meta.url));
const DESKTOP = path.resolve(HERE, "..");
const packageJson = JSON.parse(fs.readFileSync(path.join(DESKTOP, "package.json"), "utf8"));
const edition = require("../src/edition.js");

describe("Essential package identity", () => {
  it("uses one immutable Essential identity across runtime and packaged metadata", () => {
    assert.equal(edition.APP_ID, "com.tactico.ionic.essential");
    assert.equal(edition.DESKTOP_EDITION, "essential");
    assert.equal(edition.PRODUCT_NAME, "Ionic Essential");
    assert.equal(edition.EDITION.id, edition.DESKTOP_EDITION);
    assert.equal(edition.EDITION.appId, edition.APP_ID);
    assert.equal(edition.EDITION.productName, edition.PRODUCT_NAME);
    assert.equal(packageJson.name, "ionic-essential-desktop");
    assert.equal(packageJson.productName, edition.PRODUCT_NAME);
    assert.equal(packageJson.ionicEdition, edition.DESKTOP_EDITION);
    assert.equal(packageJson.build.appId, edition.APP_ID);
    assert.equal(packageJson.build.productName, edition.PRODUCT_NAME);
    assert.equal(packageJson.build.executableName, edition.PRODUCT_NAME);
    assert.equal(packageJson.build.extraMetadata.ionicEdition, edition.DESKTOP_EDITION);
  });

  it("keeps Essential distributions in their edition-specific paths", () => {
    assert.equal(packageJson.build.directories.output, "dist/essential");
    assert.equal(
      packageJson.build.artifactName,
      "Ionic-Essential-${version}-${arch}.${ext}"
    );
    assert.deepEqual(packageJson.build.win.target, [
      { target: "nsis", arch: ["x64"] },
      { target: "zip", arch: ["x64"] },
    ]);
    assert.equal(
      packageJson.build.nsis.artifactName,
      "Ionic-Essential-Setup-${version}-${arch}.${ext}"
    );
    assert.equal(packageJson.build.nsis.oneClick, false);
    assert.equal(packageJson.build.nsis.perMachine, false);
    assert.equal(packageJson.build.nsis.allowElevation, false);
    assert.equal(packageJson.build.nsis.allowToChangeInstallationDirectory, true);
    assert.equal(packageJson.build.nsis.packElevateHelper, false);
    assert.equal(packageJson.build.nsis.license, "../EULA.txt");
    assert.equal(packageJson.build.nsis.include, "nsis/native-shell.nsh");
    assert.equal("portable" in packageJson.build, false);
    assert.equal(
      packageJson.build.win.target.some(({ target }) => ["nsis-web", "portable"].includes(target)),
      false
    );
    for (const script of ["dist", "dist:win", "dist:mac", "dist:linux"]) {
      assert.match(packageJson.scripts[script], /--publish never$/);
    }
    assert.deepEqual(packageJson.build.electronFuses, {
      runAsNode: false,
      enableCookieEncryption: true,
      enableNodeOptionsEnvironmentVariable: false,
      enableNodeCliInspectArguments: false,
      enableEmbeddedAsarIntegrityValidation: true,
      onlyLoadAppFromAsar: true,
    });
  });

  it("packages only Essential-staged engine and legal inventory resources", () => {
    const resources = new Map(
      packageJson.build.extraResources.map((entry) => [entry.to, entry.from])
    );
    assert.equal(resources.get("ionic"), "build/essential/cli/${os}-${arch}/ionic");
    assert.equal(resources.get("legal/EULA.txt"), "../EULA.txt");
    assert.equal(resources.get("legal/licenses"), "build/essential/legal/licenses");
    assert.equal(packageJson.scripts.postinstall, "node node_modules/electron/install.js");
    assert.equal(resources.get("legal/licenses/Electron/LICENSE"), "node_modules/electron/dist/LICENSE");
    assert.equal(
      resources.get("legal/licenses/Electron/LICENSES.chromium.html"),
      "node_modules/electron/dist/LICENSES.chromium.html"
    );
    assert.equal(
      resources.get("legal/licenses/NSIS-3.0.4.1/COPYING.txt"),
      "src/legal/licenses/NSIS-3.0.4.1/COPYING.txt"
    );
    assert.equal(
      resources.get("legal/licenses/NSIS-plugins"),
      "src/legal/licenses/NSIS-plugins"
    );
    assert.equal(
      resources.get("legal/sources/NSIS-plugins"),
      "src/legal/sources/NSIS-plugins"
    );
    assert.ok(packageJson.build.files.includes("!src/legal/sources/**/*"));
  });

  it("provides a fail-closed signed-release path without changing ordinary local builds", () => {
    const verifier = fs.readFileSync(
      path.join(DESKTOP, "scripts", "verify-windows-signatures.ps1"),
      "utf8"
    );
    assert.match(packageJson.scripts["dist:win:signed"], /forceCodeSigning=true/);
    assert.match(packageJson.scripts["dist:win:signed"], /release:verify:win/);
    assert.equal(packageJson.scripts["release:stage"], "node scripts/stage-release-artifacts.mjs");
    assert.match(verifier, /Get-AuthenticodeSignature -LiteralPath/);
    assert.match(verifier, /resources\\ionic\\ionic\.exe/);
    assert.match(verifier, /Ionic-Essential-Setup-\*\.exe/);
    assert.match(verifier, /TimeStamperCertificate/);
  });
});
