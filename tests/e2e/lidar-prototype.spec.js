const { test, expect } = require('@playwright/test');

const UUID = {
  user: '11111111-1111-4111-8111-111111111111',
  instance: '22222222-2222-4222-8222-222222222222',
  base: '33333333-3333-4333-8333-333333333333',
  base2: '66666666-6666-4666-8666-666666666666',
  epoch: '44444444-4444-4444-8444-444444444444',
  epoch2: '77777777-7777-4777-8777-777777777777',
  se: '55555555-5555-4555-8555-555555555555',
};
const ix = 2734, iy = 25781;
const west = ix * 256, south = iy * 256;
const block = { id: 'a'.repeat(64), ix, iy, bbox: [west, south, west + 256, south + 256],
  availability: 'UNCHECKED' };
const mask = { version: 'LIDAR_MASK_V1', masks: [{ status: 'READY',
  bbox: [west + 100, south + 100, west + 150, south + 150] }] };
const tinyPng = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9n4Z8AAAAASUVORK5CYII=', 'base64');

async function fixture() {
  const { syntheticLidarGeoTiff } = await import('../../services/plan-ign-raster/lidar-fixture.mjs');
  const bytes = syntheticLidarGeoTiff({ ix, iy }, { value: 50 });
  // L2a's uncompressed synthetic strip starts at byte 312. Vary elevation by
  // column and row so view-wide min/max and fixed colors differ observably.
  for (let row = 0; row < 512; row++) {
    for (let col = 0; col < 512; col++) {
      bytes.writeFloatLE(20 + col / 8 + row / 32, 312 + (row * 512 + col) * 4);
    }
  }
  return bytes;
}

