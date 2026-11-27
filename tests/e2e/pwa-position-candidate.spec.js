const { expect, test } = require("@playwright/test");
const { mockMapApi } = require("./mock-pwa-map-api");

async function simulateGps(context, standalone = true) {
  await context.addInitScript((initialStandalone) => {
    const originalMatchMedia = window.matchMedia.bind(window);
    const listeners = new Set();
    const mode = {
      matches: initialStandalone,
      media: "(display-mode: standalone)",
      addEventListener(type, callback) { if (type === "change") listeners.add(callback); },
      removeEventListener(type, callback) { if (type === "change") listeners.delete(callback); },
    };
    window.matchMedia = (query) => query === mode.media ? mode : originalMatchMedia(query);
    window.__setStandalone = (value) => {
      mode.matches = value;
      for (const callback of listeners) callback({ matches: value });
    };
    window.__gpsRequests = [];
    Object.defineProperty(navigator, "geolocation", {
      configurable: true,
      value: {
        getCurrentPosition(resolve, reject) { window.__gpsRequests.push({ resolve, reject }); },
        watchPosition() { throw new Error("GEO-3 ne doit pas démarrer de watch"); },
        clearWatch() {},
      },
    });
  }, standalone);
}

async function exposePwaCoordinateInputs(page) {
  // La saisie numérique reste testée comme capacité interne, bien que ses
  // commandes soient volontairement masquées dans l'interface PWA courante.
  await page.addStyleTag({ content: `
    body.pwa-standalone #desordre-mode-selector { display: flex !important; }
    body.pwa-standalone #desordre-create-xy:not([hidden]),
    body.pwa-standalone #desordre-create-lonlat:not([hidden]) { display: grid !important; }
    body.pwa-standalone #desordre-create-line-coordinates:not([hidden]),
    body.pwa-standalone #desordre-create-bornage:not([hidden]) { display: block !important; }
  ` });
}

async function openCreation(page) {
  await mockMapApi(page.context());
  await page.goto("/carte");
  await exposePwaCoordinateInputs(page);
  await expect(page.locator("#map")).toBeVisible();
  await page.waitForFunction(() => typeof topNavigation !== "undefined"
    && topNavigation.getState().create === false);
  await page.locator("#toggle-create-menu").click();
  await expect(page.locator("#create-menu-list")).toBeVisible();
  await page.locator('[data-create-type="desordre"]').click();
  await expect(page.locator("#desordre-editor")).toBeVisible();
}

async function openExistingDesordre(page, geometryType) {
  const responsePromise = page.waitForResponse((response) => (
    response.request().method() === "GET"
    && new URL(response.url()).pathname === "/api/desordres"
  ));
  await page.goto("/carte");
  await exposePwaCoordinateInputs(page);
  await expect(page.locator("#map")).toBeVisible();
  const response = await responsePromise;
  const collection = await response.json();
  expect(response.ok()).toBeTruthy();
  const feature = collection.features?.find(item => item.geometry?.type === geometryType);
  expect(feature, `La fixture doit fournir un désordre ${geometryType}`).toBeTruthy();
  await page.waitForFunction(id => typeof desordreLayersById !== 'undefined'
    && desordreLayersById.get(id)?.getElement(), feature.properties.id);
  await page.evaluate(id => {
    desordreLayersById.get(id).getElement().dataset.e2eSelectedDesordre = 'true';
  }, feature.properties.id);
  await page.locator('[data-e2e-selected-desordre="true"]').dispatchEvent("click");
  await expect(page.locator("#desordre-create-id")).not.toHaveJSProperty("value", "");
  await expect(page.locator("#desordre-create-geometry-type")).toHaveValue(geometryType);
}

