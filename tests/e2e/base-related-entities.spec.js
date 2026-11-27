const { test, expect } = require('@playwright/test');
const { randomUUID } = require('node:crypto');
const { mockBusinessCRS } = require('./mock-business-crs');
const { mockResourceJournal } = require('./mock-resource-journal');
const { photoQualityPolicy } = require('./fixtures/photo-quality-policy');

test.use({ serviceWorkers: 'block' });

const T1 = '00000000-0000-4000-8000-000000000601';
const T2 = '00000000-0000-4000-8000-000000000602';
const D1 = '00000000-0000-4000-8000-000000000603';
const A1 = '00000000-0000-4000-8000-000000000604';
const C1 = '00000000-0000-4000-8000-000000000605';
const CASES = [
  { classId: 'mobilier', endpoint: '/api/mobilier', resource: 'mobilier',
    label: 'Mobilier', id: '00000000-0000-4000-8000-000000000606',
    fields: { type_id: 'CT', type_libelle: 'Type pilote', troncon_id: T1,
      troncon_libelle: 'Tronçon 1', desordre_ids: [D1], valid: true } },
  { classId: 'equipements_mesure', endpoint: '/api/equipements-mesure',
    resource: 'equipements-mesure', label: 'Équipements de mesure',
    id: '00000000-0000-4000-8000-000000000607',
    fields: { type_id: 'CT', type_libelle: 'Type pilote', troncon_id: T1,
      troncon_libelle: 'Tronçon 1', desordre_ids: [D1], valid: true } },
  { classId: 'reseaux_techniques', endpoint: '/api/reseaux-techniques',
    resource: 'reseaux-techniques', label: 'Réseaux techniques',
    id: '00000000-0000-4000-8000-000000000608',
    fields: { type_id: 'CT', type_libelle: 'Type pilote', troncon_id: T1,
      troncon_libelle: 'Tronçon 1', desordre_ids: [D1], valid: true } },
  { classId: 'ouvrages_particuliers', endpoint: '/api/ouvrages-particuliers',
    resource: 'ouvrages-particuliers', label: 'Ouvrages particuliers',
    id: '00000000-0000-4000-8000-000000000610',
    fields: { troncon_id: T1, troncon_libelle: 'Tronçon 1', desordre_ids: [D1], valid: true } },
  { classId: 'cheminements', endpoint: '/api/cheminements', resource: 'cheminements',
    label: 'Cheminements', id: '00000000-0000-4000-8000-000000000611',
    fields: { type_cheminement_id: 'CT', type_libelle: 'Chemin piéton',
      troncon_ids: [T1, T2], desordre_ids: [D1], valid: true } },
  { classId: 'vegetation', endpoint: '/api/vegetation', resource: 'vegetation',
    label: 'Végétation', id: '00000000-0000-4000-8000-000000000612',
    fields: { famille_vegetation: 'ARBRES', disposition: 'ISOLE',
      sous_type: 'ARBRE_SUR_PIED', invasif: true, problematique_ids: ['INV'],
      troncon_ids: [T1, T2], amenagement_hydraulique_ids: [A1],
      desordre_ids: [D1], valid: true } },
  { classId: 'amenagements_hydrauliques', endpoint: '/api/amenagements-hydrauliques',
    resource: 'amenagements-hydrauliques', label: 'AH réglementaire',
    id: '00000000-0000-4000-8000-000000000613',
    fields: { type_id: 'AH', type_libelle: 'Aménagement', troncon_ids: [T1, T2],
      desordre_ids: [D1],
      troncons: [{ id: '00000000-0000-4000-8000-000000000615',
        type_physique_id: 'INDETERMINE',
        type_physique_libelle: 'Indéterminé', designation: 'Tronçon associé' }],
      zones_stockage: [{ id: A1, designation: 'Zone 1' }], valid: true } },
  { classId: 'prestations', endpoint: '/api/prestations', resource: 'prestations',
    label: 'Prestations', id: '00000000-0000-4000-8000-000000000614',
    fields: { type_prestation_id: 'PT', type_libelle: 'Inspection',
      date_fin: '2026-10-09', record_origin: 'NATIVE',
      troncon_ids: [T1, T2], intervenant_ids: [C1],
      object_ids: { desordres: [D1], amenagements_hydrauliques: [A1] }, valid: true } },
];
const FAMILIES = ['desordres', 'ouvrages_hydrauliques', 'ouvrages_particuliers',
  'mobilier', 'equipements_mesure', 'reseaux_techniques', 'cheminements',
  'vegetation', 'amenagements_hydrauliques'];

