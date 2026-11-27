"use strict";

// UX-LOCAL-1, niveau B : vraies pages Carte/Base du serveur local sans base.
// Chaque scénario repart d'un BrowserContext Playwright isolé ; captures sous
// audits/ seulement. Coût indicatif : 2-4 min pour quatre dimensions Chromium.
const { expect, test } = require('@playwright/test');
const { mkdirSync } = require('node:fs');

const EVIDENCE = 'audits/ux-local-1/captures';
const SIZES = [
  ['bureau', 1280, 800], ['tablette', 820, 1180],
  ['paysage-mobile', 844, 390], ['portrait-mobile', 390, 844],
];
test.use({ serviceWorkers: 'block' });

function bounds(locator) { return locator.boundingBox(); }
function intersects(a, b) {
  return a && b && a.x < b.x + b.width && b.x < a.x + a.width
    && a.y < b.y + b.height && b.y < a.y + a.height;
}
async function capture(page, name) {
  mkdirSync(EVIDENCE, { recursive: true });
  await page.screenshot({ path: `${EVIDENCE}/${name}.png`, fullPage: false });
}
async function noHorizontalOverflow(page, width) {
  const state = await page.evaluate(() => ({ viewport: innerWidth,
    document: document.documentElement.scrollWidth }));
  expect(state.document).toBeLessThanOrEqual(width + 1);
}

for (const [name, width, height] of SIZES) {
  test(`prévisualisation ${name} : titre, message, légende et Paramètres`, async ({ page }) => {
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.setViewportSize({ width, height });
    await page.goto('/carte');
    await expect(page.locator('#map-legend')).toBeVisible();
    await expect(page.locator('.legend-business-layer')).toHaveCount(10);
    await expect(page.locator('#status')).toContainText(/tronçon/);
    await expect(page.locator('.backend-status-panel')).toBeVisible();
    await noHorizontalOverflow(page, width);

    // Un diagnostic multilignes flotte sous le bandeau sans déplacer le titre ;
    // Patrimoine reste au-dessus et sa commande de fermeture reste accessible.
    await page.locator('#toggle-heritage').click();
    await expect(page.locator('#heritage-panel')).toBeVisible();
    await page.locator('#status').evaluate(element => {
      element.classList.add('error');
      element.textContent = 'Prévisualisation : vérification de l’affichage d’un diagnostic important sur plusieurs lignes.';
    });
    await expect(page.locator('.backend-status-panel')).toBeVisible();
    const status = await bounds(page.locator('.backend-status-panel'));
    const title = await bounds(page.locator('.heritage-title'));
    expect(status).toBeTruthy();
    expect(title).toBeTruthy();
    const header = await bounds(page.locator('.app-header'));
    expect(status.y).toBeGreaterThanOrEqual(header.y + header.height);
    const closeAccessible = await page.locator('#close-heritage').evaluate(element => {
      const r = element.getBoundingClientRect();
      return element.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2));
    });
    expect(closeAccessible).toBe(true);
    await expect(page.locator('.backend-status-close')).toBeVisible();
    await capture(page, `${name}-patrimoine-et-message`);
    await page.locator('#toggle-heritage').click();
    await expect(page.locator('#heritage-panel')).toBeHidden();
    await page.locator('#status').evaluate(element => {
      element.classList.remove('error');
      element.textContent = 'Prévisualisation — données fictives';
    });

    const legend = page.locator('#map-legend');
    await expect(legend).toBeVisible();
    const legendBox = await bounds(legend);
    expect(legendBox).toBeTruthy();
    if (name === 'paysage-mobile') {
      expect(legendBox.height).toBeLessThanOrEqual(Math.min(112, height * .30) + 1);
    } else if (name === 'portrait-mobile') {
      expect(legendBox.height).toBeLessThanOrEqual(Math.min(176, height * .30) + 1);
    } else if (name === 'tablette') {
      expect(legendBox.height).toBeLessThanOrEqual(Math.min(224, height * .32) + 1);
    }
    await capture(page, `${name}-legende-haut`);
    if (width < 900) {
      const scroll = await legend.evaluate(element => ({
        available: element.clientHeight, content: element.scrollHeight,
      }));
      expect(scroll.content).toBeGreaterThan(scroll.available);
      // Les catégories du bas restent accessibles, sans changer l'état des couches.
      const morphology = legend.locator('.legend-business-layer').filter({ hasText: 'Éléments morphologiques du lit' });
      await morphology.scrollIntoViewIfNeeded();
      const itemBox = await bounds(morphology);
      const visibleLegend = await bounds(legend);
      expect(itemBox.y).toBeGreaterThanOrEqual(visibleLegend.y - 1);
      expect(itemBox.y + itemBox.height).toBeLessThanOrEqual(visibleLegend.y + visibleLegend.height + 1);
      await capture(page, `${name}-legende-lit`);
      await legend.evaluate(element => { element.scrollTop = element.scrollHeight; });
      await expect.poll(() => legend.evaluate(element => element.scrollTop)).toBeGreaterThan(0);
      await expect(page.locator('.legend-business-layer').last()).toBeVisible();
    }
    await capture(page, `${name}-legende`);

    await page.locator('.settings-menu-toggle').click();
    await expect(page.locator('.settings-menu-list')).toBeVisible();
    const menu = await bounds(page.locator('.settings-menu-list'));
    expect(menu.x).toBeGreaterThanOrEqual(-1);
    expect(menu.x + menu.width).toBeLessThanOrEqual(width + 1);
    expect(menu.y + menu.height).toBeLessThanOrEqual(height + 1);
    if (name.includes('mobile')) {
      await expect(legend).toBeHidden();
    } else {
      expect(intersects(menu, await bounds(legend))).toBe(false);
    }
    await capture(page, `${name}-parametres`);
    await page.locator('.settings-menu-toggle').click();
    await expect(page.locator('.settings-menu-list')).toBeHidden();
    await expect(legend).toBeVisible();
    if (name.includes('mobile')) await capture(page, `${name}-legende-retablie`);
    await noHorizontalOverflow(page, width);

    await page.goto('/base');
    if (width < 700) {
      await expect(page).toHaveURL(/\/carte\?/);
    } else {
      await expect(page.locator('#data-statistics-title')).toBeVisible();
      await expect(page.locator('.backend-status-panel')).toBeVisible();
      const baseStatus = await bounds(page.locator('.backend-status-panel'));
      const statistics = await bounds(page.locator('#data-statistics-title'));
      const header = await bounds(page.locator('.app-header'));
      expect(baseStatus.y).toBeGreaterThanOrEqual(header.y + header.height);
      await page.locator('.backend-status-close').click();
      expect(await bounds(page.locator('#data-statistics-title'))).toEqual(statistics);
      // La languette Patrimoine reste accessible sans recouvrir la première
      // lettre du titre Statistiques sur un écran paysage compact.
      expect(intersects(await bounds(page.locator('#toggle-heritage')), statistics)).toBe(false);
      await capture(page, `${name}-statistiques`);
      await noHorizontalOverflow(page, width);
    }
    expect(errors).toEqual([]);
  });
}