async function gpsPosition(page, index, longitude, latitude, accuracy = 8) {
  await expect.poll(() => page.evaluate(() => window.__gpsRequests.length)).toBe(index + 1);
  await page.evaluate(({ index: requestIndex, longitude: lng, latitude: lat, accuracy: meters }) => {
    window.__gpsRequests[requestIndex].resolve({
      coords: { longitude: lng, latitude: lat, accuracy: meters },
      timestamp: Date.now(),
    });
  }, { index, longitude, latitude, accuracy });
}

test("web classique : aucun contrôle GPS métier et aucune acquisition", async ({ context, page }) => {
  await simulateGps(context, false);
  await page.setViewportSize({ width: 390, height: 844 });
  await openCreation(page);
  await page.locator("#desordre-lonlat-choice").click();
  await expect(page.locator("#gps-point-lonlat")).toBeHidden();
  await page.locator("#gps-point-lonlat").evaluate((button) => button.click());
  expect(await page.evaluate(() => window.__gpsRequests.length)).toBe(0);
});

test("Point lon/lat : candidat, Réessayer, Annuler, acceptation et offline", async ({ context, page }) => {
  await simulateGps(context);
  await page.setViewportSize({ width: 390, height: 844 });
  await openCreation(page);
  const writes = [];
  page.on("request", (request) => {
    if (["POST", "PUT"].includes(request.method())
        && new URL(request.url()).pathname.startsWith("/api/")) writes.push(request.url());
  });
  await page.locator("#desordre-create-designation").fill("Saisie conservée");
  await page.locator("#desordre-lonlat-choice").click();
  await expect(page.locator("#gps-point-lonlat")).toBeVisible();
  await context.setOffline(true);
  await page.locator("#gps-point-lonlat").click();
  await gpsPosition(page, 0, 2.35, 48.85);
  await expect(page.locator("#gps-candidate-details")).toContainText("±8 m");
  await expect(page.locator("#desordre-create-longitude")).toHaveValue("");
  await page.locator("#gps-candidate-retry").click();
  await expect(page.locator("#gps-candidate-longitude")).toHaveText("2.35");
  await gpsPosition(page, 1, 2.36, 48.86);
  await expect(page.locator("#gps-candidate-longitude")).toHaveText("2.36");
  await expect(page.locator("#desordre-create-longitude")).toHaveValue("");
  await page.locator("#gps-candidate-cancel").click();
  await expect(page.locator("#gps-position-candidate")).toBeHidden();
  await expect(page.locator("#desordre-create-longitude")).toHaveValue("");
  await page.locator("#gps-point-lonlat").click();
  await gpsPosition(page, 2, 2.37, 48.87);
  await page.locator("#gps-candidate-accept").click();
  await expect(page.locator("#desordre-create-longitude")).toHaveValue("2.370000");
  await expect(page.locator("#desordre-create-latitude")).toHaveValue("48.870000");
  expect(await page.locator("#desordre-create-longitude").evaluate((input) => input.validity.stepMismatch)).toBe(false);
  await expect(page.locator("#desordre-create-designation")).toHaveValue("Saisie conservée");
  expect(writes).toEqual([]);
});

test("LineString 4326 : début et fin indépendants sans persistence automatique", async ({ context, page }) => {
  await simulateGps(context);
  await page.setViewportSize({ width: 390, height: 844 });
  await openCreation(page);
  const writes = [];
  page.on("request", (request) => {
    if (["POST", "PUT"].includes(request.method())
        && new URL(request.url()).pathname.startsWith("/api/")) writes.push(request.url());
  });
  await page.locator("#desordre-create-geometry-type").selectOption("LineString");
  await page.locator("#desordre-lonlat-choice").click();
  await expect(page.locator("#desordre-create-line-crs")).toHaveValue("EPSG:4326");
  await page.locator("#gps-line-start").click();
  await gpsPosition(page, 0, 2.35, 48.85);
  await page.locator("#gps-line-end").evaluate((button) => button.click());
  await expect(page.locator("#gps-candidate-details")).toBeHidden();
  await gpsPosition(page, 1, 2.36, 48.86);
  await page.locator("#gps-candidate-accept").click();
  await expect(page.locator("#desordre-create-line-start-1")).toHaveValue("");
  await expect(page.locator("#desordre-create-line-end-1")).toHaveValue("2.36");
  await page.locator("#gps-line-start").click();
  await gpsPosition(page, 2, 2.34, 48.84);
  await page.locator("#gps-candidate-accept").click();
  await expect(page.locator("#desordre-create-line-start-1")).toHaveValue("2.34");
  await expect(page.locator("#desordre-create-line-start-2")).toHaveValue("48.84");
  await expect(page.locator("#desordre-create-line-end-2")).toHaveValue("48.86");
  expect(writes).toEqual([]);
});

