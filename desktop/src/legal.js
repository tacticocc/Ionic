"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { DESKTOP_EDITION } = require("./edition");

const AGREEMENT_ID = "tactico-ionic-essential-official-distribution";
const TERMS_VERSION = "2026-08-12.1";
const MAX_LICENSE_INDEX_BYTES = 2 * 1024 * 1024;
const MAX_LICENSE_TEXT_BYTES = 2 * 1024 * 1024;
const MAX_CHROMIUM_NOTICES_BYTES = 32 * 1024 * 1024;
const MAX_CHROMIUM_PRODUCTS = 2_000;
const MAX_CHROMIUM_CACHE_ENTRIES = 2;
const MAX_LICENSE_COMPONENTS = 2_000;
const MAX_LICENSE_DOCUMENTS = 10_000;
const LICENSE_ID_PATTERN = /^oss-[a-f0-9]{32}$/;
const SAFE_RELATIVE_PATH_PATTERN = /^[A-Za-z0-9._-]+(?:\/[A-Za-z0-9._-]+)*$/;
const CHROMIUM_PRODUCT_START = '<div class="product">';
const CHROMIUM_NOTICES_CACHE = new Map();
const CUSTOM_LICENSE_LABEL = "Custom or multiple terms — see text";

const BUNDLED_DESKTOP_COMPONENTS = Object.freeze([
  Object.freeze({
    name: "Inter",
    version: "4.1",
    license: "SIL Open Font License, Version 1.1",
    source: "https://github.com/rsms/inter",
    packagedLicenseFile: "Inter-4.1/OFL-1.1.txt",
    developmentLicenseFile: "INTER-OFL-1.1.txt",
  }),
  Object.freeze({
    name: "Material Symbols Rounded",
    version: null,
    license: "Apache License, Version 2.0",
    source: "https://github.com/google/material-design-icons",
    packagedLicenseFile: "Material-Symbols/Apache-2.0.txt",
    developmentLicenseFile: "MATERIAL-SYMBOLS-APACHE-2.0.txt",
  }),
  Object.freeze({
    name: "NSIS",
    version: "3.0.4.1",
    license: "zlib/libpng; bzip2; CPL-1.0 with NSIS LZMA linking exception",
    source: "https://nsis.sourceforge.io/License",
    packagedLicenseFile: "NSIS-3.0.4.1/COPYING.txt",
    developmentDirectory: "legal/licenses",
    developmentLicenseFile: "NSIS-3.0.4.1/COPYING.txt",
  }),
  Object.freeze({
    name: "StdUtils NSIS plug-in",
    version: "1.14 (FileVersion 1.1.4.0)",
    license: "LGPL-2.1-or-later with upstream NSIS plug-in clarification",
    source: "https://github.com/lordmulder/stdutils/releases/tag/1.14",
    developmentDirectory: "legal/licenses",
    documents: Object.freeze([
      "NSIS-plugins/LGPL-2.1.txt",
      "NSIS-plugins/StdUtils-1.14-CLARIFICATION.txt",
      "NSIS-plugins/PROVENANCE.txt",
    ]),
  }),
  Object.freeze({
    name: "Nsis7z NSIS plug-in",
    version: "19.00",
    license: "7-Zip 19.00 / LZMA SDK terms (LGPL-2.1-or-later and component-specific terms)",
    source: "https://nsis.sourceforge.io/Nsis7z_plug-in",
    developmentDirectory: "legal/licenses",
    documents: Object.freeze([
      "NSIS-plugins/LGPL-2.1.txt",
      "NSIS-plugins/Nsis7z-19.00-LZMA-SDK-License.txt",
      "NSIS-plugins/Nsis7z-19.00-7-Zip-License.txt",
      "NSIS-plugins/PROVENANCE.txt",
    ]),
  }),
  Object.freeze({
    name: "UAC NSIS plug-in",
    version: "0.2.4c (20150526)",
    license: "zlib License",
    source: "https://nsis.sourceforge.io/UAC_plug-in",
    developmentDirectory: "legal/licenses",
    documents: Object.freeze([
      "NSIS-plugins/UAC-0.2.4c-License.txt",
      "NSIS-plugins/PROVENANCE.txt",
    ]),
  }),
]);

