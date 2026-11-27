const { test, expect } = require('@playwright/test');
const { createRequire } = require('node:module');
const { mockBusinessCRS } = require('./mock-business-crs');
const { mockResourceJournal } = require('./mock-resource-journal');
const { photoQualityPolicy } = require('./fixtures/photo-quality-policy');
const rendererRequire = createRequire(require.resolve('../../services/plan-ign-raster/package.json'));
const sharp = rendererRequire('sharp');

// Niveau B : vrai catalogue FastAPI et vrai sélecteur Leaflet. Les réponses
// raster locales sont synthétiques ; les requêtes fournisseur et caches réels
// du même catalogue sont exercés par wmts-raster.test.mjs. Aucun SQL ni IGN ici.
test.use({ serviceWorkers: 'block' });
test('le catalogue livré propose les trois fonds et le sélecteur garde leurs sources distinctes', async ({ context, page }, testInfo) => {
  await context.route(/^https?:\/\/(?!127\.0\.0\.1(?::|\/)|localhost(?::|\/))/, route => route.abort());
  await context.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/basemaps') return route.continue();
    // /api/config reads the control DB for Photo policy; this layout fixture
    // supplies that unrelated configuration, never a fake basemap catalogue.
    if (path === '/api/config') return route.fulfill({ json: {
      legacy_single_database_enabled: true, show_uuid: false,
      basemap_catalog_url: '/api/basemaps', basemap_tilejson_url: '/basemap/plan-ign.json',
      photo_quality_policy: photoQualityPolicy(),
    } });
    if (await mockBusinessCRS(route) || await mockResourceJournal(route)) return;
    await route.fulfill({ json: path.endsWith('/systemes-endiguement')
      ? { systemes: [] } : { type: 'FeatureCollection', features: [] } });
  });
  const response = await page.request.get('/api/basemaps');
  expect(response.ok()).toBe(true);
  const catalogue = await response.json();
  expect(catalogue.default_basemap_id).toBe('ign-epure');
  expect(catalogue.basemaps.map(row => row.id)).toEqual(['ign-epure', 'ign-plan', 'ign-ortho']);
  const calls = Object.fromEntries(catalogue.basemaps.map(row => [row.id, 0]));
  const images = {};
  for (const [index, entry] of catalogue.basemaps.entries()) {
    const image = sharp({ create: { width: 256, height: 256, channels: 3,
      background: ['#d5e7dc', '#f5dfb6', '#456d36'][index] } });
    images[entry.id] = await image.toFormat(entry.tile_format).toBuffer();
  }
  const template = entry => entry.id === 'ign-epure'
    ? '/basemap/tiles/browser-fixture/{z}/{x}/{y}.webp'
    : `/basemap/${entry.id}/tiles/browser-fixture/{z}/{x}/{y}.${entry.tile_format === 'jpeg' ? 'jpg' : entry.tile_format}`;
  await page.route('**/basemap/**', async route => {
    const path = new URL(route.request().url()).pathname;
    const entry = catalogue.basemaps.find(row => path === row.delivery.url);
    if (entry) return route.fulfill({ json: { tilejson: '3.0.0',
      tiles: [template(entry)], minzoom: entry.zoom.native_min, maxzoom: entry.zoom.native_max,
      attribution: entry.attribution, format: entry.tile_format } });
    const raster = catalogue.basemaps.find(row => path.startsWith(template(row).split('{z}')[0]));
    if (!raster) return route.fulfill({ status: 404 });
    calls[raster.id]++;
    await route.fulfill({ body: images[raster.id], contentType: `image/${raster.tile_format}` });
  });

  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/carte');
  await expect(page.locator('main')).toHaveJSProperty('inert', false);
  await page.evaluate(() => map.setView([50.607, 2.807], 15));
  await page.locator('.settings-menu-toggle').click();
  const choices = page.locator('#basemap-selector .basemap-selector-choice');
  await expect(choices).toHaveCount(3);
  await expect(choices.first()).toHaveAttribute('aria-pressed', 'true');
  for (const [index, entry] of catalogue.basemaps.entries()) {
    await choices.nth(index).click();
    await expect(choices.nth(index)).toHaveAttribute('aria-pressed', 'true');
    await expect.poll(() => calls[entry.id]).toBeGreaterThan(0);
    // Check the displayed tiles, irrespective of loadStrict's URL normalization.
    const prefix = template(entry).split('{z}')[0];
    await expect.poll(() => page.locator('#map img.leaflet-tile-loaded').evaluateAll(
      (images, expected) => images.length > 0 && images.every(image =>
        new URL(image.src).origin === location.origin && new URL(image.src).pathname.startsWith(expected)), prefix))
      .toBe(true);
    await expect(page.locator('img.leaflet-tile-loaded').first()).toBeVisible();
  }
  await page.locator('.settings-menu-toggle').click();
  await page.screenshot({ path: testInfo.outputPath('trois-fonds-orthophoto-selectionnee.png') });
  await page.reload();
  await expect(page.locator('main')).toHaveJSProperty('inert', false);
  await page.locator('.settings-menu-toggle').click();
  await expect(choices.nth(2)).toHaveAttribute('aria-pressed', 'true');
});
