// Niveau B : vrais scripts/DOM Chromium, services locaux simulés. La dernière
// dépendance est retenue explicitement : aucune commande sans gestionnaire ne
// doit accepter un clic, puis les mêmes commandes fonctionnent après chargement.
const { test, expect } = require('@playwright/test');
const { mockBusinessCRS } = require('./mock-business-crs');
const { mockResourceJournal } = require('./mock-resource-journal');
const { photoQualityPolicy } = require('./fixtures/photo-quality-policy');

test.use({ serviceWorkers: 'block' });
for (const path of ['/carte', '/base']) {
  test(`commandes indisponibles pendant le chargement des scripts ${path}`, async ({ page }) => {
    await page.route(/^https:\/\//, route => route.abort());
    await page.route('**/api/**', async route => {
      if (await mockBusinessCRS(route) || await mockResourceJournal(route)) return;
      const endpoint = new URL(route.request().url()).pathname;
      if (endpoint === '/api/config') return route.fulfill({ json: {
        legacy_single_database_enabled: true, photo_quality_policy: photoQualityPolicy(),
      } });
      if (endpoint === '/api/systemes-endiguement') return route.fulfill({ json: { systemes: [] } });
      return route.fulfill({ json: { type: 'FeatureCollection', features: [] } });
    });
    let release;
    const held = new Promise(resolve => { release = resolve; });
    await page.route('**/static/js/help-panel.js', async route => {
      await held;
      await route.continue();
    });
    try {
      await page.goto(path, { waitUntil: 'domcontentloaded' });
      await expect(page.locator('#toggle-help')).toBeVisible();
      await expect(page.locator('main')).toHaveJSProperty('inert', true);
      // Exercise native focus, rather than Playwright's role-selector treatment
      // of inert ancestors (which differs from the browser accessibility tree).
      await page.locator('#toggle-help').evaluate(button => button.focus());
      await expect(page.locator('#toggle-help')).not.toBeFocused();
      release();
      await expect(page.locator('main')).toHaveJSProperty('inert', false);
      await page.getByRole('button', { name: 'Aide', exact: true }).click();
      await expect(page.locator('#help-modal')).toBeVisible();
    } finally { release(); }
  });
}
