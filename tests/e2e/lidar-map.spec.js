const { test, expect } = require('@playwright/test');

const user = '11111111-1111-4111-8111-111111111111';
const instance = '22222222-2222-4222-8222-222222222222';
const base = '33333333-3333-4333-8333-333333333333';
const epoch = '44444444-4444-4444-8444-444444444444';
const se = '55555555-5555-4555-8555-555555555555';
const ah = '66666666-6666-4666-8666-666666666666';
const digue = '77777777-7777-4777-8777-777777777777';
const ix = 2734, iy = 25781;
const block = { id: 'a'.repeat(64), ix, iy,
  bbox: [ix * 256, iy * 256, (ix + 1) * 256, (iy + 1) * 256] };
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9n4Z8AAAAASUVORK5CYII=', 'base64');
const ahBlock = { id: 'b'.repeat(64), ix: ix + 40, iy,
  bbox: [(ix + 40) * 256, iy * 256, (ix + 41) * 256, (iy + 1) * 256] };
let syntheticLidarGeoTiff, fixtureTagOffset, distribution;

test.beforeAll(async () => {
  ({ syntheticLidarGeoTiff, fixtureTagOffset } = await import('../../services/plan-ign-raster/lidar-fixture.mjs'));
  const {createLidarDistribution} = await import('../../services/plan-ign-raster/lidar-distribution.mjs');
  const {loadCacheProducts} = await import('../../services/plan-ign-raster/cache-products.mjs');
  const {pathToFileURL} = require('node:url');
  const catalogue = await loadCacheProducts(pathToFileURL(require('node:path').resolve('webapp/backend/digues_webapp/cartographic_caches.json')));
  distribution = createLidarDistribution({getBlock(){throw new Error('No real acquisition in browser tests');}},
    catalogue.products.get('ign-lidar-hd-mnt'));
});