const DOCUMENTS = Object.freeze({
  eula: Object.freeze({
    title: "End User License Agreement",
    development: "EULA.txt",
    packaged: "EULA.txt",
  }),
  mit: Object.freeze({
    title: "Ionic MIT License",
    development: "LICENSE",
    packaged: "IONIC-MIT-LICENSE.txt",
  }),
  "third-party": Object.freeze({
    title: "Third-Party Notices",
    development: "THIRD-PARTY-NOTICES.txt",
    packaged: "THIRD-PARTY-NOTICES.txt",
  }),
});

function legalDirectory({ appDir, resourcesDir, isPackaged }) {
  if (isPackaged) return path.join(resourcesDir, "legal");
  return path.resolve(appDir, "..");
}

function licensesDirectory(options) {
  if (options.isPackaged) return path.join(legalDirectory(options), "licenses");
  return path.join(options.appDir, "build", DESKTOP_EDITION, "legal", "licenses");
}

function documentPath(name, options) {
  const document = DOCUMENTS[name];
  if (!document) throw new TypeError("unknown legal document");
  const filename = options.isPackaged ? document.packaged : document.development;
  return path.join(legalDirectory(options), filename);
}

function readDocument(name, options) {
  const document = DOCUMENTS[name];
  if (!document) throw new TypeError("unknown legal document");
  const text = fs.readFileSync(documentPath(name, options), "utf8").replace(/^\uFEFF/, "");
  return { name, title: document.title, text };
}

function listOpenSourceLicenses(options) {
  const { licenses } = buildLicenseInventory(options);
  return { licenses };
}

function readOpenSourceLicense(id, options) {
  if (typeof id !== "string" || !LICENSE_ID_PATTERN.test(id)) {
    throw new TypeError("unknown open-source license document");
  }
  const { licenses, documents } = buildLicenseInventory(options);
  const document = documents.get(id);
  if (!document) throw new TypeError("unknown open-source license document");
  const metadata = licenses.find((entry) => entry.id === id);
  if (!metadata) throw new Error("open-source license inventory is inconsistent");
  const text = document.text === undefined
    ? readBoundedRegularFile(
        document.absolutePath,
        document.root,
        document.maxBytes,
        "open-source license document"
      )
    : document.text;
  return { ...metadata, text };
}

