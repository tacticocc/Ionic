import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, it } from "node:test";
import { fileURLToPath } from "node:url";

import {
  expectedArtifactNames,
  stageReleaseArtifacts,
} from "../scripts/stage-release-artifacts.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DESKTOP = path.resolve(HERE, "..");
const REPOSITORY = path.resolve(DESKTOP, "..");
const temporaryDirectories = [];

function fixture() {
  const desktopRoot = fs.mkdtempSync(path.join(os.tmpdir(), "ionic-essential-release-"));
  temporaryDirectories.push(desktopRoot);
  fs.writeFileSync(path.join(desktopRoot, "package.json"), JSON.stringify({
    ionicEdition: "essential",
    version: "0.7.0",
    build: {
      artifactName: "Ionic-Essential-${version}-${arch}.${ext}",
      directories: { output: "dist/essential" },
      nsis: { artifactName: "Ionic-Essential-Setup-${version}-${arch}.${ext}" },
    },
  }));
  const output = path.join(desktopRoot, "dist", "essential");
  fs.mkdirSync(path.join(output, "win-unpacked"), { recursive: true });
  fs.writeFileSync(path.join(output, "Ionic-Essential-Setup-0.7.0-x64.exe"), "setup");
  fs.writeFileSync(path.join(output, "Ionic-Essential-0.7.0-x64.zip"), "archive");
  fs.writeFileSync(path.join(output, "builder-debug.yml"), "C:\\private\\workspace");
  fs.writeFileSync(path.join(output, "latest.yml"), "stale update metadata");
  fs.writeFileSync(path.join(output, "Ionic-Essential-0.5.0-x64.zip"), "stale artifact");
  fs.writeFileSync(path.join(output, "win-unpacked", "Ionic Essential.exe"), "unpacked app");
  const oldStage = path.join(desktopRoot, "release-staging", "essential", "win");
  fs.mkdirSync(oldStage, { recursive: true });
  fs.writeFileSync(path.join(oldStage, "old-local-paths.txt"), "must be removed");
  return desktopRoot;
}

afterEach(() => {
  while (temporaryDirectories.length) {
    fs.rmSync(temporaryDirectories.pop(), { recursive: true, force: true });
  }
});

describe("Essential release staging", () => {
  it("copies only the exact versioned platform allowlist into a clean directory", () => {
    const desktopRoot = fixture();
    const staged = stageReleaseArtifacts({ target: "win", arch: "x64", desktopRoot });
    assert.deepEqual(staged.files, [
      "Ionic-Essential-Setup-0.7.0-x64.exe",
      "Ionic-Essential-0.7.0-x64.zip",
      "artifacts.json",
    ]);
    assert.deepEqual(fs.readdirSync(staged.directory).sort(), [...staged.files].sort());

    const manifestText = fs.readFileSync(path.join(staged.directory, "artifacts.json"), "utf8");
    const manifest = JSON.parse(manifestText);
    assert.equal(manifest.schema_version, 1);
    assert.equal(manifest.edition, "essential");
    assert.equal(manifest.version, "0.7.0");
    assert.deepEqual(manifest.artifacts.map(({ name }) => name), staged.files.slice(0, 2));
    assert.doesNotMatch(manifestText, /builder-debug|latest\.yml|win-unpacked|0\.5\.0|private|ionic-essential-release/i);
    assert.equal(fs.existsSync(path.join(staged.directory, "old-local-paths.txt")), false);
  });

  it("fails closed when an allowlisted artifact is absent", () => {
    const desktopRoot = fixture();
    fs.rmSync(path.join(desktopRoot, "dist", "essential", "Ionic-Essential-0.7.0-x64.zip"));
    assert.throws(
      () => stageReleaseArtifacts({ target: "win", desktopRoot }),
      /Required release artifact is missing/
    );
    assert.throws(
      () => stageReleaseArtifacts({ target: "windows", desktopRoot }),
      /Unsupported release target/
    );
  });

  it("uses Electron Builder's native Linux architecture names without widening the allowlist", () => {
    const desktopRoot = fixture();
    const output = path.join(desktopRoot, "dist", "essential");
    fs.writeFileSync(
      path.join(output, "Ionic-Essential-0.7.0-x86_64.AppImage"),
      "appimage"
    );
    fs.writeFileSync(
      path.join(output, "Ionic-Essential-0.7.0-amd64.deb"),
      "deb"
    );

    assert.deepEqual(expectedArtifactNames("linux", "0.7.0"), [
      "Ionic-Essential-0.7.0-x86_64.AppImage",
      "Ionic-Essential-0.7.0-amd64.deb",
    ]);
    assert.deepEqual(expectedArtifactNames("mac", "0.7.0"), [
      "Ionic-Essential-0.7.0-x64.dmg",
      "Ionic-Essential-0.7.0-x64.zip",
    ]);
    assert.deepEqual(expectedArtifactNames("win", "0.7.0"), [
      "Ionic-Essential-Setup-0.7.0-x64.exe",
      "Ionic-Essential-0.7.0-x64.zip",
    ]);
    assert.deepEqual(
      stageReleaseArtifacts({ target: "linux", arch: "x64", desktopRoot }).files,
      [
        "Ionic-Essential-0.7.0-x86_64.AppImage",
        "Ionic-Essential-0.7.0-amd64.deb",
        "artifacts.json",
      ]
    );
  });
});

