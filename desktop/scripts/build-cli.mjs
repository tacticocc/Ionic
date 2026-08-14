/**
 * Cross-platform launcher for the Python sidecar build.
 *
 * npm cannot assume an activated virtual environment. Resolve a project-local
 * build interpreter first, then fall back to the normal Python launchers.
 */

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const desktopDir = path.resolve(here, "..");
const repoRoot = path.resolve(desktopDir, "..");
const script = path.join(here, "build_cli.py");
const isWindows = process.platform === "win32";

function cleanEnvPath(value) {
  if (!value) return null;
  const trimmed = value.trim();
  if (
    trimmed.length >= 2 &&
    ((trimmed.startsWith('"') && trimmed.endsWith('"')) ||
      (trimmed.startsWith("'") && trimmed.endsWith("'")))
  ) {
    return trimmed.slice(1, -1);
  }
  return trimmed;
}

function venvPython(name) {
  return path.join(repoRoot, name, isWindows ? "Scripts" : "bin", isWindows ? "python.exe" : "python");
}

const candidates = [];
for (const configured of [process.env.IONIC_BUILD_PYTHON, process.env.PYTHON]) {
  const command = cleanEnvPath(configured);
  if (command) candidates.push({ command, args: [], source: "configured environment" });
}
candidates.push(
  { command: venvPython(".desktop-build-venv"), args: [], source: ".desktop-build-venv" },
  { command: venvPython(".venv"), args: [], source: ".venv" },
  { command: isWindows ? "python.exe" : "python3", args: [], source: "PATH" },
  { command: "python", args: [], source: "PATH" }
);
if (isWindows) {
  candidates.push(
    { command: "py.exe", args: ["-3.12"], source: "Python launcher" },
    { command: "py.exe", args: ["-3"], source: "Python launcher" }
  );
}

const seen = new Set();
let selected = null;
for (const candidate of candidates) {
  const key = `${candidate.command}\0${candidate.args.join("\0")}`.toLowerCase();
  if (seen.has(key)) continue;
  seen.add(key);
  if (candidate.command.includes(path.sep) && !fs.existsSync(candidate.command)) continue;
  const probe = spawnSync(candidate.command, [...candidate.args, "--version"], {
    cwd: repoRoot,
    encoding: "utf8",
    windowsHide: true,
  });
  if (probe.status === 0) {
    selected = candidate;
    break;
  }
}

if (!selected) {
  console.error(
    "No working Python 3.11+ interpreter was found. Install Python, create " +
      `${path.join(repoRoot, ".desktop-build-venv")}, or set IONIC_BUILD_PYTHON.`
  );
  process.exit(1);
}

console.log(`Using ${selected.source}: ${selected.command}`);
const result = spawnSync(selected.command, [...selected.args, script], {
  cwd: repoRoot,
  env: process.env,
  stdio: "inherit",
  windowsHide: true,
});
if (result.error) {
  console.error(`Could not launch the sidecar build: ${result.error.message}`);
  process.exit(1);
}
process.exit(result.status ?? 1);