test('Leaflet places full Float32 pixels, recolors in memory and rejects stale views', async ({ page }, testInfo) => {
  // Niveau B, isolated browser boundary: real Leaflet, proj4 and geotiff.js;
  // mocked auth/L2c transport and synthetic source only, no mutable SQL or IGN.
  const bytes = await fixture();
  let plans = 0, reads = 0, failPlan = false;
  await page.route('**/api/auth/me', route => route.fulfill({ json: { id: UUID.user } }));
  await page.route('**/api/auth/databases', route => route.fulfill({ json: {
    user_id: UUID.user, instance_id: UUID.instance, databases: [{ business_database_id: UUID.base,
      data_epoch: UUID.epoch, status: 'active', slug: 'test', structure_name: 'Structure de test',
      display_name: 'Base de test', permissions: ['business.read'] }, {
      business_database_id: UUID.base2, data_epoch: UUID.epoch2, status: 'active',
      slug: 'test2', structure_name: 'Autre structure', display_name: 'Autre base',
      permissions: ['business.read'],
    }],
  } }));
  await page.route('**/basemap/plan-ign.json', route => route.fulfill({ json: {
    tiles: ['/basemap/test/{z}/{x}/{y}.png'], maxzoom: 19, attribution: 'Fond de test',
  } }));
  await page.route('**/basemap/test/**', route => route.fulfill({ body: tinyPng, contentType: 'image/png' }));
  await page.route('**/lidar/policy*',route=>route.fulfill({json:{contract_version:1,buffer_m:25,policy_version:1}}));
  await page.route('**/lidar/masks/v1*', route => route.fulfill({ json: mask }));
  await page.route('**/lidar/blocks/v1/plan*', async route => {
    plans++;
    if (failPlan) {
      await route.fulfill({ status: 503, json: { detail: 'Test provider unavailable' } });
      return;
    }
    const request = route.request().postDataJSON();
    const visible = request.vue.bbox[0] < west + 128 && request.vue.bbox[2] > west + 128 &&
      request.vue.bbox[1] < south + 128 && request.vue.bbox[3] > south + 128;
    await route.fulfill({ json: { version: 'LIDAR_DISTRIBUTION_V2',policy_version:1,buffer_m:25,
      grid_id: 'epsg2154-050cm-512-v1', crs: 'EPSG:2154', blocks: visible ? [block] : [],
      mask: {type:'MultiPolygon',coordinates:[[[[west,south],[west+256,south],[west+256,south+256],[west,south+256],[west,south]]]]},
      ensembles: [{ type: 'SE', id: UUID.se, status: 'READY', diagnostics: [] }] } });
  });
  await page.route('**/lidar/blocks/v1/read*', async route => {
    reads++;
    if (reads === 2) await new Promise(resolve => setTimeout(resolve, 450));
    await route.fulfill({ body: bytes, contentType: 'image/geotiff',
      headers: { 'X-Lidar-Status': 'DATA', 'X-Cache': reads === 1 ? 'MISS' : 'HIT' } });
  });
  await page.goto('/dev/lidar');
  await expect(page.locator('#status')).toContainText('Saisir des UUID');
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Performance.enable');
  const beforeMetrics = await cdp.send('Performance.getMetrics');
  await page.locator('#ensembles').fill(`SE:${UUID.se}`);
  await page.getByRole('button', { name: 'Afficher cette sélection' }).click();
  await expect(page.locator('#m-planned')).toHaveText('1');
  await expect(page.locator('#status')).toContainText('bloc(s) affiché(s)', { timeout: 30000 });
  expect(reads).toBe(1);
  const initial = await page.evaluate(() => {
    const canvas = window.LidarPrototypeDemo.state.overlay.canvas;
    const ctx = canvas.getContext('2d');
    const pixel = ctx.getImageData(canvas.width / 2 | 0, canvas.height / 2 | 0, 1, 1).data;
    return { pixel: [...pixel], count: window.LidarPrototypeDemo.state.blocks.size,
      samples: window.LidarPrototypeDemo.state.sampled.length };
  });
  expect(initial.pixel[3]).toBe(255);
  expect(initial.count).toBe(1);
  expect(initial.samples).toBeGreaterThan(100000);
  const groundResolution = await page.evaluate(() => {
    const map = window.LidarPrototypeDemo.map;
    const center = map.getSize().divideBy(2);
    const a = map.containerPointToLatLng(center);
    const b = map.containerPointToLatLng(center.add([1, 0]));
    const projectedA = proj4('EPSG:4326', 'EPSG:2154', [a.lng, a.lat]);
    const projectedB = proj4('EPSG:4326', 'EPSG:2154', [b.lng, b.lat]);
    return Math.hypot(projectedA[0] - projectedB[0], projectedA[1] - projectedB[1]);
  });
  expect(groundResolution).toBeLessThan(0.5);
  const afterMetrics = await cdp.send('Performance.getMetrics');
  const metricValue = (snapshot, name) => snapshot.metrics.find(item => item.name === name)?.value ?? null;
  const mainThreadTaskMs = (metricValue(afterMetrics, 'TaskDuration') -
    metricValue(beforeMetrics, 'TaskDuration')) * 1000;
  const scriptMs = (metricValue(afterMetrics, 'ScriptDuration') -
    metricValue(beforeMetrics, 'ScriptDuration')) * 1000;
  await page.screenshot({ path: testInfo.outputPath('lidar-prototype.png') });
  const measurements = await page.evaluate(() => ({
    plan: document.querySelector('#m-plan-time').textContent,
    fetch: document.querySelector('#m-fetch-time').textContent,
    decode: document.querySelector('#m-decode-time').textContent,
    sample: document.querySelector('#m-sample-time').textContent,
    color: document.querySelector('#m-color-time').textContent,
    projectionError: document.querySelector('#m-projection-error').textContent,
    total: document.querySelector('#m-total-time').textContent,
    network: document.querySelector('#m-network').textContent,
    floatMemory: document.querySelector('#m-memory').textContent,
    jsHeapBytes: performance.memory?.usedJSHeapSize ?? null,
  }));
  console.log('L3A_BROWSER_METRICS', JSON.stringify({ ...measurements,
    metresPerCssPixel: groundResolution, blocks: initial.count,
    mainThreadTaskMs, scriptMs }));
  expect(parseFloat(measurements.projectionError)).toBeLessThanOrEqual(0.01);
  await expect(page.locator('#m-range')).not.toHaveText('Aucune altitude valide');
  await expect(page.locator('#fixed-min, #fixed-max, input[name="scale-mode"]')).toHaveCount(0);
  expect(await page.evaluate(() => {
    const state = LidarPrototypeDemo.state, {width,height} = state.sampledSize;
    const value = state.sampled[(height / 2 | 0) * width + (width / 2 | 0)];
    const range = LidarPrototypeCore.rangeOf(state.sampled);
    return LidarPrototypeCore.color(value,range.min,range.max);
  })).toEqual(initial.pixel);
  expect(reads).toBe(1);
  await page.locator('#opacity').fill('40');
  await expect(page.locator('#opacity-label')).toHaveText('40 %');
  await page.evaluate(() => window.LidarPrototypeDemo.map.panBy([40, 0], { animate: false }));
  await expect.poll(() => plans).toBeGreaterThanOrEqual(2);
  await expect(page.locator('#m-cache')).toContainText('MEMORY');
  expect(reads).toBe(1);

  // Re-selecting clears browser memory. Change the map before the delayed read
  // resolves: its old GeoTIFF must not be painted onto the new empty view.
  await page.getByRole('button', { name: 'Afficher cette sélection' }).click();
  await expect.poll(() => reads).toBe(2);
  await page.evaluate(() => window.LidarPrototypeDemo.map.setView([48, 5], 18));
  await expect(page.locator('#m-planned')).toHaveText('0');
  await expect(page.locator('#status')).toContainText('Aucun bloc');
  const after = await page.evaluate(() => {
    const canvas = window.LidarPrototypeDemo.state.overlay.canvas;
    return canvas.getContext('2d').getImageData(canvas.width / 2 | 0,
      canvas.height / 2 | 0, 1, 1).data[3];
  });
  expect(after).toBe(0);
  expect(reads).toBe(2);
  failPlan = true;
  await page.evaluate(() => window.LidarPrototypeDemo.map.panBy([30, 0], { animate: false }));
  await expect(page.locator('#status')).toContainText('HTTP 503');
  expect(reads).toBe(2);
  await page.locator('#business-base').selectOption(UUID.base2);
  expect(await page.evaluate(() => ({ blocks: window.LidarPrototypeDemo.state.blocks.size,
    selection: window.LidarPrototypeDemo.state.selection.length }))).toEqual({ blocks: 0, selection: 0 });
  await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
  expect(await page.evaluate(() => ({ disposed: window.LidarPrototypeDemo.state.disposed,
    blocks: window.LidarPrototypeDemo.state.blocks.size,
    sampled: window.LidarPrototypeDemo.state.sampled }))).toEqual({
    disposed: true, blocks: 0, sampled: null,
  });
});
