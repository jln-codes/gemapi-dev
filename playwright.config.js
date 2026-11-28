const { defineConfig, devices } = require("@playwright/test");

const baseURL = process.env.GEMAPI_E2E_URL || "http://127.0.0.1:8080";
const address = new URL(baseURL);
if (!["http:", "https:"].includes(address.protocol) || !["127.0.0.1", "localhost"].includes(address.hostname)) {
  throw new Error("Playwright exige un serveur de qualification sur boucle locale.");
}

module.exports = defineConfig({
  testDir: "./tests/e2e",
  timeout: 120_000,
  testMatch: "gemapi-*.spec.js",
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  use: { baseURL,
    launchOptions: process.env.GEMAPI_TEST_TLS_PIN
      ? {args: ["--ignore-certificate-errors-spki-list=" + process.env.GEMAPI_TEST_TLS_PIN]} : {},
    trace: process.env.GEMAPI_TEST_PASSWORD ? "off" : "retain-on-failure" },
});
