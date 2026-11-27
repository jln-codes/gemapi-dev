const { test, expect } = require("@playwright/test");
const { mockBusinessCRS } = require("./mock-business-crs");

test.use({ serviceWorkers: "block" });

const ID = "11111111-1111-4111-8111-111111111111";
const USER = "22222222-2222-4222-8222-222222222222";
const BASE = "33333333-3333-4333-8333-333333333333";
const BASE_SLUG = "base_0";
const INSTANCE = "44444444-4444-4444-8444-444444444444";
const EPOCH = "55555555-5555-4555-8555-555555555555";
const original = { type: "Feature",
  geometry: { type: "LineString", coordinates: [[2.34, 48.84], [2.341, 48.84]] },
  properties: { id: ID, version: 1, designation: "Ligne existante", valid: true,
    troncon_ids: [], type_desordre_id: null, categorie_desordre_id: null },
};
const NOISY_LOOP = [[2, 48], [2.002, 48], [2.002, 48.002], [2.001, 48.002],
  [2.001, 48.0021], [2.0011, 48.0021], [2.001, 48.002], [2, 48.002]];

const trackerFor = (page, owner) => page.locator(`[data-gps-owner="${owner}"]`);

async function setup(context, { existing = false, existingFeature = original, multi = false } = {}) {
  await context.addInitScript(() => {
    const real = window.matchMedia.bind(window);
    window.matchMedia = query => query === "(display-mode: standalone)"
      ? { matches: true, addEventListener() {} } : real(query);
    window.__gpsWatches = [];
    window.__gpsClears = [];
    Object.defineProperty(navigator, "geolocation", { configurable: true, value: {
      getCurrentPosition() {},
      watchPosition(resolve, reject) {
        const id = window.__gpsWatches.length + 1;
        window.__gpsWatches.push({ resolve, reject });
        return id;
      },
      clearWatch(id) { window.__gpsClears.push(id); },
    } });
  });
  const writes = [];
  await context.route("**/api/**", async route => {
    const request = route.request();
    const rawPath = new URL(request.url()).pathname;
    if (!multi && await mockBusinessCRS(route)) return;
    if (multi && rawPath === "/api/auth/me") {
      await route.fulfill({ json: { id: USER, login: "gps-test", status: "active" } }); return;
    }
    if (multi && rawPath === "/api/auth/databases") {
      await route.fulfill({ json: { instance_id: INSTANCE, user_id: USER,
        expires_at: new Date(Date.now() + 3600000).toISOString(), databases: [{
          business_database_id: BASE, slug: BASE_SLUG, data_epoch: EPOCH, status: "active",
          permissions: ["business.read", "business.create", "business.update", "offline.prepare"],
          structure_name: "Test", display_name: "GPS", role_code: "editor",
        }] } }); return;
    }
    const path = multi ? rawPath.replace(`/api/bases/${BASE}/`, "/api/") : rawPath;
    if (path === "/api/base-summary") {
      await route.fulfill({ json: { crs: { authority: "EPSG", srid: 3950,
        code: "EPSG:3950", name: "RGF93 / CC50" } } }); return;
    }
    if (request.method() !== "GET") {
      writes.push({ method: request.method(), path, body: request.postDataJSON(),
        ifMatch: request.headers()["if-match"] });
      if (path === "/api/desordres" || path === `/api/desordres/${ID}`) {
        const body = request.postDataJSON();
        await route.fulfill({ json: { ...existingFeature, geometry: body.geometry,
          properties: { ...existingFeature.properties, version: request.method() === "PUT" ? 2 : 1,
            designation: body.designation || existingFeature.properties.designation } } });
        return;
      }
    }
    const empty = { type: "FeatureCollection", features: [] };
    const json = path === "/api/config"
      ? { legacy_single_database_enabled: !multi, show_uuid: false, build_id: "gps-tracking-test" }
      : path === "/api/resource-events/head" ? { cursor: "0" }
      : path === "/api/resource-events" ? { events: [], hasMore: false, nextCursor: "0" }
      : path === "/api/desordres" ? { type: "FeatureCollection", features: existing ? [existingFeature] : [] }
      : path === `/api/desordres/${ID}` ? existingFeature
      : ["/api/troncons", "/api/territoire-administratif"].includes(path) ? empty
      : path === "/api/troncons/options" ? { troncons: [] }
      : path.startsWith("/api/referentiels/") ? { types: [], categories: [], references: [] }
      : path === "/api/systemes-endiguement" ? { systemes: [] } : {};
    await route.fulfill({ json });
  });
  return writes;
}

async function emit(page, index, longitude, latitude, timestamp) {
  await expect.poll(() => page.evaluate(() => window.__gpsWatches.length)).toBeGreaterThan(index);
  await page.evaluate(({ index: i, longitude: lon, latitude: lat, timestamp: time }) => {
    window.__gpsWatches[i].resolve({ coords: { longitude: lon, latitude: lat,
      accuracy: 7 }, timestamp: time });
  }, { index, longitude, latitude, timestamp });
}

async function openPolygon(page) {
  await page.goto("/carte");
  await page.waitForFunction(() => typeof mapViewportReady !== "undefined" && mapViewportReady);
  await page.locator("#toggle-create-menu").click();
  await page.locator('[data-create-type="desordre"]').click();
  await page.locator("#desordre-create-geometry-type").selectOption("Polygon");
  return trackerFor(page, "desordre-create-geometry");
}

