const { test, expect } = require('@playwright/test');
const { randomUUID } = require('node:crypto');
const { mockBusinessCRS } = require('./mock-business-crs');
const { mockResourceJournal } = require('./mock-resource-journal');
const { photoQualityPolicy } = require('./fixtures/photo-quality-policy');

test.use({ serviceWorkers: 'block' });

const TRONCON = '00000000-0000-4000-8000-000000000401';
const OTHER_TRONCON = '00000000-0000-4000-8000-000000000402';
const TYPE = '00000000-0000-4000-8000-000000000403';
const CASES = [
  { classId: 'mobilier', endpoint: '/api/mobilier', resource: 'mobilier', label: 'Mobilier', id: '00000000-0000-4000-8000-000000000404' },
  { classId: 'equipements_mesure', endpoint: '/api/equipements-mesure', resource: 'equipements-mesure', label: 'Équipements de mesure', id: '00000000-0000-4000-8000-000000000405' },
  { classId: 'reseaux_techniques', endpoint: '/api/reseaux-techniques', resource: 'reseaux-techniques', label: 'Réseaux techniques', id: '00000000-0000-4000-8000-000000000406' },
  { classId: 'ouvrages_particuliers', endpoint: '/api/ouvrages-particuliers', resource: 'ouvrages-particuliers', label: 'Ouvrages particuliers', id: '00000000-0000-4000-8000-000000000407' },
];

function catalog() {
  return { default_root: 'mobilier', roots: CASES.map(item => ({ id: item.classId, label: item.label })),
    relations: [], tables: CASES.map(item => ({ id: item.classId, label: item.label,
      compatible_roots: [item.classId], fields: (item.classId === 'ouvrages_particuliers'
        ? ['designation', 'libelle', 'commentaire', 'date_debut', 'date_fin', 'troncon', 'localisation', 'valid']
        : ['designation', 'type', 'commentaire', 'date_debut', 'troncon', 'localisation', 'valid'])
        .map(field => ({ id: field,
        label: field === 'designation' ? 'Désignation' : field,
        type: 'text', source_class: item.classId, source_field: field,
        navigable: field === 'designation' })) })) };
}

