const { mockBusinessCRS } = require('./mock-business-crs');
const { expect, test } = require('@playwright/test');

test.use({ serviceWorkers: 'block' });

const POINT_ID = '11111111-1111-4111-8111-111111111111';
const savedPoint = {
  type: 'Feature', geometry: { type: 'Point', coordinates: [2.34, 48.84] },
  properties: { id: POINT_ID, designation: 'Point existant', valid: true,
    version: 1, troncon_ids: [], longitude: 2.34, latitude: 48.84 },
};

async function setup(context, { existing = false, userPosition = false } = {}) {
  await context.addInitScript((showUserPosition) => {
    if (showUserPosition) localStorage.setItem('digues:pwa-map-settings:v1', JSON.stringify({
      version: 1, showUserPosition: true,
      tracking: { distanceMeters: 5, intervalSeconds: 5, maxAccuracyMeters: 25 },
      proximity: { enabled: false, radiusKm: 5 },
    }));
    const real = window.matchMedia.bind(window);
    const listeners = new Set();
    const standalone = {
      matches: true, media: '(display-mode: standalone)',
      addEventListener(type, callback) { if (type === 'change') listeners.add(callback); },
    };
    window.matchMedia = query => query === standalone.media ? standalone : real(query);
    window.__setStandalone = value => {
      standalone.matches = value;
      for (const callback of listeners) callback({ matches: value });
    };
    window.__gpsRequests = [];
    window.__gpsWatches = [];
    window.__gpsClears = [];
    Object.defineProperty(navigator, 'geolocation', { configurable: true, value: {
      getCurrentPosition(resolve, reject) { window.__gpsRequests.push({ resolve, reject }); },
      watchPosition(resolve, reject) {
        const id = window.__gpsWatches.length + 1;
        window.__gpsWatches.push({ resolve, reject });
        return id;
      },
      clearWatch(id) { window.__gpsClears.push(id); },
    } });
  }, userPosition);
  const calls = [];
  await context.route('**/api/**', async route => {
    if (await mockBusinessCRS(route)) return;
    const request = route.request();
    const path = new URL(request.url()).pathname;
    calls.push({ method: request.method(), path });
    const empty = { type: 'FeatureCollection', features: [] };
    const json = path === '/api/config' ? { legacy_single_database_enabled: true, show_uuid: false, build_id: 'geo-5a-test' }
      : path === '/api/resource-events/head' ? { cursor: '0' }
      : path === '/api/resource-events' ? { events: [], hasMore: false, nextCursor: '0' }
      : path === '/api/desordres' ? { type: 'FeatureCollection',
        features: existing ? [savedPoint] : [] }
      : path === `/api/desordres/${POINT_ID}` ? savedPoint
      : ['/api/troncons', '/api/territoire-administratif'].includes(path) ? empty
      : path === '/api/troncons/options' ? { troncons: [] }
      : path.startsWith('/api/referentiels/') ? { types: [], categories: [], references: [] }
      : path === '/api/systemes-endiguement' ? { systemes: [] }
      : {};
    await route.fulfill({ json });
  });
  return calls;
}

async function openCreation(page) {
  await page.goto('/carte');
  // Le test de nettoyage lors d'un changement de mode exerce ici la capacité
  // conservée sous l'interface PWA simplifiée.
  await page.addStyleTag({ content: `
    body.pwa-standalone #desordre-mode-selector { display: flex !important; }
  ` });
  await expect(page.locator('#map')).toBeVisible();
  await page.waitForFunction(() => Boolean(window.MapResourceConsumers)
    && typeof mapViewportReady !== 'undefined' && mapViewportReady);
  await page.locator('#toggle-create-menu').click();
  await page.locator('[data-create-type="desordre"]').click();
  await expect(page.locator('#gps-map-create')).toBeVisible();
}