function buildLicenseInventory(options) {
  const root = licensesDirectory(options);
  const indexPath = path.join(root, "index.json");
  const indexText = readBoundedRegularFile(
    indexPath,
    root,
    MAX_LICENSE_INDEX_BYTES,
    "open-source license index"
  );
  let index;
  try {
    index = JSON.parse(indexText);
  } catch {
    throw new Error("open-source license index is invalid JSON");
  }
  if (!index || typeof index !== "object" || Array.isArray(index)) {
    throw new Error("open-source license index must be an object");
  }
  if (index.edition !== DESKTOP_EDITION) {
    throw new Error(`open-source license index is not for Ionic ${DESKTOP_EDITION}`);
  }
  if (!Array.isArray(index.distributions) || index.distributions.length > MAX_LICENSE_COMPONENTS) {
    throw new Error("open-source license index has an invalid component count");
  }

  const components = [
    ...index.distributions.map((raw) => validateComponent(raw, root)),
    validateComponent(pythonRuntimeComponent(index), root),
    ...coreAndElectronComponents(options),
    ...desktopRuntimeComponents(options, root),
  ];
  if (
    components.length >
    MAX_LICENSE_COMPONENTS + MAX_CHROMIUM_PRODUCTS + BUNDLED_DESKTOP_COMPONENTS.length + 3
  ) {
    throw new Error("open-source license index has too many components");
  }
  const licenses = [];
  const documents = new Map();
  const validatedFiles = new Map();
  for (const component of components) {
    const documentLabels = licenseDocumentLabels(component.licenseDocuments);
    for (const [position, document] of component.licenseDocuments.entries()) {
      if (documents.size >= MAX_LICENSE_DOCUMENTS) {
        throw new Error("open-source license index has too many documents");
      }
      const normalizedPath = validateLicenseRelativePath(document.relativePath);
      const idPath = validateLicenseRelativePath(document.idPath);
      const absolutePath = resolveAllowlistedLicensePath(document.root, normalizedPath);
      if (document.text === undefined) {
        const validationKey = JSON.stringify([
          path.resolve(document.root),
          normalizedPath,
          document.maxBytes,
        ]);
        if (!validatedFiles.has(validationKey)) {
          validateBoundedRegularFile(
            absolutePath,
            document.root,
            document.maxBytes,
            "open-source license document"
          );
          validatedFiles.set(validationKey, true);
        }
      } else {
        validateInlineLicenseText(document.text);
      }
      const id = document.opaqueId || licenseDocumentId(component, idPath);
      if (!LICENSE_ID_PATTERN.test(id)) {
        throw new Error("open-source license inventory contains an invalid opaque id");
      }
      if (documents.has(id)) {
        throw new Error("open-source license index contains duplicate document metadata");
      }
      const metadata = Object.freeze({
        id,
        name: component.name,
        version: component.version,
        license: component.license,
        source: component.source,
        document: documentLabels[position],
      });
      licenses.push(metadata);
      documents.set(
        id,
        Object.freeze({
          root: document.root,
          absolutePath,
          relativePath: idPath,
          maxBytes: document.maxBytes,
          text: document.text,
        })
      );
    }
  }
  licenses.sort(compareLicenseEntries);
  return { licenses, documents };
}

function pythonRuntimeComponent(index) {
  if (typeof index.python !== "string" || !index.python.trim()) {
    throw new Error("open-source license index is missing the Python version");
  }
  if (typeof index.python_license !== "string") {
    throw new Error("open-source license index is missing the Python license document");
  }
  return {
    name: "Python",
    version: index.python,
    // The build index does not currently provide a normalized Python license
    // expression. Keep it unknown rather than inferring legal metadata.
    license: null,
    source: "https://www.python.org/",
    licenseFiles: [index.python_license],
  };
}

function validateComponent(raw, root) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error("open-source license component is invalid");
  }
  const name = requiredInventoryString(raw.name, "component name");
  const version = optionalInventoryString(raw.version, "component version");
  // Report build metadata verbatim. Do not normalize or reinterpret legal terms.
  const license = optionalInventoryString(
    raw.license_expression === undefined ? raw.license : raw.license_expression,
    "license expression"
  );
  const source = optionalInventoryUrl(
    raw.homepage === undefined ? raw.source : raw.homepage,
    "component source"
  );
  const files = raw.license_files === undefined ? raw.licenseFiles : raw.license_files;
  if (!Array.isArray(files) || files.length > MAX_LICENSE_DOCUMENTS) {
    throw new Error(`open-source license files are invalid for ${name}`);
  }
  return {
    name,
    version,
    license,
    source,
    licenseDocuments: files.map((value) => {
      const relativePath = requiredInventoryString(value, "license document path");
      return { root, relativePath, idPath: relativePath, maxBytes: MAX_LICENSE_TEXT_BYTES };
    }),
  };
}