async function mockCarte(page, counters, identity = { user, base, epoch }) {
  const {user: uid, base: bid, epoch: eid}=identity;
  await page.route(/^https?:\/\/(?!127\.0\.0\.1(?::|\/)|localhost(?::|\/))/, route => route.abort());
  await page.route('**/api/config', route => route.fulfill({ json: {
    show_uuid: false, basemap_tilejson_url: '/basemap/plan-ign.json', build_id: 'l3b-test',
    legacy_single_database_enabled: false, lidar_ui_enabled: true,
  } }));
  await page.route('**/api/auth/me', route => route.fulfill({ json: { id: uid } }));
  await page.route('**/api/auth/databases', route => route.fulfill({ json: {
    user_id: uid, instance_id: instance, expires_at: new Date(Date.now()+3600000).toISOString(),
    databases: [{ business_database_id: bid, data_epoch: eid, status: 'active',
      slug: 'test', structure_name: 'Test', display_name: 'Test',
      permissions: ['business.read', 'business.update', 'business.create', 'offline.prepare'] }],
  } }));
  const entries = [
    ['ign-epure', 'PLAN.IGN Épuré', 0, 18],
    ['ign-plan', 'PLANIGNV2 raster', 0, 19],
    ['ign-ortho', 'BD ORTHO IGN', 6, 19],
  ];
  const catalogue = {contract_version:1, default_basemap_id:'ign-epure',
    basemaps:entries.map(([id,name,min,max],index)=>({id,name,order:index*10,
      delivery:{adapter:'tilejson',url:`/basemap/${id}.json`},
      zoom:{native_min:min,native_max:max,display_max:max+1},attribution:`© ${name}`}))};
  await page.route('**/api/basemaps',route=>route.fulfill({json:catalogue}));
  await page.route('**/basemap/*.json', route => {
    const id = new URL(route.request().url()).pathname.split('/').at(-1).replace('.json', '');
    counters.tilejson.push(id);
    route.fulfill({ json: { tiles: [`/basemap/${id}/tiles/{z}/{x}/{y}.png`], minzoom: id === 'ign-ortho' ? 6 : 0,
      maxzoom: id === 'ign-epure' ? 18 : 19, attribution: `© ${id}` } });
  });
  await page.route('**/basemap/*/tiles/**', route => route.fulfill({ body: png, contentType: 'image/png' }));
  await page.route('**/api/bases/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/basemaps')) route.fulfill({json:catalogue});
    else if (path.endsWith('/systemes-endiguement')) route.fulfill({ json: { systemes: [{ id: se,
      libelle: 'SE témoin', digues: [{ id: digue, libelle: 'Digue témoin', troncons: [] }] }] } });
    else if (path.endsWith('/patrimoine/geometries/v1')) route.fulfill({ json: {
      version: 1, crs: { srid: 2154 },
      troncons: [{ id: '88888888-8888-4888-8888-888888888888', digue_id: digue, bbox: block.bbox }],
      zones_stockage: counters.hideZone ? [] : [{ id: '99999999-9999-4999-8999-999999999999', amenagement_hydraulique_id: ah, bbox: ahBlock.bbox }],
    } });
    else if (path.endsWith('/resource-events/head')) route.fulfill({ json: { cursor: '0' } });
    else if (path.includes('/territoire')) route.fulfill({ json: { type: 'FeatureCollection', features: [] } });
    else route.fulfill({ json: { type: 'FeatureCollection', features: [], systemes: [], records: [] } });
  });
  await page.route('**/api/bases/**/lidar/policy*', route => route.fulfill({json:{contract_version:1, buffer_m:25, policy_version:1}}));
  await page.route('**/api/bases/**/lidar/blocks/v1/plan*', route => {
    counters.plan++;
    const request = route.request().postDataJSON(), selection = request.ensembles;
    (counters.selections ||= []).push(selection);
    const noBlocks = ['EMPTY', 'ANOMALY'].includes(counters.mode);
    const masks = selection.map(row => {
      const [x0,y0,x1,y1] = (row.type === 'AH' ? ahBlock : block).bbox;
      return {...row,status:noBlocks ? counters.mode : 'READY',geometry:noBlocks ? null :
        {type:'MultiPolygon',coordinates:counters.maskCoordinates || [[[[x0,y0],[x1,y0],[x1,y1],[x0,y1],[x0,y0]]]]}};
    });
    const plan = distribution.plan({masks:{version:'LIDAR_MASK_V1',crs:'EPSG:2154',masks},view:request.vue});
    plan.policy_version = 1; plan.buffer_m = 25;
    plan.ensembles = masks.map(({type,id,status}) => ({type,id,status,diagnostics:[]}));
    counters.lastPlan = plan;
    (counters.levels ||= []).push(plan.level);
    counters.blocks ||= new Map();
    for (const part of plan.blocks) counters.blocks.set(part.id, part);
    if (counters.mode === 'OVERSIZED') plan.blocks = Array(129).fill(plan.blocks[0]);
    route.fulfill({json:plan});
  });
  await page.route('**/api/bases/**/lidar/blocks/v1/read*', async route => {
    counters.read++;
    if (counters.readGate) await counters.readGate;
    if (counters.mode === 'ERROR') return route.fulfill({status:503,json:{detail:'Synthetic unavailable'}});
    const part = counters.blocks.get(route.request().postDataJSON().bloc.id);
    const ahPart = part.bbox[0] <= ahBlock.bbox[2] && part.bbox[2] >= ahBlock.bbox[0];
    const bytes = syntheticLidarGeoTiff(part, {value:counters.mode === 'NO_DATA' ? -9999 : ahPart ? 80 : 50});
    if (counters.extremeOutside) {
      const start = bytes.readUInt32LE(fixtureTagOffset(bytes,273)+8);
      const resolution=.5*2**part.level, [x,y]=block.bbox;
      for(let row=0;row<part.height;row++) for(let col=0;col<part.width;col++) {
        const east=part.bbox[0]+(col+.5)*resolution, north=part.bbox[3]-(row+.5)*resolution;
        if (east>x+40 && east<x+80 && north>y+80 && north<y+160) {
          bytes.writeFloatLE(9000,start+(row*part.width+col)*4);
        }
      }
    }
    if (counters.mode === 'NO_COVERAGE') route.fulfill({ status: 204,
      headers: { 'X-Lidar-Status': 'NO_COVERAGE' } });
    else route.fulfill({ body: bytes,
      contentType: 'image/geotiff', headers: { 'X-Lidar-Status':
        counters.mode === 'NO_DATA' ? 'NO_DATA' : 'DATA', 'X-Cache': 'MISS' } });
  });
}