async function expectTrackingControlsReachable(page, owner) {
  const result = await page.evaluate((name) => {
    const section = document.querySelector(`[data-gps-owner="${name}"]`);
    return { parent: section.parentElement === document.body,
      controls: ["pause", "finish", "cancel"].map((key) => {
        const button = section.querySelector(`[data-gps="${key}"]`);
        const rect = button.getBoundingClientRect();
        const hit = document.elementFromPoint(rect.left + rect.width / 2,
          rect.top + rect.height / 2);
        return { key, top: rect.top, height: rect.height, width: rect.width,
          disabled: button.disabled, hidden: button.hidden,
          inside: rect.top >= 0 && rect.bottom <= innerHeight
            && rect.left >= 0 && rect.right <= innerWidth,
          hit: hit === button || button.contains(hit) };
      }) };
  }, owner);
  expect(result.parent).toBe(true);
  await expect(page.locator("#editor-map-interaction")).toBeHidden();
  expect(new Set(result.controls.map(control => control.top)).size).toBe(1);
  for (const control of result.controls) {
    expect(control.height).toBeGreaterThanOrEqual(44);
    expect(control.width).toBeGreaterThanOrEqual(44);
    expect(control, control.key).toMatchObject({ disabled: false, hidden: false,
      inside: true, hit: true });
  }
}

test("Désordre : tracé continu, pause et création seulement après confirmation", async ({ context, page }) => {
  const writes = await setup(context);
  await page.goto("/carte");
  await page.waitForFunction(() => typeof mapViewportReady !== "undefined" && mapViewportReady);
  await page.locator("#toggle-create-menu").click();
  await page.locator('[data-create-type="desordre"]').click();
  await page.locator("#desordre-create-geometry-type").selectOption("LineString");
  await page.locator("#desordre-create-designation").fill("Relevé GPS");
  const tracker = trackerFor(page, "desordre-create-geometry");
  await expect(tracker.locator('[data-gps="start"]')).toBeVisible();
  await tracker.locator('[data-gps="start"]').click();
  const time = Date.now();
  await emit(page, 0, 2.34, 48.84, time - 10000);
  await tracker.locator('[data-gps="pause"]').click();
  await expect.poll(() => page.evaluate(() => window.__gpsClears.length)).toBe(1);
  await tracker.locator('[data-gps="pause"]').click();
  await emit(page, 1, 2.3403, 48.84, time - 5000);
  await tracker.locator('[data-gps="finish"]').click();
  await expect(tracker.locator('[data-gps="status"]')).toContainText("Ajustez les sommets");
  expect(writes).toEqual([]);
  await tracker.locator('[data-gps="confirm"]').click();
  await expect(tracker).not.toHaveClass(/floating/);
  await expect(page.locator("#editor-map-interaction")).toBeHidden();
  expect(await page.evaluate(() => buildDesordreCreationPayload().geometry)).toEqual({
    type: "LineString", coordinates: [[2.34, 48.84], [2.3403, 48.84]],
  });
  expect(writes).toEqual([]);
  await page.locator("#submit-desordre-create").click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0].body.geometry.type).toBe("LineString");
});

test("Désordre existant : ancien tracé comparé et PUT protégé au seul Enregistrer", async ({ context, page }) => {
  const writes = await setup(context, { existing: true });
  await page.goto("/carte");
  await page.waitForFunction(id => typeof desordreLayersById !== "undefined"
    && desordreLayersById.get(id), ID);
  await page.evaluate(id => desordreLayersById.get(id).fire("click"), ID);
  await expect(page.locator("#desordre-create-id")).toHaveJSProperty("value", ID);
  await page.locator("#desordre-create-designation").fill("Attribut et tracé conservés");
  const tracker = trackerFor(page, "desordre-create-geometry");
  await tracker.locator('[data-gps="start"]').click();
  const time = Date.now();
  await emit(page, 0, 2.34, 48.84, time - 10000);
  await emit(page, 0, 2.3404, 48.84, time - 5000);
  await tracker.locator('[data-gps="finish"]').click();
  await expect(page.locator('.leaflet-overlay-pane path[stroke="#475569"]')).not.toHaveCount(0);
  expect(writes).toEqual([]);
  await tracker.locator('[data-gps="confirm"]').click();
  expect(writes).toEqual([]);
  await page.locator("#submit-desordre-create").click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0]).toMatchObject({ method: "PUT", path: `/api/desordres/${ID}`,
    ifMatch: '"1"', body: { designation: "Attribut et tracé conservés", geometry: { type: "LineString",
      coordinates: [[2.34, 48.84], [2.3404, 48.84]] } } });
  await expect(page.locator("#desordre-create-message")).toContainText("Enregistré");
});

test("Désordre polygonal : contour fermé dans le brouillon", async ({ context, page }) => {
  const writes = await setup(context);
  await page.goto("/carte");
  await page.waitForFunction(() => typeof mapViewportReady !== "undefined" && mapViewportReady);
  await page.locator("#toggle-create-menu").click();
  await page.locator('[data-create-type="desordre"]').click();
  await page.locator("#desordre-create-geometry-type").selectOption("Polygon");
  const tracker = trackerFor(page, "desordre-create-geometry");
  await tracker.locator('[data-gps="start"]').click();
  const time = Date.now();
  await emit(page, 0, 2.34, 48.84, time - 15000);
  await emit(page, 0, 2.3403, 48.84, time - 10000);
  await emit(page, 0, 2.3403, 48.8403, time - 5000);
  await tracker.locator('[data-gps="finish"]').click();
  await tracker.locator('[data-gps="confirm"]').click();
  const geometry = await page.evaluate(() => buildDesordreCreationPayload().geometry);
  expect(geometry.type).toBe("Polygon");
  expect(geometry.coordinates[0][0]).toEqual(geometry.coordinates[0].at(-1));
  expect(geometry.coordinates[0]).toHaveLength(4);
  expect(writes).toEqual([]);
});