function coreAndElectronComponents(options) {
  const ionicVersion = requiredInventoryString(options.appVersion, "Ionic version");
  const electronVersion = resolveElectronVersion(options);
  const ionicRoot = options.isPackaged
    ? legalDirectory(options)
    : path.resolve(options.appDir, "..");
  const electronRoot = options.isPackaged
    ? path.join(licensesDirectory(options), "Electron")
    : path.join(options.appDir, "node_modules", "electron", "dist");
  return [
    {
      name: "Ionic",
      version: ionicVersion,
      license: "MIT",
      source: "https://github.com/tacticocc/Ionic",
      licenseDocuments: [
        {
          root: ionicRoot,
          relativePath: options.isPackaged ? "IONIC-MIT-LICENSE.txt" : "LICENSE",
          idPath: "Ionic/IONIC-MIT-LICENSE.txt",
          maxBytes: MAX_LICENSE_TEXT_BYTES,
        },
      ],
    },
    {
      name: "Electron",
      version: electronVersion,
      license: "MIT",
      source: "https://github.com/electron/electron",
      licenseDocuments: [
        {
          root: electronRoot,
          relativePath: "LICENSE",
          idPath: "Electron/LICENSE",
          maxBytes: MAX_LICENSE_TEXT_BYTES,
        },
      ],
    },
    ...chromiumProductComponents(electronRoot),
  ];
}

function chromiumProductComponents(electronRoot) {
  const relativePath = "LICENSES.chromium.html";
  const parsed = readChromiumProducts(
    resolveAllowlistedLicensePath(electronRoot, relativePath),
    electronRoot
  );
  const byName = new Map();
  for (const product of parsed) {
    const key = product.title.toLowerCase();
    if (!byName.has(key)) byName.set(key, []);
    byName.get(key).push(product);
  }
  const labels = new Map();
  for (const products of byName.values()) {
    const ordered = [...products].sort((first, second) => stableTextCompare(first.id, second.id));
    for (const [position, product] of ordered.entries()) {
      labels.set(
        product.id,
        ordered.length === 1
          ? "Bundled notice"
          : `Bundled notice ${position + 1} of ${ordered.length}`
      );
    }
  }
  return parsed.map((product) => ({
    name: product.title,
    version: null,
    license: classifyChromiumLicense(product.text),
    source: product.source,
    licenseDocuments: [
      {
        root: electronRoot,
        relativePath,
        idPath: `Electron/Chromium/${product.id.slice(4)}.txt`,
        maxBytes: MAX_LICENSE_TEXT_BYTES,
        opaqueId: product.id,
        label: labels.get(product.id),
        text: product.text,
      },
    ],
  }));
}

function resolveElectronVersion(options) {
  const runtimeVersion = options.electronVersion || process.versions.electron;
  if (runtimeVersion !== undefined && runtimeVersion !== null) {
    return requiredInventoryString(runtimeVersion, "Electron version");
  }
  if (options.isPackaged) {
    throw new Error("open-source license inventory is missing the Electron version");
  }
  const packageRoot = path.join(options.appDir, "node_modules", "electron");
  const packageText = readBoundedRegularFile(
    path.join(packageRoot, "package.json"),
    packageRoot,
    128 * 1024,
    "Electron package metadata"
  );
  let metadata;
  try {
    metadata = JSON.parse(packageText);
  } catch {
    throw new Error("Electron package metadata is invalid JSON");
  }
  return requiredInventoryString(metadata?.version, "Electron version");
}

function desktopRuntimeComponents(options, packagedRoot) {
  return BUNDLED_DESKTOP_COMPONENTS.map((raw) => {
    const documentPaths = raw.documents || [
      options.isPackaged ? raw.packagedLicenseFile : raw.developmentLicenseFile,
    ];
    return {
      name: raw.name,
      version: raw.version,
      license: raw.license,
      source: raw.source,
      licenseDocuments: documentPaths.map((relativePath) => ({
        root: options.isPackaged
          ? packagedRoot
          : path.join(
              options.appDir,
              "src",
              ...(raw.developmentDirectory
                ? raw.developmentDirectory.split("/")
                : ["renderer", "fonts"])
            ),
        relativePath,
        idPath: raw.documents ? relativePath : raw.packagedLicenseFile,
        maxBytes: MAX_LICENSE_TEXT_BYTES,
      })),
    };
  });
}

