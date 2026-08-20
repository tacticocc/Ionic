import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT_PATH = fileURLToPath(import.meta.url);
const DEFAULT_DESKTOP_ROOT = path.resolve(path.dirname(SCRIPT_PATH), "..");
const PLATFORM_EXTENSIONS = Object.freeze({
  win: Object.freeze(["setup.exe", "zip"]),
  mac: Object.freeze(["dmg", "zip"]),
  linux: Object.freeze(["AppImage", "deb"]),
});

function artifactArchitecture(target, extension, arch) {
  if (target === "linux" && arch === "x64") {
    return extension === "AppImage" ? "x86_64" : "amd64";
  }
  return arch;
}

function readPackage(desktopRoot) {
  const packagePath = path.join(desktopRoot, "package.json");
  const parsed = JSON.parse(fs.readFileSync(packagePath, "utf8"));
  if (parsed.ionicEdition !== "essential") {
    throw new Error("Essential release staging requires Essential package metadata.");
  }
  if (!/^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/.test(parsed.version || "")) {
    throw new Error("Essential release staging requires a valid package version.");
  }
  if (parsed.build?.directories?.output !== "dist/essential") {
    throw new Error("Essential release staging requires desktop/dist/essential output.");
  }
  if (parsed.build?.artifactName !== "Ionic-Essential-${version}-${arch}.${ext}" ||
      parsed.build?.nsis?.artifactName !== "Ionic-Essential-Setup-${version}-${arch}.${ext}") {
    throw new Error("Essential release artifact names do not match the staging allowlist.");
  }
  return parsed;
}

export function expectedArtifactNames(target, version, arch = "x64") {
  const extensions = PLATFORM_EXTENSIONS[target];
  if (!extensions) throw new Error(`Unsupported release target: ${target || "missing"}.`);
  if (!new Set(["x64", "arm64"]).has(arch)) {
    throw new Error(`Unsupported release architecture: ${arch || "missing"}.`);
  }
  return extensions.map((extension) => {
    const artifactArch = artifactArchitecture(target, extension, arch);
    return extension === "setup.exe"
      ? `Ionic-Essential-Setup-${version}-${artifactArch}.exe`
      : `Ionic-Essential-${version}-${artifactArch}.${extension}`;
  });
}

function assertRegularArtifact(target, sourceRoot) {
  const stats = fs.lstatSync(target);
  if (stats.isSymbolicLink() || !stats.isFile() || stats.size < 1) {
    throw new Error(`Release artifact is not a non-empty regular file: ${path.basename(target)}.`);
  }
  const realTarget = fs.realpathSync(target);
  const realRoot = fs.realpathSync(sourceRoot);
  if (!realTarget.startsWith(`${realRoot}${path.sep}`)) {
    throw new Error(`Release artifact escapes the expected output directory: ${path.basename(target)}.`);
  }
}

function sha256(target) {
  return crypto.createHash("sha256").update(fs.readFileSync(target)).digest("hex");
}

export function stageReleaseArtifacts({
  target,
  arch = "x64",
  desktopRoot = DEFAULT_DESKTOP_ROOT,
} = {}) {
  const resolvedDesktopRoot = path.resolve(desktopRoot);
  const packageJson = readPackage(resolvedDesktopRoot);
  const sourceRoot = path.join(resolvedDesktopRoot, "dist", "essential");
  const stageBase = path.join(resolvedDesktopRoot, "release-staging", "essential");
  const stageRoot = path.join(stageBase, target || "");
  const expectedStageRoot = path.resolve(stageBase, target || "");
  if (!PLATFORM_EXTENSIONS[target] ||
      expectedStageRoot === path.resolve(stageBase) ||
      !expectedStageRoot.startsWith(`${path.resolve(stageBase)}${path.sep}`)) {
    throw new Error(`Unsupported release target: ${target || "missing"}.`);
  }
  if (!fs.existsSync(sourceRoot) || !fs.statSync(sourceRoot).isDirectory()) {
    throw new Error("Essential distribution output is missing; build it before staging.");
  }

  const names = expectedArtifactNames(target, packageJson.version, arch);
  for (const name of names) {
    const target = path.join(sourceRoot, name);
    if (!fs.existsSync(target)) throw new Error(`Required release artifact is missing: ${name}.`);
    assertRegularArtifact(target, sourceRoot);
  }

  fs.rmSync(stageRoot, { recursive: true, force: true });
  fs.mkdirSync(stageRoot, { recursive: true });
  const artifacts = [];
  for (const name of names) {
    const source = path.join(sourceRoot, name);
    const destination = path.join(stageRoot, name);
    fs.copyFileSync(source, destination, fs.constants.COPYFILE_EXCL);
    artifacts.push(Object.freeze({
      name,
      size: fs.statSync(destination).size,
      sha256: sha256(destination),
    }));
  }
  fs.writeFileSync(path.join(stageRoot, "artifacts.json"), `${JSON.stringify({
    schema_version: 1,
    edition: "essential",
    version: packageJson.version,
    target,
    arch,
    artifacts,
  }, null, 2)}\n`, {
    encoding: "utf8",
    flag: "wx",
  });
  return Object.freeze({
    target,
    arch,
    directory: stageRoot,
    files: Object.freeze([...names, "artifacts.json"]),
  });
}

function argument(argv, name, fallback = undefined) {
  const inline = argv.find((value) => value.startsWith(`${name}=`));
  if (inline) return inline.slice(name.length + 1);
  const position = argv.indexOf(name);
  return position >= 0 ? argv[position + 1] : fallback;
}

if (process.argv[1] && path.resolve(process.argv[1]) === path.resolve(SCRIPT_PATH)) {
  try {
    const argv = process.argv.slice(2);
    const staged = stageReleaseArtifacts({
      target: argument(argv, "--target"),
      arch: argument(argv, "--arch", "x64"),
    });
    process.stdout.write(`${JSON.stringify(staged)}\n`);
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  }
}