test("Tronçon : relevé LineString confirmé dans le brouillon sans écriture", async ({ context, page }) => {
  const writes = await setup(context);
  await page.goto("/carte");
  await page.waitForFunction(() => typeof mapViewportReady !== "undefined" && mapViewportReady);
  await page.locator("#toggle-create-menu").click();
  await page.locator('[data-create-type="troncon"]').click();
  const tracker = trackerFor(page, "troncon-create-geometry");
  await expect(tracker.locator('[data-gps="start"]')).toBeVisible();
  await tracker.locator('[data-gps="start"]').click();
  const time = Date.now();
  await emit(page, 0, 2.34, 48.84, time - 10000);
  await emit(page, 0, 2.3403, 48.84, time - 5000);
  await tracker.locator('[data-gps="finish"]').click();
  await tracker.locator('[data-gps="confirm"]').click();
  expect(await page.evaluate(() => provisionalTronconLayer?.toGeoJSON(false).geometry))
    .toEqual({ type: "LineString", coordinates: [[2.34, 48.84], [2.3403, 48.84]] });
  expect(writes).toEqual([]);
});

test("Désordre polygonal existant : annuler l’ajustement garde exactement la géométrie serveur", async ({ context, page }) => {
  const existingFeature = { ...original,
    geometry: { type: "Polygon", coordinates: [[
      [2.34, 48.84], [2.3405, 48.84], [2.3405, 48.8405], [2.34, 48.84],
    ]] },
  };
  const writes = await setup(context, { existing: true, existingFeature });
  await page.goto("/carte");
  await page.waitForFunction(id => typeof desordreLayersById !== "undefined"
    && desordreLayersById.get(id), ID);
  await page.evaluate(id => desordreLayersById.get(id).fire("click"), ID);
  await expect(page.locator("#desordre-create-id")).toHaveJSProperty("value", ID);
  const tracker = trackerFor(page, "desordre-create-geometry");
  await tracker.locator('[data-gps="start"]').click();
  const time = Date.now();
  await emit(page, 0, 2.34, 48.84, time - 15000);
  await emit(page, 0, 2.3404, 48.84, time - 10000);
  await emit(page, 0, 2.3404, 48.8404, time - 5000);
  await tracker.locator('[data-gps="finish"]').click();
  await tracker.locator('[data-gps="cancel"]').click();
  expect(writes).toEqual([]);
  expect(await page.evaluate(() => activePolygonLayer.toGeoJSON(false).geometry))
    .toEqual(existingFeature.geometry);
  await expect(page.locator("#desordre-create-designation"))
    .toHaveValue("Ligne existante");
  expect(writes).toEqual([]);
});

test("PWA étroite : carte visible et commandes tactiles accessibles durant le relevé", async ({ context, page }) => {
  const writes = await setup(context);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/carte");
  await page.waitForFunction(() => typeof mapViewportReady !== "undefined" && mapViewportReady);
  await page.locator("#toggle-create-menu").click();
  await page.locator('[data-create-type="desordre"]').click();
  await page.locator("#desordre-create-geometry-type").selectOption("LineString");
  await page.locator("#desordre-create-geometry [data-gps='start']").click();
  const floating = page.locator(".gps-geometry-tracking--floating");
  await expect(floating).toBeVisible();
  await expect(page.locator("main")).toHaveClass(/editor-map-mode/);
  await expect(page.locator("#editor-panel")).toBeHidden();
  await expect(page.locator("#map")).toHaveJSProperty("inert", false);
  const time = Date.now();
  await emit(page, 0, 2.34, 48.84, time - 10000);
  await emit(page, 0, 2.3403, 48.84, time - 5000);
  await expect(floating.locator('[data-gps="status"]')).toContainText("2 points");
  await floating.locator('[data-gps="finish"]').click();
  await floating.locator('[data-gps="confirm"]').click();
  await expect(page.locator("#editor-map-interaction")).toBeHidden();
  await expect(page.locator("#editor-panel")).toBeVisible();
  expect(writes).toEqual([]);
});