async function fix(page, index, longitude, latitude, accuracy = 8) {
  await expect.poll(() => page.evaluate(() => window.__gpsRequests.length)).toBe(index + 1);
  await page.evaluate(({ index: i, longitude: lon, latitude: lat, accuracy: meters }) => {
    window.__gpsRequests[i].resolve({
      coords: { longitude: lon, latitude: lat, accuracy: meters }, timestamp: Date.now(),
    });
  }, { index, longitude, latitude, accuracy });
  await expect(page.locator('.map-candidate-marker')).toBeVisible();
}

async function candidateState(page) {
  return page.evaluate(() => ({
    candidateLayers: Object.values(map._layers).filter(layer =>
      (layer instanceof L.Marker || layer instanceof L.Circle)
      && layer.options?.pane === 'map-candidate').length,
    accuracy: Object.values(map._layers).find(layer =>
      layer instanceof L.Circle && layer.options.pane === 'map-candidate')?.getRadius(),
    draft: provisionalDesordreLayer?.toGeoJSON(false).geometry ?? null,
    editEnabled: provisionalDesordreLayer?.editEnabled?.() ?? false,
    provisional: provisionalLatLng ? [provisionalLatLng.lng, provisionalLatLng.lat] : null,
  }));
}

function noWrites(calls) {
  expect(calls.filter(call => ['POST', 'PUT', 'PATCH', 'DELETE'].includes(call.method))).toEqual([]);
}

test('Point Carto : candidat sur carte, Réessayer, acceptation et retour au formulaire', async ({ context, page }) => {
  const calls = await setup(context);
  await page.setViewportSize({ width: 390, height: 844 });
  await openCreation(page);
  await page.locator('#desordre-create-designation').fill('Brouillon conservé');
  await context.setOffline(true);
  await page.locator('#gps-map-create').click();
  await fix(page, 0, 2.35, 48.85, 9);
  await expect(page.locator('#editor-panel')).toBeHidden();
  await expect(page.locator('#gps-candidate-accept')).toBeFocused();
  await expect(page.locator('#gps-position-candidate')).toBeVisible();
  await expect(page.locator('#finish-editor-map-interaction')).toBeHidden();
  await expect(page.locator('#cancel-editor-map-interaction')).toBeHidden();
  expect(await candidateState(page)).toMatchObject({ candidateLayers: 2, accuracy: 9,
    draft: null });
  noWrites(calls);
  await expect.poll(() => page.evaluate(() => map.getZoom())).toBeGreaterThanOrEqual(16);
  await page.evaluate(() => map.panBy([80, 0], { animate: false }));
  const exploredCenter = await page.evaluate(() => map.getCenter().lng);
  await page.waitForTimeout(150);
  expect(await page.evaluate(() => map.getCenter().lng)).toBeCloseTo(exploredCenter, 6);

  await page.setViewportSize({ width: 700, height: 844 });
  await expect(page.locator('#editor-panel')).toBeVisible();
  await expect(page.locator('#gps-candidate-accept')).toBeFocused();
  await expect(page.locator('#gps-position-candidate')).toBeVisible();
  expect(await candidateState(page)).toMatchObject({ candidateLayers: 2, draft: null });
  await page.setViewportSize({ width: 699, height: 844 });
  await expect(page.locator('#editor-panel')).toBeHidden();
  await expect(page.locator('#gps-candidate-accept')).toBeFocused();

  await page.locator('#gps-candidate-retry').click();
  await expect(page.locator('.map-candidate-marker')).toHaveCount(0);
  await fix(page, 1, 2.36, 48.86, 6);
  expect(await candidateState(page)).toMatchObject({ candidateLayers: 2, accuracy: 6,
    draft: null });
  await page.locator('#gps-candidate-accept').click();
  await expect(page.locator('.map-candidate-marker')).toHaveCount(0);
  await expect(page.locator('#finish-editor-map-interaction')).toBeVisible();
  await expect(page.locator('#finish-editor-map-interaction')).toBeFocused();
  expect(await candidateState(page)).toMatchObject({ candidateLayers: 0,
    draft: { type: 'Point', coordinates: [2.36, 48.86] }, editEnabled: true });
  await page.locator('#finish-editor-map-interaction').click();
  await expect(page.locator('#editor-panel')).toBeVisible();
  await expect(page.locator('#desordre-create-designation')).toHaveValue('Brouillon conservé');
  noWrites(calls);
  await context.setOffline(false);
});

