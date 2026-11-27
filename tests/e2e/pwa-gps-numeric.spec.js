const { mockBusinessCRS } = require('./mock-business-crs');
const { expect, test } = require('@playwright/test');

test.use({ serviceWorkers: 'block' });
const T = '11111111-1111-4111-8111-111111111111';
const S = '22222222-2222-4222-8222-222222222222';
const B = '33333333-3333-4333-8333-333333333333';
const B2 = '44444444-4444-4444-8444-444444444444';

async function setup(context, standalone = true, srid = 3950) {
  const calls = [];
  let calculationFails = false;
  let calculationGate = null;
  await context.addInitScript((initial) => {
    const real = window.matchMedia.bind(window);
    window.matchMedia = query => query === '(display-mode: standalone)'
      ? { matches: initial, addEventListener() {} } : real(query);
    window.__gpsRequests = [];
    Object.defineProperty(navigator, 'geolocation', { configurable: true, value: {
      getCurrentPosition(resolve, reject) { window.__gpsRequests.push({ resolve, reject }); },
      watchPosition() { throw new Error('Capture ponctuelle seulement'); }, clearWatch() {},
    } });
  }, standalone);
  await context.route('**/api/**', async route => {
    if (await mockBusinessCRS(route, srid)) return;
    const request = route.request();
    const path = new URL(request.url()).pathname;
    calls.push({ method: request.method(), path });
    if (path === '/api/coordinates/transform'
        || path === `/api/troncons/${T}/position-reperage`) {
      if (calculationGate) await calculationGate.promise;
      if (calculationFails) return route.fulfill({ status: 503, json: { detail: 'Calcul indisponible' } });
      return route.fulfill({ json: path.includes('transform')
        ? { x: 1700012.34, y: 8200056.78 }
        : { statut: 'OK', borne_id: B, distance_borne_m: 12.34,
          position_relative: 'AVANT_BORNE', distance_axe_m: 7.4,
          point_projete: { type: 'Point', coordinates: [1700012, 8200056] } } });
    }
    const empty = { type: 'FeatureCollection', features: [] };
    const json = path === '/api/config' ? { legacy_single_database_enabled: true, show_uuid: false, build_id: 'geo-4-test' }
      : path === '/api/resource-events/head' ? { cursor: '0' }
      : path === '/api/resource-events' ? { events: [], hasMore: false, nextCursor: '0' }
      : ['/api/troncons', '/api/desordres', '/api/territoire-administratif'].includes(path) ? empty
      : path === '/api/systemes-endiguement' ? { systemes: [] }
      : path === '/api/troncons/options' ? { troncons: [{ id: T, libelle: 'Tronçon GPS',
        digue_id: S, digue_libelle: 'Digue', valid: true }] }
      : path === `/api/troncons/${T}/reperage-options` ? { troncon_id: T,
        systeme_reperage_id: S, bornes: [{ id: B, libelle: 'Borne GPS', valid: true },
          { id: B2, libelle: 'Borne manuelle', valid: true }] }
      : path.startsWith('/api/referentiels/') ? { types: [], categories: [], references: [] }
      : {};
    return route.fulfill({ json });
  });
  return { calls, fail(value) { calculationFails = value; },
    hold() {
      let release;
      const promise = new Promise(resolve => { release = resolve; });
      calculationGate = { promise, release };
    },
    release() { calculationGate?.release(); calculationGate = null; },
  };
}

async function create(page) {
  await page.goto('/carte');
  // Ces scénarios protègent les conversions et le repérage conservés sous
  // l'interface, même si leurs commandes ne sont plus proposées dans la PWA.
  await page.addStyleTag({ content: `
    body.pwa-standalone #desordre-mode-selector { display: flex !important; }
    body.pwa-standalone #desordre-create-xy:not([hidden]),
    body.pwa-standalone #desordre-create-lonlat:not([hidden]) { display: grid !important; }
    body.pwa-standalone #desordre-create-line-coordinates:not([hidden]),
    body.pwa-standalone #desordre-create-bornage:not([hidden]) { display: block !important; }
  ` });
  await expect(page.locator('#map')).toBeVisible();
  // Le shell peut être visible avant que map.js installe ses handlers.
  await page.waitForFunction(() => typeof topNavigation !== 'undefined'
    && topNavigation.getState().create === false);
  await page.locator('#toggle-create-menu').click();
  await expect(page.locator('#create-menu-list')).toBeVisible();
  await page.locator('[data-create-type="desordre"]').click();
  await expect(page.locator('#desordre-editor')).toBeVisible();
}

