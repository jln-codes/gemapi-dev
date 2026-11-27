const { test, expect } = require('@playwright/test');
const path = require('node:path');

const selectorScript = path.resolve('webapp/frontend/js/relation-selector.js');

async function mount(page, { readOnly = false } = {}) {
  await page.setContent(`<form><select name="desordre_ids" multiple>
    <option value="existing" selected>Désordre existant</option>
  </select></form>`);
  await page.addScriptTag({ path: selectorScript });
  await page.evaluate(({ readOnly }) => {
    const rows = {
      '': [{ id: 'a', label: 'Alpha' }],
      beta: [{ id: 'b', label: 'Beta' }],
      fast: [{ id: 'fast', label: 'Réponse récente' }],
    };
    window.calls = [];
    window.slowResolve = null;
    window.fetchRelation = async (url) => {
      const parsed = new URL(url, 'https://digues.test');
      const q = parsed.searchParams.get('q') || '';
      const cursor = parsed.searchParams.get('cursor');
      window.calls.push({ q, cursor });
      if (q === 'slow') return new Promise(resolve => { window.slowResolve = resolve; });
      if (q === 'error') throw new Error('Réseau indisponible');
      if (cursor === 'page-2') return {
        items: [{ id: 'c', label: 'Suite' }], next_cursor: null, has_more: false,
      };
      return {
        items: rows[q] || [], next_cursor: q === '' ? 'page-2' : null,
        has_more: q === '',
      };
    };
    window.controller = RelationSelector.attachSearch(
      document.querySelector('select'),
      { fetchJson: window.fetchRelation, readOnly },
    );
  }, { readOnly });
}

async function search(page, query) {
  const input = page.getByRole('searchbox', { name: 'Rechercher un Désordre' });
  await input.evaluate((node, value) => { node.value = value; }, query);
  await page.getByRole('button', { name: 'Rechercher' }).click();
}

test('les sélections restent indépendantes des recherches et des pages', async ({ page }) => {
  await mount(page);
  await expect(page.getByRole('heading', { name: 'Désordres associés' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Résultats de la recherche' })).toBeVisible();
  await expect(page.getByText('Rechercher un Désordre', { exact: true })).toBeVisible();
  await expect(page.locator('.relation-search-selector')).toHaveAttribute('aria-busy', 'false');
  await expect(page.getByRole('button', { name: 'Alpha' })).toBeVisible();
  await page.getByRole('button', { name: 'Alpha' }).click();
  await search(page, 'beta');
  await page.getByRole('button', { name: 'Beta' }).click();

  const selected = page.locator('.relation-search-selected');
  await expect(selected).toContainText('Désordre existant');
  await expect(selected).toContainText('Alpha');
  await expect(selected).toContainText('Beta');
  await expect(page.getByRole('searchbox')).toBeFocused();
  await selected.getByRole('button', { name: 'Retirer Alpha' }).click();
  await expect(selected).not.toContainText('Alpha');
  await expect(selected).toContainText('Désordre existant');
  await expect(selected).toContainText('Beta');
  await expect(page.getByRole('searchbox')).toBeFocused();
  expect(await page.locator('select[name=desordre_ids]').evaluate(select =>
    [...select.selectedOptions].map(option => option.value))).toEqual(['existing', 'b']);

  await search(page, '');
  await page.getByRole('button', { name: 'Afficher la suite' }).click();
  await expect(page.getByRole('button', { name: 'Suite' })).toBeVisible();
  await expect(selected).toContainText('Beta');
});

test('une réponse tardive, une erreur et un changement de base restent sûrs', async ({ page }) => {
  await mount(page);
  await expect(page.getByRole('button', { name: 'Alpha' })).toBeVisible();
  const input = page.getByRole('searchbox', { name: 'Rechercher un Désordre' });
  await input.fill('slow');
  await expect.poll(() => page.evaluate(() => typeof window.slowResolve)).toBe('function');
  await input.fill('fast');
  await expect(page.getByRole('button', { name: 'Réponse récente' })).toBeVisible();
  await page.evaluate(() => window.slowResolve({
    items: [{ id: 'slow', label: 'Réponse périmée' }],
    next_cursor: null,
    has_more: false,
  }));
  await expect(page.getByText('Réponse périmée')).toHaveCount(0);

  await search(page, 'error');
  await expect(page.getByRole('alert')).toHaveText('Réseau indisponible');
  await expect(page.locator('.relation-search-selected')).toContainText('Désordre existant');

  await page.evaluate(() => dispatchEvent(new Event('digues-app:context-closed')));
  await expect(page.locator('.relation-search-selector')).toHaveCount(0);
  await expect(page.locator('select[name=desordre_ids]')).toBeVisible();
});

test('le mode lecture seule conserve les valeurs sans contrôles de mutation', async ({ page }) => {
  await mount(page, { readOnly: true });
  await expect(page.getByRole('heading', { name: 'Désordres associés' })).toBeVisible();
  await expect(page.locator('.relation-search-selector')).toHaveClass(/relation-search-selector--readonly/);
  await expect(page.getByText('Lecture seule.', { exact: true })).toBeVisible();
  await expect(page.locator('.relation-search-selected')).toContainText('Désordre existant');
  await expect(page.getByRole('searchbox')).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Retirer/ })).toHaveCount(0);
  expect(await page.evaluate(() => window.calls.length)).toBe(0);
});
