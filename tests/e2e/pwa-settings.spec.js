const { test, expect } = require("@playwright/test");
const { mockMapApi } = require("./mock-pwa-map-api");

async function emulateStandalone(context) {
  await context.addInitScript(() => {
    const originalMatchMedia = window.matchMedia.bind(window);
    const standaloneListeners = new Set();
    const standaloneMedia = {
      matches: true,
      media: "(display-mode: standalone)",
      addEventListener(type, callback) {
        if (type === "change") standaloneListeners.add(callback);
      },
      removeEventListener(type, callback) {
        if (type === "change") standaloneListeners.delete(callback);
      },
    };
    window.matchMedia = (query) => {
      if (query === "(display-mode: standalone)") {
        return standaloneMedia;
      }
      return originalMatchMedia(query);
    };
    window.__setStandaloneMode = (matches) => {
      standaloneMedia.matches = matches;
      for (const callback of standaloneListeners) callback({ matches });
    };
    window.__geolocationCalls = [];
    Object.defineProperty(navigator, "geolocation", {
      configurable: true,
      value: {
        getCurrentPosition() { window.__geolocationCalls.push("get"); },
        watchPosition() { window.__geolocationCalls.push("watch"); return 1; },
        clearWatch() { window.__geolocationCalls.push("clear"); },
      },
    });
  });
}

test("Paramètres reste inaccessible en navigation web classique", async ({ context, page }) => {
  await mockMapApi(context);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/carte");
  const toggle = page.locator("#toggle-pwa-settings");
  await expect(toggle).toBeHidden();
  await expect(page.locator(".settings-menu")).toBeVisible();
  await expect(page.locator("#pwa-settings-panel")).toBeHidden();
  await toggle.evaluate((button) => button.click());
  await expect(page.locator("#pwa-settings-panel")).toBeHidden();
  expect(await page.evaluate(() => localStorage.getItem("digues:pwa-map-settings:v1"))).toBeNull();
});

