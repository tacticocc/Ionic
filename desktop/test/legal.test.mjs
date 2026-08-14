import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, it } from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const legal = require("../src/legal.js");
const temporaryDirectories = [];

function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "ionic-legal-test-"));
  const appDir = path.join(root, "desktop");
  fs.mkdirSync(appDir);
  fs.writeFileSync(path.join(root, "EULA.txt"), "Example agreement\n", "utf8");
  fs.writeFileSync(path.join(root, "LICENSE"), "MIT\n", "utf8");
  fs.writeFileSync(path.join(root, "THIRD-PARTY-NOTICES.txt"), "Notices\n", "utf8");
  temporaryDirectories.push(root);
  return {
    appDir,
    resourcesDir: path.join(root, "resources"),
    isPackaged: false,
    recordFile: path.join(root, "user-data", "legal.json"),
    appVersion: "0.1.0",
  };
}

function openSourceFixture({ packaged = false } = {}) {
  const options = fixture();
  options.isPackaged = packaged;
  options.electronVersion = "43.0.0";
  const root = packaged
    ? path.join(options.resourcesDir, "legal", "licenses")
    : path.join(options.appDir, "build", "essential", "legal", "licenses");
  fs.mkdirSync(root, { recursive: true });

  const index = {
    generated_by: "test fixture",
    edition: "essential",
    python: "3.12.13",
    python_license: "Python-3.12.13/LICENSE.txt",
    distributions: [
      {
        name: "No metadata package",
        version: "2.0.0",
        role: "transitive",
        license_expression: null,
        homepage: null,
        license_files: ["no-metadata/LICENSE"],
      },
      {
        name: "Zeta SDK",
        version: "1.2.3",
        role: "direct",
        license_expression: "Upstream-Proprietary-Identifier",
        homepage: "https://example.test/zeta",
        license_files: ["zeta/LICENSE.txt", "zeta/NOTICE", "zeta/nested/NOTICE"],
      },
    ],
  };
  fs.writeFileSync(path.join(root, "index.json"), `${JSON.stringify(index, null, 2)}\n`, "utf8");
  writeFixtureFile(root, "Python-3.12.13/LICENSE.txt", "Python exact terms\n");
  writeFixtureFile(root, "no-metadata/LICENSE", "Unknown metadata exact terms\n");
  writeFixtureFile(root, "zeta/LICENSE.txt", "\uFEFFZeta exact terms\n");
  writeFixtureFile(root, "zeta/NOTICE", "Zeta exact notice\n");
  writeFixtureFile(root, "zeta/nested/NOTICE", "Zeta nested exact notice\n");

  if (packaged) {
    writeFixtureFile(path.join(options.resourcesDir, "legal"), "IONIC-MIT-LICENSE.txt", "Ionic packaged MIT terms\n");
    const electron = path.join(root, "Electron");
    writeFixtureFile(electron, "LICENSE", "Electron packaged MIT terms\n");
    writeFixtureFile(electron, "LICENSES.chromium.html", chromiumFixtureHtml());
    writeFixtureFile(root, "Inter-4.1/OFL-1.1.txt", "Inter packaged terms\n");
    writeFixtureFile(root, "Material-Symbols/Apache-2.0.txt", "Material packaged terms\n");
    writeFixtureFile(
      root,
      "NSIS-3.0.4.1/COPYING.txt",
      "COMMON PUBLIC LICENSE VERSION 1.0\nSPECIAL EXCEPTION FOR LZMA COMPRESSION MODULE\n"
    );
    writeNsisPluginLicenseFixtures(root);
  } else {
    const electron = path.join(options.appDir, "node_modules", "electron", "dist");
    writeFixtureFile(electron, "LICENSE", "Electron development MIT terms\n");
    writeFixtureFile(electron, "LICENSES.chromium.html", chromiumFixtureHtml());
    const fonts = path.join(options.appDir, "src", "renderer", "fonts");
    writeFixtureFile(fonts, "INTER-OFL-1.1.txt", "Inter development terms\n");
    writeFixtureFile(
      fonts,
      "MATERIAL-SYMBOLS-APACHE-2.0.txt",
      "Material development terms\n"
    );
    writeFixtureFile(
      path.join(options.appDir, "src", "legal", "licenses"),
      "NSIS-3.0.4.1/COPYING.txt",
      "COMMON PUBLIC LICENSE VERSION 1.0\nSPECIAL EXCEPTION FOR LZMA COMPRESSION MODULE\n"
    );
    writeNsisPluginLicenseFixtures(
      path.join(options.appDir, "src", "legal", "licenses")
    );
  }
  return { options, root, index };
}

