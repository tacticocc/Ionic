"use strict";

const fs = require("node:fs");
const path = require("node:path");

/**
 * Immutable product identity for the Essential release branch.
 *
 * Keeping this in one small module makes the distribution identity, runtime
 * profile, legal records, and UI status agree instead of deriving an edition
 * from a mutable environment variable at runtime.
 */
const EDITION = Object.freeze({
  id: "essential",
  label: "Essential",
  productName: "Ionic Essential",
  appId: "com.tactico.ionic.essential",
  publisher: "Tactico Technologies",
  profileDirectory: "Ionic Essential",
});
const DESKTOP_EDITION = EDITION.id;
const PRODUCT_NAME = EDITION.productName;
const APP_ID = EDITION.appId;

function configureAppIdentity(app, { appData = null } = {}) {
  if (!app || typeof app.getPath !== "function" || typeof app.setPath !== "function") {
    throw new TypeError("an Electron app instance is required");
  }
  const root = path.join(appData || app.getPath("appData"), EDITION.publisher, EDITION.profileDirectory);
  const session = path.join(root, "Session");
  fs.mkdirSync(session, { recursive: true });
  app.setName(EDITION.productName);
  app.setPath("userData", root);
  app.setPath("sessionData", session);
  if (typeof app.setAppUserModelId === "function") app.setAppUserModelId(EDITION.appId);
  return Object.freeze({ userData: root, sessionData: session });
}

module.exports = {
  APP_ID,
  DESKTOP_EDITION,
  EDITION,
  PRODUCT_NAME,
  configureAppIdentity,
};