test("Point carte : le candidat ne devient draft qu'après Accepter", async ({ context, page }) => {
  await simulateGps(context);
  await page.setViewportSize({ width: 390, height: 844 });
  await openCreation(page);
  const markers = page.locator(".desordre-point-marker");
  const initialCount = await markers.count();
  await page.locator("#gps-map-create").click();
  await gpsPosition(page, 0, 2.35, 48.85);
  expect(await markers.count()).toBe(initialCount);
  await page.locator("#gps-candidate-accept").click();
  await expect(page.locator("main")).toHaveClass(/editor-map-mode/);
  await expect(page.locator("#editor-panel")).toBeHidden();
  await expect(page.locator("#map")).toHaveJSProperty("inert", false);
  await expect(page.locator("#editor-map-interaction")).toBeVisible();
  await expect(markers).toHaveCount(initialCount + 1);
  await expect(page.locator("#gps-map-banner")).toHaveCount(0);
  await expect(page.locator("#gps-position-candidate")).toBeHidden();
  await expect(markers).toHaveCount(initialCount + 1);
  await page.setViewportSize({ width: 700, height: 844 });
  await expect(page.locator("#editor-panel")).toBeVisible();
  await expect(page.locator("#map")).toHaveJSProperty("inert", false);
  await page.setViewportSize({ width: 699, height: 844 });
  await expect(page.locator("#editor-panel")).toBeHidden();
  await page.locator("#finish-editor-map-interaction").click();
  await expect(page.locator("#editor-panel")).toBeVisible();
  await expect(page.locator("#desordre-draw-status")).toContainText("Point GPS provisoire");
});

test("Point existant : GPS cartographique provisoire puis Annuler sans PUT", async ({ context, page }) => {
  await simulateGps(context);
  await page.setViewportSize({ width: 390, height: 844 });
  await openExistingDesordre(page, "Point");
  const writes = [];
  page.on("request", (request) => {
    if (["POST", "PUT"].includes(request.method())
        && new URL(request.url()).pathname.startsWith("/api/")) writes.push(request.url());
  });
  const comment = page.locator("#desordre-create-commentaire");
  await comment.fill("Commentaire GPS non enregistré");
  const originalLongitude = await page.locator("#desordre-create-longitude").inputValue();
  await page.locator("#gps-map-edit").click();
  await gpsPosition(page, 0, 2.35, 48.85);
  await expect(page.locator("#desordre-create-longitude")).toHaveValue(originalLongitude);
  await page.locator("#gps-candidate-accept").click();
  await expect(page.locator("main")).toHaveClass(/editor-map-mode/);
  await expect(page.locator("#validate-map-position")).toBeEnabled();
  await expect(page.locator("#desordre-create-longitude")).toHaveValue("2.350000");
  await page.locator("#cancel-editor-map-interaction").click();
  await expect(page.locator("#editor-panel")).toBeVisible();
  await expect(comment).toHaveValue("Commentaire GPS non enregistré");
  await expect(page.locator("#desordre-create-longitude")).toHaveValue(originalLongitude);
  expect(writes).toEqual([]);
});