function writeNsisPluginLicenseFixtures(root) {
  writeFixtureFile(root, "NSIS-plugins/LGPL-2.1.txt", "GNU LESSER GENERAL PUBLIC LICENSE Version 2.1\n");
  writeFixtureFile(root, "NSIS-plugins/StdUtils-1.14-CLARIFICATION.txt", "StdUtils exact clarification\n");
  writeFixtureFile(root, "NSIS-plugins/Nsis7z-19.00-LZMA-SDK-License.txt", "LZMA SDK public-domain terms\n");
  writeFixtureFile(root, "NSIS-plugins/Nsis7z-19.00-7-Zip-License.txt", "7-Zip 19.00 exact terms\n");
  writeFixtureFile(root, "NSIS-plugins/UAC-0.2.4c-License.txt", "UAC zlib exact terms\n");
  writeFixtureFile(root, "NSIS-plugins/PROVENANCE.txt", "Exact release provenance\n");
}

function chromiumFixtureHtml() {
  return `<!doctype html>
<html><body>
<div class="product">
<span class="title">Alpha &amp; Utility</span>
<span class="homepage"><a href="https://example.test/alpha?one=1&amp;two=2">homepage</a></span>
<div class="license"><pre>Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software").
THE SOFTWARE IS PROVIDED "AS IS".</pre></div>
</div>
<div class="product">
<span class="title">Duplicate</span>
<span class="homepage"><a href="Internal">homepage</a></span>
<div class="license"><pre>Unique custom exact terms &lt;retain&gt;.</pre></div>
</div>
<div class="product">
<span class="title">Duplicate</span>
<span class="homepage"><a href="https://example.test/duplicate">homepage</a></span>
<div class="license"><pre>Apache License
Version 2.0, January 2004
http://www.apache.org/licenses/
Permission is hereby granted, free of charge, to any person obtaining a copy.
THE SOFTWARE IS PROVIDED "AS IS".</pre></div>
</div>
<div class="product">
<span class="title">Legacy Library</span>
<span class="homepage"><a href="https://example.test/library">homepage</a></span>
<div class="license"><pre>GNU LIBRARY GENERAL PUBLIC LICENSE
Version 2, June 1991
This is the Library General Public License and it refers to the ordinary GNU General Public License.</pre></div>
</div>
</body></html>
`;
}

function writeFixtureFile(root, relativePath, text) {
  const target = path.join(root, ...relativePath.split("/"));
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, text, "utf8");
  return target;
}

afterEach(() => {
  while (temporaryDirectories.length) {
    fs.rmSync(temporaryDirectories.pop(), { recursive: true, force: true });
  }
});

describe("legal acceptance", () => {
  it("requires acceptance for a new or corrupt local record", () => {
    const options = fixture();
    assert.equal(legal.legalStatus(options).accepted, false);
    fs.mkdirSync(path.dirname(options.recordFile), { recursive: true });
    fs.writeFileSync(options.recordFile, "not json", "utf8");
    assert.equal(legal.legalStatus(options).accepted, false);
  });

  it("records the exact agreement version and hash atomically", () => {
    const options = fixture();
    const status = legal.legalStatus(options);
    assert.equal(status.edition, "essential");
    assert.equal(status.agreementId, "tactico-ionic-essential-official-distribution");
    const accepted = legal.acceptAgreement(status, options);
    assert.equal(accepted.accepted, true);
    assert.equal(legal.legalStatus(options).accepted, true);
    assert.equal(fs.existsSync(`${options.recordFile}.tmp`), false);
  });

  it("rejects acceptance details that name another edition", () => {
    const options = fixture();
    const status = legal.legalStatus(options);
    assert.throws(
      () => legal.acceptAgreement({ ...status, edition: "other" }, options),
      /agreement changed/i
    );
  });

  it("prompts again when the agreement text changes", () => {
    const options = fixture();
    legal.acceptAgreement(legal.legalStatus(options), options);
    fs.appendFileSync(path.join(path.dirname(options.appDir), "EULA.txt"), "Material update\n");
    assert.equal(legal.legalStatus(options).accepted, false);
  });

  it("rejects stale or forged acceptance details", () => {
    const options = fixture();
    const status = legal.legalStatus(options);
    assert.throws(
      () => legal.acceptAgreement({ ...status, sha256: "0".repeat(64) }, options),
      /agreement changed/i
    );
  });

  it("allows only known packaged legal documents", () => {
    const options = fixture();
    assert.equal(legal.readDocument("mit", options).text, "MIT\n");
    assert.deepEqual(legal.readDocument("third-party", options), {
      name: "third-party",
      title: "Third-Party Notices",
      text: "Notices\n",
    });
    assert.throws(() => legal.readDocument("../../secret", options), /unknown legal document/i);
  });
});