// FORM-CORE-3, niveau B : les refus HTTP conservent le même brouillon Base.
for (const status of [403, 422, 503]) test(`Base Mobilier : HTTP ${status} et état des actions communes`, async ({context,page}) => {
  const item=CASES[0], state=await fixture(context); await openPilot(page,item);
  await page.locator('#data-entity-detail-edit').click();
  const view=page.locator('#data-entity-detail .entity-form');
  await view.locator('[name=designation]').fill('Saisie Base conservée');
  await expect(view).toHaveAttribute('data-state','dirty');
  let attempts=0;
  await context.route(`**${item.endpoint}/${item.id}`,route=>{
    if(route.request().method()!=='PUT') return route.fallback();
    attempts++; return route.fulfill({status,json:{detail:'Refus contrôlé'}});
  });
  await view.locator('[data-role=save]').click();
  await expect(view.locator('.entity-form__messages [role=alert]')).toContainText('Refus contrôlé');
  await expect(view.locator('[name=designation]')).toHaveValue('Saisie Base conservée');
  await expect(view).toHaveAttribute('data-state', status===403?'forbidden':status===503?'uncertain':'error');
  if(status===403) {
    await expect(view.locator('[data-role=save]')).toBeHidden();
    await expect(view.locator('[data-role=cancel]')).toHaveText('Annuler');
    page.once('dialog',dialog=>dialog.dismiss()); await view.locator('[data-role=cancel]').click();
    await expect(view.locator('[name=designation]')).toHaveValue('Saisie Base conservée');
  }
  else if(status===503) {
    await expect(view.locator('[data-role=save]')).toBeDisabled();
    await expect(view.locator('[data-role=close]')).toBeDisabled();
  } else await expect(view.locator('[data-role=save]')).toBeEnabled();
  if(status!==422) {
    await view.locator('form').evaluate(form=>form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
    expect(attempts).toBe(1);
  }
  expect(state.records.mobilier.properties.version).toBe(1);
});

test('Base : ouvrir la fiche du parent respecte le brouillon et conserve la navigation Base', async ({context,page}) => {
  const item = CASES[0];
  const state = await fixture(context);
  await context.route(`**/api/troncons/${TRONCON}`, route => route.fulfill({json:{
    type:'Feature', geometry:{type:'LineString',coordinates:[[2.1,48.2],[2.2,48.3]]},
    properties:{id:TRONCON,version:1,libelle:'Parent dans Base',valid:true},
  }}));
  await openPilot(page,item);
  await page.locator('#data-entity-detail-edit').click();
  const view = page.locator('#data-entity-detail .entity-form');
  await view.locator('[name=designation]').fill('Brouillon Base');
  const link = view.locator('[data-role=open-relation]');
  await expect(link).toBeVisible();
  page.once('dialog', dialog => dialog.dismiss());
  await link.click();
  await expect(view.locator('[name=designation]')).toHaveValue('Brouillon Base');
  await expect(view.locator('[name=troncon_id]')).toHaveValue(TRONCON);
  page.once('dialog', dialog => dialog.accept());
  await link.click();
  await expect(page.locator('#data-entity-detail .entity-form__title')).toContainText('Parent dans Base');
  await expect(page).toHaveURL(/\/base/);
  expect(state.writes).toHaveLength(0);
});

async function fixture(context, { conflict = false, withHeritage = false } = {}) {
  const records = Object.fromEntries(CASES.map(item => [item.classId, { type: 'Feature', id: item.id,
    geometry: item.classId === 'reseaux_techniques'
      ? { type: 'LineString', coordinates: [[2.1, 48.2], [2.2, 48.3]] }
      : item.classId === 'ouvrages_particuliers'
        ? { type: 'Polygon', coordinates: [[[2.1, 48.2], [2.2, 48.2], [2.1, 48.3], [2.1, 48.2]]] }
        : { type: 'Point', coordinates: [2.1, 48.2] }, properties: {
      id: item.id, version: 1, designation: `${item.label} initial`, commentaire: 'Initial',
      type_id: TYPE, type_libelle: 'Type pilote', date_debut: '2026-10-09',
      libelle: item.classId === 'ouvrages_particuliers' ? 'Ouvrage initial' : null,
      date_fin: item.classId === 'ouvrages_particuliers' ? '2026-10-10' : null,
      source_class: item.classId === 'ouvrages_particuliers' ? 'OuvrageSourceV2' : null,
      type_source_id: item.classId === 'ouvrages_particuliers' ? 'RefHistorique:1' : null,
      desordre_ids: [],
      troncon_id: TRONCON, troncon_libelle: 'Tronçon initial', valid: true,
    } }]));
  const writes = [];
  const viewQueries = [];
  await context.route('**/api/**', async route => {
    if (await mockBusinessCRS(route) || await mockResourceJournal(route)) return;
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const send = (value, status = 200) => route.fulfill({ status, json: value });
    if (path === '/api/config') return send({ legacy_single_database_enabled: true,
      show_uuid: false, photo_quality_policy: photoQualityPolicy() });
    if (path === '/api/systemes-endiguement') return send({ systemes: withHeritage ? [{
      id: '00000000-0000-4000-8000-000000000408', libelle: 'Système test', digues: [{
        id: '00000000-0000-4000-8000-000000000409', libelle: 'Digue test', troncons: [
          { id: TRONCON, libelle: 'Tronçon initial' },
          { id: OTHER_TRONCON, libelle: 'Tronçon alternatif' },
        ],
      }],
    }] : [] });
    if (path === '/api/data/desordres/query') return send({ columns: [], rows: [], total: 0,
      offset: 0, has_next: false });
    if (path === '/api/data/view-catalog') return send(catalog());
    if (path === '/api/data/view-query') {
      const body = request.postDataJSON();
      viewQueries.push(body);
      const item = CASES.find(candidate => candidate.classId === body.root);
      if (!item) return send({ detail: 'Racine inconnue' }, 422);
      const record = records[item.classId];
      const visible = !(body.hidden_troncon_ids || []).includes(record.properties.troncon_id);
      const values = body.fields.map(field => ({
        designation: record.properties.designation, type: record.properties.type_libelle,
        libelle: record.properties.libelle, commentaire: record.properties.commentaire,
        date_debut: record.properties.date_debut, date_fin: record.properties.date_fin,
        troncon: record.properties.troncon_libelle,
        localisation: record.geometry?.type || 'Non localisé', valid: 'Oui',
      })[field.field]);
      const rows = visible && body.offset === 0 ? [{ values, entities: [{ class: item.classId,
        id: item.id, attributes: { designation: record.properties.designation } }] }] : [];
      return send({ root: body.root, granularity: body.root, columns: body.fields.map(field => ({
        key: `${body.root}.${field.field}`, label: `${item.label} — ${field.field}`,
        source_class: body.root, source_field: field.field, navigable: field.field === 'designation',
      })), rows, total: visible ? 1 : 0, offset: body.offset, has_next: false });
    }
    if (path === '/api/troncons/options') return send({ troncons: [
      { id: TRONCON, libelle: 'Tronçon initial', digue_libelle: 'Digue A', valid: true },
      { id: OTHER_TRONCON, libelle: 'Tronçon alternatif', digue_libelle: 'Digue B', valid: true },
    ] });
    if (path === '/api/desordres/selection') return send({ items: [], missing_ids: [] });
    if (path === '/api/desordres/search') return send({
      items: [], next_cursor: null, has_more: false,
    });
    if (path.startsWith('/api/referentiels/types-')) return send({ references: [
      { id: TYPE, libelle: 'Type pilote', valid: true },
    ] });
    const item = CASES.find(candidate => path === `${candidate.endpoint}/${candidate.id}`);
    if (item) {
      if (request.method() === 'PUT') {
        const body = request.postDataJSON();
        writes.push({ classId: item.classId, headers: request.headers(), body });
        if (conflict) return send({ detail: { code: 'version-conflict', message: 'Version dépassée' } }, 409);
        const record = records[item.classId];
        record.properties = { ...record.properties, ...body, version: record.properties.version + 1,
          troncon_libelle: body.troncon_id === OTHER_TRONCON ? 'Tronçon alternatif' : 'Tronçon initial' };
        record.mutation = { schemaVersion: 1, eventId: randomUUID(), resource: item.resource,
          action: 'update', ids: [item.id], versions: { [item.id]: record.properties.version },
          occurredAt: Date.now(), originClientId: randomUUID(), originInstanceId: randomUUID() };
      }
      return send(records[item.classId]);
    }
    return send({});
  });
  return { records, writes, viewQueries };
}

async function openPilot(page, item, { openDetail = true } = {}) {
  await page.goto('/base');
  await expect(page.locator('#identity-gate')).toBeHidden();
  await expect(page.locator('#base-summary-crs')).not.toHaveText('');
  const dismissNotification = page.getByRole('button', { name: 'Fermer le message' });
  if (await dismissNotification.isVisible()) await dismissNotification.click();
  const table = page.locator('#data-view-table');
  if (!await table.isVisible()) await page.locator('#toggle-views').click();
  while (await page.locator('#data-view-field-list button').count()) {
    await page.locator('#data-view-field-list button').first().click();
  }
  await expect(table).toBeVisible();
  await expect(table.locator('option').first()).toBeAttached();
  await table.selectOption(item.classId);
  await page.locator('#data-view-field').selectOption('designation');
  await page.locator('#data-view-add-field').click();
  await expect(page.locator(`#data-view-result-body [data-entity-class="${item.classId}"]`)).toHaveCount(1);
  if (!openDetail) return;
  await page.locator(`#data-view-result-body [data-entity-class="${item.classId}"]`).click();
  await expect(page.locator('#data-entity-detail-title')).toContainText(item.label);
}

// MIG-GEOM-01 B: actual Base renderer, synthetic API, no write or migration.
for (const item of CASES.filter(item => ['equipements_mesure', 'ouvrages_particuliers'].includes(item.classId))) {
  test(`${item.label} : géométrie historique invalide consultable dans Base`, async ({ context, page }) => {
    const state = await fixture(context);
    const record = state.records[item.classId];
    record.properties.valid = false;
    record.properties.invalidity_causes = ['INVALID_GEOMETRY', 'UNEXPECTED_GEOMETRY'];
    record.geometry = { type: 'LineString', coordinates: [[2.1, 48.2], [2.1, 48.2]] };
    await openPilot(page, item);
    const read = page.locator('#data-entity-detail-read');
    await expect(read.locator('[data-invalidity-causes]')).toHaveText('Géométrie invalide · Type de géométrie incompatible');
    await expect(read).toContainText(record.properties.designation);
    await expect(read).not.toContainText('INVALID_GEOMETRY');
    expect(state.writes).toEqual([]);
  });
}

for (const item of CASES) {
  test(`${item.label} : liste, fiche, PUT versionné et retour aux résultats`, async ({ context, page }) => {
    const state = await fixture(context);
    await openPilot(page, item);
    if (item.classId === 'ouvrages_particuliers') {
      await expect(page.locator('#data-entity-detail-read')).toContainText('Type pilote');
      await expect(page.locator('#data-entity-detail-read')).not.toContainText('RefHistorique:1');
    } else {
      await expect(page.locator('#data-entity-detail-read')).toContainText('Type pilote');
    }
    await expect(page.locator('#data-entity-detail-read')).toContainText(
      item.classId === 'reseaux_techniques' ? 'LineString'
        : item.classId === 'ouvrages_particuliers' ? 'Polygon' : 'Point 48.200000, 2.100000');
    await page.evaluate(() => {
      window.__publishedPilotMutations = [];
      const original = ResourceMutationController.publishMutation.bind(ResourceMutationController);
      ResourceMutationController.publishMutation = (mutation, options) => {
        window.__publishedPilotMutations.push(mutation);
        return original(mutation, options);
      };
    });
    await page.locator('#data-entity-detail-edit').click();
    if (['mobilier', 'equipements_mesure'].includes(item.classId)) {
      const view = page.locator('#data-entity-detail .entity-form');
      await expect(view).toHaveCount(1);
      await expect(view.locator('.entity-form__header')).toHaveCount(1);
      await expect(page.locator('.data-entity-detail-header')).toBeHidden();
      expect(await view.locator('[data-section]').evaluateAll(nodes => nodes.map(node => node.dataset.section)))
        .toEqual(['identification', 'classification', 'attachments', 'geometry', 'actions']);
      await expect(view.getByRole('tab', {name:'Général', exact:true})).toHaveAttribute('aria-selected','true');
      await expect(view.getByRole('tab', {name:'Observations', exact:true})).toBeEnabled();
      await expect(view.locator('[data-role=place-point]')).toBeHidden();
    }
    if (item.classId === 'ouvrages_particuliers') {
      await expect(page.locator('#data-entity-detail form:visible [name=source_class]')).toHaveCount(0);
      await expect(page.locator('#data-entity-detail form:visible [name=type_source_id]')).toHaveCount(0);
      await expect(page.locator('#data-entity-detail form:visible [name=desordre_id]')).toHaveCount(0);
    }
    await page.locator('#data-entity-detail form:visible [name=designation]').fill(`${item.label} modifié`);
    await page.locator('#data-entity-detail form:visible [name=troncon_id]').selectOption(OTHER_TRONCON);
    await page.locator('#data-entity-detail button[type=submit]:visible').click();
    await expect(page.locator('#data-entity-detail-read')).toContainText(`${item.label} modifié`);
    expect(state.writes).toHaveLength(1);
    expect(state.writes[0].headers['if-match']).toBe('"1"');
    expect(state.writes[0].body).not.toHaveProperty('geometry');
    await expect.poll(() => page.evaluate(() => window.__publishedPilotMutations.length)).toBe(1);
    expect(await page.evaluate(() => window.__publishedPilotMutations[0].resource)).toBe(item.resource);
    expect(state.records[item.classId].geometry).toBeTruthy();
    expect(state.records[item.classId].geometry.type).toBe(
      item.classId === 'reseaux_techniques' ? 'LineString'
        : item.classId === 'ouvrages_particuliers' ? 'Polygon' : 'Point');
    await page.locator('#data-entity-detail-back').click();
    await expect(page.locator('#data-view-result-body')).toContainText(`${item.label} modifié`);
    expect(state.viewQueries.length).toBeGreaterThanOrEqual(2);
    await expect(page.locator('#data-view-table')).toHaveValue(item.classId);
    await expect(page.locator('#data-view-field-list')).toContainText('Désignation');
    await expect(page.locator('#data-view-builder-workspace [data-role=new]')).toHaveCount(0);
  });
}

test('Ouvrage particulier : un PUT qui sort du filtre Patrimoine retire la ligne sans changer la vue', async ({ context, page }) => {
  const item = CASES[3];
  const state = await fixture(context, { withHeritage: true });
  await openPilot(page, item, { openDetail: false });
  await page.locator('#toggle-heritage').click();
  await page.getByRole('button', { name: 'Déplier Système test' }).click();
  await page.getByRole('button', { name: 'Déplier Digue test' }).click();
  await page.getByRole('checkbox', { name: 'Afficher ou masquer Tronçon alternatif' }).click();
  await expect(page.locator('#data-view-result-body [data-entity-class="ouvrages_particuliers"]')).toHaveCount(1);
  await page.locator('#close-heritage').click();
  await page.locator('#data-view-result-body [data-entity-class="ouvrages_particuliers"]').click();
  await page.locator('#data-entity-detail-edit').click();
  await page.locator('#data-entity-detail form:visible [name=troncon_id]').selectOption(OTHER_TRONCON);
  await page.locator('#data-entity-detail button[type=submit]:visible').click();
  await expect(page.locator('#data-entity-detail-read')).toContainText('Tronçon alternatif');
  await page.locator('#data-entity-detail-back').click();
  await expect(page.locator('#data-view-result-body [data-entity-class="ouvrages_particuliers"]')).toHaveCount(0);
  await expect(page.locator('#data-view-result-status')).toHaveText('0 ligne(s) chargée(s) sur 0');
  await expect(page.locator('#data-view-table')).toHaveValue(item.classId);
  await expect(page.locator('#data-view-field-list')).toContainText('Désignation');
  expect(state.viewQueries.at(-1).hidden_troncon_ids).toEqual([OTHER_TRONCON]);
  expect(state.writes).toHaveLength(1);
});

test('Ouvrage particulier sans géométrie : fiche lisible et édition attributaire', async ({ context, page }) => {
  const item = CASES[3]; const state = await fixture(context);
  state.records[item.classId].geometry = null;
  await openPilot(page, item);
  await expect(page.locator('#data-entity-detail-read')).toContainText('Non localisé');
  await page.locator('#data-entity-detail-edit').click();
  await page.locator('#data-entity-detail form:visible [name=libelle]').fill('Sans géométrie');
  await page.locator('#data-entity-detail button[type=submit]:visible').click();
  await expect(page.locator('#data-entity-detail-read')).toContainText('Sans géométrie');
  expect(state.writes).toHaveLength(1);
  expect(state.writes[0].body).not.toHaveProperty('geometry');
});

for (const item of [CASES[0], CASES[2], CASES[3]]) {
  test(`${item.label} : conflit conserve le brouillon et le retour demande confirmation`, async ({ context, page }) => {
    const state = await fixture(context, { conflict: true });
    await openPilot(page, item);
    await page.locator('#data-entity-detail-edit').click();
    await page.locator('#data-entity-detail form:visible [name=commentaire]').fill('Brouillon conservé');
    await page.locator('#data-entity-detail button[type=submit]:visible').click();
    await expect(page.locator('#data-entity-detail .entity-form__messages [role=alert]:visible, #data-entity-detail .entity-form [role=alert]:visible')).toContainText('Votre brouillon est conservé');
    await expect(page.locator('#data-entity-detail form:visible [name=commentaire]')).toHaveValue('Brouillon conservé');
    page.once('dialog', dialog => dialog.dismiss());
    await page.locator('#data-entity-detail-back').click();
    await expect(page.locator('#data-entity-detail form:visible [name=commentaire]')).toHaveValue('Brouillon conservé');
    expect(state.writes).toHaveLength(1);
  });
}

for (const item of [CASES[1], CASES[2], CASES[3]]) {
  test(`${item.label} : consultation en lecture seule sans démarrer une édition`, async ({ context, page }) => {
    await fixture(context);
    await openPilot(page, item);
    await page.evaluate(() => {
      const runtime = BusinessRuntime;
      BusinessRuntime = Object.freeze({ ...runtime, can: permission => permission !== 'business.update' });
      document.body.dataset.businessUpdate = 'false';
    });
    await expect(page.locator('#data-entity-detail-edit')).toBeHidden();
    await page.evaluate(() => DataEntityDetail && document.querySelector('#data-entity-detail-edit').click());
    await expect(page.locator('#data-entity-detail .entity-form__form')).toBeHidden();
  });
}

for (const item of [CASES[0], CASES[2], CASES[3]]) {
  test(`${item.label} : une réponse tardive ne rouvre pas la fiche après fermeture du contexte`, async ({ context, page }) => {
    const state = await fixture(context);
    await openPilot(page, item, { openDetail: false });
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
    await page.evaluate(() => new Promise(resolve => setTimeout(resolve, 0)));
    await expect(page.locator('#data-entity-detail')).toBeHidden();
    await expect.poll(() => page.locator('main').getAttribute('data-selected-entity-id')).toBeNull();
    expect(state.writes).toHaveLength(0);
  });
}

for (const item of [CASES[1], CASES[2], CASES[3]]) {
  test(`${item.label} : une mutation Carte invalide la fiche et la liste Base`, async ({ context, page }) => {
    const state = await fixture(context);
    await openPilot(page, item);
    state.records[item.classId].properties.designation = 'Capteur modifié dans Carte';
    state.records[item.classId].properties.version = 2;
    await page.evaluate(mutation => ResourceMutationController.handleRemoteMutation(mutation), {
      schemaVersion: 1, eventId: randomUUID(), resource: item.resource, action: 'update',
      ids: [item.id], versions: { [item.id]: 2 }, occurredAt: Date.now(),
      originClientId: randomUUID(), originInstanceId: randomUUID(),
    });
    await expect(page.locator('#data-entity-detail-read')).toContainText('Capteur modifié dans Carte');
    await page.locator('#data-entity-detail-back').click();
    await expect(page.locator('#data-view-result-body')).toContainText('Capteur modifié dans Carte');
  });
}