test("Point existant : GPS lon/lat renseigne les champs sans validation métier", async ({ context, page }) => {
  await simulateGps(context);
  await page.setViewportSize({ width: 390, height: 844 });
  await openExistingDesordre(page, "Point");
  const writes = [];
  page.on("request", (request) => {
    if (["POST", "PUT"].includes(request.method())
        && new URL(request.url()).pathname.startsWith("/api/")) writes.push(request.url());
  });
  await page.locator("#desordre-lonlat-choice").click();
  const comment = page.locator("#desordre-create-commentaire");
  await comment.fill("Point GPS non enregistré");
  const previousLongitude = await page.locator("#desordre-create-longitude").inputValue();
  await page.locator("#gps-point-lonlat").click();
  await gpsPosition(page, 0, 2.35, 48.85);
  await expect(page.locator("#desordre-create-longitude")).toHaveValue(previousLongitude);
  await page.locator("#gps-candidate-accept").click();
  await expect(page.locator("#desordre-create-longitude")).toHaveValue("2.350000");
  await expect(page.locator("#desordre-create-latitude")).toHaveValue("48.850000");
  await expect(comment).toHaveValue("Point GPS non enregistré");
  expect(writes).toEqual([]);
});

test("LineString existante : GPS d'extrémité garde l'autre extrémité et le commentaire", async ({ context, page }) => {
  await simulateGps(context);
  await page.setViewportSize({ width: 390, height: 844 });
  await openExistingDesordre(page, "LineString");
  const writes = [];
  page.on("request", (request) => {
    if (["POST", "PUT"].includes(request.method())
        && new URL(request.url()).pathname.startsWith("/api/")) writes.push(request.url());
  });
  await page.locator("#desordre-lonlat-choice").click();
  const comment = page.locator("#desordre-create-commentaire");
  await comment.fill("Ligne GPS non enregistrée");
  const previousEnd = await page.locator("#desordre-create-line-end-1").inputValue();
  await page.locator("#gps-line-start").click();
  await gpsPosition(page, 0, 2.35, 48.85);
  await page.locator("#gps-candidate-accept").click();
  await expect(page.locator("#desordre-create-line-start-1")).toHaveValue("2.35");
  await expect(page.locator("#desordre-create-line-start-2")).toHaveValue("48.85");
  await expect(page.locator("#desordre-create-line-end-1")).toHaveValue(previousEnd);
  await expect(comment).toHaveValue("Ligne GPS non enregistrée");
  expect(writes).toEqual([]);
});

test("erreur et sortie standalone conservent les champs", async ({ context, page }) => {
  await simulateGps(context);
  await page.setViewportSize({ width: 390, height: 844 });
  await openCreation(page);
  await page.locator("#desordre-lonlat-choice").click();
  await page.locator("#desordre-create-longitude").fill("2.2");
  await page.locator("#gps-point-lonlat").click();
  await gpsPosition(page, 0, 2.35, 48.85, 50);
  await expect(page.locator("#gps-candidate-status")).toContainText("Précision insuffisante");
  await expect(page.locator("#desordre-create-longitude")).toHaveValue("2.2");
  await page.locator("#gps-candidate-retry").click();
  await expect.poll(() => page.evaluate(() => window.__gpsRequests.length)).toBe(2);
  await page.evaluate(() => window.__gpsRequests[1].reject({ code: 1, message: "denied" }));
  await expect(page.locator("#gps-candidate-status")).toContainText("refusé");
  await page.locator("#gps-candidate-retry").click();
  await expect.poll(() => page.evaluate(() => window.__gpsRequests.length)).toBe(3);
  await page.evaluate(() => window.__gpsRequests[2].reject({ code: 3, message: "timeout" }));
  await expect(page.locator("#gps-candidate-status")).toContainText("expiré");
  await page.evaluate(() => window.__setStandalone(false));
  await expect(page.locator("#gps-position-candidate")).toBeHidden();
  await expect(page.locator("#gps-point-lonlat")).toBeHidden();
  await expect(page.locator("#desordre-create-longitude")).toHaveValue("2.2");
});