function requiredInventoryString(value, label) {
  if (typeof value !== "string" || !value.trim() || value.length > 2_048 || /[\r\n\0]/.test(value)) {
    throw new Error(`open-source license index has an invalid ${label}`);
  }
  return value.trim();
}

function optionalInventoryString(value, label) {
  if (value === null || value === undefined || value === "") return null;
  return requiredInventoryString(value, label);
}

function optionalInventoryUrl(value, label) {
  const source = optionalInventoryString(value, label);
  if (source === null) return null;
  let parsed;
  try {
    parsed = new URL(source);
  } catch {
    throw new Error(`open-source license index has an invalid ${label}`);
  }
  if (parsed.protocol !== "https:" || parsed.username || parsed.password) {
    throw new Error(`open-source license index has an invalid ${label}`);
  }
  return source;
}

function readChromiumProducts(target, root) {
  const validation = validateBoundedRegularFile(
    target,
    root,
    MAX_CHROMIUM_NOTICES_BYTES,
    "Chromium bundled notices"
  );
  const stats = fs.statSync(validation.absoluteTarget, { bigint: true });
  const cacheKey = `${validation.absoluteTarget}\0${stats.size}\0${stats.mtimeNs}`;
  const cached = CHROMIUM_NOTICES_CACHE.get(cacheKey);
  if (cached) return cached;

  const html = readBoundedRegularFile(
    validation.absoluteTarget,
    root,
    MAX_CHROMIUM_NOTICES_BYTES,
    "Chromium bundled notices"
  );
  const products = parseChromiumNotices(html);
  CHROMIUM_NOTICES_CACHE.set(cacheKey, products);
  while (CHROMIUM_NOTICES_CACHE.size > MAX_CHROMIUM_CACHE_ENTRIES) {
    CHROMIUM_NOTICES_CACHE.delete(CHROMIUM_NOTICES_CACHE.keys().next().value);
  }
  return products;
}

function parseChromiumNotices(html) {
  const starts = [];
  for (let offset = html.indexOf(CHROMIUM_PRODUCT_START); offset !== -1; ) {
    starts.push(offset);
    if (starts.length > MAX_CHROMIUM_PRODUCTS) {
      throw new Error("Chromium bundled notices contain too many products");
    }
    offset = html.indexOf(CHROMIUM_PRODUCT_START, offset + CHROMIUM_PRODUCT_START.length);
  }
  if (!starts.length) throw new Error("Chromium bundled notices contain no products");
  starts.push(html.length);

  const products = [];
  const ids = new Set();
  for (let position = 0; position < starts.length - 1; position += 1) {
    const block = html.slice(starts[position], starts[position + 1]);
    const titleRaw = exactChromiumCapture(
      block,
      /^<div class="product">\s*<span class="title">([\s\S]*?)<\/span>/,
      "product title"
    );
    const homepageRaw = exactChromiumCapture(
      block,
      /<span class="homepage"><a href="([^"]*)">homepage<\/a><\/span>/,
      "product homepage"
    );
    const licenseMatch = /<div class="license">\s*<pre>([\s\S]*?)<\/pre>\s*<\/div>/.exec(block);
    if (!licenseMatch || licenseMatch.length !== 2) {
      throw new Error("Chromium bundled notices have an invalid product license text");
    }
    const trailing = block.slice(licenseMatch.index + licenseMatch[0].length);
    if (!/^\s*<\/div>[\s\S]*$/.test(trailing)) {
      throw new Error("Chromium bundled notices have an invalid product boundary");
    }
    const licenseRaw = licenseMatch[1];
    const title = decodedChromiumField(titleRaw, "product title").trim();
    const homepage = decodedChromiumField(homepageRaw, "product homepage").trim();
    const text = decodedChromiumField(licenseRaw, "product license text");
    if (!title || title.length > 2_048 || /[\r\n\0]/.test(title)) {
      throw new Error("Chromium bundled notices contain an invalid product title");
    }
    validateInlineLicenseText(text);
    const source = chromiumProductSource(homepage);
    // Position disambiguates byte-identical duplicate records while remaining
    // stable for one exact generated notices file.
    const identity = JSON.stringify([title, source, sha256(text), position]);
    const id = `oss-${sha256(`chromium-product-v1:${identity}`).slice(0, 32)}`;
    if (ids.has(id)) throw new Error("Chromium bundled notices contain a duplicate product");
    ids.add(id);
    products.push(Object.freeze({ id, title, source, text }));
  }
  if (products.length !== starts.length - 1) {
    throw new Error("Chromium bundled notices product count changed during parsing");
  }
  return Object.freeze(products);
}

