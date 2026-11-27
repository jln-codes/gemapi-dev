const { mockBusinessCRS } = require('./mock-business-crs');
const { expect, test } = require("@playwright/test");

test.use({ serviceWorkers: "block" });

async function mockMapApi(context) {
  await context.route("**/api/**", async (route) => {
    if (await mockBusinessCRS(route)) return;
    const path = new URL(route.request().url()).pathname;
    const empty = { type: "FeatureCollection", features: [] };
    await route.fulfill({ json: path === "/api/config"
      ? { legacy_single_database_enabled: true, show_uuid: false, build_id: "geo-3b-test" }
      : path === "/api/resource-events/head" ? { cursor: "0" }
        : path === "/api/resource-events" ? { events: [], hasMore: false, nextCursor: "0" }
      : ["/api/troncons", "/api/desordres", "/api/territoire-administratif"].includes(path)
        ? empty : {} });
  });
}

async function mockGps(context, standalone) {
  await context.addInitScript((initialStandalone) => {
    const originalMatchMedia = window.matchMedia.bind(window);
    const listeners = new Set();
    const mode = {
      matches: initialStandalone, media: "(display-mode: standalone)",
      addEventListener(type, callback) { if (type === "change") listeners.add(callback); },
      removeEventListener(type, callback) { if (type === "change") listeners.delete(callback); },
    };
    window.matchMedia = (query) => query === mode.media ? mode : originalMatchMedia(query);
    window.__setStandalone = (value) => {
      mode.matches = value;
      for (const callback of listeners) callback({ matches: value });
    };
    window.__gpsWatches = [];
    window.__gpsClears = [];
    Object.defineProperty(navigator, "geolocation", {
      configurable: true,
      value: {
        getCurrentPosition() { throw new Error("La télémétrie doit utiliser subscribe."); },
        watchPosition(success, error) {
          const id = window.__gpsWatches.length + 1;
          window.__gpsWatches.push({ success, error });
          return id;
        },
        clearWatch(id) { window.__gpsClears.push(id); },
      },
    });
  }, standalone);
}

async function emitPosition(page, latitude, timestamp, accuracy = 8) {
  await page.evaluate(({ latitude: lat, timestamp: time, accuracy: meters }) => {
    window.__gpsWatches.at(-1).success({
      coords: { latitude: lat, longitude: 2.3522, accuracy: meters }, timestamp: time,
    });
  }, { latitude, timestamp, accuracy });
}

async function telemetry(page) {
  return page.evaluate(() => {
    const layers = Object.values(map._layers);
    const marker = layers.find((layer) => layer instanceof L.Marker
      && layer.options.pane === "user-position");
    const accuracy = layers.find((layer) => layer instanceof L.Circle
      && layer.options.pane === "user-position");
    return {
      marker: marker?.getLatLng().lat ?? null,
      accuracy: accuracy?.getRadius() ?? null,
      markerCount: layers.filter((layer) => layer instanceof L.Marker
        && layer.options.pane === "user-position").length,
      accuracyCount: layers.filter((layer) => layer instanceof L.Circle
        && layer.options.pane === "user-position").length,
      center: map.getCenter().lat,
      viewChanges: window.__userPositionViewChanges?.length ?? 0,
    };
  });
}

test("web classique : contrôle absent et aucun watch", async ({ context, page }) => {
  await mockGps(context, false);
  await mockMapApi(context);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/carte");
  await expect(page.locator("#toggle-user-position")).toHaveCount(0);
  await expect(page.locator("#pwa-settings-panel")).toBeHidden();
  expect(await page.evaluate(() => window.__gpsWatches.length)).toBe(0);
});