test('Annuler et interaction active conservent le brouillon cartographique', async ({ context, page }) => {
  const calls = await setup(context);
  await page.setViewportSize({ width: 390, height: 844 });
  await openCreation(page);
  await page.locator('#start-desordre-draw').click();
  await expect(page.locator('#editor-map-interaction')).toBeVisible();
  await page.locator('#gps-map-create').evaluate(button => button.click());
  await expect(page.locator('#desordre-create-message')).toContainText('Annulez explicitement');
  expect(await page.evaluate(() => window.__gpsRequests.length)).toBe(0);
  await page.locator('#cancel-editor-map-interaction').click();
  await page.locator('#gps-map-create').click();
  await fix(page, 0, 2.35, 48.85);
  await page.locator('#gps-candidate-cancel').click();
  await expect(page.locator('.map-candidate-marker')).toHaveCount(0);
  await expect(page.locator('#gps-map-create')).toBeFocused();
  expect((await candidateState(page)).draft).toBeNull();

  await page.locator('#gps-map-create').click();
  await fix(page, 1, 2.35, 48.85);
  await page.locator('#gps-candidate-accept').click();
  await page.locator('#finish-editor-map-interaction').click();
  const previous = (await candidateState(page)).draft;
  await page.locator('#gps-map-create').click();
  await expect(page.locator('#desordre-create-message')).toContainText('Annulez explicitement');
  expect(await page.evaluate(() => window.__gpsRequests.length)).toBe(2);
  expect((await candidateState(page)).draft).toEqual(previous);
  noWrites(calls);
});