test('Carte keeps LiDAR above changing basemaps and reuses numeric blocks offline', async ({ page, context }) => {
  const counters = { tilejson: [], plan: 0, read: 0 };
  await mockCarte(page, counters);
  await page.goto('/carte?base=test');
  await expect(page.locator('#identity-gate')).toBeHidden();
  await expect(page.locator('#lidar-legend-item')).toBeVisible();
  await expect(page.locator('#basemap-selector')).toHaveCount(1);
  await expect(page.locator('#basemap-selector .basemap-selector-options')).not.toBeVisible();
  await page.locator('.settings-menu-toggle').click();
  await expect(page.locator('#basemap-selector .basemap-selector-options')).toBeVisible();
  await expect(page.locator('#basemap-selector .basemap-selector-choice')).toHaveCount(3);
  await page.locator('#basemap-selector .basemap-selector-choice').nth(2).click();
  await expect(page.locator('#basemap-selector .basemap-selector-choice').nth(2)).toHaveAttribute('aria-pressed', 'true');
  await page.locator('#basemap-selector .basemap-selector-choice').first().click();
  await page.locator('.settings-menu-toggle').click();
  await page.locator('#lidar-toggle').check();
  await page.evaluate(({ ix, iy }) => {
    const [lon, lat] = proj4('EPSG:2154', 'EPSG:4326', [(ix + 0.5) * 256, (iy + 0.5) * 256]);
    map.setView([lat, lon], 18);
  }, { ix, iy });
  await expect(page.locator('#lidar-status')).toContainText('disponibles localement', { timeout: 30000 });
  expect(counters.read).toBe(1);
  console.log('L3B_BROWSER_METRICS', JSON.stringify(await page.evaluate(() => ({
    ...LidarMapController.state.metrics,
    jsHeapBytes: performance.memory?.usedJSHeapSize ?? null,
  }))));
  const layer = await page.evaluate(() => ({
    z: map.getPane('lidarRelief').style.zIndex,
    pointer: map.getPane('lidarRelief').style.pointerEvents,
    alpha: LidarMapController.state.overlay.canvas.getContext('2d').getImageData(200, 200, 1, 1).data[3],
    panes: Object.fromEntries(Object.entries(LeafletLayerOrder.PANES).map(([key, definition]) => [
      key,
      {
        name: definition.name,
        zIndex: Number(map.getPane(definition.name).style.zIndex),
        pointerEvents: map.getPane(definition.name).style.pointerEvents,
      },
    ])),
  }));
  expect(layer.z).toBe('325');
  expect(layer.pointer).toBe('none');
  expect(layer.alpha).toBeGreaterThanOrEqual(0);
  expect(layer.panes.BASEMAP.zIndex).toBeLessThan(layer.panes.LIDAR.zIndex);
  expect(layer.panes.LIDAR.zIndex).toBeLessThan(layer.panes.WEATHER.zIndex);
  expect(layer.panes.LIDAR.zIndex).toBeLessThan(layer.panes.HYDROMETRY.zIndex);
  expect(layer.panes.WEATHER.zIndex).toBeLessThan(layer.panes.BUSINESS_VECTORS.zIndex);
  expect(layer.panes.HYDROMETRY.zIndex).toBeLessThan(layer.panes.BUSINESS_VECTORS.zIndex);
  expect(layer.panes.BUSINESS_VECTORS.zIndex).toBeLessThan(layer.panes.BUSINESS_MARKERS.zIndex);
  expect(layer.panes.LIDAR.pointerEvents).toBe('none');
  await page.evaluate(() => {
    const center = map.getCenter();
    window.__lidarLineClicks = 0;
    L.polyline([[center.lat, center.lng - 0.0005], [center.lat, center.lng + 0.0005]],
      { color: '#e00000', weight: 12 }).addTo(map)
      .on('click', () => { window.__lidarLineClicks++; });
  });
  const rect = await page.locator('#map').boundingBox();
  await page.mouse.click(rect.x + rect.width / 2, rect.y + rect.height / 2);
  await expect.poll(() => page.evaluate(() => window.__lidarLineClicks)).toBe(1);
  await page.locator('.settings-menu-toggle').click();
  await page.locator('#basemap-selector .basemap-selector-choice').nth(2).click();
  await expect(page.locator('#lidar-toggle')).toBeChecked();
  expect(counters.read).toBe(1);
  await page.locator('.settings-menu-toggle').click();
  await page.locator('#lidar-toggle').uncheck();
  await page.locator('#lidar-toggle').check();
  await expect.poll(() => page.evaluate(() => map.getPane('lidarRelief').style.zIndex)).toBe('325');
  expect(counters.read).toBe(1);
  await page.evaluate(() => navigator.serviceWorker.ready);
  await expect.poll(() => page.evaluate(() => Boolean(navigator.serviceWorker.controller))).toBe(true);
  const plannedBeforeOffline = counters.plan;
  await context.setOffline(true);
  await page.evaluate(() => { LidarMapController.state.memory.clear(); LidarMapController.schedule(); });
  await expect(page.locator('#lidar-status')).toContainText('disponibles localement', { timeout: 30000 });
  expect(counters.read).toBe(1);
  await expect(page.locator('input[name="lidar-scale-mode"], #lidar-fixed-min, #lidar-fixed-max')).toHaveCount(0);
  await expect(page.locator('#lidar-range')).toContainText('50.00 – 50.00 m');
  expect(counters.read).toBe(1);
  expect(counters.plan).toBe(plannedBeforeOffline);
  await page.reload();
  await expect(page.locator('#identity-gate')).toBeHidden();
  await expect(page.locator('main')).toHaveJSProperty('inert', false);
  await expect.poll(() => page.evaluate(() => ({
    lidar: map.getPane('lidarRelief').style.zIndex,
    weather: map.getPane('weather-precipitations').style.zIndex,
    hydrometry: map.getPane('environment-stations').style.zIndex,
    business: map.getPane('overlayPane').style.zIndex,
  }))).toEqual({ lidar: '325', weather: '340', hydrometry: '345', business: '400' });
  await expect(page.locator('#lidar-legend-item')).toBeVisible();
  await page.locator('#lidar-toggle').check();
  await page.evaluate(({ ix, iy }) => {
    const [lon, lat] = proj4('EPSG:2154', 'EPSG:4326', [(ix + 0.5) * 256, (iy + 0.5) * 256]);
    map.setView([lat, lon], 18);
  }, { ix, iy });
  await expect(page.locator('#lidar-status')).toContainText('disponibles localement', { timeout: 30000 });
  expect(counters.plan).toBe(plannedBeforeOffline);
  expect(counters.read).toBe(1);
  await page.evaluate(async () => {
    const connection = await new Promise((resolve, reject) => {
      const request = indexedDB.open('digues-app-basemap-packages', 2);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    await new Promise((resolve, reject) => {
      const tx = connection.transaction('lidarBlocks', 'readwrite');
      const store = tx.objectStore('lidarBlocks');
      const request = store.getAll();
      request.onsuccess = () => {
        const row = request.result[0];
        row.bytes = new Uint8Array(row.size).buffer;
        store.put(row);
      };
      tx.oncomplete = resolve;
      tx.onerror = () => reject(tx.error);
    });
    connection.close();
    LidarMapController.state.memory.clear();
    LidarMapController.schedule();
  });
  await expect(page.locator('#lidar-status')).toContainText('cache local incomplet', { timeout: 30000 });
  expect(counters.plan).toBe(plannedBeforeOffline);
  expect(counters.read).toBe(1);
  await page.evaluate(() => map.setView([48, 5], 18));
  await expect(page.locator('#lidar-status')).toContainText('non disponibles hors connexion', { timeout: 30000 });
  expect(counters.plan).toBe(plannedBeforeOffline);
  expect(counters.read).toBe(1);
  await context.setOffline(false);
  await page.reload();
  await expect(page.locator('#identity-gate')).toBeHidden();
  await page.locator('.settings-menu-toggle').click();
  await expect(page.locator('#basemap-selector .basemap-selector-choice').nth(2)).toHaveAttribute('aria-pressed', 'true');
});

// Niveau B : Leaflet et contrôleur réels, géométries canoniques et TIFF locaux.
// Vérifie l'activation sans identifiants saisis, les changements de vue et les
// refus sans téléchargement hors patrimoine ou au-delà du budget pilote.
async function centerOnBlock(page, target, zoom = 18) {
  await page.evaluate(({ ix, iy, zoom }) => {
    const [lon, lat] = proj4('EPSG:2154', 'EPSG:4326', [(ix + 0.5) * 256, (iy + 0.5) * 256]);
    map.setView([lat, lon], zoom);
  }, { ...target, zoom });
}
test('checkbox automatically follows SE/AH at both sides of zoom 17 and stops outside patrimoine', async ({ page }) => {
  const counters = { tilejson: [], plan: 0, read: 0 };
  await mockCarte(page, counters);
  await page.goto('/carte?base=test');
  await expect(page.locator('#lidar-toggle')).toBeVisible();
  await expect(page.locator('#lidar-selection, #lidar-apply-selection')).toHaveCount(0);
  await centerOnBlock(page, block);
  expect(counters.plan).toBe(0);
  await page.locator('#lidar-toggle').check();
  await expect(page.locator('#lidar-status')).toContainText('disponibles localement');
  expect(counters.selections.at(-1)).toEqual([{ type: 'SE', id: se }]);
  expect(counters.read).toBe(1);
  await expect(page.locator('#lidar-range')).toContainText('50.00');
  await page.locator('#lidar-opacity').fill('30');
  await expect.poll(() => page.evaluate(() => LidarMapController.state.overlay.canvas.style.opacity)).toBe('0.3');
  const plansBeforeZoom = counters.plan;
  await centerOnBlock(page, block, 16);
  await expect.poll(() => counters.plan).toBeGreaterThan(plansBeforeZoom);
  await expect(page.locator('#lidar-status')).toContainText('disponibles localement');
  expect(counters.levels.at(-1)).toBeGreaterThan(0);
  const readsBeforeAH = counters.read;
  await centerOnBlock(page, ahBlock);
  await expect(page.locator('#lidar-status')).toContainText('disponibles localement');
  expect(counters.selections.at(-1)).toEqual([{ type: 'AH', id: ah }]);
  expect(counters.read).toBe(readsBeforeAH + 1);
  await expect(page.locator('#lidar-range')).toContainText('80.00');
  const plansBeforeExit = counters.plan;
  await page.evaluate(() => map.setView([48, 5], 18));
  await expect(page.locator('#lidar-status')).toContainText('Aucun SE/AH');
  expect(counters.plan).toBe(plansBeforeExit);
  expect(counters.read).toBe(readsBeforeAH + 1);
  await centerOnBlock(page, block);
  await expect(page.locator('#lidar-status')).toContainText('disponibles localement');
  expect(counters.selections.at(-1)).toEqual([{ type: 'SE', id: se }]);
  expect(counters.read).toBe(readsBeforeAH + 1); // Numeric cache reused when returning to the first area.
  await page.locator('#lidar-toggle').uncheck();
  expect(await page.evaluate(() => map.getPane('lidarRelief').querySelector('canvas'))).toBeNull();
});

test('patrimoine invalidation refreshes the automatic extent without a parallel selection', async ({ page }) => {
  const counters = { tilejson: [], plan: 0, read: 0 };
  await mockCarte(page, counters);
  await page.goto('/carte?base=test');
  await expect(page.locator('#lidar-toggle')).toBeVisible();
  await centerOnBlock(page, ahBlock);
  await page.locator('#lidar-toggle').check();
  await expect(page.locator('#lidar-status')).toContainText('disponibles localement');
  counters.hideZone = true; // The canonical source fixture changes, not the selection.
  const result = await page.evaluate(() => ResourceMutationController.handleRemoteMutation({
    schemaVersion: 1, eventId: crypto.randomUUID(), originClientId: crypto.randomUUID(),
    originInstanceId: 'remote-map-test', occurredAt: Date.now(), resource: 'zones-stockage',
    action: 'delete', ids: ['99999999-9999-4999-8999-999999999999'], aspects: ['geometry', 'relations'],
  }));
  expect(result.accepted).toBe(true);
  await expect(page.locator('#lidar-status')).toContainText('Aucun SE/AH');
  expect(counters.read).toBe(1);
  expect(counters.plan).toBe(1);
});

test('automatic LiDAR refuses oversized plans before reading any block', async ({ page }) => {
  const counters = { tilejson: [], plan: 0, read: 0, mode: 'OVERSIZED' };
  await mockCarte(page, counters);
  await page.goto('/carte?base=test');
  await expect(page.locator('#lidar-toggle')).toBeVisible();
  await centerOnBlock(page, block);
  await page.locator('#lidar-toggle').check();
  await expect(page.locator('#lidar-status.error')).toContainText('trop volumineux');
  expect(counters.read).toBe(0);
});

test('standalone PWA settings host the same basemap choices without a floating panel', async ({ page }) => {
  // Only display-mode is emulated; the rendered settings and Leaflet are real.
  await page.goto('/dev/lidar');
  await page.evaluate(async () => {
    await new Promise((resolve, reject) => {
      const request = indexedDB.open('digues-app-basemap-packages', 1);
      request.onupgradeneeded = () => {
        request.result.createObjectStore('packages', { keyPath: 'id' });
        request.result.createObjectStore('pointers', { keyPath: 'key' });
        request.result.createObjectStore('catalogues', { keyPath: 'contextKey' });
      };
      request.onsuccess = () => { request.result.close(); resolve(); };
      request.onerror = () => reject(request.error);
    });
  });
  await page.addInitScript(() => {
    const original = window.matchMedia.bind(window);
    window.matchMedia = query => query === '(display-mode: standalone)'
      ? { matches: true, media: query, addEventListener() {}, removeEventListener() {},
          addListener() {}, removeListener() {} } : original(query);
  });
  const counters = { tilejson: [], plan: 0, read: 0 };
  await mockCarte(page, counters);
  await page.goto('/carte?base=test');
  await expect(page.locator('#identity-gate')).toBeHidden();
  // Dismiss the real initial data-count toast before using a control beneath
  // its surface; do not force a click through the notification.
  await expect(page.locator('.backend-status-panel')).toContainText('0 tronçons');
  await page.getByRole('button', {name:'Fermer le message',exact:true}).click();
  await expect.poll(() => page.evaluate(async () => {
    const request = indexedDB.open('digues-app-basemap-packages');
    return new Promise((resolve, reject) => {
      request.onsuccess = () => {
        const stores = [...request.result.objectStoreNames].sort();
        request.result.close(); resolve(stores);
      };
      request.onerror = () => reject(request.error);
    });
  })).toEqual(['catalogues', 'lidarBlocks', 'lidarPlans', 'packages', 'pointers']);
  await expect(page.locator('#toggle-pwa-settings')).toBeVisible();
  await expect(page.locator('.settings-menu')).toBeHidden();
  await expect(page.locator('#basemap-selector')).toHaveCount(1);
  await expect(page.locator('#basemap-selector .basemap-selector-options')).not.toBeVisible();
  await page.locator('#toggle-pwa-settings').click();
  await expect(page.locator('#pwa-settings-panel')).toBeVisible();
  await expect(page.locator('#basemap-selector .basemap-selector-choice')).toHaveCount(3);
  await page.locator('#basemap-selector .basemap-selector-choice').nth(1).click();
  await expect(page.locator('#basemap-selector .basemap-selector-choice').nth(1)).toHaveAttribute('aria-pressed', 'true');
  await page.locator('#close-pwa-settings').click();
  await expect(page.locator('#basemap-selector .basemap-selector-options')).not.toBeVisible();
  await centerOnBlock(page, block);
  await page.locator('#lidar-toggle').check();
  await expect(page.locator('#lidar-status')).toContainText('disponibles localement');
  const plansBeforeBasemapChange = counters.plan, readsBeforeBasemapChange = counters.read;
  await page.locator('#toggle-pwa-settings').click();
  await page.locator('#basemap-selector .basemap-selector-choice').nth(2).click();
  await expect(page.locator('#lidar-toggle')).toBeChecked();
  await page.locator('#basemap-selector .basemap-selector-choice').nth(1).click();
  await page.locator('#close-pwa-settings').click();
  // The PWA settings can resize the map and therefore replan its viewport.
  // Changing the background must preserve the selected identities and reuse
  // numeric data instead of acquiring it again.
  expect(counters.selections.slice(plansBeforeBasemapChange).every(selection=>
    selection.length===1 && selection[0].type==='SE' && selection[0].id===se)).toBe(true);
  expect(counters.read).toBe(readsBeforeBasemapChange);
  await page.reload();
  await expect(page.locator('#identity-gate')).toBeHidden();
  await page.locator('#toggle-pwa-settings').click();
  await expect(page.locator('#basemap-selector .basemap-selector-choice').nth(1)).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#lidar-legend-item')).toBeVisible();
});

test('EMPTY, ANOMALY, NO_COVERAGE and NO_DATA remain distinct without fallback downloads', async ({ page }) => {
  const counters = { tilejson: [], plan: 0, read: 0, mode: 'EMPTY' };
  await mockCarte(page, counters);
  await page.goto('/carte?base=test');
  await expect(page.locator('#identity-gate')).toBeHidden();
  await page.locator('#lidar-toggle').check();
  await page.evaluate(({ ix, iy }) => {
    const [lon, lat] = proj4('EPSG:2154', 'EPSG:4326', [(ix + 0.5) * 256, (iy + 0.5) * 256]);
    map.setView([lat, lon], 18);
  }, { ix, iy });
  await expect(page.locator('#lidar-status')).toContainText('Aucun relief contributif', { timeout: 30000 });
  expect(counters.read).toBe(0);
  counters.mode = 'ANOMALY';
  await page.evaluate(() => LidarMapController.schedule());
  await expect(page.locator('#lidar-status')).toContainText('en anomalie', { timeout: 30000 });
  expect(counters.read).toBe(0);
  counters.mode = 'NO_COVERAGE';
  await page.evaluate(() => LidarMapController.schedule());
  await expect(page.locator('#lidar-status')).toContainText('Absence de couverture IGN', { timeout: 30000 });
  expect(counters.read).toBe(1);
  counters.mode = 'NO_DATA';
  await page.evaluate(async () => {
    await OfflineBasemapStore.createLidarStore().dropBlock(BusinessRuntime.context.key, LidarMapController.state.lastPlan.blocks[0]);
    LidarMapController.schedule();
  });
  await expect(page.locator('#lidar-status')).toContainText('sans altitude utile', { timeout: 30000 });
  await expect(page.locator('#lidar-range')).toHaveText('Aucune altitude valide');
  expect(counters.read).toBe(2);
});

test('multiresolution follows all map scales, caches stable windows and ignores a superseded read', async ({page}) => {
  // Niveau B: real controller/Leaflet/decoder/planner, synthetic MNT transport.
  // No SQL or IGN; each browser has its own IndexedDB. Expected cost <30 s.
  const counters = {tilejson:[],plan:0,read:0};
  await mockCarte(page,counters);
  await page.goto('/carte?base=test');
  await expect(page.locator('#lidar-toggle')).toBeVisible();
  await centerOnBlock(page,block);
  await page.locator('#lidar-toggle').check();
  await expect(page.locator('#lidar-status')).toContainText('disponibles localement');
  const observed = [];
  for (const zoom of [16,12,6,0,17,19]) {
    const before = counters.plan;
    await centerOnBlock(page,block,zoom);
    await expect.poll(() => counters.plan).toBeGreaterThan(before);
    await expect(page.locator('#lidar-status')).not.toContainText('Chargement');
    await expect(page.locator('#lidar-status')).not.toContainText('Zoomer');
    expect(counters.lastPlan.estimated_bytes).toBeLessThanOrEqual(24*1024*1024);
    observed.push({zoom,resolution:counters.lastPlan.resolution_m,blocks:counters.lastPlan.blocks.length,
      bytes:counters.lastPlan.estimated_bytes});
  }
  console.log('LIDAR_SYNTHETIC_SCALES',JSON.stringify(observed));
  expect(observed[3].resolution).toBeGreaterThan(observed[0].resolution);
  expect(observed[3].resolution).toBeLessThanOrEqual(32); // No continental pixel around the two small masks.
  expect(observed.at(-1).resolution).toBe(.5);
  const reads = counters.read, plans = counters.plan;
  await page.evaluate(() => map.panBy([2,0],{animate:false}));
  await expect.poll(() => counters.plan).toBeGreaterThan(plans);
  await expect(page.locator('#lidar-status')).toContainText('disponibles localement');
  expect(counters.read).toBe(reads);
  await expect(page.locator('#lidar-range')).toHaveText('50.00 – 50.00 m');
  await expect(page.locator('input[name="lidar-scale-mode"],#lidar-fixed-min,#lidar-fixed-max')).toHaveCount(0);
  let release;
  counters.readGate = new Promise(resolve => {release=resolve;});
  await centerOnBlock(page,ahBlock);
  await expect.poll(() => counters.read).toBeGreaterThan(reads);
  await page.evaluate(() => map.setView([48,5],18));
  await expect(page.locator('#lidar-status')).toContainText('Aucun SE/AH');
  release(); counters.readGate = null;
  // Wait for transport handlers to settle by exercising a fresh empty view.
  await page.evaluate(() => LidarMapController.schedule());
  await expect(page.locator('#lidar-status')).toContainText('Aucun SE/AH');
  await expect(page.locator('#lidar-range')).toHaveText('Aucune altitude');
  expect(await page.evaluate(() => LidarMapController.state.sampled)).toBeNull();
});

test('loading errors do not invent an altitude or retry into another sector',async ({page}) => {
  const counters = {tilejson:[],plan:0,read:0,mode:'ERROR'};
  await mockCarte(page,counters);
  await page.goto('/carte?base=test');
  await expect(page.locator('#lidar-toggle')).toBeVisible();
  await centerOnBlock(page,block,16);
  await page.locator('#lidar-toggle').check();
  await expect(page.locator('#lidar-status.error')).toContainText('Erreur de chargement');
  await expect(page.locator('#lidar-range')).toHaveText('Aucune altitude');
  expect(counters.read).toBe(counters.lastPlan.blocks.length);
});

test('mask holes and outside pixels are transparent and excluded from automatic altitudes',async ({page}) => {
  const [x,y] = block.bbox;
  const rectangle=(a,b,c,d)=>[[a,b],[c,b],[c,d],[a,d],[a,b]];
  const counters={tilejson:[],plan:0,read:0,extremeOutside:true,
    maskCoordinates:[[rectangle(x,y,x+128,y+256),rectangle(x+40,y+80,x+80,y+160)]]};
  await mockCarte(page,counters);
  await page.goto('/carte?base=test');
  await expect(page.locator('#lidar-toggle')).toBeVisible();
  await centerOnBlock(page,block);
  await page.locator('#lidar-toggle').check();
  await expect(page.locator('#lidar-status')).toContainText('disponibles localement');
  await expect(page.locator('#lidar-range')).toHaveText('50.00 – 50.00 m');
  const pixels=await page.evaluate(({x,y})=>{
    const canvas=LidarMapController.state.overlay.canvas, ctx=canvas.getContext('2d');
    return [[20,128],[60,128],[200,128]].map(([dx,dy])=>{
      const [lng,lat]=proj4('EPSG:2154','EPSG:4326',[x+dx,y+dy]);
      const p=map.latLngToContainerPoint([lat,lng]);
      return ctx.getImageData(p.x|0,p.y|0,1,1).data[3];
    });
  },{x,y});
  expect(pixels).toEqual([255,0,0]);
});

for (const viewport of [{width:390,height:844},{width:844,height:390}]) {
  test(`PWA automatic LiDAR legend and opacity fit ${viewport.width}x${viewport.height}`,async ({page},info) => {
    await page.setViewportSize(viewport);
    await page.addInitScript(() => {
      const original=matchMedia.bind(window);
      window.matchMedia=q=>q==='(display-mode: standalone)' ?
        {matches:true,media:q,addEventListener(){},removeEventListener(){},addListener(){},removeListener(){}} : original(q);
    });
    const counters = {tilejson:[],plan:0,read:0};
    await mockCarte(page,counters);
    await page.goto('/carte?base=test');
    await expect(page.locator('#lidar-toggle')).toBeVisible();
    await centerOnBlock(page,block,16);
    await page.locator('#lidar-toggle').check();
    await expect(page.locator('#lidar-status')).toContainText('disponibles localement');
    await expect(page.locator('#lidar-range')).toHaveText('50.00 – 50.00 m');
    await page.locator('#lidar-opacity').fill('30');
    await expect.poll(()=>page.evaluate(()=>LidarMapController.state.overlay.canvas.style.opacity)).toBe('0.3');
    const box=await page.locator('#lidar-legend-controls').boundingBox();
    expect(box.x).toBeGreaterThanOrEqual(0); expect(box.x+box.width).toBeLessThanOrEqual(viewport.width);
    // The existing mobile legend is scrollable; verify the palette and its
    // automatic labels can actually be reached, rather than only DOM text.
    await page.locator('#lidar-range').scrollIntoViewIfNeeded();
    await expect(page.locator('#lidar-range')).toBeInViewport();
    await expect(page.locator('.legend-lidar-ramp')).toBeInViewport();
    await page.screenshot({path:info.outputPath('lidar-mobile.png')});
    await page.locator('#lidar-toggle').uncheck();
    expect(await page.evaluate(()=>map.getPane('lidarRelief').querySelector('canvas'))).toBeNull();
  });
}

// B: two independent Chromium sessions keep business contexts separate while
// public geographic block identities match. Real SQL authorization is covered
// by test_business_postgres; this fixture qualifies the frontend boundary only.
test('two users on two bases load the shared geographic data concurrently with separate local contexts', async ({browser}) => {
 const contexts=await Promise.all([browser.newContext(),browser.newContext()]);
 try {
  const pages=await Promise.all(contexts.map(c=>c.newPage()));
  const identities=[{user,base,epoch},{user:'77777777-7777-4777-8777-777777777777',base:'88888888-8888-4888-8888-888888888888',epoch:'99999999-9999-4999-8999-999999999999'}];
  const counts=[{plan:0,read:0,tilejson:[],hideZone:true},{plan:0,read:0,tilejson:[],hideZone:true}],paths=[[],[]];
  await Promise.all(pages.map(async(page,i)=>{
   await mockCarte(page,counts[i],identities[i]);
   page.on('request',r=>{const path=new URL(r.url()).pathname;if(path.startsWith('/api/bases/'))paths[i].push(path);});
   await page.goto(`/carte?base=${identities[i].base}`);
   await expect(page.locator('#identity-gate')).toBeHidden();
   await page.locator('.settings-menu-toggle').click();await page.locator('#lidar-toggle').check();
   await page.evaluate(({ix,iy})=>{const [lon,lat]=proj4('EPSG:2154','EPSG:4326',[(ix+.5)*256,(iy+.5)*256]);map.setView([lat,lon],18);},{ix,iy});
   await expect(page.locator('#lidar-status')).toContainText('disponibles localement',{timeout:30000});
   const active=await page.evaluate(()=>({user:BusinessRuntime.context.user_id,base:BusinessRuntime.context.business_database_id,key:BusinessRuntime.context.key}));
   expect(active.user).toBe(identities[i].user);expect(active.base).toBe(identities[i].base);
   expect(paths[i].length).toBeGreaterThan(0);expect(paths[i].every(p=>p.startsWith(`/api/bases/${identities[i].base}/`))).toBe(true);
  }));
  expect(counts[0].lastPlan.blocks.map(b=>b.id)).toEqual(counts[1].lastPlan.blocks.map(b=>b.id));
  const keys=await Promise.all(pages.map(page=>page.evaluate(()=>BusinessRuntime.context.key)));expect(keys[0]).not.toBe(keys[1]);
 } finally {await Promise.all(contexts.map(c=>c.close()));}
});
