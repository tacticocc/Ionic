import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DESKTOP = path.resolve(HERE, "..");
const TEMPLATE_ROOT = path.join(
  DESKTOP,
  "node_modules",
  "app-builder-lib",
  "templates",
  "nsis"
);

// electron-builder 26.15.3 template hashes. The patched hashes make the
// operation idempotent. Any dependency change must be reviewed explicitly.
const TEMPLATES = Object.freeze([
  Object.freeze({
    relativePath: "include/installer.nsh",
    originalSha256: "0e319437dd01dcbf911f3f48f664fde0cefbaef704f1cdb1739f63d563f5d4a0",
    patchedSha256: "d26f63b6aafe97263ab72fa69a5620941ed40a520a4e91b685bf1f561f549410",
  }),
  Object.freeze({
    relativePath: "uninstaller.nsh",
    originalSha256: "9ee2dac4593478083e8aa6f8487287ce9401006ccd50ecc538871d133ea4a42c",
    patchedSha256: "9a56ebaa5a5cdfafb891e14f18fabb319cb534b7cb2bca4db3a986f910600535",
  }),
]);

function sha256(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

function replaceWinShellCalls(value) {
  let result = value
    .replaceAll("WinShell::SetLnkAUMI", "!insertmacro TacticoSetShortcutAppId")
    .replaceAll("WinShell::UninstShortcut", "!insertmacro TacticoUnpinShortcut")
    .replaceAll(
      "WinShell::UninstAppUserModelId",
      "!insertmacro TacticoClearAppDestinations"
    );
  if (/WinShell::/u.test(result)) {
    throw new Error("Patched NSIS template still references the unverifiable WinShell plug-in");
  }
  return result;
}

export function prepareNsisScript() {
  const patched = [];
  for (const template of TEMPLATES) {
    const target = path.join(TEMPLATE_ROOT, ...template.relativePath.split("/"));
    const source = fs.readFileSync(target, "utf8");
    const actual = sha256(source);
    if (actual === template.patchedSha256) {
      patched.push(target);
      continue;
    }
    if (actual !== template.originalSha256) {
      throw new Error(
        `Refusing to patch changed electron-builder NSIS template ${template.relativePath}: ` +
          `expected ${template.originalSha256} or ${template.patchedSha256}, received ${actual}`
      );
    }
    const replacement = replaceWinShellCalls(source);
    const replacementHash = sha256(replacement);
    if (replacementHash !== template.patchedSha256) {
      throw new Error(`Internal patched hash mismatch for ${template.relativePath}`);
    }
    fs.writeFileSync(target, replacement, "utf8");
    patched.push(target);
  }
  return patched;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.stdout.write(`${prepareNsisScript().join("\n")}\n`);
}