describe("open-source license inventory", () => {
  it("lists opaque document metadata and reads the exact allowlisted text in development", () => {
    const { options } = openSourceFixture();

    const first = legal.listOpenSourceLicenses(options);
    const second = legal.listOpenSourceLicenses(options);

    assert.deepEqual(first, second);
    assert.equal(first.licenses.length, 23);
    for (const entry of first.licenses) {
      assert.deepEqual(Object.keys(entry), [
        "id",
        "name",
        "version",
        "license",
        "source",
        "document",
      ]);
      assert.match(entry.id, /^oss-[a-f0-9]{32}$/);
      assert.equal(entry.id.includes("/"), false);
      assert.match(entry.document, /^[A-Za-z0-9 ._/-]+$/);
    }

    const zeta = first.licenses.find(
      (entry) => entry.name === "Zeta SDK" && legal.readOpenSourceLicense(entry.id, options).text.includes("Zeta exact terms")
    );
    assert.ok(zeta);
    assert.equal(zeta.license, "Upstream-Proprietary-Identifier");
    assert.equal(zeta.source, "https://example.test/zeta");
    assert.ok(["LICENSE.txt", "NOTICE", "nested/NOTICE"].includes(zeta.document));
    assert.deepEqual(legal.readOpenSourceLicense(zeta.id, options), {
      ...zeta,
      text: "\uFEFFZeta exact terms\n",
    });

    const unknown = first.licenses.find((entry) => entry.name === "No metadata package");
    const python = first.licenses.find((entry) => entry.name === "Python");
    assert.equal(unknown.license, null);
    assert.equal(unknown.source, null);
    assert.equal(python.license, null);
    const ionic = first.licenses.find((entry) => entry.name === "Ionic");
    const electron = first.licenses.find((entry) => entry.name === "Electron");
    const bundled = first.licenses.filter((entry) => entry.document.startsWith("Bundled notice"));
    assert.equal(ionic.version, "0.1.0");
    assert.equal(ionic.license, "MIT");
    assert.equal(legal.readOpenSourceLicense(ionic.id, options).text, "MIT\n");
    assert.equal(electron.version, "43.0.0");
    assert.equal(electron.license, "MIT");
    assert.equal(
      legal.readOpenSourceLicense(electron.id, options).text,
      "Electron development MIT terms\n"
    );
    assert.equal(bundled.length, 4);
    const alpha = bundled.find((entry) => entry.name === "Alpha & Utility");
    const duplicate = bundled.filter((entry) => entry.name === "Duplicate");
    assert.equal(alpha.version, null);
    assert.equal(alpha.license, "MIT");
    assert.equal(alpha.source, "https://example.test/alpha?one=1&two=2");
    assert.equal(
      bundled.find((entry) => entry.name === "Legacy Library")?.license,
      "LGPL-2.0"
    );
    assert.equal(
      legal.readOpenSourceLicense(alpha.id, options).text,
      'Permission is hereby granted, free of charge, to any person obtaining a copy\n' +
        'of this software and associated documentation files (the "Software").\n' +
        'THE SOFTWARE IS PROVIDED "AS IS".'
    );
    assert.deepEqual(duplicate.map((entry) => entry.document).sort(), [
      "Bundled notice 1 of 2",
      "Bundled notice 2 of 2",
    ]);
    assert.deepEqual(duplicate.map((entry) => entry.license).sort(), [
      "Custom or multiple terms — see text",
      "Multiple terms — see text",
    ]);
    assert.equal(duplicate.some((entry) => entry.source === null), true);
    assert.ok(first.licenses.some((entry) => entry.name === "Inter"));
    assert.ok(first.licenses.some((entry) => entry.name === "Material Symbols Rounded"));
    const nsis = first.licenses.find((entry) => entry.name === "NSIS");
    assert.equal(nsis.version, "3.0.4.1");
    assert.match(nsis.license, /CPL-1\.0.*LZMA linking exception/);
    const nsisText = legal.readOpenSourceLicense(nsis.id, options).text;
    assert.match(nsisText, /COMMON PUBLIC LICENSE VERSION 1\.0/);
    assert.match(nsisText, /SPECIAL EXCEPTION FOR LZMA COMPRESSION MODULE/);
    const stdUtils = first.licenses.filter((entry) => entry.name === "StdUtils NSIS plug-in");
    const nsis7z = first.licenses.filter((entry) => entry.name === "Nsis7z NSIS plug-in");
    const uac = first.licenses.filter((entry) => entry.name === "UAC NSIS plug-in");
    assert.equal(stdUtils.length, 3);
    assert.equal(nsis7z.length, 4);
    assert.equal(uac.length, 2);
    assert.match(stdUtils[0].license, /LGPL-2\.1-or-later/u);
    assert.match(nsis7z[0].license, /LGPL-2\.1-or-later/u);
    assert.equal(uac[0].license, "zlib License");
    assert.equal(first.licenses.some((entry) => /WinShell/u.test(entry.name)), false);
    const groupedLabels = new Map();
    for (const entry of first.licenses) {
      if (!groupedLabels.has(entry.name)) groupedLabels.set(entry.name, []);
      groupedLabels.get(entry.name).push(entry);
    }
    for (const entries of groupedLabels.values()) {
      assert.equal(
        new Set(entries.map((entry) => entry.document.toLowerCase())).size,
        entries.length
      );
    }
    assert.deepEqual(
      first.licenses
        .filter((entry) => entry.name === "Zeta SDK")
        .map((entry) => entry.document)
        .sort(),
      ["LICENSE.txt", "NOTICE", "nested/NOTICE"]
    );
  });

  it("uses the packaged legal directory and its packaged font mappings", () => {
    const { options } = openSourceFixture({ packaged: true });
    const inventory = legal.listOpenSourceLicenses(options);
    const inter = inventory.licenses.find((entry) => entry.name === "Inter");
    const material = inventory.licenses.find((entry) => entry.name === "Material Symbols Rounded");
    const ionic = inventory.licenses.find((entry) => entry.name === "Ionic");
    const electron = inventory.licenses.find((entry) => entry.name === "Electron");
    const alpha = inventory.licenses.find((entry) => entry.name === "Alpha & Utility");

    assert.equal(
      legal.readOpenSourceLicense(ionic.id, options).text,
      "Ionic packaged MIT terms\n"
    );
    assert.equal(
      legal.readOpenSourceLicense(electron.id, options).text,
      "Electron packaged MIT terms\n"
    );
    assert.equal(
      legal.readOpenSourceLicense(alpha.id, options).text.includes("Permission is hereby granted"),
      true
    );
    assert.equal(legal.readOpenSourceLicense(inter.id, options).text, "Inter packaged terms\n");
    assert.equal(
      legal.readOpenSourceLicense(material.id, options).text,
      "Material packaged terms\n"
    );
  });

  it("rejects arbitrary and unknown document ids", () => {
    const { options } = openSourceFixture();
    assert.throws(
      () => legal.readOpenSourceLicense("../../EULA.txt", options),
      /unknown open-source license document/i
    );
    assert.throws(
      () => legal.readOpenSourceLicense(`oss-${"0".repeat(32)}`, options),
      /unknown open-source license document/i
    );
  });

  it("rejects traversal supplied by a malformed generated index", () => {
    const { options, root, index } = openSourceFixture();
    index.distributions[0].license_files = ["../secret.txt"];
    fs.writeFileSync(path.join(root, "index.json"), JSON.stringify(index), "utf8");

    assert.throws(
      () => legal.listOpenSourceLicenses(options),
      /unsafe document path/i
    );
  });

  it("rejects license files that exceed the bounded read limit", () => {
    const { options, root } = openSourceFixture();
    const target = path.join(root, "zeta", "LICENSE.txt");
    fs.truncateSync(target, 2 * 1024 * 1024 + 1);

    assert.throws(
      () => legal.listOpenSourceLicenses(options),
      /exceeds the .*byte limit/i
    );
  });

  it("grants the larger source-file cap only to the known Chromium notices file", () => {
    const { options } = openSourceFixture();
    const chromiumPath = path.join(
      options.appDir,
      "node_modules",
      "electron",
      "dist",
      "LICENSES.chromium.html"
    );
    const padding = " ".repeat(2 * 1024 * 1024 + 1);
    fs.writeFileSync(chromiumPath, chromiumFixtureHtml().replace("<html><body>", `<html><body>${padding}`));
    assert.equal(
      legal.listOpenSourceLicenses(options).licenses.filter((entry) =>
        entry.document.startsWith("Bundled notice")
      ).length,
      4
    );

    fs.truncateSync(chromiumPath, 32 * 1024 * 1024 + 1);
    assert.throws(
      () => legal.listOpenSourceLicenses(options),
      /exceeds the .*byte limit/i
    );
  });

  it("rejects invalid UTF-8 instead of replacing bytes in exact license text", () => {
    const { options, root } = openSourceFixture();
    const zeta = legal
      .listOpenSourceLicenses(options)
      .licenses.find((entry) => entry.name === "Zeta SDK" && entry.license === "Upstream-Proprietary-Identifier");
    // The first deterministic Zeta document may be NOTICE or LICENSE, so
    // corrupt each in turn until the selected opaque id addresses one of them.
    const candidates = [
      path.join(root, "zeta", "LICENSE.txt"),
      path.join(root, "zeta", "NOTICE"),
      path.join(root, "zeta", "nested", "NOTICE"),
    ];
    let rejected = false;
    for (const candidate of candidates) {
      const original = fs.readFileSync(candidate);
      fs.writeFileSync(candidate, Buffer.from([0xff, 0xfe, 0xfd]));
      try {
        legal.readOpenSourceLicense(zeta.id, options);
      } catch (error) {
        if (/not valid UTF-8/i.test(error.message)) rejected = true;
      } finally {
        fs.writeFileSync(candidate, original);
      }
      if (rejected) break;
    }
    assert.equal(rejected, true);
  });

  it("rejects a symlink that escapes the bundled license directory", (context) => {
    const { options, root, index } = openSourceFixture();
    const outside = path.join(path.dirname(root), "outside-license.txt");
    fs.writeFileSync(outside, "outside secret\n", "utf8");
    const link = path.join(root, "zeta", "escape.txt");
    try {
      fs.symlinkSync(outside, link, "file");
    } catch (error) {
      if (["EPERM", "EACCES", "ENOTSUP"].includes(error.code)) {
        context.skip(`symlinks unavailable: ${error.code}`);
        return;
      }
      throw error;
    }
    index.distributions[1].license_files = ["zeta/escape.txt"];
    fs.writeFileSync(path.join(root, "index.json"), JSON.stringify(index), "utf8");

    assert.throws(
      () => legal.listOpenSourceLicenses(options),
      /escapes its bundled directory/i
    );
  });

  it("caps the number of generated components", () => {
    const { options, root, index } = openSourceFixture();
    index.distributions = Array.from({ length: 2_001 }, (_, position) => ({
      name: `package-${position}`,
      version: "1.0.0",
      license_expression: "MIT",
      homepage: null,
      license_files: [],
    }));
    fs.writeFileSync(path.join(root, "index.json"), JSON.stringify(index), "utf8");

    assert.throws(
      () => legal.listOpenSourceLicenses(options),
      /invalid component count/i
    );
  });

  it("parses every real Electron Chromium product into an exact plain-text row", (context) => {
    const appDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
    const electronNotices = path.join(
      appDir,
      "node_modules",
      "electron",
      "dist",
      "LICENSES.chromium.html"
    );
    const generatedInventory = path.join(
      appDir,
      "build",
      "essential",
      "legal",
      "licenses",
      "index.json"
    );
    if (!fs.existsSync(electronNotices) || !fs.existsSync(generatedInventory)) {
      context.skip("Electron runtime notices or the Essential license inventory are not built");
      return;
    }
    const options = {
      appDir,
      resourcesDir: "unused",
      isPackaged: false,
      appVersion: "0.4.0",
    };
    const inventory = legal.listOpenSourceLicenses(options);
    const bundled = inventory.licenses.filter((entry) =>
      entry.document.startsWith("Bundled notice")
    );

    assert.equal(bundled.length, 773);
    assert.equal(
      inventory.licenses.some(
        (entry) => entry.name === "Chromium, Node.js, and bundled components"
      ),
      false
    );
    assert.equal(new Set(bundled.map((entry) => entry.id)).size, 773);
    const sample = legal.readOpenSourceLicense(bundled[0].id, options);
    assert.equal(sample.text.includes("<pre>"), false);
    assert.ok(sample.text.length > 0);
    assert.ok([
      "MIT",
      "Apache-2.0",
      "BSD-2-Clause",
      "BSD-3-Clause",
      "ISC",
      "MPL-2.0",
      "GPL-2.0",
      "GPL-3.0",
      "LGPL-2.0",
      "LGPL-2.1",
      "LGPL-3.0",
      "AGPL-3.0",
      "Multiple terms — see text",
      "Custom or multiple terms — see text",
    ].includes(sample.license));
  });
});
