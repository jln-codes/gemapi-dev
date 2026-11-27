const { test, expect } = require("@playwright/test");
const { mockMapApi } = require("./mock-pwa-map-api");

test.use({ serviceWorkers: "block" });

async function setup(context, page, size = { width: 390, height: 844 }) {
  await context.addInitScript(() => {
    const real = window.matchMedia.bind(window);
    window.matchMedia = query => query === "(display-mode: standalone)"
      ? { matches: true, addEventListener() {} } : real(query);
    window.__gpsRequests = [];
    window.__gpsWatches = [];
    Object.defineProperty(navigator, "geolocation", { configurable: true, value: {
      getCurrentPosition(resolve, reject, options) {
        window.__gpsRequests.push({ resolve, reject, options });
      },
      watchPosition(resolve, reject) {
        window.__gpsWatches.push({ resolve, reject });
        return window.__gpsWatches.length;
      },
      clearWatch() {},
    } });
  });
  await mockMapApi(context);
  await page.setViewportSize(size);
  await page.goto("/carte");
  await page.waitForFunction(() => typeof mapViewportReady !== "undefined" && mapViewportReady);
  // Playwright's matchMedia mock does not evaluate CSS display-mode queries.
  await page.addStyleTag({ content: ".map-page .map-directions-button:not([hidden]) { display: flex; }" });
}

test("boutons terrain empilés : géolocalisation ponctuelle, zoom borné et Waze vers le centre", async ({ context, page }) => {
  await setup(context, page);
  const locate = page.locator("#locate-map");
  const directions = page.locator("#open-directions");
  await expect(locate).toBeVisible();
  await expect(directions).toBeVisible();
  for (const size of [{ width: 390, height: 844 }, { width: 844, height: 390 },
    { width: 390, height: 844 }]) {
    await page.setViewportSize(size);
    const above = await directions.boundingBox();
    const below = await locate.boundingBox();
    expect(above.width).toBe(42);
    expect(below.width).toBe(42);
    expect(above.height).toBe(42);
    expect(below.height).toBe(42);
    expect(above.y + above.height).toBeLessThan(below.y);
    expect(Math.abs(above.x - below.x)).toBeLessThan(1);
  }
  await page.evaluate(() => map.setView([46.8, 2.5], 6, { animate: false }));
  await locate.click();
  await expect(locate).toBeDisabled();
  await locate.evaluate(button => { button.click(); button.click(); });
  expect(await page.evaluate(() => window.__gpsRequests.length)).toBe(1);
  await page.evaluate(() => window.__gpsRequests[0].resolve({
    coords: { longitude: 2.34, latitude: 48.84, accuracy: 8 }, timestamp: Date.now(),
  }));
  await expect(locate).toBeEnabled();
  await expect(page.locator("#user-position-status")).toContainText("Carte centrée");
  const first = await page.evaluate(() => ({ center: map.getCenter(), zoom: map.getZoom(),
    maxZoom: map.getMaxZoom() }));
  expect(first.center.lat).toBeCloseTo(48.84, 5);
  expect(first.center.lng).toBeCloseTo(2.34, 5);
  expect(first.zoom).toBe(Math.min(19, first.maxZoom));
  expect(await page.evaluate(() => window.__gpsWatches.length)).toBe(0);

  await page.evaluate(() => { map.setMaxZoom(18); map.setView([46.8, 2.5], 6,
    { animate: false }); });
  await locate.click();
  await page.evaluate(() => window.__gpsRequests[1].resolve({
    coords: { longitude: 2.35, latitude: 48.85, accuracy: 8 }, timestamp: Date.now(),
  }));
  await expect(locate).toBeEnabled();
  expect(await page.evaluate(() => map.getZoom())).toBe(18);
  // The demo territory constrains the viewport; choose a point within its
  // allowed area rather than asserting an unreachable off-territory center.
  const { center, opened } = await page.evaluate(() => {
    map.setView([48.83, 2.33], 18, { animate: false });
    const center = map.getCenter();
    window.__opened = null;
    window.open = (...args) => { window.__opened = args; };
    document.querySelector("#open-directions").click();
    return { center, opened: window.__opened };
  });
  expect(center.lat).toBeCloseTo(48.83, 2);
  expect(center.lng).toBeCloseTo(2.33, 2);
  expect(opened).toEqual([
    `https://waze.com/ul?ll=${center.lat},${center.lng}&navigate=yes`,
    "_blank", "noopener,noreferrer",
  ]);
  expect(opened[0]).not.toContain("google.com");
  await page.evaluate(() => {
    PwaMapSettings.saveSettings({ ...PwaMapSettings.loadSettings(), showWazeButton: false });
    window.dispatchEvent(new StorageEvent("storage", { key: PwaMapSettings.STORAGE_KEY }));
  });
  await expect(directions).toBeHidden();
  await expect(locate).toBeVisible();
  await page.setViewportSize({ width: 844, height: 390 });
  await expect(directions).toBeHidden();
  await expect(locate).toBeVisible();
});

test("position ponctuelle : refus, indisponibilité et délai affichés sans requêtes simultanées", async ({ context, page }) => {
  await setup(context, page);
  const locate = page.locator("#locate-map");
  for (const [code, message] of [[1, "refusée"], [2, "indisponible"], [3, "Délai"]]) {
    await locate.click();
    const index = await page.evaluate(() => window.__gpsRequests.length - 1);
    await locate.evaluate(button => button.click());
    expect(await page.evaluate(() => window.__gpsRequests.length)).toBe(index + 1);
    await page.evaluate(({ index: i, code: value }) => {
      window.__gpsRequests[i].reject({ code: value, message: "test" });
    }, { index, code });
    await expect(page.locator("#user-position-status")).toContainText(message);
    await expect(locate).toBeEnabled();
  }
  expect(await page.evaluate(() => window.__gpsWatches.length)).toBe(0);
});

test("recentrage ponctuel pendant un relevé : points et abonnement GPS inchangés", async ({ context, page }) => {
  await setup(context, page, { width: 1200, height: 800 });
  await page.locator("#toggle-create-menu").click();
  await page.locator('[data-create-type="desordre"]').click();
  await page.locator("#desordre-create-geometry-type").selectOption("LineString");
  const tracker = page.locator('[data-gps-owner="desordre-create-geometry"]');
  await tracker.locator('[data-gps="start"]').click();
  await page.evaluate(() => window.__gpsWatches[0].resolve({
    coords: { longitude: 2.34, latitude: 48.84, accuracy: 7 }, timestamp: Date.now(),
  }));
  expect(await page.evaluate(() => gpsDesordreTracking.getState().points.length)).toBe(1);
  await page.locator("#locate-map").click();
  await page.evaluate(() => window.__gpsRequests[0].resolve({
    coords: { longitude: 2.35, latitude: 48.85, accuracy: 8 }, timestamp: Date.now(),
  }));
  await expect(page.locator("#locate-map")).toBeEnabled();
  expect(await page.evaluate(() => gpsDesordreTracking.getState().points.length)).toBe(1);
  expect(await page.evaluate(() => window.__gpsWatches.length)).toBe(1);
});