test("PWA : le réglage seul pilote la télémétrie sans recentrage continu", async ({ context, page }) => {
  await mockGps(context, true);
  await mockMapApi(context);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/carte");
  const businessWrites = [];
  page.on("request", (request) => {
    if (["POST", "PUT", "PATCH", "DELETE"].includes(request.method())
        && new URL(request.url()).pathname.startsWith("/api/")) {
      businessWrites.push(request.url());
    }
  });
  await expect(page.locator("#map")).toBeVisible();
  await page.waitForFunction(() => typeof mapViewportReady !== 'undefined' && mapViewportReady);
  await page.evaluate(() => {
    window.__userPositionViewChanges = [];
    const original = map.setView;
    map.setView = function (...args) {
      window.__userPositionViewChanges.push(args[0]);
      return original.apply(this, args);
    };
  });
  await expect(page.locator("#toggle-user-position")).toHaveCount(0);
  expect(await page.evaluate(() => window.__gpsWatches.length)).toBe(0);

  await page.locator("#toggle-pwa-settings").click();
  await expect(page.locator("#pwa-show-user-position")).not.toBeChecked();
  await page.locator("#pwa-show-user-position").check();
  await page.locator('#pwa-settings-form button[type="submit"]').click();
  await expect(page.locator("#pwa-show-user-position")).toBeChecked();
  expect(await page.evaluate(() => window.__gpsWatches.length)).toBe(1);
  await page.locator("#close-pwa-settings").click();
  await emitPosition(page, 48.8566, 1000);
  await expect(page.locator(".user-position-marker")).toBeVisible();
  await expect(page.locator(".leaflet-user-position-pane path")).toBeVisible();
  expect(await telemetry(page)).toMatchObject({
    marker: 48.8566, accuracy: 8, markerCount: 1, accuracyCount: 1, viewChanges: 1,
  });
  await expect(page.locator("#gps-position-candidate")).toBeHidden();
  await expect(page.locator("#editor-panel")).toBeHidden();

  await emitPosition(page, 48.8567, 2000, 50);
  await expect(page.locator("#user-position-status")).toContainText("Précision insuffisante");
  expect(await telemetry(page)).toMatchObject({ marker: 48.8566, accuracy: 8 });
  await emitPosition(page, 48.85661, 3000);
  expect(await telemetry(page)).toMatchObject({ marker: 48.8566, accuracy: 8 });
  await emitPosition(page, 48.8567, 4000, 10);
  expect(await telemetry(page)).toMatchObject({
    marker: 48.8567, accuracy: 10, markerCount: 1, accuracyCount: 1, viewChanges: 1,
  });
  await page.evaluate(() => window.__gpsWatches.at(-1).error({ code: 3 }));
  await expect(page.locator("#user-position-status")).toContainText("expirée");
  expect(await telemetry(page)).toMatchObject({ marker: 48.8567, accuracy: 10 });
  await emitPosition(page, 48.8568, 5000);
  await expect(page.locator("#user-position-status")).toBeEmpty();

  await page.setViewportSize({ width: 844, height: 390 });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator("#toggle-heritage").click();
  await expect(page.locator("main")).toHaveAttribute("data-layout", "single-tool");
  await page.locator("#close-heritage").click();
  expect(await page.evaluate(() => document.querySelector("#app-workspace")
    .getBoundingClientRect().bottom)).toBe(844);

  await page.locator("#toggle-pwa-settings").click();
  await page.locator("#pwa-show-user-position").uncheck();
  await page.locator('#pwa-settings-form button[type="submit"]').click();
  expect(await page.evaluate(() => window.__gpsClears)).toEqual([1]);
  expect(await telemetry(page)).toMatchObject({ marker: null, accuracy: null });
  await expect(page.locator("#pwa-show-user-position")).not.toBeChecked();
  await page.locator("#pwa-show-user-position").check();
  await page.locator('#pwa-settings-form button[type="submit"]').click();
  expect(await page.evaluate(() => window.__gpsWatches.length)).toBe(2);
  await page.locator("#close-pwa-settings").click();
  await page.reload();
  await page.waitForFunction(() => typeof mapViewportReady !== 'undefined' && mapViewportReady);
  await expect.poll(() => page.evaluate(() => window.__gpsWatches.length)).toBe(1);
  await expect(page.locator("#map")).toBeVisible();
  await context.setOffline(true);
  await emitPosition(page, 48.8566, 5000);
  await emitPosition(page, 48.8568, 6000);
  expect(await telemetry(page)).toMatchObject({ marker: 48.8568 });
  await context.setOffline(false);

  await page.evaluate(() => window.__setStandalone(false));
  await expect(page.locator("#toggle-pwa-settings")).toBeHidden();
  expect(await telemetry(page)).toMatchObject({ marker: null, accuracy: null });
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem(
    "digues:pwa-map-settings:v1",
  )).showUserPosition)).toBe(true);
  await page.evaluate(() => window.__setStandalone(true));
  await expect(page.locator("#toggle-pwa-settings")).toBeVisible();
  expect(await page.evaluate(() => window.__gpsWatches.length)).toBe(2);

  await page.evaluate(() => window.__gpsWatches.at(-1).error({ code: 1 }));
  expect(await telemetry(page)).toMatchObject({ marker: null, accuracy: null });
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem(
    "digues:pwa-map-settings:v1",
  )).showUserPosition)).toBe(false);
  await expect(page.locator("#user-position-status")).toContainText("refusé");
  expect(businessWrites).toEqual([]);
});