async function position(page, count = 1) {
  await expect.poll(() => page.evaluate(() => window.__gpsRequests.length)).toBe(count);
  await page.evaluate(index => window.__gpsRequests[index].resolve({
    coords: { longitude: 2.35, latitude: 46.5, accuracy: 8 }, timestamp: Date.now(),
  }), count - 1);
  await expect(page.locator('#gps-candidate-longitude')).toHaveText('2.35');
}

function noWrites(calls) {
  expect(calls.filter(call => ['POST', 'PUT', 'PATCH', 'DELETE'].includes(call.method))).toEqual([]);
}

test('Point X/Y : candidat 4326, application atomique, erreur et web classique', async ({ context, page }) => {
  const mock = await setup(context, true, 2154);
  await create(page);
  await page.locator('#desordre-xy-choice').click();
  const x = page.locator('#desordre-create-x'), y = page.locator('#desordre-create-y');
  await expect(x.locator('..')).toContainText('EPSG:2154');
  await x.fill('100'); await y.fill('200');
  const payload = await page.evaluate(() => buildDesordreCreationPayload());
  expect(payload.x).toBe(100); expect(payload.y).toBe(200);
  expect(payload).not.toHaveProperty('coord_x_3950');
  await page.locator('#gps-point-xy').click();
  await position(page);
  await expect(x).toHaveValue('100'); await expect(y).toHaveValue('200');
  mock.fail(true);
  await page.locator('#gps-candidate-accept').click();
  await expect(page.locator('#gps-candidate-status')).toContainText('Calcul GPS impossible');
  await expect(x).toHaveValue('100'); await expect(y).toHaveValue('200');
  mock.fail(false);
  await page.locator('#gps-candidate-accept').click();
  await expect(x).toHaveValue('1700012.34'); await expect(y).toHaveValue('8200056.78');
  await context.setOffline(true);
  await page.locator('#gps-point-xy').click(); await position(page, 2);
  await page.locator('#gps-candidate-accept').click();
  await expect(page.locator('#gps-candidate-status')).toContainText('hors connexion');
  await expect(x).toHaveValue('1700012.34'); await expect(y).toHaveValue('8200056.78');
  noWrites(mock.calls);
});

test('Une réponse tardive ou une saisie pendant le calcul ne remplace pas le formulaire', async ({ context, page }) => {
  const mock = await setup(context);
  await create(page);
  await page.locator('#desordre-xy-choice').click();
  const x = page.locator('#desordre-create-x'), y = page.locator('#desordre-create-y');
  await x.fill('100'); await y.fill('200');
  const payload = await page.evaluate(() => buildDesordreCreationPayload());
  expect(payload.x).toBe(100); expect(payload.y).toBe(200);
  expect(payload).not.toHaveProperty('coord_x_3950');
  mock.hold();
  await page.locator('#gps-point-xy').click(); await position(page);
  await page.locator('#gps-candidate-accept').click();
  await expect(page.locator('#gps-candidate-status')).toContainText('Calcul en cours');
  await page.locator('#gps-candidate-cancel').click();
  mock.release();
  await expect(page.locator('#gps-position-candidate')).toBeHidden();
  await expect(x).toHaveValue('100'); await expect(y).toHaveValue('200');
  mock.hold();
  await page.locator('#gps-point-xy').click(); await position(page, 2);
  await page.locator('#gps-candidate-accept').click();
  await x.evaluate(input => { input.value = '123'; input.dispatchEvent(new Event('input', { bubbles: true })); });
  mock.release();
  await expect(page.locator('#gps-candidate-status')).toContainText('La saisie a changé');
  await expect(x).toHaveValue('123'); await expect(y).toHaveValue('200');
  noWrites(mock.calls);
});

