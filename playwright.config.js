const { defineConfig, devices } = require("@playwright/test");

const baseURL = process.env.GEMAPI_E2E_URL || "http://127.0.0.1:8080";
const address = new URL(baseURL);
if (address.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(address.hostname)) {
  throw new Error("Playwright exige un serveur de qualification HTTP local.");
}

module.exports = defineConfig({
  testDir: "./tests/e2e",
  testMatch: "gemapi-*.spec.js",
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  use: { baseURL, trace: "retain-on-failure" },
});