test("rotations répétées : Terminer reste cliquable et l'erreur polygonale conserve les points", async ({ context, page }) => {
  const writes = await setup(context);
  await page.setViewportSize({ width: 390, height: 844 });
  const tracker = await openPolygon(page);
  await tracker.locator('[data-gps="start"]').click();
  const time = Date.now();
  await emit(page, 0, 2.34, 48.84, time - 15000);
  await emit(page, 0, 2.3403, 48.84, time - 10000);
  for (const size of [{ width: 320, height: 640 }, { width: 844, height: 390 }, { width: 390, height: 844 },
    { width: 844, height: 390 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(size);
    await expect(tracker).toBeVisible();
    await expectTrackingControlsReachable(page, "desordre-create-geometry");
    expect(await page.evaluate(() => gpsDesordreTracking.getState().points.length)).toBe(2);
  }
  await tracker.locator('[data-gps="finish"]').click();
  await expect(tracker.locator('[data-gps="error"]')).toContainText("Trois positions");
  expect(await page.evaluate(() => gpsDesordreTracking.getState().phase)).toBe("paused");
  await expectTrackingControlsReachable(page, "desordre-create-geometry");
  await tracker.locator('[data-gps="pause"]').click();
  await emit(page, 1, 2.3403, 48.8403, time - 2000);
  await expect(tracker.locator('[data-gps="error"]')).toBeHidden();
  await tracker.locator('[data-gps="pause"]').click();
  await tracker.locator('[data-gps="finish"]').click();
  await expect(tracker.locator('[data-gps="confirm"]')).toBeVisible();
  expect(await page.evaluate(() => gpsDesordreTracking.getState().points.length)).toBe(3);
  expect(writes).toEqual([]);
  await page.setViewportSize({ width: 844, height: 390 });
  await expect(tracker.locator('[data-gps="confirm"]')).toBeVisible();
  await tracker.locator('[data-gps="confirm"]').click();
  expect(await page.evaluate(() => gpsDesordreTracking.getState().phase)).toBe("idle");
  expect(writes).toEqual([]);
});

test("premier point accepté : centrage unique au zoom du fond, sans suivi caméra", async ({ context, page }) => {
  await setup(context);
  await page.goto("/carte");
  await page.waitForFunction(() => typeof mapViewportReady !== "undefined" && mapViewportReady);
  await page.locator("#toggle-create-menu").click();
  await page.locator('[data-create-type="desordre"]').click();
  await page.locator("#desordre-create-geometry-type").selectOption("LineString");
  const tracker = trackerFor(page, "desordre-create-geometry");
  await page.evaluate(() => map.setView([46.8, 2.5], 6, { animate: false }));
  await tracker.locator('[data-gps="start"]').click();
  const time = Date.now();
  await emit(page, 0, 2.34, 48.84, time - 10000);
  const centered = await page.evaluate(() => ({ center: map.getCenter(), zoom: map.getZoom(),
    maxZoom: map.getMaxZoom() }));
  expect(centered.center.lat).toBeCloseTo(48.84, 5);
  expect(centered.center.lng).toBeCloseTo(2.34, 5);
  expect(centered.zoom).toBe(Math.min(19, centered.maxZoom));
  await page.evaluate(() => map.setView([48.8, 2.3], 18, { animate: false }));
  await emit(page, 0, 2.3404, 48.84, time - 5000);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.setViewportSize({ width: 844, height: 390 });
  const manual = await page.evaluate(() => ({ center: map.getCenter(), zoom: map.getZoom() }));
  expect(manual.center.lat).toBeCloseTo(48.8, 5);
  expect(manual.center.lng).toBeCloseTo(2.3, 5);
  expect(manual.zoom).toBe(18);
  await tracker.locator('[data-gps="cancel"]').click();
  await page.evaluate(() => { map.setMaxZoom(20); map.setView([46.8, 2.5], 20,
    { animate: false }); });
  await tracker.locator('[data-gps="start"]').click();
  await emit(page, 1, 2.34, 48.84, Date.now() - 2000);
  expect(await page.evaluate(() => map.getZoom())).toBe(20);
  await tracker.locator('[data-gps="cancel"]').click();
  await page.evaluate(() => { map.setMaxZoom(18); map.setView([46.8, 2.5], 6,
    { animate: false }); });
  await tracker.locator('[data-gps="start"]').click();
  await emit(page, 2, 2.34, 48.84, Date.now() - 2000);
  expect(await page.evaluate(() => map.getZoom())).toBe(18);
});

test("correction intelligente : boucle locale proposée, brut et contour distincts avant confirmation", async ({ context, page }) => {
  const writes = await setup(context);
  const tracker = await openPolygon(page);
  await tracker.locator('[data-gps="start"]').click();
  const time = Date.now();
  for (let i = 0; i < NOISY_LOOP.length; i += 1) {
    await emit(page, 0, NOISY_LOOP[i][0], NOISY_LOOP[i][1], time - 58000 + i * 8000);
  }
  await tracker.locator('[data-gps="finish"]').click();
  await expect(tracker.locator('[data-gps="status"]')).toContainText("Correction automatique");
  const preview = await page.evaluate(() => gpsDesordreTracking.getState());
  expect(preview.correction).toMatch(/boucle/);
  expect(preview.points).toHaveLength(NOISY_LOOP.length);
  expect(preview.geometry.coordinates[0]).toHaveLength(6);
  expect(await page.locator('.leaflet-overlay-pane path[stroke="#168064"]').count())
    .toBeGreaterThan(0);
  expect(await page.locator('.leaflet-overlay-pane path[stroke="#e87c16"]').count())
    .toBeGreaterThan(0);
  expect(writes).toEqual([]);
  await tracker.locator('[data-gps="choice"]').selectOption("raw");
  await tracker.locator('[data-gps="confirm"]').click();
  await expect(tracker.locator('[data-gps="error"]')).toContainText("croise");
  expect(await page.evaluate(() => gpsDesordreTracking.getState().points.length)).toBe(NOISY_LOOP.length);
  await tracker.locator('[data-gps="choice"]').selectOption("corrected");
  await tracker.locator('[data-gps="confirm"]').click();
  expect(await page.evaluate(() => buildDesordreCreationPayload().geometry.coordinates[0].length))
    .toBe(6);
  expect(writes).toEqual([]);
});

test("correction désactivée : boucle éditable mais validation refusée, points intacts", async ({ context, page }) => {
  const writes = await setup(context);
  const tracker = await openPolygon(page);
  await page.evaluate(() => PwaMapSettings.saveSettings({
    ...PwaMapSettings.loadSettings(), tracking: {
      ...PwaMapSettings.loadSettings().tracking, smartPolygonCorrection: false,
    },
  }));
  await tracker.locator('[data-gps="start"]').click();
  const time = Date.now();
  for (let i = 0; i < NOISY_LOOP.length; i += 1) {
    await emit(page, 0, NOISY_LOOP[i][0], NOISY_LOOP[i][1], time - 58000 + i * 8000);
  }
  await tracker.locator('[data-gps="finish"]').click();
  await expect(tracker.locator('[data-gps="error"]')).toContainText("se croise");
  expect(await page.evaluate(() => gpsDesordreTracking.getState().points.length))
    .toBe(NOISY_LOOP.length);
  await expect(page.locator(".leaflet-vertex-icon")).not.toHaveCount(0);
  await expect(tracker.locator('[data-gps="pause"]')).toBeHidden();
  await tracker.locator('[data-gps="confirm"]').click();
  await expect(tracker.locator('[data-gps="error"]')).toContainText("croise");
  expect(await page.evaluate(() => gpsDesordreTracking.getState().points.length)).toBe(NOISY_LOOP.length);
  expect(writes).toEqual([]);
});

test("échec IndexedDB : message visible et Terminer reste fonctionnel", async ({ context, page }) => {
  const writes = await setup(context, { multi: true });
  await page.goto(`/carte?base=${BASE_SLUG}`);
  await page.waitForFunction(() => typeof mapViewportReady !== "undefined" && mapViewportReady);
  await page.locator("#toggle-create-menu").click();
  await page.locator('[data-create-type="desordre"]').click();
  await page.locator("#desordre-create-geometry-type").selectOption("Polygon");
  const tracker = trackerFor(page, "desordre-create-geometry");
  await tracker.locator('[data-gps="start"]').click();
  await expect.poll(() => page.evaluate(async () => {
    const store = PwaGeometryDrafts.createStore({ indexedDB, runtime: BusinessRuntime });
    return (await store.read("desordres:create:new"))?.points?.length;
  })).toBe(0);
  await page.evaluate(() => {
    IDBFactory.prototype.open = function () { throw new Error("Test stockage indisponible"); };
  });
  const time = Date.now();
  await emit(page, 0, 2.34, 48.84, time - 15000);
  await emit(page, 0, 2.3403, 48.84, time - 10000);
  await expect(tracker.locator('[data-gps="status"]')).toContainText("Stockage local indisponible");
  await expectTrackingControlsReachable(page, "desordre-create-geometry");
  await tracker.locator('[data-gps="finish"]').click();
  await expect(tracker.locator('[data-gps="error"]')).toContainText("Trois positions");
  expect(await page.evaluate(() => gpsDesordreTracking.getState().points.length)).toBe(2);
  expect(writes).toEqual([]);
});

test("relevé local : rechargement, reprise, pause et confirmation sans synchronisation automatique", async ({ context, page }) => {
  const writes = await setup(context, { multi: true });
  await page.goto(`/carte?base=${BASE_SLUG}`);
  await page.waitForFunction(() => typeof mapViewportReady !== "undefined" && mapViewportReady);
  await page.locator("#toggle-create-menu").click();
  await page.locator('[data-create-type="desordre"]').click();
  await page.locator("#desordre-create-geometry-type").selectOption("LineString");
  const tracker = trackerFor(page, "desordre-create-geometry");
  await expect(tracker.locator('[data-gps="start"]')).toBeEnabled();
  await tracker.locator('[data-gps="start"]').click();
  const time = Date.now();
  await emit(page, 0, 2.34, 48.84, time - 15000);
  await emit(page, 0, 2.3403, 48.84, time - 10000);
  await expect.poll(() => page.evaluate(async () => {
    const store = PwaGeometryDrafts.createStore({ indexedDB, runtime: BusinessRuntime });
    return (await store.read("desordres:create:new"))?.points?.length;
  })).toBe(2);
  expect(writes).toEqual([]);
  await page.reload();
  await page.waitForFunction(() => typeof mapViewportReady !== "undefined" && mapViewportReady);
  await page.locator("#toggle-create-menu").click();
  await page.locator('[data-create-type="desordre"]').click();
  await expect(tracker.locator('[data-gps="abandon"]')).toBeVisible();
  await expect(tracker.locator('[data-gps="restore"]')).toBeDisabled();
  await page.locator("#desordre-create-geometry-type").selectOption("LineString");
  await expect(tracker.locator('[data-gps="restore"]')).toBeVisible();
  await expect(tracker.locator('[data-gps="status"]')).toContainText("2 points");
  await tracker.locator('[data-gps="restore"]').click();
  await expect(tracker.locator('[data-gps="status"]')).toContainText("en pause");
  await tracker.locator('[data-gps="pause"]').click();
  await emit(page, 0, 2.3406, 48.84, Date.now() - 5000);
  await tracker.locator('[data-gps="pause"]').click();
  await expect(tracker.locator('[data-gps="status"]')).toContainText("3 points");
  await tracker.locator('[data-gps="finish"]').click();
  expect(writes).toEqual([]);
  await tracker.locator('[data-gps="confirm"]').click();
  await expect.poll(() => page.evaluate(async () => {
    const store = PwaGeometryDrafts.createStore({ indexedDB, runtime: BusinessRuntime });
    return await store.read("desordres:create:new");
  })).toBeUndefined();
  expect(writes).toEqual([]);
});

test("relevé local : abandon explicite après rechargement", async ({ context, page }) => {
  const writes = await setup(context, { multi: true });
  await page.goto(`/carte?base=${BASE_SLUG}`);
  await page.waitForFunction(() => typeof mapViewportReady !== "undefined" && mapViewportReady);
  await page.locator("#toggle-create-menu").click();
  await page.locator('[data-create-type="troncon"]').click();
  const tracker = trackerFor(page, "troncon-create-geometry");
  await expect(tracker.locator('[data-gps="start"]')).toBeEnabled();
  await tracker.locator('[data-gps="start"]').click();
  await emit(page, 0, 2.34, 48.84, Date.now() - 10000);
  await expect.poll(() => page.evaluate(async () => {
    const store = PwaGeometryDrafts.createStore({ indexedDB, runtime: BusinessRuntime });
    return (await store.read("troncons:create:new"))?.points?.length;
  })).toBe(1);
  await page.reload();
  await page.waitForFunction(() => typeof mapViewportReady !== "undefined" && mapViewportReady);
  await page.locator("#toggle-create-menu").click();
  await page.locator('[data-create-type="troncon"]').click();
  await expect(tracker.locator('[data-gps="abandon"]')).toBeVisible();
  await tracker.locator('[data-gps="abandon"]').click();
  await expect(tracker.locator('[data-gps="start"]')).toBeEnabled();
  await expect.poll(() => page.evaluate(async () => {
    const store = PwaGeometryDrafts.createStore({ indexedDB, runtime: BusinessRuntime });
    return await store.read("troncons:create:new");
  })).toBeUndefined();
  expect(writes).toEqual([]);
});

test("stockage GPS : isolation utilisateur/base et protection contre un autre onglet", async ({ context, page }) => {
  await setup(context, { multi: true });
  await page.goto(`/carte?base=${BASE_SLUG}`);
  await page.waitForFunction(() => typeof PwaGeometryDrafts !== "undefined");
  const result = await page.evaluate(async () => {
    const runtime = identity => ({ context: { key: identity }, isActive: () => true,
      assertActive() {}, key: name => `${name}:${identity}` });
    const key = "desordres:create:new";
    const record = { trackId: "first", revision: 1,
      points: [{ longitude: 2, latitude: 48 }] };
    const first = PwaGeometryDrafts.createStore({ indexedDB, runtime: runtime("user-a:base-a") });
    const otherUser = PwaGeometryDrafts.createStore({ indexedDB, runtime: runtime("user-b:base-a") });
    const otherBase = PwaGeometryDrafts.createStore({ indexedDB, runtime: runtime("user-a:base-b") });
    await first.write(key, record);
    let refused = false;
    try { await first.write(key, { ...record, trackId: "second", revision: 2 }); }
    catch (error) { refused = /autre onglet/.test(error.message); }
    await first.write(key, { ...record, revision: 2 });
    let staleRemovalRefused = false;
    try { await first.remove(key, "first", 1); }
    catch (error) { staleRemovalRefused = /autre onglet/.test(error.message); }
    const result = { refused, first: await first.read(key),
      staleRemovalRefused, otherUser: await otherUser.read(key),
      otherBase: await otherBase.read(key) };
    await first.remove(key, "first", 2);
    await first.write(key, { trackId: "broken", revision: 1, points: [] });
    const invalid = await first.read(key);
    await first.removeInvalid(key, invalid);
    result.invalidRemoved = await first.read(key) === undefined;
    return result;
  });
  expect(result).toEqual({ refused: true, staleRemovalRefused: true, invalidRemoved: true,
    first: { trackId: "first", revision: 2, points: [{ longitude: 2, latitude: 48 }] },
    otherUser: undefined, otherBase: undefined });
});

test("reprise d'une correction : version serveur changée, validation interdite", async ({ context, page }) => {
  const feature = structuredClone(original);
  const writes = await setup(context, { multi: true, existing: true, existingFeature: feature });
  await page.goto(`/carte?base=${BASE_SLUG}`);
  await page.waitForFunction(id => typeof desordreLayersById !== "undefined"
    && desordreLayersById.get(id), ID);
  await page.evaluate(id => desordreLayersById.get(id).fire("click"), ID);
  const tracker = trackerFor(page, "desordre-create-geometry");
  await expect(tracker.locator('[data-gps="start"]')).toBeEnabled();
  await tracker.locator('[data-gps="start"]').click();
  await emit(page, 0, 2.34, 48.84, Date.now() - 10000);
  await expect.poll(() => page.evaluate(async () => {
    const store = PwaGeometryDrafts.createStore({ indexedDB, runtime: BusinessRuntime });
    return (await store.read(`desordres:edit:${"11111111-1111-4111-8111-111111111111"}`))?.points?.length;
  })).toBe(1);
  feature.properties.version = 2;
  await page.reload();
  await page.waitForFunction(id => typeof desordreLayersById !== "undefined"
    && desordreLayersById.get(id), ID);
  await page.evaluate(id => desordreLayersById.get(id).fire("click"), ID);
  await expect(tracker.locator('[data-gps="restore"]')).toBeVisible();
  await expect(tracker.locator('[data-gps="restore"]')).toBeDisabled();
  await expect(tracker.locator('[data-gps="status"]')).toContainText("version");
  expect(writes).toEqual([]);
});

test("territoire changé pendant un relevé : GPS suspendu et brouillon non validable", async ({ context, page }) => {
  const writes = await setup(context, { multi: true });
  await page.goto(`/carte?base=${BASE_SLUG}`);
  await page.waitForFunction(() => typeof mapViewportReady !== "undefined" && mapViewportReady);
  await page.locator("#toggle-create-menu").click();
  await page.locator('[data-create-type="troncon"]').click();
  const tracker = trackerFor(page, "troncon-create-geometry");
  await expect(tracker.locator('[data-gps="start"]')).toBeEnabled();
  await tracker.locator('[data-gps="start"]').click();
  await emit(page, 0, 2.34, 48.84, Date.now() - 10000);
  await page.evaluate(() => setTerritoireAdministratifState({ type: "FeatureCollection", features: [
    { type: "Feature", properties: {}, geometry: { type: "Polygon",
      coordinates: [[[2, 48], [3, 48], [3, 49], [2, 48]]] } },
  ] }));
  await expect(tracker.locator('[data-gps="status"]')).toContainText("territoire");
  await expect(tracker.locator('[data-gps="pause"]')).toBeDisabled();
  expect(await page.evaluate(() => gpsTronconTracking.getState().points.length)).toBe(1);
  expect(writes).toEqual([]);
});

// Niveau B : vraie édition Leaflet et IndexedDB, API métier simulée. Aucun SQL.
test('ajustement : sommets invalides refusés puis réparés sans nouvelle acquisition', async ({context,page}) => {
  const writes = await setup(context);
  await page.setViewportSize({width:320,height:640});
  const tracker = await openPolygon(page);
  await tracker.locator('[data-gps=start]').click();
  await expect(tracker).toBeFocused();
  await expectTrackingControlsReachable(page,'desordre-create-geometry');
  const points = [[2.34,48.84],[2.3404,48.84],[2.3404,48.8404],[2.34,48.8404]];
  for(let i=0;i<points.length;i++) await emit(page,0,...points[i],Date.now()-25000+i*5000);
  await page.screenshot({path:test.info().outputPath('acquisition-320.png')});
  await tracker.locator('[data-gps=finish]').click();
  await expect(tracker.locator('button:visible')).toHaveText(['Valider','Annuler']);
  await expect(page.locator('.leaflet-vertex-icon')).not.toHaveCount(0);
  await page.screenshot({path:test.info().outputPath('ajustement-320.png')});
  // The existing map summary is dismissible and can cover a high vertex.
  for (const close of await page.getByRole('button',{name:'Fermer le message',exact:true}).all())
    if (await close.isVisible()) await close.click();
  // Drag the actual Leaflet handle through browser pointer events.
  const drag = await page.evaluate(() => {
    let layer; map.eachLayer(item => {if(item.getLatLngs && item.editEnabled?.()) layer=item});
    const point=layer.getLatLngs()[0][0];
    const box=point.__vertex._icon.getBoundingClientRect();
    const target=map.latLngToContainerPoint([48.8405,2.3402]);
    const mapBox=map.getContainer().getBoundingClientRect();
    return {from:{x:box.x+box.width/2,y:box.y+box.height/2},
      to:{x:mapBox.x+target.x,y:mapBox.y+target.y}};
  });
  await page.mouse.move(drag.from.x,drag.from.y);await page.mouse.down();
  await page.mouse.move(drag.to.x,drag.to.y,{steps:8});await page.mouse.up();
  await tracker.locator('[data-gps=confirm]').click();
  await expect(tracker.locator('[data-gps=error]')).toBeVisible();
  expect(writes).toEqual([]);
  await page.mouse.move(drag.to.x,drag.to.y);await page.mouse.down();
  await page.mouse.move(drag.from.x,drag.from.y,{steps:8});await page.mouse.up();
  await tracker.locator('[data-gps=confirm]').click();
  await expect(tracker).not.toHaveClass(/floating/);
  expect(writes).toEqual([]);
});

test('ajustement durable : brut, corrigé et sommets modifiés restaurés séparément', async ({context,page}) => {
  const writes=await setup(context,{multi:true});
  await page.goto(`/carte?base=${BASE_SLUG}`);
  await page.waitForFunction(()=>typeof mapViewportReady!=='undefined'&&mapViewportReady);
  await page.locator('#toggle-create-menu').click();await page.locator('[data-create-type=desordre]').click();
  await page.locator('#desordre-create-geometry-type').selectOption('Polygon');
  const tracker=trackerFor(page,'desordre-create-geometry');await tracker.locator('[data-gps=start]').click();
  for(let i=0;i<NOISY_LOOP.length;i++)await emit(page,0,...NOISY_LOOP[i],Date.now()-58000+i*8000);
  await tracker.locator('[data-gps=finish]').click();
  await page.evaluate(()=>{let layer;map.eachLayer(item=>{if(item.getLatLngs && item.editEnabled?.())layer=item});
    layer.getLatLngs()[0][0].lng-=.0001;layer.editor.refresh();layer.fire('editable:editing',{layer});});
  const before=await page.evaluate(()=>gpsDesordreTracking.getState());
  await expect.poll(()=>page.evaluate(async()=>{
    const store=PwaGeometryDrafts.createStore({indexedDB,runtime:BusinessRuntime});return(await store.read('desordres:create:new'))?.geometry;
  })).toEqual(before.geometry);
  await page.reload();await page.waitForFunction(()=>typeof mapViewportReady!=='undefined'&&mapViewportReady);
  await page.locator('#toggle-create-menu').click();await page.locator('[data-create-type=desordre]').click();
  await page.locator('#desordre-create-geometry-type').selectOption('Polygon');
  await tracker.locator('[data-gps=restore]').click();
  const restored=await page.evaluate(()=>gpsDesordreTracking.getState());
  for(const key of ['points','rawGeometry','correctedGeometry','choice','geometry','editedGeometries'])expect(restored[key]).toEqual(before[key]);
  await expect(tracker.locator('[data-gps=pause]')).toBeHidden();
  expect(await page.evaluate(()=>window.__gpsWatches.length)).toBe(0);
  await tracker.locator('[data-gps=choice]').selectOption('raw');
  expect(await page.evaluate(()=>gpsDesordreTracking.getState().geometry)).toEqual(before.rawGeometry);
  await tracker.locator('[data-gps=choice]').selectOption('corrected');
  expect(await page.evaluate(()=>gpsDesordreTracking.getState().geometry)).toEqual(before.geometry);
  await tracker.locator('[data-gps=cancel]').click();expect(writes).toEqual([]);
});

test('deuxième relevé : annulation et rechargement préservent le premier brouillon',async({context,page})=>{
  const writes=await setup(context,{multi:true});
  await page.goto(`/carte?base=${BASE_SLUG}`);await page.waitForFunction(()=>typeof mapViewportReady!=='undefined'&&mapViewportReady);
  await page.locator('#toggle-create-menu').click();await page.locator('[data-create-type=troncon]').click();
  const tracker=trackerFor(page,'troncon-create-geometry');await tracker.locator('[data-gps=start]').click();
  await emit(page,0,2.34,48.84,Date.now()-15000);await emit(page,0,2.3403,48.84,Date.now()-10000);
  await tracker.locator('[data-gps=finish]').click();await tracker.locator('[data-gps=confirm]').click();
  await expect(tracker.locator('[data-gps=start]')).toBeEnabled();
  const previous=await page.evaluate(()=>provisionalTronconLayer.toGeoJSON(false).geometry);
  await tracker.locator('[data-gps=start]').click();await emit(page,1,2.341,48.84,Date.now()-5000);
  await tracker.locator('[data-gps=cancel]').click();
  expect(await page.evaluate(()=>provisionalTronconLayer.toGeoJSON(false).geometry)).toEqual(previous);
  await tracker.locator('[data-gps=start]').click();await emit(page,2,2.342,48.84,Date.now()-2000);
  await expect.poll(()=>page.evaluate(async()=>{const store=PwaGeometryDrafts.createStore({indexedDB,runtime:BusinessRuntime});return(await store.read('troncons:create:new'))?.baselineGeometry})).toEqual(previous);
  await page.reload();await page.waitForFunction(()=>typeof mapViewportReady!=='undefined'&&mapViewportReady);
  await page.locator('#toggle-create-menu').click();await page.locator('[data-create-type=troncon]').click();
  await tracker.locator('[data-gps=restore]').click();await tracker.locator('[data-gps=cancel]').click();
  expect(await page.evaluate(()=>provisionalTronconLayer.toGeoJSON(false).geometry)).toEqual(previous);
  expect(writes).toEqual([]);
});

// Niveau B : commandes clavier et ressources Leaflet réelles, sans écriture métier.
for (const type of ['LineString','Polygon']) test(`clavier ${type} : focus visible et cibles Leaflet nettoyées`,async({context,page})=>{
  const writes=await setup(context);
  await page.setViewportSize({width:390,height:844});
  const tracker=await openPolygon(page);
  await page.locator('#desordre-create-geometry-type').selectOption(type);
  const before=await page.evaluate(()=>({zoom:map.doubleClickZoom.enabled(),
    dangling:Object.values(map._targets).filter(target=>target!==map&&!map.hasLayer(target)).length}));
  await tracker.locator('[data-gps=start]').click();await expect(tracker).toBeFocused();
  const points=[[2.34,48.84],[2.3403,48.84],[2.3403,48.8403]];
  for(let i=0;i<(type==='Polygon'?3:2);i++)await emit(page,0,...points[i],Date.now()-20000+i*5000);
  await page.keyboard.press('Tab');await expect(tracker.locator('[data-gps=pause]')).toBeFocused();
  await page.keyboard.press('Space');await expect(tracker.locator('[data-gps=pause]')).toHaveText('Reprendre');
  await expect(tracker).toBeFocused();await page.keyboard.press('Tab');await page.keyboard.press('Enter');
  await expect(tracker).toBeFocused();
  await page.keyboard.press('Tab');await page.keyboard.press('Tab');
  await expect(tracker.locator('[data-gps=finish]')).toBeFocused();await page.keyboard.press('Enter');
  await expect(tracker).toBeFocused();await page.keyboard.press('Tab');
  await expect(tracker.locator('[data-gps=confirm]')).toBeFocused();await page.keyboard.press('Enter');
  await expect(tracker).not.toHaveClass(/floating/);await expect(page.locator('#editor-panel [data-role=close]:visible')).toBeFocused();
  const clean=()=>page.evaluate(()=>({zoom:map.doubleClickZoom.enabled(),blocked:Boolean(map.editTools._oldTargets),
    dangling:Object.values(map._targets).filter(target=>target!==map&&!map.hasLayer(target)).length}));
  expect(await clean()).toEqual({...before,blocked:false});
  await expect(page.locator('.leaflet-vertex-icon')).toHaveCount(0);
  const draft=await page.evaluate(()=>buildDesordreCreationPayload().geometry);
  await tracker.locator('[data-gps=start]').click();
  for(let i=0;i<(type==='Polygon'?3:2);i++)await emit(page,2,...points[i],Date.now()-20000+i*5000);
  await tracker.locator('[data-gps=finish]').click();await expect(tracker).toBeFocused();
  await page.keyboard.press('Tab');await page.keyboard.press('Tab');
  await expect(tracker.locator('[data-gps=cancel]')).toBeFocused();await page.keyboard.press('Enter');
  await expect(page.locator('#editor-panel [data-role=close]:visible')).toBeFocused();expect(await clean()).toEqual({...before,blocked:false});
  expect(await page.evaluate(()=>buildDesordreCreationPayload().geometry)).toEqual(draft);
  expect(writes).toEqual([]);
});