describe("Essential signed-release boundary", () => {
  it("requires Tactico signatures and trusted timestamps on every shipped Windows executable", () => {
    const packageJson = JSON.parse(fs.readFileSync(path.join(DESKTOP, "package.json"), "utf8"));
    const verifier = fs.readFileSync(
      path.join(DESKTOP, "scripts", "verify-windows-signatures.ps1"),
      "utf8"
    );
    assert.equal(
      packageJson.build.win.signtoolOptions.publisherName,
      "Tactico Technologies"
    );
    assert.match(packageJson.scripts["dist:win:signed"], /forceCodeSigning=true/);
    assert.match(packageJson.scripts["dist:win:signed"], /release:verify:win/);
    assert.match(verifier, /Get-AuthenticodeSignature -LiteralPath/);
    assert.match(verifier, /Ionic-Essential-Setup-\*\.exe/);
    assert.match(verifier, /\$ExpectedProductName\.exe/);
    assert.match(verifier, /resources\\ionic\\ionic\.exe/);
    assert.match(verifier, /SignatureStatus\]::Valid/);
    assert.match(verifier, /ExpectedPublisherPattern/);
    assert.match(verifier, /TimeStamperCertificate/);
    assert.match(verifier, /RFC 3161 or Authenticode timestamp/);
  });

  it("uploads only staged validation artifacts and gates published Windows releases on signing", () => {
    const workflow = fs.readFileSync(
      path.join(REPOSITORY, ".github", "workflows", "desktop.yml"),
      "utf8"
    );
    const ciWorkflow = fs.readFileSync(
      path.join(REPOSITORY, ".github", "workflows", "ci.yml"),
      "utf8"
    );
    assert.doesNotMatch(workflow, /output_directory\s*\}\}\/\*\*/);
    assert.match(workflow, /release-staging\/\$\{\{ needs\.edition\.outputs\.edition \}\}\/\$\{\{ matrix\.target \}\}\/\*/);
    assert.match(workflow, /github\.event_name != 'release'/);
    assert.match(workflow, /github\.event_name == 'release' && needs\.edition\.outputs\.edition == 'essential' && vars\.ENABLE_SIGNED_RELEASES == 'true'/);
    assert.match(workflow, /expectedTag = `v\$\{pkg\.version\}`/);
    assert.match(workflow, /git merge-base --is-ancestor "\$GITHUB_SHA" "origin\/\$RELEASE_TARGET"/);
    assert.match(workflow, /npm run dist:win:signed/);
    assert.match(workflow, /secrets\.WINDOWS_CSC_LINK/);
    assert.match(workflow, /secrets\.WINDOWS_CSC_KEY_PASSWORD/);
    assert.match(workflow, /permissions:\s*\n\s*contents: write/);
    assert.match(workflow, /gh release upload \$env:RELEASE_TAG @files/);
    assert.doesNotMatch(workflow, /gh release upload[^\n]*--clobber/);
    assert.equal((workflow.match(/PIP_CONSTRAINT:/g) || []).length, 3);
    assert.match(workflow, /requirements\/desktop-build-constraints\.txt/);
    assert.equal(
      (workflow.match(/"\$IONIC_BUILD_PYTHON" -m pip install -e "\.\[dev\]"/g) || []).length,
      2
    );
    assert.doesNotMatch(workflow, /\$\{\{ env\.IONIC_BUILD_PYTHON \}\}/);
    const workflows = `${workflow}\n${ciWorkflow}`;
    assert.doesNotMatch(workflows, /uses:\s+actions\/[^@\s]+@v\d+/);
    for (const sha of [
      "11d5960a326750d5838078e36cf38b85af677262",
      "a26af69be951a213d495a4c3e4e4022e16d87065",
      "49933ea5288caeca8642d1e84afbd3f7d6820020",
      "ea165f8d65b6e75b540449e92b4886f43607fa02",
    ]) {
      assert.match(workflows, new RegExp(`actions/[^@\\s]+@${sha}`));
    }
  });
});
