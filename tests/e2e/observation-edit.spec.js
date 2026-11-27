const { mockBusinessCRS } = require('./mock-business-crs');
const { test, expect } = require('@playwright/test');
const { randomUUID } = require('node:crypto');
const { mockResourceJournal } = require('./mock-resource-journal');
const D = '00000000-0000-4000-8000-000000000042';
const O = '00000000-0000-4000-8000-000000000043';
const fc = features => ({ type: 'FeatureCollection', features });
const disorder = { type: 'Feature', properties: { id: D, version: 1, designation: 'Parement', troncon_ids: [], valid: true },
  geometry: { type: 'Point', coordinates: [2, 46] } };

async function setup(context, resource = 'desordres', onPut) {
  let observation = { id: O,
    parent: { resource, id: D, designation: 'Parent métier' }, version: 1, designation: 'Inspection',
    date: '2026-09-19', evolution: 'Stable', suite: 'Surveiller', suite_apporter_id: null,
    urgence_id: null, nombre_desordres: 3, valid: false, observateur_source_id: 'secret-v2', photos: [] };
  const puts = [];
  await context.route(/^https:\/\//, route => route.abort());
  await context.route('**/api/**', async route => {
    if (await mockBusinessCRS(route)) return;
    if (await mockResourceJournal(route)) return;
    const request = route.request(), path = new URL(request.url()).pathname;
    let payload = {};
    if (path === `/api/observations/${O}`) {
      if (request.method() === 'PUT') {
        const changes = request.postDataJSON();
        puts.push({ changes, headers: request.headers() });
        if (onPut && await onPut(route, puts.length)) return;
        observation = { ...observation, ...changes, version: observation.version + 1 };
        payload = { ...observation, mutation: { schemaVersion: 1, eventId: randomUUID(), resource: 'observations',
          action: 'update', ids: [O], versions: { [O]: observation.version }, occurredAt: Date.now(),
          originClientId: request.headers()['x-digues-app-client-id'], originInstanceId: request.headers()['x-digues-app-instance-id'] } };
      } else payload = observation;
    } else if (path === '/api/config') payload = { legacy_single_database_enabled: true, show_uuid: false };
    else if (path === '/api/systemes-endiguement') payload = { systemes: [] };
    else if (path === '/api/desordres') payload = fc([disorder]);
    else if (path === `/api/desordres/${D}`) payload = disorder;
    else if (path === `/api/desordres/${D}/observations`) payload = {
      parent: { resource: 'desordres', id: D, designation: 'Parent métier' },
      observations: [observation],
    };
    else if (path === '/api/troncons/options') payload = { troncons: [] };
    else if (path === '/api/troncons' || path === '/api/territoire-administratif') payload = fc([]);
    else if (path === '/api/referentiels/types-desordre') payload = { types: [] };
    else if (path === '/api/referentiels/categories-desordre') payload = { categories: [] };
    else if (path.startsWith('/api/referentiels/')) payload = { references: [{ id: 'r1', libelle: 'Référence métier', valid: true }] };
    else if (path === '/api/data/desordres/query') payload = { columns: [], rows: [], total: 0, offset: 0, has_next: false };
    await route.fulfill({ json: payload });
  });
  return puts;
}

async function open(page, ui) {
  await page.goto(ui === 'map' ? '/carte' : '/base');
  if (ui === 'map') {
    await page.locator('.desordre-point-marker').click();
    await page.locator('#editor-panel .entity-form:visible > .entity-form__tabs [data-role=observations-tab]').click();
    await page.locator('#observations-list button').first().click();
    await expect(page.locator('#observation-suite-form [name="designation"]')).toBeVisible();
  } else {
    await page.waitForFunction(() => document.querySelector('main')?.dataset.layout !== undefined);
    await page.evaluate(id => document.querySelector('main').dispatchEvent(new CustomEvent('digues-app:open-entity', {
      detail: { entityClass: 'observations', entityId: id, source: 'views', entities: [] },
    })), O);
    await expect(page.locator('#data-entity-detail-read')).toContainText('Parent métier');
    await page.locator('#data-entity-detail-edit').click();
  }
  return page.locator(ui === 'map' ? '#observation-suite-form' : '#data-entity-detail .entity-form__form');
}
function save(page, ui) { return page.locator(ui === 'map' ? '#observation-suite-form [type="submit"]' : '#data-entity-detail .entity-form__actions [data-role=save]'); }

for (const ui of ['map', 'base']) {
  test(`${ui} : tous les champs Désordre, parent verrouillé, PUT partiel, version suivante`, async ({ context, page }) => {
    const puts = await setup(context);
    const form = await open(page, ui);
    await expect(form).toContainText('Désordre — Parent métier');
    await expect(form).not.toContainText(D);
    for (const name of ['parent', 'valid', 'observateur_source_id', 'version', 'desordre_id']) {
      await expect(form.locator(`[name="${name}"]`)).toHaveCount(0);
    }
    await form.locator('[name="designation"]').fill('  Revue  ');
    await form.locator('[name="date"]').fill('2026-09-23');
    await form.locator('[name="evolution"]').fill('Progression');
    await form.locator('[name="suite_apporter_id"]').selectOption('r1');
    await form.locator('[name="suite"]').fill('  ');
    await form.locator('[name="urgence_id"]').selectOption('r1');
    await form.locator('[name="nombre_desordres"]').fill('0');
    await save(page, ui).click();
    await expect.poll(() => puts.length).toBe(1);
    expect(puts[0].changes).toEqual({ designation: 'Revue', date: '2026-09-23', evolution: 'Progression',
      suite_apporter_id: 'r1', suite: null, urgence_id: 'r1', nombre_desordres: 0 });
    expect(puts[0].headers['if-match']).toBe('"1"');
    if (ui === 'base') {
      await expect(page.locator('#data-entity-detail-read')).toBeVisible();
      await page.locator('#data-entity-detail-edit').click();
    } else {
      await page.waitForFunction(() => currentObservation?.version === 2);
      await expect(form.locator('[name=designation]')).toHaveValue('Revue');
    }
    await form.locator('[name="designation"]').fill('Seconde revue');
    await save(page, ui).click();
    await expect.poll(() => puts.length).toBe(2);
    expect(puts[1].headers['if-match']).toBe('"2"');
    expect(puts[1].changes).toEqual({ designation: 'Seconde revue' });
  });

  for (const status of [409, 503]) {
    test(`${ui} : ${status}, brouillon conservé et double soumission bloquée`, async ({ context, page }) => {
      let release;
      const gate = new Promise(resolve => { release = resolve; });
      const puts = await setup(context, 'desordres', async route => {
        await gate;
        await route.fulfill({ status, json: { detail: status === 409 ? 'Conflit de version' : 'Erreur serveur' } });
        return true;
      });
      const form = await open(page, ui);
      await form.locator('[name="designation"]').fill('Brouillon à garder');
      await save(page, ui).click();
      await expect.poll(() => puts.length).toBe(1);
      await form.evaluate(node => node.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
      await expect(save(page, ui)).toBeDisabled();
      release();
      await expect(save(page, ui)).toBeEnabled();
      await expect(form.locator('[name="designation"]')).toHaveValue('Brouillon à garder');
      await expect(page.locator(ui === 'map' ? '#observation-suite-message' : '#data-entity-detail .entity-form__messages [role=alert]'))
        .toContainText(status === 409 ? 'brouillon' : 'Erreur');
      expect(puts).toHaveLength(1);
    });
  }
}

for (const resource of ['troncons', 'ouvrages_hydrauliques', 'equipements_mesure', 'cheminements',
  'mobilier', 'reseaux_techniques', 'amenagements_hydrauliques', 'vegetation']) {
  test(`Base ${resource} : champs communs seuls et conservation des valeurs historiques`, async ({ context, page }) => {
    const puts = await setup(context, resource);
    const form = await open(page, 'base');
    await expect(form).toContainText('Parent métier');
    await expect(form.locator('[name="urgence_id"], [name="nombre_desordres"]')).toHaveCount(0);
    await form.locator('[name="designation"]').fill('Inspection revue');
    await save(page, 'base').click();
    await expect(page.locator('#data-entity-detail-read')).toBeVisible();
    expect(puts[0].changes).toEqual({ designation: 'Inspection revue' });
  });
}