test("Paramètres standalone : navigation, validation, persistance et responsive", async ({ context, page }) => {
  test.setTimeout(90_000); // deux chargements complets de /carte et un redimensionnement
  await emulateStandalone(context);
  await mockMapApi(context);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/carte");
  const writes = [];
  page.on("request", (request) => {
    if (["POST", "PUT"].includes(request.method())
        && new URL(request.url()).pathname.startsWith("/api/")) {
      writes.push(request.url());
    }
  });
  const toggle = page.locator("#toggle-pwa-settings");
  const panel = page.locator("#pwa-settings-panel");
  await expect(toggle).toBeVisible();
  await expect(page.locator("#locate-map")).toHaveJSProperty("hidden", false);
  await expect(page.locator("#open-directions")).toHaveJSProperty("hidden", false);
  await expect(page.locator(".settings-menu")).toBeHidden();

  await page.locator("#toggle-ai").click();
  await expect(page.locator("#ai-panel")).toBeVisible();
  await page.locator("#ai-message").focus();
  await toggle.evaluate((button) => button.click());
  await expect(page.locator("#ai-panel")).toBeHidden();
  await expect(panel).toBeVisible();
  await expect(page.locator("main")).toHaveClass(/settings-open/);
  await expect(page.locator("#primary-area")).toBeHidden();
  await expect(page.locator("#primary-area")).toHaveJSProperty("inert", true);
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator("#pwa-distance-meters")).toBeFocused();
  await expect(page.locator("#pwa-distance-meters")).toHaveValue("5");
  await expect(page.locator("#pwa-interval-seconds")).toHaveValue("5");
  await expect(page.locator("#pwa-max-accuracy-meters")).toHaveValue("25");
  await expect(page.locator("#pwa-smart-polygon-correction")).toBeChecked();
  await expect(page.locator("#pwa-show-waze-button")).toBeChecked();
  await expect(page.locator("#pwa-proximity-enabled")).not.toBeChecked();
  await expect(page.locator("#open-directions")).toHaveJSProperty("hidden", false);
  await expect(page.locator("#pwa-radius-km")).toHaveValue("5");
  const compactBounds = await panel.boundingBox();
  const workspaceBounds = await page.locator("#app-workspace").boundingBox();
  expect(compactBounds.x).toBeGreaterThan(workspaceBounds.x);
  expect(compactBounds.x + compactBounds.width).toBeLessThan(workspaceBounds.x + workspaceBounds.width);
  expect(compactBounds.y).toBeGreaterThan(workspaceBounds.y);
  expect(compactBounds.y + compactBounds.height).toBeLessThan(workspaceBounds.y + workspaceBounds.height);
  expect(await panel.evaluate((element) => getComputedStyle(element).overflow)).toBe("hidden");
  expect(await page.locator("#pwa-settings-form").evaluate(
    (element) => getComputedStyle(element).overflowY,
  )).toBe("auto");

  await page.locator("#pwa-distance-meters").fill("12");
  await page.locator("#pwa-interval-seconds").fill("8");
  await page.locator("#pwa-smart-polygon-correction").uncheck();
  await page.locator("#pwa-show-waze-button").uncheck();
  await page.locator("#pwa-proximity-enabled").check();
  await page.locator("#pwa-radius-km").fill("12.5");
  await page.locator('#pwa-settings-form button[type="submit"]').click();
  await expect(page.locator("#pwa-settings-message")).toContainText("enregistrés");
  const saved = await page.evaluate(() => JSON.parse(
    localStorage.getItem("digues:pwa-map-settings:v1"),
  ));
  expect(saved).toEqual({
    version: 1,
    showUserPosition: false,
    showWazeButton: false,
    tracking: { distanceMeters: 12, intervalSeconds: 8, maxAccuracyMeters: 25,
      smartPolygonCorrection: false },
    proximity: { enabled: true, radiusKm: 12.5 },
  });

  await page.locator("#pwa-radius-km").fill("999");
  await page.locator('#pwa-settings-form button[type="submit"]').click();
  await expect(page.locator("#pwa-settings-message")).toContainText("hors limites");
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem(
    "digues:pwa-map-settings:v1",
  )))).toEqual(saved);

  await page.setViewportSize({ width: 1200, height: 800 });
  await expect(panel).toBeVisible();
  await expect(page.locator("#primary-area")).toBeVisible();
  await expect(page.locator("#primary-area")).toHaveJSProperty("inert", false);
  const mapBounds = await page.locator("#primary-area").boundingBox();
  const panelBounds = await panel.boundingBox();
  expect(panelBounds.x).toBeGreaterThanOrEqual(mapBounds.x + mapBounds.width - 1);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator("#primary-area")).toBeHidden();
  await page.locator("#close-pwa-settings").click();
  await expect(panel).toBeHidden();
  await expect(toggle).toBeFocused();
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(page.locator("#primary-area")).toBeVisible();
  await expect(page.locator("#open-directions")).toBeHidden();
  await expect(page.locator("#locate-map")).toHaveJSProperty("hidden", false);
  expect(await page.evaluate(() => window.__geolocationCalls)).toEqual([]);

  await page.reload();
  await toggle.click();
  await expect(page.locator("#pwa-distance-meters")).toHaveValue("12");
  await expect(page.locator("#pwa-smart-polygon-correction")).not.toBeChecked();
  await expect(page.locator("#pwa-show-waze-button")).not.toBeChecked();
  await expect(page.locator("#pwa-proximity-enabled")).toBeChecked();
  await expect(page.locator("#pwa-radius-km")).toHaveValue("12.5");
  await page.locator("#reset-pwa-settings").click();
  await expect(page.locator("#pwa-distance-meters")).toHaveValue("5");
  await expect(page.locator("#pwa-smart-polygon-correction")).toBeChecked();
  await expect(page.locator("#pwa-show-waze-button")).toBeChecked();
  await expect(page.locator("#open-directions")).toHaveJSProperty("hidden", false);
  await expect(page.locator("#pwa-proximity-enabled")).not.toBeChecked();
  expect(await page.evaluate(() => localStorage.getItem("digues:pwa-map-settings:v1"))).toBeNull();
  expect(await page.evaluate(() => window.__geolocationCalls)).toEqual([]);
  await page.evaluate(() => window.__setStandaloneMode(false));
  await expect(panel).toBeHidden();
  await expect(toggle).toBeHidden();
  await expect(page.locator(".settings-menu")).toBeVisible();
  await expect(page.locator(".settings-menu-toggle")).toBeFocused();
  await toggle.evaluate((button) => button.click());
  await expect(panel).toBeHidden();
  expect(writes).toEqual([]);
});