function catalog() {
  return { default_root: 'cheminements', roots: CASES.map(item => ({ id: item.classId, label: item.label })),
    relations: [], tables: CASES.map(item => ({ id: item.classId, label: item.label,
      compatible_roots: [item.classId], fields: ['designation', 'troncons'].map(field => ({
        id: field, label: field, type: 'text', source_class: item.classId,
        source_field: field, navigable: field === 'designation',
      })) })) };
}

async function fixture(context, { conflict = false, associationConflict = false } = {}) {
  const records = Object.fromEntries(CASES.map(item => [item.classId, {
    type: 'Feature', id: item.id,
    geometry: { type: item.classId === 'cheminements' ? 'LineString' : 'Point',
      coordinates: item.classId === 'cheminements' ? [[2, 48], [2.1, 48.1]] : [2, 48] },
    properties: { id: item.id, version: 1, designation: `${item.label} initial`,
      commentaire: 'Initial', date_debut: '2026-10-01', ...item.fields },
  }]));
  const writes = [];
  await context.route('**/api/**', async route => {
    if (await mockBusinessCRS(route) || await mockResourceJournal(route)) return;
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const send = (json, status = 200) => route.fulfill({ status, json });
    if (path === '/api/config') return send({ legacy_single_database_enabled: true,
      show_uuid: false, photo_quality_policy: photoQualityPolicy() });
    if (path === '/api/systemes-endiguement') return send({ systemes: [] });
    if (path === '/api/data/desordres/query') return send({ columns: [], rows: [], total: 0,
      offset: 0, has_next: false });
    if (path === '/api/data/view-catalog') return send(catalog());
    if (path === '/api/data/view-query') {
      const query = request.postDataJSON();
      const item = CASES.find(row => row.classId === query.root);
      const record = item && records[item.classId];
      const visible = item && !(record.properties.troncon_ids || [])
        .some(id => (query.hidden_troncon_ids || []).includes(id));
      const rows = visible && query.offset === 0 ? [{ values: query.fields.map(field =>
        field.field === 'designation' ? record.properties.designation : 'Tronçon 1 · Tronçon 2'),
        entities: [{ class: item.classId, id: item.id,
          attributes: { designation: record.properties.designation } }] }] : [];
      return send({ root: query.root, granularity: query.root,
        columns: query.fields.map(field => ({ key: `${query.root}.${field.field}`,
          label: field.field, source_class: query.root, source_field: field.field,
          navigable: field.field === 'designation' })),
        rows, total: visible ? 1 : 0, offset: query.offset, has_next: false });
    }
    if (path === '/api/troncons/options') return send({ troncons: [
      { id: T1, libelle: 'Tronçon 1', valid: true },
      { id: T2, libelle: 'Tronçon 2', valid: true },
    ] });
    if (path === '/api/desordres/search') return send({ items: [
      { id: D1, label: 'Désordre 1', valid: true },
    ], next_cursor: null, has_more: false });
    if (path === '/api/desordres/selection') {
      const ids = url.searchParams.getAll('ids');
      return send({ items: ids.includes(D1)
        ? [{ id: D1, label: 'Désordre 1', valid: true }] : [],
      missing_ids: ids.filter(id => id !== D1) });
    }
    if (path === '/api/vegetation/relations/options') return send({ troncons: [
      { id: T1, libelle: 'Tronçon 1', valid: true },
      { id: T2, libelle: 'Tronçon 2', valid: true },
    ], amenagements_hydrauliques: [{ id: A1, libelle: 'Aménagement 1', valid: true }] });
    if (path === '/api/prestations/relations/options') return send({
      troncons: [T1, T2].map((id, index) => ({ id, libelle: `Tronçon ${index + 1}`, valid: true })),
      contacts: [{ id: C1, libelle: 'Contact 1', valid: true }],
      ...Object.fromEntries(FAMILIES.map(family => [family,
        family === 'desordres' ? [{ id: D1, libelle: 'Désordre 1', valid: true }]
          : family === 'amenagements_hydrauliques'
            ? [{ id: A1, libelle: 'Aménagement 1', valid: true }] : []])),
    });
    if (path === '/api/amenagements-hydrauliques/relations/options') return send({
      troncons: [T1, T2].map((id, index) => ({ id, libelle: `Tronçon ${index + 1}`, valid: true })),
      zones: [],
    });
    if (path.startsWith('/api/referentiels/')) return send({ references: [
      { id: 'CT', libelle: 'Chemin piéton', valid: true },
      { id: 'ARB', libelle: 'Arbre', valid: true, native_selectable: true,
        compatible_point: true, compatible_linestring: true, compatible_polygon: true },
      { id: 'INV', libelle: 'Invasive', valid: true },
      { id: 'AH', libelle: 'Aménagement', valid: true },
      { id: 'PT', libelle: 'Inspection', designation: 'Inspection', valid: true },
    ] });
    const item = CASES.find(row => path === `${row.endpoint}/${row.id}`);
    if (item) {
      if (request.method() === 'PUT') {
        const body = request.postDataJSON();
        writes.push({ classId: item.classId, headers: request.headers(), body });
        if (associationConflict && item.classId === 'amenagements_hydrauliques'
            && body.desordre_ids?.includes(D1)) {
          return send({ detail: { code: 'disorder-already-associated',
            message: 'Ce Désordre est déjà associé à un autre aménagement hydraulique.' } }, 409);
        }
        if (conflict) return send({ detail: { code: 'version-conflict', message: 'Version dépassée' } }, 409);
        const record = records[item.classId];
        record.properties = { ...record.properties, ...body, version: record.properties.version + 1 };
        record.mutation = { schemaVersion: 1, eventId: randomUUID(), resource: item.resource,
          action: 'update', ids: [item.id], versions: { [item.id]: record.properties.version },
          occurredAt: Date.now(), originClientId: randomUUID(), originInstanceId: randomUUID() };
      }
      return send(records[item.classId]);
    }
    return send({});
  });
  return { records, writes };
}