function exactChromiumCapture(block, pattern, label) {
  const match = pattern.exec(block);
  if (!match || match.length !== 2) {
    throw new Error(`Chromium bundled notices have an invalid ${label}`);
  }
  return match[1];
}

function decodedChromiumField(value, label) {
  if (/<(?!\/?(?:br)\s*\/?>)/i.test(value)) {
    throw new Error(`Chromium bundled notices have markup in ${label}`);
  }
  return value
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/&quot;/g, '"')
    .replace(/&#x27;/gi, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&");
}

function chromiumProductSource(value) {
  if (!/^https?:\/\//i.test(value)) return null;
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    return null;
  }
  if (
    !["http:", "https:"].includes(parsed.protocol) ||
    parsed.username ||
    parsed.password ||
    parsed.href.length > 2_048
  ) {
    return null;
  }
  return value;
}

function validateInlineLicenseText(text) {
  if (typeof text !== "string" || !text || Buffer.byteLength(text, "utf8") > MAX_LICENSE_TEXT_BYTES) {
    throw new Error("Chromium product license text exceeds the safe per-product limit");
  }
  if (text.includes("\0")) throw new Error("Chromium product license text contains binary data");
}

function classifyChromiumLicense(text) {
  const normalized = text.replace(/\r\n?/g, "\n");
  const upper = normalized.toUpperCase();
  const recognized = new Set();
  if (/APACHE LICENSE\s+VERSION 2\.0, JANUARY 2004/.test(upper)) recognized.add("Apache-2.0");
  if (/MOZILLA PUBLIC LICENSE\s+VERSION 2\.0/.test(upper)) recognized.add("MPL-2.0");
  if (/GNU AFFERO GENERAL PUBLIC LICENSE/.test(upper)) {
    if (/VERSION 3/.test(upper)) recognized.add("AGPL-3.0");
  }
  if (/GNU LESSER GENERAL PUBLIC LICENSE/.test(upper)) {
    if (/VERSION 3/.test(upper)) recognized.add("LGPL-3.0");
    if (/VERSION 2\.1/.test(upper)) recognized.add("LGPL-2.1");
  }
  // LGPL 2.0 was titled the "GNU Library General Public License". Its
  // preamble also mentions the ordinary GPL, so detect that title first and
  // exclude it from the ordinary-GPL branch below. Without this distinction,
  // Electron's GTK notice is incorrectly presented as GPL-2.0.
  if (/GNU LIBRARY GENERAL PUBLIC LICENSE/.test(upper) && /VERSION 2/.test(upper)) {
    recognized.add("LGPL-2.0");
  }
  if (
    /GNU GENERAL PUBLIC LICENSE/.test(upper) &&
    !/GNU (?:AFFERO|LESSER|LIBRARY) GENERAL PUBLIC LICENSE/.test(upper)
  ) {
    if (/VERSION 3/.test(upper)) recognized.add("GPL-3.0");
    if (/VERSION 2/.test(upper)) recognized.add("GPL-2.0");
  }
  if (
    /PERMISSION TO USE, COPY, MODIFY, AND\/OR DISTRIBUTE THIS SOFTWARE FOR ANY PURPOSE WITH OR WITHOUT FEE IS HEREBY GRANTED/.test(
      upper
    )
  ) {
    recognized.add("ISC");
  }
  if (/REDISTRIBUTION AND USE IN SOURCE AND BINARY FORMS/.test(upper)) {
    if (/NEITHER THE NAME OF/.test(upper)) recognized.add("BSD-3-Clause");
    else if (/THIS LIST OF CONDITIONS AND THE FOLLOWING DISCLAIMER/.test(upper)) {
      recognized.add("BSD-2-Clause");
    }
  }
  if (
    /PERMISSION IS HEREBY GRANTED, FREE OF CHARGE, TO ANY PERSON OBTAINING A COPY/.test(
      upper
    ) &&
    /THE SOFTWARE IS PROVIDED [“\"]AS IS[”\"]/.test(upper)
  ) {
    recognized.add("MIT");
  }
  if (recognized.size === 1) return recognized.values().next().value;
  if (recognized.size > 1) return "Multiple terms — see text";
  return CUSTOM_LICENSE_LABEL;
}

function validateLicenseRelativePath(value) {
  if (
    typeof value !== "string" ||
    value.length > 1_024 ||
    !SAFE_RELATIVE_PATH_PATTERN.test(value) ||
    path.posix.isAbsolute(value) ||
    value.split("/").some((part) => part === "." || part === "..")
  ) {
    throw new Error("open-source license index contains an unsafe document path");
  }
  return value;
}

function resolveAllowlistedLicensePath(root, relativePath) {
  const absoluteRoot = path.resolve(root);
  const candidate = path.resolve(absoluteRoot, ...relativePath.split("/"));
  const relative = path.relative(absoluteRoot, candidate);
  if (!relative || relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
    throw new Error("open-source license document escapes its bundled directory");
  }
  return candidate;
}

function readBoundedRegularFile(target, root, maxBytes, label) {
  const { absoluteTarget } = validateBoundedRegularFile(target, root, maxBytes, label);
  const buffer = fs.readFileSync(absoluteTarget);
  if (buffer.length > maxBytes) throw new Error(`${label} exceeds the ${maxBytes}-byte limit`);
  const text = buffer.toString("utf8");
  if (!Buffer.from(text, "utf8").equals(buffer)) {
    throw new Error(`${label} is not valid UTF-8 text`);
  }
  return text;
}

function validateBoundedRegularFile(target, root, maxBytes, label) {
  const absoluteRoot = fs.realpathSync(root);
  const absoluteTarget = fs.realpathSync(target);
  const relative = path.relative(absoluteRoot, absoluteTarget);
  if (!relative || relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
    throw new Error(`${label} escapes its bundled directory`);
  }
  const stats = fs.statSync(absoluteTarget);
  if (!stats.isFile()) throw new Error(`${label} is not a regular file`);
  if (stats.size > maxBytes) throw new Error(`${label} exceeds the ${maxBytes}-byte limit`);
  return { absoluteRoot, absoluteTarget, size: stats.size };
}

function licenseDocumentId(component, relativePath) {
  const canonical = JSON.stringify([
    component.name,
    component.version,
    component.license,
    component.source,
    relativePath,
  ]);
  return `oss-${sha256(canonical).slice(0, 32)}`;
}

function licenseDocumentLabels(documents) {
  const paths = documents.map((document) => validateLicenseRelativePath(document.idPath));
  const basenameCounts = new Map();
  for (const value of paths) {
    const key = path.posix.basename(value).toLowerCase();
    basenameCounts.set(key, (basenameCounts.get(key) || 0) + 1);
  }
  const labels = paths.map((value) => {
    const basename = path.posix.basename(value);
    return basenameCounts.get(basename.toLowerCase()) === 1 ? basename : null;
  });

  const duplicateGroups = new Map();
  for (const [position, value] of paths.entries()) {
    if (labels[position] !== null) continue;
    const key = path.posix.basename(value).toLowerCase();
    if (!duplicateGroups.has(key)) duplicateGroups.set(key, []);
    duplicateGroups.get(key).push(position);
  }
  for (const positions of duplicateGroups.values()) {
    const groupedPaths = positions.map((position) => paths[position]);
    const parts = groupedPaths.map((value) => value.split("/"));
    const canTrimCommonRoot = parts.every(
      (value) => value.length > 1 && value[0].toLowerCase() === parts[0][0].toLowerCase()
    );
    const candidates = canTrimCommonRoot
      ? parts.map((value) => value.slice(1).join("/"))
      : groupedPaths;
    const unique = new Set(candidates.map((value) => value.toLowerCase())).size === candidates.length;
    for (const [offset, position] of positions.entries()) {
      labels[position] = unique ? candidates[offset] : groupedPaths[offset];
    }
  }
  return labels.map((derived, position) => {
    const explicit = documents[position].label;
    return explicit === undefined
      ? derived
      : requiredInventoryString(explicit, "license document label");
  });
}

function compareLicenseEntries(first, second) {
  return stableTextCompare(first.name.toLowerCase(), second.name.toLowerCase()) ||
    stableTextCompare(first.name, second.name) ||
    stableTextCompare(String(first.version || ""), String(second.version || "")) ||
    stableTextCompare(first.id, second.id);
}

function stableTextCompare(first, second) {
  if (first < second) return -1;
  if (first > second) return 1;
  return 0;
}

function sha256(text) {
  return crypto.createHash("sha256").update(text, "utf8").digest("hex");
}

function readAcceptance(recordFile) {
  try {
    const parsed = JSON.parse(fs.readFileSync(recordFile, "utf8"));
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : null;
  } catch (error) {
    if (error.code !== "ENOENT") console.error("could not read legal acceptance:", error.message);
    return null;
  }
}

function currentAgreement(options) {
  const document = readDocument("eula", options);
  return {
    agreementId: AGREEMENT_ID,
    edition: DESKTOP_EDITION,
    termsVersion: TERMS_VERSION,
    sha256: sha256(document.text),
    title: document.title,
    text: document.text,
  };
}

function legalStatus(options) {
  const agreement = currentAgreement(options);
  const record = readAcceptance(options.recordFile);
  const accepted = Boolean(
    record &&
      record.agreementId === agreement.agreementId &&
      record.edition === agreement.edition &&
      record.termsVersion === agreement.termsVersion &&
      record.sha256 === agreement.sha256
  );
  return {
    ...agreement,
    accepted,
    acceptedAt: accepted ? record.acceptedAt || null : null,
  };
}

function acceptAgreement(expected, options) {
  if (!expected || typeof expected !== "object" || Array.isArray(expected)) {
    throw new TypeError("agreement details are required");
  }
  const current = currentAgreement(options);
  if (
    expected.agreementId !== current.agreementId ||
    expected.edition !== current.edition ||
    expected.termsVersion !== current.termsVersion ||
    expected.sha256 !== current.sha256
  ) {
    throw new Error("The agreement changed before it was accepted. Review the current terms and try again.");
  }

  const record = {
    agreementId: current.agreementId,
    edition: current.edition,
    termsVersion: current.termsVersion,
    sha256: current.sha256,
    acceptedAt: new Date().toISOString(),
    appVersion: options.appVersion,
    acceptanceMethod: "in-app-clickwrap",
  };
  writeJsonAtomic(options.recordFile, record);
  return { ...record, accepted: true };
}

function writeJsonAtomic(target, value) {
  const temporary = `${target}.tmp`;
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(temporary, JSON.stringify(value, null, 2), "utf8");
  fs.renameSync(temporary, target);
}

module.exports = {
  AGREEMENT_ID,
  TERMS_VERSION,
  acceptAgreement,
  currentAgreement,
  documentPath,
  legalDirectory,
  licensesDirectory,
  legalStatus,
  listOpenSourceLicenses,
  readAcceptance,
  readDocument,
  readOpenSourceLicense,
  sha256,
  writeJsonAtomic,
};