test('LineString X/Y : début et fin indépendants, saisie manuelle mixte', async ({ context, page }) => {
  const mock = await setup(context);
  await create(page);
  await page.locator('#desordre-create-geometry-type').selectOption('LineString');
  await page.locator('#desordre-xy-choice').click();
  const start = page.locator('#desordre-create-line-start-1');
  const end = page.locator('#desordre-create-line-end-1');
  await end.fill('42');
  await page.locator('#gps-line-start').click(); await position(page);
  await page.locator('#gps-candidate-accept').click();
  await expect(start).toHaveValue('1700012.34'); await expect(end).toHaveValue('42');
  await start.fill('24');
  await page.locator('#gps-line-end').click(); await position(page, 2);
  await page.locator('#gps-candidate-accept').click();
  await expect(start).toHaveValue('24'); await expect(end).toHaveValue('1700012.34');
  noWrites(mock.calls);
});

test('Point et LineString Bornage : projection, écart et extrémités mixtes', async ({ context, page }) => {
  const mock = await setup(context);
  await create(page);
  await page.locator('#desordre-create-troncons').selectOption(T);
  await expect(page.locator('#desordre-bornage-choice input')).toBeEnabled();
  await page.locator('#desordre-bornage-choice').click();
  await page.locator('#desordre-create-borne-start').selectOption(B2);
  await page.locator('#desordre-create-distance-start').fill('99');
  await page.locator('#desordre-create-sense-start').selectOption('APRES_BORNE');
  await page.locator('#gps-bornage-start').click(); await position(page);
  await expect(page.locator('#desordre-create-distance-start')).toHaveValue('99');
  mock.fail(true);
  await page.locator('#gps-candidate-accept').click();
  await expect(page.locator('#gps-candidate-status')).toContainText('Calcul GPS impossible');
  await expect(page.locator('#desordre-create-borne-start')).toHaveValue(B2);
  await expect(page.locator('#desordre-create-distance-start')).toHaveValue('99');
  await expect(page.locator('#desordre-create-sense-start')).toHaveValue('APRES_BORNE');
  mock.fail(false);
  await page.locator('#gps-candidate-accept').click();
  await expect(page.locator('#desordre-create-borne-start')).toHaveValue(B);
  await expect(page.locator('#desordre-create-distance-start')).toHaveValue('12.34');
  await expect(page.locator('#desordre-create-sense-start')).toHaveValue('AVANT_BORNE');
  await expect(page.locator('#gps-bornage-start-distance')).toContainText('7.4 m');
  await page.locator('#desordre-create-geometry-type').selectOption('LineString');
  await page.locator('#desordre-create-troncons').selectOption(T);
  await expect(page.locator('#desordre-bornage-choice input')).toBeEnabled();
  await page.locator('#desordre-bornage-choice').click();
  await page.locator('#desordre-create-distance-end').fill('25');
  await page.locator('#gps-bornage-start').click(); await position(page, 2);
  await page.locator('#gps-candidate-accept').click();
  await expect(page.locator('#desordre-create-distance-end')).toHaveValue('25');
  await page.locator('#desordre-create-distance-start').fill('15');
  await page.locator('#gps-bornage-end').click(); await position(page, 3);
  await page.locator('#gps-candidate-accept').click();
  await expect(page.locator('#desordre-create-distance-start')).toHaveValue('15');
  await expect(page.locator('#desordre-create-distance-end')).toHaveValue('12.34');
  noWrites(mock.calls);
});

test('Polygon masque son unique mode tout en conservant Carto', async ({ context, page }) => {
  await setup(context, false);
  await create(page);
  await page.locator('#desordre-create-geometry-type').selectOption('Polygon');
  await expect(page.locator('#desordre-mode-selector')).toBeHidden();
  await expect(page.locator('input[name="desordre-mode"][value="map"]')).toBeChecked();
  await expect(page.locator('#start-desordre-draw')).toBeVisible();
  await page.locator('#desordre-create-geometry-type').selectOption('Point');
  await expect(page.locator('#desordre-mode-selector')).toBeVisible();
  await page.locator('#desordre-xy-choice').click();
  await expect(page.locator('#gps-point-xy')).toBeHidden();
});