test('Aménagement hydraulique : conflit de cardinalité conserve puis corrige le brouillon', async ({ context, page }) => {
  const state = await fixture(context, { associationConflict: true });
  const item = CASES.find(candidate => candidate.classId === 'amenagements_hydrauliques');
  state.records[item.classId].properties.desordre_ids = [];
  await openDetail(page, item);
  await page.locator('#data-entity-detail-edit').click();
  await page.getByRole('button', { name: 'Désordre 1' }).click();
  await page.locator('#data-entity-detail .entity-form__form [name=commentaire]').fill('Brouillon AH conservé');
  await page.locator('#data-entity-detail .entity-form__actions [data-role=save]').click();
  await expect(page.locator('#data-entity-detail .entity-form__messages [role=alert]'))
    .toContainText('déjà associé à un autre aménagement hydraulique');
  await expect(page.locator('#data-entity-detail .entity-form__form [name=commentaire]'))
    .toHaveValue('Brouillon AH conservé');
  await expect(page.getByRole('button', { name: 'Retirer Désordre 1' })).toBeVisible();
  await page.getByRole('button', { name: 'Retirer Désordre 1' }).click();
  await page.locator('#data-entity-detail .entity-form__actions [data-role=save]').click();
  await expect(page.locator('#data-entity-detail .entity-form__form')).toBeHidden();
  expect(state.writes).toHaveLength(2);
  expect(state.writes[0].headers['if-match']).toBe('"1"');
  expect(state.writes[1].headers['if-match']).toBe('"1"');
  expect(state.writes[1].body.desordre_ids).toBeUndefined();
});