test('Point existant : inspection sans déplacement, puis édition provisoire et annulation métier', async ({ context, page }) => {
  const calls = await setup(context, { existing: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/carte');
  await expect(page.locator('.desordre-point-marker')).toHaveCount(1);
  await page.locator('.desordre-point-marker').dispatchEvent('click');
  await expect(page.locator('#gps-map-edit')).toBeVisible();
  const original = await page.evaluate(() => [activePointLayer.getLatLng().lng,
    activePointLayer.getLatLng().lat]);
  await page.locator('#gps-map-edit').click();
  await fix(page, 0, 2.35, 48.85);
  expect(await page.evaluate(() => [activePointLayer.getLatLng().lng,
    activePointLayer.getLatLng().lat])).toEqual(original);
  expect((await candidateState(page)).provisional).toBeNull();
  await page.locator('#gps-candidate-cancel').click();
  expect(await page.evaluate(() => [activePointLayer.getLatLng().lng,
    activePointLayer.getLatLng().lat])).toEqual(original);
  await page.locator('#gps-map-edit').click();
  await fix(page, 1, 2.36, 48.86);
  await page.locator('#gps-candidate-accept').click();
  await expect(page.locator('.map-candidate-marker')).toHaveCount(0);
  expect((await candidateState(page)).provisional).toEqual([2.36, 48.86]);
  await expect(page.locator('#finish-editor-map-interaction')).toBeVisible();
  await page.locator('#gps-map-edit').evaluate(button => button.click());
  await expect(page.locator('#desordre-create-message')).toContainText('Annulez explicitement');
  expect(await page.evaluate(() => window.__gpsRequests.length)).toBe(2);
  await page.locator('#cancel-editor-map-interaction').click();
  expect(await page.evaluate(() => [activePointLayer.getLatLng().lng,
    activePointLayer.getLatLng().lat])).toEqual(original);
  noWrites(calls);
});

test('GEO-3B et candidat restent deux couches et un seul watch natif', async ({ context, page }) => {
  const calls = await setup(context, { userPosition: true });
  await page.setViewportSize({ width: 1200, height: 800 });
  await openCreation(page);
  await expect.poll(() => page.evaluate(() => window.__gpsWatches.length)).toBe(1);
  await page.evaluate(() => window.__gpsWatches[0].resolve({
    coords: { longitude: 2.35, latitude: 48.85, accuracy: 8 }, timestamp: Date.now(),
  }));
  await expect(page.locator('.user-position-marker')).toBeVisible();
  await page.locator('#gps-map-create').click();
  await fix(page, 0, 2.35001, 48.85001);
  await expect(page.locator('.user-position-marker')).toBeVisible();
  await expect(page.locator('.map-candidate-marker')).toBeVisible();
  await expect(page.locator('#editor-map-interaction')).toBeVisible();
  await expect(page.locator('#finish-editor-map-interaction')).toBeHidden();
  expect(await page.evaluate(() => ({ watches: window.__gpsWatches.length,
    requests: window.__gpsRequests.length,
    candidateZ: Number(map.getPane('map-candidate').style.zIndex),
    userZ: Number(map.getPane('user-position').style.zIndex) })))
    .toMatchObject({ watches: 1, requests: 1, candidateZ: 660, userZ: 650 });
  await page.locator('#gps-candidate-cancel').click();
  await expect(page.locator('.user-position-marker')).toBeVisible();
  noWrites(calls);
});

test('Un Réessayer en erreur conserve le candidat accepté précédemment sur la carte', async ({ context, page }) => {
  const calls = await setup(context);
  await page.setViewportSize({ width: 390, height: 844 });
  await openCreation(page);
  await page.locator('#gps-map-create').click();
  await fix(page, 0, 2.35, 48.85);
  await page.locator('#gps-candidate-retry').click();
  await expect(page.locator('.map-candidate-marker')).toHaveCount(0);
  await expect.poll(() => page.evaluate(() => window.__gpsRequests.length)).toBe(2);
  await page.evaluate(() => window.__gpsRequests[1].reject({ code: 3 }));
  await expect(page.locator('#gps-candidate-status')).toContainText('Position précédente conservée');
  await expect(page.locator('.map-candidate-marker')).toBeVisible();
  expect((await candidateState(page)).draft).toBeNull();
  await page.locator('#gps-candidate-accept').click();
  expect((await candidateState(page)).draft).toEqual({
    type: 'Point', coordinates: [2.35, 48.85],
  });
  noWrites(calls);
});

test('Changement de mode, pagehide et callback tardif nettoient le candidat', async ({ context, page }) => {
  const calls = await setup(context);
  await page.setViewportSize({ width: 1200, height: 800 });
  await openCreation(page);
  await page.locator('#gps-map-create').click();
  await fix(page, 0, 2.35, 48.85);
  await page.locator('#desordre-xy-choice').click();
  await expect(page.locator('.map-candidate-marker')).toHaveCount(0);
  await expect(page.locator('#gps-position-candidate')).toBeHidden();
  expect((await candidateState(page)).draft).toBeNull();
  await page.locator('#desordre-mode-selector label').filter({ hasText: 'Carto' }).click();
  await page.locator('#gps-map-create').click();
  await expect.poll(() => page.evaluate(() => window.__gpsRequests.length)).toBe(2);
  await page.locator('#gps-candidate-cancel').click();
  await page.evaluate(() => window.__gpsRequests[1].resolve({
    coords: { longitude: 2.36, latitude: 48.86, accuracy: 8 }, timestamp: Date.now(),
  }));
  await expect(page.locator('.map-candidate-marker')).toHaveCount(0);
  await page.locator('#gps-map-create').click();
  await fix(page, 2, 2.37, 48.87);
  await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
  await expect(page.locator('.map-candidate-marker')).toHaveCount(0);
  await expect(page.locator('#gps-position-candidate')).toBeHidden();
  expect((await candidateState(page)).draft).toBeNull();
  noWrites(calls);
});
