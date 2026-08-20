import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const RENDERER = path.resolve(HERE, "..", "src", "renderer");
const html = fs.readFileSync(path.join(RENDERER, "index.html"), "utf8");
const css = fs.readFileSync(path.join(RENDERER, "styles.css"), "utf8");
const js = fs.readFileSync(path.join(RENDERER, "app.js"), "utf8");
const themeInit = fs.readFileSync(path.join(RENDERER, "theme-init.js"), "utf8");
const main = fs.readFileSync(path.resolve(RENDERER, "..", "main.js"), "utf8");
const preload = fs.readFileSync(path.resolve(RENDERER, "..", "preload.js"), "utf8");
const packageJson = JSON.parse(fs.readFileSync(path.resolve(RENDERER, "..", "..", "package.json"), "utf8"));

describe("renderer accessibility contract", () => {
  it("announces boot, setup failures, check progress, and notifications", () => {
    assert.match(html, /id="boot"[^>]+role="status"[^>]+aria-live="polite"/);
    assert.match(html, /id="setup-message"/);
    assert.match(html, /id="check-progress"[^>]+role="status"[^>]+aria-live="polite"/);
    assert.match(html, /id="toast"[^>]+role="status"[^>]+aria-live="polite"/);
  });

  it("keeps current navigation and contract selection programmatic", () => {
    assert.match(html, /data-view="contracts"[\s\S]+?aria-current="page"/);
    assert.match(js, /setAttribute\("aria-current", "page"\)/);
    assert.match(js, /handleContractKeydown/);
  });

  it("defines visible focus, narrow reflow, and reduced motion", () => {
    assert.match(css, /:focus-visible/);
    assert.match(css, /@media \(max-width: 800px\)/);
    assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
  });

  it("makes graph nodes keyboard operable", () => {
    assert.match(js, /group\.setAttribute\("tabindex", "0"\)/);
    assert.match(js, /event\.key === "Enter" \|\| event\.key === " "/);
  });

  it("gates first launch with an accessible agreement surface", () => {
    assert.match(html, /id="legal"[\s\S]+?role="dialog"[\s\S]+?aria-modal="true"/);
    assert.match(html, /id="legal-agree"[^>]+type="checkbox"/);
    assert.match(html, /id="legal-accept"[^>]+disabled/);
    assert.match(html, /The Ionic CLI and source remain available under the MIT License if you decline\./);
    assert.match(js, /window\.ionic\.legalStatus\(\)/);
    assert.match(js, /window\.ionic\.acceptLegal\(state\.legal\.agreement\)/);
    assert.match(js, /state\.legal\.agreement = result\.data[\s\S]+?edition: result\.data\.edition/);
    assert.match(js, /if \(!state\.legal\.accepted\)[\s\S]+?openLegalDocument\("eula", \{ required: true \}\)/);
  });

  it("checks legal status before registering bridge-backed menu actions", () => {
    const initialization = js.indexOf("const legalInitialization = initializeLegal();");
    const firstMenuRegistration = js.indexOf('window.ionic.onMenu("menu:register"');
    assert.notEqual(initialization, -1);
    assert.notEqual(firstMenuRegistration, -1);
    assert.ok(initialization < firstMenuRegistration);
  });

  it("enforces accepted terms in the main process for every sensitive IPC", () => {
    assert.match(
      main,
      /if \(!LEGAL_GATE_EXEMPT_CHANNELS\.has\(channel\)\) await requireAcceptedLegal\(\)/
    );
    const exemptions = main.match(
      /const LEGAL_GATE_EXEMPT_CHANNELS = new Set\(\[([\s\S]*?)\]\);/
    )?.[1] || "";
    for (const sensitive of [
      "app:save-settings",
      "app:save-credential",
      "ionic:register",
      "ionic:workspace-scan",
      "ionic:workspace-check",
      "ionic:workspace-sync",
      "ionic:check",
      "dialog:pick-file",
      "shell:reveal",
    ]) {
      assert.doesNotMatch(exemptions, new RegExp(sensitive.replaceAll(":", "\\:")));
    }
    for (const recovery of [
      "legal:status",
      "legal:accept",
      "app:clear-credential",
      "app:reset-credentials",
      "subscription:cancel",
      "subscription:logout",
    ]) {
      assert.match(exemptions, new RegExp(recovery.replaceAll(":", "\\:")));
    }
  });

  it("never leaves startup waiting forever when the secure bridge is missing or stalled", () => {
    assert.match(js, /const LEGAL_STATUS_TIMEOUT_MS = 12_000/);
    assert.match(js, /typeof window\.ionic\?\.legalStatus !== "function"/);
    assert.match(js, /statusRequest = window\.ionic\.legalStatus\(\)/);
    assert.match(
      js,
      /bridgeEnvelope\(statusRequest, \{[\s\S]+?timeoutMs: LEGAL_STATUS_TIMEOUT_MS[\s\S]+?desktop terms check took too long/
    );
    assert.match(js, /const request = \+\+state\.legal\.initializationRequest/);
    assert.match(js, /if \(request !== state\.legal\.initializationRequest\) return/);
    assert.match(js, /showLegalStatusError\(error\)/);
    assert.match(js, /\$\("#legal-retry"\)\.focus/);
  });

  it("renders legal documents as plain text and exposes every legal menu channel", () => {
    assert.match(js, /text\.textContent = legalPayload\(result\.data, name\)/);
    assert.doesNotMatch(js, /legal-text[^\n]+innerHTML/);
    assert.match(js, /menu:show-eula/);
    assert.match(js, /menu:show-mit/);
    assert.match(js, /menu:show-third-party/);
  });

  it("browses exact packaged open source licenses without inventing inventory", () => {
    assert.match(html, /data-legal-document="third-party">\s*Open Source Licenses/);
    assert.match(html, /id="open-source-filter"[^>]+type="search"/);
    assert.match(html, /id="open-source-list"[^>]+role="list"[^>]+aria-label="Packaged open source components"/);
    assert.match(html, /id="open-source-detail"[^>]+tabindex="0"[^>]+aria-label="Packaged license text"/);
    for (const id of [
      "open-source-license-name",
      "open-source-license-version",
      "open-source-license-type",
      "open-source-license-source",
      "open-source-license-document",
      "open-source-license-text",
    ]) {
      assert.match(html, new RegExp(`id="${id}"`));
    }
    assert.match(js, /typeof window\.ionic\?\.listOpenSourceLicenses === "function"/);
    assert.match(js, /typeof window\.ionic\?\.readOpenSourceLicense === "function"/);
    assert.match(js, /window\.ionic\.listOpenSourceLicenses\(\)/);
    assert.match(js, /window\.ionic\.readOpenSourceLicense\(item\.id\)/);
    assert.match(js, /document: safeLicenseField\(value\.document/);
    assert.match(js, /open-source-list-document/);
    assert.match(js, /#open-source-license-text"\)\.textContent/);
    assert.doesNotMatch(js, /open-source-license-text[^\n]+innerHTML/);
    assert.match(js, /const isLicenseBrowser = name === "third-party"[\s\S]+?if \(isLicenseBrowser\)[\s\S]+?await loadOpenSourceLicenses\(\)[\s\S]+?return/);
    assert.match(css, /\.open-source-browser\s*\{[\s\S]+?grid-template-columns:/);
    assert.match(css, /@media \(max-width: 620px\)[\s\S]+?\.open-source-browser\s*\{[\s\S]+?grid-template-columns: 1fr/);
    assert.match(css, /@media \(forced-colors: active\)[\s\S]+?\.open-source-licenses/);
    for (const [from, to] of [
      ["node_modules/electron/dist/LICENSE", "legal/licenses/Electron/LICENSE"],
      [
        "node_modules/electron/dist/LICENSES.chromium.html",
        "legal/licenses/Electron/LICENSES.chromium.html",
      ],
    ]) {
      assert.ok(
        packageJson.build.extraResources.some(
          (resource) => resource.from === from && resource.to === to
        ),
        `${from} must be packaged for the cross-platform license browser`
      );
    }
  });

  it("repairs the included engine without runtime installation instructions", () => {
    assert.match(html, /id="setup-managed"[\s\S]+?Use managed engine/);
    assert.match(html, /id="setup-choose"[\s\S]+?Choose executable/);
    assert.match(html, /id="setup-retry"[\s\S]+?Retry/);
    assert.match(js, /onMenu\("menu:use-managed-cli", useManagedCli\)/);
    assert.match(js, /window\.ionic\.useManagedCli\(\)/);
    assert.doesNotMatch(`${html}\n${js}`, /pip install|download the cli/i);
  });

  it("provides a dedicated, searchable settings workspace", () => {
    assert.match(html, /id="btn-settings"[\s\S]+?Settings/);
    assert.match(html, /id="settings"[\s\S]+?id="settings-back"[\s\S]+?id="settings-filter"/);
    for (const category of ["appearance", "ai", "analysis", "workspace", "engine", "legal"]) {
      assert.match(html, new RegExp(`data-settings-category="${category}"`));
      assert.match(html, new RegExp(`data-settings-section="${category}"`));
    }
    assert.match(js, /function filterSettings\(\)/);
    assert.match(js, /event\.key === "," && \(event\.ctrlKey \|\| event\.metaKey\)/);
    assert.match(js, /onMenu\("menu:settings", \(\) => openSettings\(\)\)/);
    assert.doesNotMatch(html, /data-settings-category="connections"|id="settings-connections"/);
    assert.doesNotMatch(`${main}\n${preload}`, /beginGithubConnection|beginGoogleDriveSelection/);
  });

  it("separates official subscription runtimes from generic API providers", () => {
    assert.match(html, /Provider-native access only/);
    assert.match(html, /never reads browser cookies or turns a subscription into an API key/);
    assert.match(html, /ChatGPT \/ Codex review[\s\S]+?app-server proves Ionic's restricted read-only boundary/);
    assert.match(html, /Grok Build[\s\S]+?official Grok Build ACP or headless runtime/);
    for (const id of ["openai-codex", "xai-grok-build"]) {
      assert.match(html, new RegExp(`data-model-runtime-id="${id}"`));
    }
    assert.doesNotMatch(html, /anthropic-claude-code/);
    assert.match(js, /ready: "Installed"/);
    assert.match(js, /missing: "Not installed"/);
    assert.match(js, /unsafe_wrapper: "Blocked"/);
    assert.match(js, /authenticationTarget\.textContent = "Not inspected here"/);
    assert.doesNotMatch(js, /ready: "Connected"/);
    assert.match(js, /subscriptionRuntimeRecords\(result\.data\?\.runtimes\)/);
    for (const provider of ["openai-codex", "xai-grok-build"]) {
      assert.match(html, new RegExp(`data-subscription-provider="${provider}"`));
    }
    for (const action of ["connect-browser", "connect-device", "disconnect", "copy-code", "cancel-login"]) {
      assert.match(html, new RegExp(`data-subscription-action="${action}"`));
    }
    assert.match(html, /data-subscription-provider="openai-codex"[\s\S]+?data-subscription-field="verification-link"[\s\S]+?Open sign-in page/);
    assert.match(html, /data-subscription-provider="xai-grok-build"[\s\S]+?data-subscription-field="verification-link"[\s\S]+?Open sign-in page/);
    assert.match(js, /window\.ionic\.subscriptionStatus\(provider, inspect\)/);
    assert.match(js, /window\.ionic\.beginSubscriptionLogin\([\s\S]+?provider/);
    assert.match(js, /window\.ionic\.pollSubscriptionLogin\(provider, login\.loginId\)/);
    assert.match(js, /window\.ionic\.cancelSubscriptionLogin\(provider, loginId\)/);
    assert.match(js, /window\.ionic\.logoutSubscription\(provider\)/);
    assert.match(js, /Authentication has not been inspected/);
    assert.match(js, /connected: raw\.connected === true \? true : raw\.connected === false \? false : null/);
    assert.doesNotMatch(js, /provider !== "openai-codex"/);
    assert.match(js, /device\.classList\.toggle\("hidden", !login\?\.loginId\)/);
    assert.match(js, /normalizedSubscriptionVerificationUrl\(provider, result\.data\?\.verificationUrl\)/);
    assert.match(js, /verificationLink\.hidden = !login\?\.verificationUrl/);
    assert.match(js, /window\.ionic\.safeSubscriptionVerificationUrl\(provider, raw\)/);
    assert.match(html, /id="codex-subscription-model"/);
    assert.match(html, /id="codex-subscription-effort"/);
    assert.match(html, /id="grok-subscription-model"/);
    assert.match(html, /id="grok-subscription-effort"/);
    assert.match(html, /data-subscription-consent="openai-codex"/);
    assert.match(html, /data-subscription-consent="xai-grok-build"/);
    assert.match(html, /data-subscription-disclosure-list="sends"/);
    assert.match(html, /data-subscription-disclosure-list="boundary"/);
    assert.match(html, /It is not the Codex app and grants no general workspace-agent permission/);
    assert.match(html, /Sign out of Ionic's Codex profile/);
    assert.match(js, /dedicated Codex profile[\s\S]+?normal Codex CLI and IDE profile is separate/);
    assert.doesNotMatch(js, /shared Codex session/);
    assert.match(js, /raw\.disclosure\.authentication/);
    assert.match(js, /renderSubscriptionDisclosure\(root, status\?\.disclosure\)/);
    assert.match(js, /window\.ionic\.subscriptionModels\(provider, consent\)/);
    assert.match(css, /@media \(max-width: 620px\)[\s\S]+?\.subscription-provider-row\s*\{[\s\S]+?grid-template-columns:/);
  });

  it("switches between direct APIs and selectable subscription runtimes without enabling semantics", () => {
    assert.match(html, /id="model-access-mode"[\s\S]+?name="model-access-mode" value="api"[\s\S]+?name="model-access-mode" value="subscription"/);
    assert.match(html, /data-model-access-panel="api"/);
    assert.match(html, /data-model-access-panel="subscription"/);
    assert.match(html, /name="subscription-runtime" value="openai-codex"/);
    assert.match(html, /name="subscription-runtime" value="xai-grok-build"/);
    assert.doesNotMatch(html, /name="subscription-runtime" value="anthropic-claude-code"/);
    assert.match(html, /Changing this mode or selection never enables semantic review automatically/);
    assert.match(js, /saveSettingsPatch\([\s\S]+?\{ modelAccessMode: mode \}/);
    assert.match(js, /saveSettingsPatch\([\s\S]+?\{ subscriptionRuntime: runtime \}/);
    assert.match(js, /\$\$\('\[data-subscription-config-provider\]'\)/);
    assert.match(js, /modelAccessMode: normalizedModelAccessMode\(state\.settings\.modelAccessMode\)/);
    assert.match(js, /subscriptionRuntime: normalizedSubscriptionRuntimeSelection\(state\.settings\.subscriptionRuntime\)/);
    assert.match(css, /\.model-access-switch:has\(input\[value="subscription"\]:checked\)::before/);
  });

  it("keeps model configuration free-form and provider aware", () => {
    assert.match(html, /id="setting-provider"[\s\S]+?value="anthropic"[\s\S]+?value="openai"[\s\S]+?value="google"[\s\S]+?value="xai"[\s\S]+?value="local"[\s\S]+?value="none"/);
    assert.match(html, /value="xai">SpaceXAI · Grok/);
    assert.match(html, /id="setting-model"[^>]+type="text"[^>]+list="setting-model-presets"/);
    assert.match(html, /<datalist id="setting-model-presets"/);
    assert.match(html, /data-provider-only="anthropic"[\s\S]+?id="setting-effort"/);
    assert.match(html, /data-provider-only="local"[\s\S]+?id="setting-local-url"/);
    assert.match(js, /providerModels:\s*\{[\s\S]+?anthropic:[\s\S]+?openai:[\s\S]+?google:[\s\S]+?xai:[\s\S]+?local:/);
    for (const model of ["claude-sonnet-5", "gpt-5.2", "gemini-3.6-flash", "grok-4.5", "qwen2.5-coder"]) {
      assert.match(js, new RegExp(model.replaceAll(".", "\\.")));
    }
    assert.match(js, /if \(provider === "none"\) patch\.useLlm = false/);
    assert.match(js, /judgeProvider: provider/);
    assert.match(js, /judgeModel: model/);
    assert.match(js, /judgeEffort:/);
    assert.match(js, /judgeMaxTokens:/);
    assert.match(js, /openaiCompatibleBaseUrl:/);
  });

  it("uses explicit secure credential actions without reading secrets back", () => {
    assert.match(html, /id="credential-input"[^>]+type="password"/);
    assert.match(html, /id="credential-status"/);
    assert.match(js, /window\.ionic\.credentialStatus\(\)/);
    assert.match(js, /window\.ionic\.saveCredential\(provider, secret\)/);
    assert.match(js, /window\.ionic\.clearCredential\(provider\)/);
    assert.match(js, /if \(!result\.ok\)[\s\S]+?return;[\s\S]+?input\.value = ""/);
    assert.match(js, /function setCredentialBusy\(busy\)[\s\S]+?#setting-provider/);
    assert.match(js, /setCredentialBusy\(true\)[\s\S]+?window\.ionic\.saveCredential/);
    assert.doesNotMatch(js, /input\.value\s*=\s*(result|entry|state\.credentials)/);
  });

  it("uses high-contrast semantic tokens for primary and checked controls", () => {
    assert.match(css, /--control-fill: #007b91/);
    assert.match(css, /--control-ink: #ffffff/);
    assert.match(css, /\.primary\s*\{[\s\S]+?background: var\(--control-fill\)[\s\S]+?color: var\(--control-ink\)/);
    assert.match(css, /\.switch input:checked \+ span\s*\{[\s\S]+?background: var\(--control-fill\)/);
    assert.match(css, /\.switch input:checked \+ span::after\s*\{[\s\S]+?background: var\(--control-ink\)/);
  });

  it("synchronizes analysis defaults with the compatibility check", () => {
    assert.match(js, /function syncAnalysisControls\(settings = state\.settings\)/);
    assert.match(js, /#setting-use-llm[\s\S]+?#check-llm/);
    assert.match(js, /#setting-transitive[\s\S]+?#check-transitive/);
    assert.match(js, /#setting-fail-on[\s\S]+?#check-failon/);
    assert.match(js, /#check-llm"\)\.addEventListener\("change", persistAnalysisFromCheck\)/);
  });

  it("labels the active analysis mode and desktop package version honestly", () => {
    assert.match(js, /return "Structural review"/);
    assert.match(js, /status\.analysis\.description/);
    assert.match(js, /status\.desktop\.productName/);
    assert.match(js, /`\$\{productName\} v\$\{status\.desktop\.version\}`/);
    assert.match(html, /id="product-edition" class="edition-badge"/);
    assert.doesNotMatch(js, /setText\("#status-version", `ionic \$\{status\.version\}`\)/);
    assert.match(main, /productName: EDITION\.productName/);
  });

  it("uses the bundled Inter variable font and reflows the settings layout", () => {
    assert.match(css, /@font-face\s*\{[\s\S]+?font-family: "Inter"[\s\S]+?InterVariable\.woff2/);
    assert.match(css, /font-weight: 100 900/);
    assert.match(css, /\.settings-page\s*\{[\s\S]+?grid-template-columns:/);
    assert.match(css, /@media \(max-width: 800px\)[\s\S]+?\.settings-page\s*\{[\s\S]+?grid-template-columns: 1fr/);
  });

  it("offers four persistent brand themes without a first-paint flash", () => {
    assert.ok(html.indexOf('src="theme-init.js"') < html.indexOf('href="styles.css"'));
    assert.match(html, /data-settings-category="appearance"/);
    assert.match(html, /data-settings-section="appearance"/);
    for (const [value, label] of [["light", "Light"], ["dark", "Dark"], ["oled", "OLED Dark"], ["custom", "Custom"]]) {
      assert.match(html, new RegExp(`name="appearance-theme" value="${value}"`));
      assert.match(html, new RegExp(`>${label}<`));
    }
    assert.match(css, /:root,\s*:root\[data-theme="light"\][\s\S]+?color-scheme: light/);
    assert.match(css, /:root\[data-theme="dark"\][\s\S]+?color-scheme: dark/);
    assert.match(css, /:root\[data-theme="dark"\][\s\S]+?--sidebar: #0d1014/);
    assert.match(css, /:root\[data-theme="oled"\][\s\S]+?--canvas: #000000/);
    assert.match(css, /:root\[data-theme="oled"\][\s\S]+?--sidebar: #030507/);
    assert.match(css, /--brand-cyan: #26dbff/);
    assert.match(css, /--brand-navy: #020a1f/);
    assert.doesNotMatch(css, /#75506c|#d7a0cc|#e1a8d6/i);
    assert.match(css, /\.theme-option::after[\s\S]+?border: 1px solid var\(--border-strong\)/);
    assert.match(css, /\.theme-option:has\(input:checked\)::after[\s\S]+?content: "check"/);
    assert.match(css, /@media \(max-width: 620px\)[\s\S]+?\.theme-options\s*\{[\s\S]+?grid-template-columns: 1fr/);
    assert.match(js, /saveSettingsPatch\(\s*\{ appearanceTheme: requested \}/);
    assert.match(js, /applyAppearanceTheme\(previous, \{ customTheme \}\)/);
    assert.match(js, /initiatingControl\.focus\(\{ preventScroll: true \}\)/);
    assert.match(js, /input\[name="appearance-theme"\]\[value="\$\{previous\}"\][\s\S]+?\.focus\(\{ preventScroll: true \}\)/);
    assert.match(themeInit, /root\.dataset\.theme = theme/);
    assert.match(main, /nativeTheme\.themeSource = base === "light" \? "light" : "dark"/);
    assert.match(main, /mainWindow\.setBackgroundColor\(appearanceBackground\(theme, customTheme\)\)/);
    assert.match(main, /`--ionic-appearance-theme=\$\{appearanceTheme\}`/);
    assert.match(main, /`--ionic-custom-theme=\$\{encodedCustomTheme\(customTheme\)\}`/);
    assert.match(preload, /initialAppearanceTheme: initialAppearanceTheme\(\)/);
    assert.match(preload, /initialCustomTheme: initialCustomTheme\(\)/);
  });

  it("uses the supplied Ionic PNG directly in the shell and package artwork", () => {
    const brandIcon = path.resolve(HERE, "..", "..", "brand", "Ionic Icon BG.png");
    const rendererIcon = path.join(RENDERER, "assets", "ionic-icon-bg.png");
    assert.equal(fs.existsSync(brandIcon), true);
    assert.equal(fs.existsSync(rendererIcon), true);
    assert.equal(
      crypto.createHash("sha256").update(fs.readFileSync(rendererIcon)).digest("hex"),
      crypto.createHash("sha256").update(fs.readFileSync(brandIcon)).digest("hex")
    );
    assert.equal(packageJson.build.directories.buildResources, "../brand");
    assert.equal(packageJson.build.win.icon, "Ionic Icon BG.png");
    assert.equal(packageJson.build.mac.icon, "Ionic Icon BG.png");
    assert.equal(packageJson.build.linux.icon, "Ionic Icon BG.png");
    assert.match(css, /--brand-cyan: #26dbff/);
    assert.match(css, /--brand-navy: #020a1f/);
    assert.match(html, /class="product-logo product-logo-sidebar"[\s\S]+?src="assets\/ionic-icon-bg\.png"/);
    assert.match(css, /\.product-logo-sidebar\s*\{[\s\S]+?width: 32px/);
    assert.doesNotMatch(html, /class="[^"]*bond/);
    assert.doesNotMatch(css, /\.bond\s*\{/);
  });

  it("makes the workspace, settings, and contract panes persistently resizable", () => {
    for (const [id, label] of [
      ["workspace-resizer", "Resize workspace navigation"],
      ["settings-resizer", "Resize settings navigation"],
      ["contract-resizer", "Resize contract list"],
      ["repository-resizer", "Resize repository list"],
    ]) {
      assert.match(
        html,
        new RegExp(`id="${id}"[\\s\\S]+?role="separator"[\\s\\S]+?aria-label="${label}"[\\s\\S]+?aria-orientation="vertical"`)
      );
    }
    assert.match(js, /PANE_LAYOUT_CACHE_KEY = "ionic\.layout\.panes\.v1"/);
    assert.match(js, /typeof value === "number" && Number\.isFinite\(value\)/);
    assert.match(js, /setPointerCapture\(event\.pointerId\)/);
    assert.match(js, /"lostpointercapture"/);
    assert.match(js, /new ResizeObserver\(\(\) => applyVisiblePaneWidths\(\)\)/);
    assert.match(js, /event\.key === "ArrowLeft"/);
    assert.match(js, /event\.key === "ArrowRight"/);
    assert.match(js, /event\.key === "Home"/);
    assert.match(js, /event\.key === "End"/);
    assert.match(js, /event\.key === "Enter"/);
    assert.match(js, /clampToVisible: event\.key !== "Enter"/);
    assert.match(js, /localStorage\.setItem\(PANE_LAYOUT_CACHE_KEY/);
    assert.match(css, /\.pane-resizer:focus-visible::after/);
    assert.match(css, /@media \(max-width: 800px\)[\s\S]+?\.pane-resizer\s*\{[\s\S]+?display: none/);
    assert.match(css, /@media \(forced-colors: active\)[\s\S]+?\.pane-resizer::after/);
  });

  it("provides an explicit offline multi-repository scan and guarded sync surface", () => {
    assert.equal(packageJson.version, "0.6.2");
    assert.ok(html.indexOf('data-view="contracts"') < html.indexOf('data-view="repositories"'));
    assert.ok(html.indexOf('data-view="repositories"') < html.indexOf('data-view="graph"'));
    assert.match(html, /id="view-repositories"[\s\S]+?id="repository-add"[\s\S]+?id="workspace-scan"/);
    assert.match(html, /id="workspace-scan-status"[^>]+role="status"[^>]+aria-live="polite"/);
    assert.match(html, /id="workspace-error"[^>]+role="alert"/);
    assert.match(html, /id="workspace-error"[^>]+tabindex="-1"/);
    assert.match(html, /id="workspace-sync-actions"/);
    assert.match(main, /properties: \["openDirectory", "multiSelections"\]/);
    assert.match(main, /ionic\.workspaceCheck\(request, cliOptions\(\)\)/);
    assert.doesNotMatch(main, /ionic:workspace-check[\s\S]{0,220}withJudgeCredential/);
    assert.match(preload, /workspaceScan: \(request\) => invoke\("ionic:workspace-scan", request\)/);
    assert.match(preload, /pickWorkspaceDirectories: \(\) => invoke\("dialog:pick-workspace-directories"\)/);
    assert.match(js, /WORKSPACE_REPOSITORIES_CACHE_KEY = "ionic\.workspace\.repositories\.v1"/);
    assert.match(js, /window\.ionic\.workspaceScan\([\s\S]+?window\.ionic\.workspaceCheck\(/);
    assert.doesNotMatch(js, /window\.ionic\.workspaceCheck\(\{[\s\S]{0,260}useLlm/);
    assert.match(js, /function renderWorkspaceChecks\(/);
    assert.match(js, /function workspaceBlockedReason\(/);
    assert.match(js, /adoptBlockedWorkspaceReport\(result\.data\)/);
    assert.match(js, /setWorkspaceError\([^)]*\{ focus: true \}/);
    assert.match(js, /workspaceCheckBlockedRefs\(\)\.has\(ref\)/);
    assert.match(js, /blockedByScanError/);
    assert.match(js, /function workspaceConflictEvidence\([\s\S]+?el\("details"[\s\S]+?el\("summary"/);
    assert.match(js, /function syncPlanActions\([\s\S]+?\["add", "update", "unchanged", "prune"\]/);
    assert.match(js, /source_scan_id \|\| result\.data\?\.sourceScanId/);
    assert.match(js, /expectedScanId: state\.workspaceSyncPlan\.scan_id/);
    assert.match(js, /Registry changed\. Run Scan workspace to review this registry before syncing\./);
    assert.doesNotMatch(js, /\.innerHTML\s*=/);
    assert.match(css, /@media \(max-width: 800px\)[\s\S]+?\.repositories-split\s*\{[\s\S]+?grid-template-columns: 1fr/);
    assert.match(css, /@media \(forced-colors: active\)[\s\S]+?\.repository-row\[aria-current="true"\]/);
  });

  it("runs one silent structural launch scan only for saved local repositories", () => {
    const launchStart = js.indexOf("async function runLaunchStructuralScan()");
    const launchEnd = js.indexOf("function hideWorkspaceSyncReview", launchStart);
    const launchScan = js.slice(launchStart, launchEnd);
    assert.notEqual(launchStart, -1);
    assert.match(launchScan, /if \(state\.launchStructuralScanAttempted\) return false/);
    assert.match(launchScan, /state\.launchStructuralScanAttempted = true/);
    assert.match(launchScan, /!state\.legal\.accepted \|\| !state\.engine \|\| !state\.repositories\.length/);
    assert.match(launchScan, /scanWorkspace\(\{ background: true \}\)/);
    assert.doesNotMatch(launchScan, /toast\(|showView\(|\.focus\(/);

    const scanStart = js.indexOf("async function scanWorkspace(options = {})");
    const scanEnd = js.indexOf("async function runLaunchStructuralScan()", scanStart);
    const structuralScan = js.slice(scanStart, scanEnd);
    assert.match(structuralScan, /window\.ionic\.workspaceScan\(\{ repositories: workspaceRepositoriesRequest\(\) \}\)/);
    assert.match(structuralScan, /window\.ionic\.workspaceCheck\(\{[\s\S]+?failOn:[\s\S]+?transitive:/);
    assert.doesNotMatch(structuralScan, /useLlm|judgeProvider|subscriptionRuntime/);
    assert.match(structuralScan, /setWorkspaceBusy\(true, background \? "" : "Scanning local instruction files…"\)/);
    assert.match(structuralScan, /finally \{[\s\S]+?setWorkspaceBusy\(false\)/);
    assert.match(structuralScan, /Automatic structural scan failed\. Use Scan workspace to retry\./);
    assert.match(structuralScan, /setWorkspaceError\(result\?\.error\?\.message/);
    assert.doesNotMatch(structuralScan, /setWorkspaceError\([^\n]+focus: true/);

    const bootStart = js.indexOf("async function boot()");
    const bootEnd = js.indexOf('document.addEventListener("DOMContentLoaded"', bootStart);
    const boot = js.slice(bootStart, bootEnd);
    assert.match(boot, /const refreshed = await refreshAll\(\)/);
    assert.match(boot, /if \(refreshed\) await runLaunchStructuralScan\(\)/);
    assert.match(html, /Saved repositories scan structurally at launch/);
  });

  it("provides an accessible developer token editor with guarded persistence", () => {
    for (const token of ["canvas", "sidebar", "surface", "border", "text", "muted", "accent"]) {
      assert.match(html, new RegExp(`data-custom-token="${token}"`));
      assert.match(js, new RegExp(`"${token}"`));
    }
    assert.match(html, /id="custom-theme-base"/);
    assert.match(html, /id="custom-theme-validation"[^>]+role="status"[^>]+aria-live="polite"/);
    assert.match(html, /id="custom-theme-import"[\s\S]+?>Import JSON</);
    assert.match(html, /id="custom-theme-export"[\s\S]+?>Export JSON</);
    assert.match(preload, /importCustomTheme: \(\) => invoke\("appearance:custom-theme:import"\)/);
    assert.match(preload, /exportCustomTheme: \(theme\) => invoke\("appearance:custom-theme:export", theme\)/);
    assert.match(main, /handle\("appearance:custom-theme:import"[\s\S]+?showOpenDialog[\s\S]+?dontAddToRecent/);
    assert.match(main, /customTheme: customThemeFile\.readCustomThemeFile\(file\)/);
    assert.match(main, /handle\("appearance:custom-theme:export"[\s\S]+?showSaveDialog[\s\S]+?showOverwriteConfirmation/);
    assert.match(js, /customThemeContrastFailures\(draft\)/);
    assert.match(js, /contrastRatio\(theme\.colors\[foreground\], theme\.colors\[background\]\)/);
    assert.match(js, /for \(const background of \["canvas", "sidebar", "surface"\]\)[\s\S]+?accent\/\$\{background\}/);
    assert.match(js, /HIGH_CONTRAST_CONTROL_FILL = "#007B91"/);
    assert.match(js, /contrastRatio\(HIGH_CONTRAST_CONTROL_FILL, theme\.colors\[background\]\)[\s\S]+?controls\/\$\{background\}/);
    assert.match(js, /Object\.entries\(CUSTOM_THEME_SEMANTIC_COLORS\[theme\.base\]\)[\s\S]+?ratio < 4\.5/);
    assert.match(js, /\{ appearanceTheme: "custom", customTheme: draft \}/);
    const importTheme = js.slice(
      js.indexOf("async function importCustomTheme"),
      js.indexOf("async function exportCustomTheme")
    );
    assert.match(importTheme, /validatedImportedCustomTheme\(result\.data\?\.customTheme\)/);
    assert.match(importTheme, /previewCustomTheme\(\{ markDirty: false \}\)/);
    assert.match(importTheme, /Review the preview, then save to apply it everywhere\./);
    assert.doesNotMatch(importTheme, /persistCustomTheme|saveSettingsPatch|credential|provider|secret/i);
    const persistTheme = js.slice(
      js.indexOf("async function persistCustomTheme"),
      js.indexOf("function updateProviderVisibility")
    );
    assert.doesNotMatch(persistTheme, /setCredentialBusy/);
    assert.match(js, /CUSTOM_THEME_CACHE_KEY, JSON\.stringify\(custom\)/);
    assert.match(css, /:root\[data-theme="custom"\]/);
    assert.match(css, /\.custom-theme-validation\s*\{[\s\S]+?overflow-wrap: anywhere/);
    assert.match(css, /@media \(max-width: 800px\)[\s\S]+?\.custom-theme-grid\s*\{[\s\S]+?grid-template-columns: 1fr/);
  });

  it("bundles Material Symbols locally and hides decorative glyphs from assistive technology", () => {
    const font = path.join(RENDERER, "fonts", "MaterialSymbolsRounded.woff2");
    const license = path.join(RENDERER, "fonts", "MATERIAL-SYMBOLS-APACHE-2.0.txt");
    assert.match(css, /font-family: "Material Symbols Rounded"/);
    assert.match(css, /fonts\/MaterialSymbolsRounded\.woff2/);
    assert.equal(fs.existsSync(font), true);
    assert.equal(fs.existsSync(license), true);
    assert.equal(
      crypto.createHash("sha256").update(fs.readFileSync(font)).digest("hex"),
      "3500043e8929d5140f34dff8f8687e1dd5fda3a33fff20bfcc96ecd0b2f99518"
    );
    assert.equal(
      packageJson.build.extraResources.some(
        (entry) => entry.to === "legal/licenses/Material-Symbols/Apache-2.0.txt"
      ),
      true
    );
    assert.doesNotMatch(html, /fonts\.googleapis\.com|fonts\.gstatic\.com/);
    assert.match(html, /material-symbol[^>]+aria-hidden="true"/);
    assert.match(html, /id="toast-dismiss"[^>]+aria-label="Dismiss notification"/);
    for (const icon of ["description", "account_tree", "fact_check", "settings", "palette"]){
      assert.match(html, new RegExp(`>${icon}<`));
    }
  });

  it("keeps Material icons in place while labeled actions are busy", () => {
    assert.match(js, /const labelTarget = label \|\| button/);
    assert.match(js, /labelTarget\.textContent = busy \? busyLabel : labelTarget\.dataset\.label/);
    assert.doesNotMatch(js, /button\.textContent = busy \? busyLabel/);
  });
});