async function openList(page, item) {
  await page.goto('/base');
  await expect(page.locator('#identity-gate')).toBeHidden();
  await expect(page.locator('#base-summary-crs')).not.toHaveText('');
  const dismiss = page.getByRole('button', { name: 'Fermer le message' });
  if (await dismiss.isVisible()) await dismiss.click();
  const table = page.locator('#data-view-table');
  if (!await table.isVisible()) await page.locator('#toggle-views').click();
  while (await page.locator('#data-view-field-list button').count()) {
    await page.locator('#data-view-field-list button').first().click();
  }
  await table.selectOption(item.classId);
  await page.locator('#data-view-field').selectOption('designation');
  await page.locator('#data-view-add-field').click();
  await expect(page.locator(`#data-view-result-body [data-entity-class="${item.classId}"]`)).toHaveCount(1);
}

async function openDetail(page, item) {
  await openList(page, item);
  await page.locator(`#data-view-result-body [data-entity-class="${item.classId}"]`).click();
  await expect(page.locator('#data-entity-detail-title')).toContainText(item.label);
}

for (const item of CASES) {
  test(`${item.label} : fiche, relations, PUT versionné et retour`, async ({ context, page }) => {
    const state = await fixture(context);
    await openDetail(page, item);
    await expect(page.locator('#data-entity-detail-read')).toContainText('Tronçon 1');
    if (item.classId === 'amenagements_hydrauliques') {
      await expect(page.locator('#data-entity-detail-read')).toContainText('Zone 1');
      await expect(page.locator('#data-entity-detail-read')).toContainText('Tronçon associé');
      await expect(page.locator('#data-entity-detail-read [data-relation-resource="troncons"]')).toHaveCount(3);
      await expect(page.locator('#data-entity-detail-read .base-detail__qualification')).toContainText(['Indéterminé']);
    }
    if (['mobilier', 'equipements_mesure', 'reseaux_techniques', 'ouvrages_particuliers',
      'cheminements', 'vegetation', 'amenagements_hydrauliques', 'prestations']
      .includes(item.classId)) {
      await expect(page.locator('#data-entity-detail-read')).toContainText('Désordre 1');
    }
    if (item.classId === 'prestations') {
      await expect(page.locator('#data-entity-detail-read')).toContainText('Contact 1');
    }
    await page.locator('#data-entity-detail-edit').click();
    await expect(page.locator('#data-entity-detail .entity-form__form [name=designation]')).toBeEditable();
    await expect(page.locator('#data-entity-detail .entity-form__fields')).toBeEnabled();
    if (item.classId !== 'prestations') {
      await expect(page.getByRole('heading', { name: 'Désordres associés' })).toBeVisible();
      await expect(page.locator('.relation-search-selector')).toHaveAttribute('aria-busy', 'false');
    }
    expect(await page.locator('#data-entity-detail .entity-form__form').evaluate(element =>
      element.scrollWidth <= element.clientWidth)).toBe(true);
    await page.locator('#data-entity-detail .entity-form__form [name=commentaire]').fill('Depuis Base');
    await page.locator('#data-entity-detail .entity-form__actions [data-role=save]').click();
    await expect(page.locator('#data-entity-detail-read')).toContainText('Depuis Base');
    expect(state.writes).toHaveLength(1);
    const { body, headers } = state.writes[0];
    expect(headers['if-match']).toBe('"1"');
    expect(body).not.toHaveProperty('geometry');
    if (['mobilier', 'equipements_mesure', 'reseaux_techniques',
      'ouvrages_particuliers', 'prestations', 'vegetation'].includes(item.classId)) {
      expect(body).not.toHaveProperty('troncon_ids');
    }
    else expect(body.troncon_ids).toEqual([T1, T2]);
    if (['ouvrages_particuliers', 'cheminements'].includes(item.classId)) {
      expect(body.desordre_ids).toEqual([D1]);
    }
    if (['mobilier', 'equipements_mesure', 'reseaux_techniques', 'vegetation',
      'amenagements_hydrauliques'].includes(item.classId)) {
      expect(body).not.toHaveProperty('desordre_ids');
    }
    if (item.classId === 'vegetation') {
      // The current vegetation contract sends only intentional changes.
      expect(body).toEqual({ commentaire: 'Depuis Base' });
      expect(state.records.vegetation.properties).toMatchObject({ ...item.fields, commentaire: 'Depuis Base' });
    }
    if (item.classId === 'prestations') {
      expect(body).not.toHaveProperty('intervenant_ids');
      expect(body).not.toHaveProperty('object_ids');
    }
    await page.locator('#data-entity-detail-back').click();
    await expect(page.locator('#data-view-result-body')).toContainText(item.label);
    await expect(page.locator('#data-view-table')).toHaveValue(item.classId);
    await expect(page.locator('#data-view-builder-workspace [data-role=new]')).toHaveCount(0);
  });

  test(`${item.label} : conflit, brouillon et lecture seule`, async ({ context, page }) => {
    const state = await fixture(context, { conflict: true });
    await openDetail(page, item);
    await page.locator('#data-entity-detail-edit').click();
    await page.locator('#data-entity-detail .entity-form__form [name=commentaire]').fill('Brouillon');
    await page.locator('#data-entity-detail .entity-form__actions [data-role=save]').click();
    await expect(page.locator('#data-entity-detail .entity-form__messages [role=alert]')).toContainText('Votre brouillon est conservé');
    await expect(page.locator('#data-entity-detail .entity-form__form [name=commentaire]')).toHaveValue('Brouillon');
    page.once('dialog', dialog => dialog.dismiss());
    await page.locator('#data-entity-detail-back').click();
    await expect(page.locator('#data-entity-detail .entity-form__form [name=commentaire]')).toHaveValue('Brouillon');
    expect(state.writes).toHaveLength(1);
    page.once('dialog', dialog => dialog.accept());
    await page.locator('#data-entity-detail-back').click();
    await expect(page.locator('#data-entity-detail .entity-form__form')).toBeHidden();
    // Retour leaves the whole consultation now; editor Annuler is its own action.
    await expect(page.locator('#data-view-builder-workspace')).toBeVisible();
    await page.locator(`#data-view-result-body [data-entity-class="${item.classId}"]`).click();
    await page.evaluate(() => {
      const runtime = BusinessRuntime;
      BusinessRuntime = Object.freeze({ ...runtime, can: permission => permission !== 'business.update' });
      document.body.dataset.businessUpdate = 'false';
    });
    await expect(page.locator('#data-entity-detail-edit')).toBeHidden();
    await page.locator('#data-entity-detail-edit').dispatchEvent('click');
    await expect(page.locator('#data-entity-detail .entity-form__form')).toBeHidden();
  });

  test(`${item.label} : modification explicite des relations après sauvegarde`, async ({ context, page }) => {
    const state = await fixture(context);
    await openDetail(page, item);
    await page.locator('#data-entity-detail-edit').click();
    await page.locator('#data-entity-detail .entity-form__form [name=designation]').fill('Première version');
    await page.locator('#data-entity-detail .entity-form__actions [data-role=save]').click();
    await expect(page.locator('#data-entity-detail-read')).toContainText('Première version');
    await page.locator('#data-entity-detail-edit').click();
    const select = name => page.locator(`#data-entity-detail .entity-form__form [name=${name}]`);
    if (['mobilier', 'equipements_mesure', 'reseaux_techniques', 'ouvrages_particuliers',
      'cheminements', 'vegetation', 'amenagements_hydrauliques'].includes(item.classId)) {
      await page.getByRole('button', { name: 'Retirer Désordre 1' }).click();
    }
    if (item.classId === 'vegetation') await select('amenagement_hydraulique_ids').selectOption([]);
    if (item.classId === 'amenagements_hydrauliques') await select('troncon_ids').selectOption([T2]);
    if (item.classId === 'prestations') await select('objects_desordres').selectOption([]);
    await page.locator('#data-entity-detail .entity-form__actions [data-role=save]').click();
    await expect(page.locator('#data-entity-detail .entity-form__form')).toBeHidden();
    expect(state.writes).toHaveLength(2);
    const second = state.writes[1];
    expect(second.headers['if-match']).toBe('"2"');
    expect(second.body).not.toHaveProperty('geometry');
    if (['mobilier', 'equipements_mesure', 'reseaux_techniques', 'ouvrages_particuliers',
      'cheminements', 'vegetation', 'amenagements_hydrauliques'].includes(item.classId)) {
      expect(second.body.desordre_ids).toEqual([]);
      if (item.classId === 'cheminements') expect(second.body.troncon_ids).toEqual([T1, T2]);
    }
    if (item.classId === 'vegetation') {
      expect(second.body.amenagement_hydraulique_ids).toEqual([]);
      expect(second.body).not.toHaveProperty('troncon_ids');
      expect(state.records.vegetation.properties.troncon_ids).toEqual([T1, T2]);
    }
    if (item.classId === 'amenagements_hydrauliques') expect(second.body.troncon_ids).toEqual([T2]);
    if (item.classId === 'prestations') {
      expect(second.body.object_ids.desordres).toEqual([]);
      expect(second.body.object_ids.amenagements_hydrauliques).toEqual([A1]);
      expect(second.body).not.toHaveProperty('intervenant_ids');
      expect(second.body).not.toHaveProperty('troncon_ids');
    }
  });
}

