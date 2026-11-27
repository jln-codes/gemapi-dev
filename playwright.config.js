const { defineConfig, devices } = require("@playwright/test");
const { execFileSync } = require("node:child_process");
const baseURL = process.env.DIGUES_E2E_BASE_URL || "http://127.0.0.1:8000";
const address = new URL(baseURL);
if (address.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(address.hostname)) {
  throw new Error("Playwright exige une URL HTTP locale explicite.");
}
const frontendPreview = process.env.DIGUES_FRONTEND_PREVIEW === "true";
if (frontendPreview && (!process.env.DIGUES_E2E_BASE_URL || process.env.DIGUES_E2E_GUARDED_HARNESS !== "true")) {
  throw new Error("Les tests de prévisualisation exigent DIGUES_E2E_BASE_URL local et DIGUES_E2E_GUARDED_HARNESS=true.");
}
const serverPort = Number(address.port || 8000);
const useDatabase = process.env.DIGUES_E2E_USE_TEST_DATABASE === "true";
if (useDatabase) {
  execFileSync(".venv/bin/python", ["-c", "from tests.postgres_safety import require_test_dsn; require_test_dsn('DIGUES_OCC_TEST_DSN')"]);
}
const testDatabase = useDatabase ? process.env.DIGUES_OCC_TEST_DSN :
  "host=/tmp/digues-test-unavailable dbname=unavailable connect_timeout=1";
const trace = process.env.DIGUES_PLAYWRIGHT_TRACE ||
  (process.env.DIGUES_RC_TIMING === "1" ? "off" : "retain-on-failure");

module.exports = defineConfig({
  testDir: "./tests/e2e",
  // Guarded live and INT-2 fixtures are owned by their dedicated runners.
  // Shared specs (for example map-panel-layout) remain in both selections.
  testIgnore: [
    ...(!process.env.DIGUES_BROWSER_FIXTURE ? ["**/multi-tenant-live.spec.js"] : []),
    ...(!process.env.DIGUES_INT2_FIXTURE ? ["**/int2-integrated.spec.js"] : []),
    ...(!frontendPreview ? ["**/preview-forms.spec.js", "**/preview-responsive.spec.js"] : []),
  ],
  // End-to-end workflows include schema-backed loading and several UI actions.
  // Keep a finite budget on shared development hosts, without retries/skips.
  timeout: 120_000,
  expect: {
    timeout: 15_000,
  },
  use: {
    baseURL,
    // Recording DOM snapshots for every passing test costs roughly a third of
    // the main Chromium gate. RC stays off by default, but an explicit
    // `qualify_rc.py --phase browser --trace=on` remains available for a
    // focused reproduction. Direct developer runs retain failures by default.
    trace,
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
      },
    },
  ],
  webServer: {
    command: `.venv/bin/python -m uvicorn --app-dir webapp/backend digues_webapp.app:app --host 127.0.0.1 --port ${serverPort}`,
    url: `${baseURL}/api/health`,
    env: {
      DATABASE_URL: testDatabase,
      DATABASE_READONLY_URL: testDatabase,
      CONTROL_DATABASE_URL: useDatabase ? process.env.CONTROL_DATABASE_URL :
        "host=/tmp/digues-test-unavailable dbname=unavailable connect_timeout=1",
      PYTHON_DOTENV_DISABLED: "1",
      // This isolated test server exercises the guarded /dev/lidar route.
      // Product/production defaults remain disabled and are tested by the API.
      DIGUES_ENVIRONMENT: "test",
      LIDAR_PROTOTYPE_ENABLED: "1",
      // Historical business scenarios use an explicit legacy test server.
      // Multi-tenant scenarios supply their own guarded fixture/config routes.
      LEGACY_SINGLE_DATABASE_ENABLED: "true",
    },
    reuseExistingServer: process.env.DIGUES_E2E_GUARDED_HARNESS === "true",
    timeout: 120_000,
    gracefulShutdown: { signal: "SIGTERM", timeout: 5000 },
  },
});