for (const item of CASES) {
  test(`${item.label} : mutation Carte actualise la fiche et la liste Base`, async ({ context, page }) => {
    const state = await fixture(context);
    await openDetail(page, item);
    state.records[item.classId].properties.designation = 'Modifié dans Carte';
    state.records[item.classId].properties.version = 2;
    await page.evaluate(mutation => ResourceMutationController.handleRemoteMutation(mutation), {
      schemaVersion: 1, eventId: randomUUID(), resource: item.resource, action: 'update',
      ids: [item.id], versions: { [item.id]: 2 }, occurredAt: Date.now(),
      originClientId: randomUUID(), originInstanceId: randomUUID(),
    });
    await expect(page.locator('#data-entity-detail-read')).toContainText('Modifié dans Carte');
    await page.locator('#data-entity-detail-back').click();
    await expect(page.locator('#data-view-result-body')).toContainText('Modifié dans Carte');
  });

  test(`${item.label} : réponse tardive ignorée après fermeture du contexte`, async ({ context, page }) => {
    const state = await fixture(context);
    await openList(page, item);
    let release, started;
    const gate = new Promise(resolve => { release = resolve; });
    const requestStarted = new Promise(resolve => { started = resolve; });
    await page.route(`**${item.endpoint}/${item.id}`, async route => {
      started();
      await gate;
      await route.fulfill({ json: state.records[item.classId] });
    });
    await page.locator(`#data-view-result-body [data-entity-class="${item.classId}"]`).click();
    await requestStarted;
    await page.evaluate(() => window.dispatchEvent(new CustomEvent('digues-app:context-closed')));
    const response = page.waitForResponse(candidate => candidate.url().endsWith(`${item.endpoint}/${item.id}`));
    release();
    await response;
    await expect(page.locator('#data-entity-detail')).toBeHidden();
    await expect.poll(() => page.locator('main').getAttribute('data-selected-entity-id')).toBeNull();
    expect(state.writes).toHaveLength(0);
  });
}

test('Prestation : sélecteurs multiples accessibles à la largeur minimale de Base', async ({ context, page }) => {
  await page.setViewportSize({ width: 720, height: 844 });
  await fixture(context);
  await openDetail(page, CASES.find(item => item.classId === 'prestations'));
  await page.locator('#data-entity-detail-edit').click();
  const select = page.locator('#data-entity-detail .entity-form__form [name=objects_desordres]');
  await select.scrollIntoViewIfNeeded();
  await expect(select).toBeVisible();
  const box = await select.boundingBox();
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(721);
  await select.selectOption([]);
  await expect(select).toHaveValues([]);
});
