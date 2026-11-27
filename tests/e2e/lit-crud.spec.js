// Niveau B : vrais contrôleurs DOM/Leaflet et transport intercepté, < 4 min.
// Chaque scénario possède ses propres objets mutables ; SQL, OCC et isolation
// réelles sont qualifiés séparément par test_lit_crud_postgres.py.
const { test, expect } = require('@playwright/test');
const { randomUUID } = require('node:crypto');
const { mockBusinessCRS } = require('./mock-business-crs');
const { mockResourceJournal } = require('./mock-resource-journal');
const { geometryCapabilities } = require('./mock-geometry-capabilities');
const { photoQualityPolicy } = require('./fixtures/photo-quality-policy');
test.use({ serviceWorkers: 'block' });
const LIT = '00000000-0000-4000-8000-000000000801';
const MORPH = '00000000-0000-4000-8000-000000000802';
const DIGUES = ['00000000-0000-4000-8000-000000000803', '00000000-0000-4000-8000-000000000804'];
const ZONE = '00000000-0000-4000-8000-000000000805';
const line = { type: 'LineString', coordinates: [[2.34, 48.84], [2.35, 48.84]] };
const empty = () => ({ type: 'FeatureCollection', features: [] });
const spec = classId => classId === 'troncons_lit'
  ? { resource: 'troncons-lit', label: 'Tronçons de lit', types: ['LineString', 'Polygon'] }
  : { resource: 'elements-morphologiques-lit', label: 'Éléments morphologiques du lit', types: ['Point', 'LineString', 'Polygon'] };
function catalog() {
  return { default_root: 'troncons_lit', roots: ['troncons_lit', 'elements_morphologiques_lit'].map(id => ({ id, label: spec(id).label })),
    relations: [], tables: ['troncons_lit', 'elements_morphologiques_lit'].map(id => ({ id, label: spec(id).label,
      compatible_roots: [id], fields: [{ id: 'designation', label: 'Désignation', type: 'text',
        source_class: id, source_field: 'designation', navigable: true }] })) };
}
async function fixture(context, { standalone = false } = {}) {
  if (standalone) await context.addInitScript(() => {
    const real = window.matchMedia.bind(window);
    window.matchMedia = query => query === '(display-mode: standalone)'
      ? { matches: true, media: query, addEventListener() {}, removeEventListener() {} } : real(query);
  });
  const state = { writes: [], fail: null, delayDetail: null, records: {
    'troncons-lit': [{ type: 'Feature', geometry: line, properties: { id: LIT, version: 1,
      designation: 'Lit initial', valid: true, digue_ids: DIGUES, largeurs_lit: [], pentes_lit: [],
      domanialites_lit: [], regimes_ecoulement_lit: [], occupations_riveraines_lit: [] } }],
    'elements-morphologiques-lit': [{ type: 'Feature', geometry: { type: 'Point', coordinates: [2.34, 48.84] },
      properties: { id: MORPH, version: 1, designation: 'Morphologie initiale', valid: true,
        type_morphologie: 'INDETERMINE', longueur: '0', largeur: '1.25', vegetalise: false,
        rattache_berge: null, segment_id: null, type_zone_id: null } }],
  } };
  function mutation(resource, feature, action, headers) {
    return { schemaVersion: 1, eventId: randomUUID(), resource, action, ids: [feature.properties.id],
      versions: { [feature.properties.id]: feature.properties.version }, occurredAt: Date.now(),
      originClientId: headers['x-client-id'] || randomUUID(), originInstanceId: headers['x-instance-id'] || randomUUID(),
      aspects: ['attributes', 'geometry', 'relations'] };
  }
  await context.route('**/api/**', async route => {
    if (await mockBusinessCRS(route) || await mockResourceJournal(route)) return;
    const request = route.request(), path = new URL(request.url()).pathname;
    const send = (value, status = 200) => route.fulfill({ status, json: value });
    if (path === '/api/config') return send({ legacy_single_database_enabled: true, show_uuid: false,
      photo_quality_policy: photoQualityPolicy(), build_id: 'lit-crud' });
    if (path === '/api/systemes-endiguement') return send({ systemes: [] });
    if (path === '/api/troncons/options') return send({ troncons: [] });
    if (path === '/api/troncons-lit/options') return send({ digues: DIGUES.map((id,i) => ({ id, libelle: `Digue ${i+1}`, valid: true })),
      troncons_lit: state.records['troncons-lit'].map(row => row.properties) });
    if (path.startsWith('/api/referentiels/')) return send({ references: [{ id: ZONE, libelle: 'Type conservé', valid: true }], types: [], categories: [] });
    if (path.startsWith('/api/geometry-capabilities/')) {
      const resource = path.split('/').pop();
      return send(geometryCapabilities({ resource, status: 'approved', requiredForValid: true, allowMissingWhenInvalid: true,
        allowedTypes: resource === 'troncons-lit' ? ['LineString', 'Polygon'] : ['Point', 'LineString', 'Polygon'] }));
    }
    if (path === '/api/data/view-catalog') return send(catalog());
    if (path === '/api/data/desordres/query') return send({ columns: [], rows: [], total: 0, offset: 0, has_next: false });
    if (path === '/api/data/view-query') {
      const body = request.postDataJSON(), rows = state.records[spec(body.root).resource];
      return send({ root: body.root, granularity: body.root, columns: body.fields.map(field => ({ key: `${body.root}.${field.field}`,
        label: 'Désignation', source_class: body.root, source_field: field.field, navigable: true })),
        rows: rows.map(row => ({ values: body.fields.map(() => row.properties.designation), entities: [{ class: body.root,
          id: row.properties.id, attributes: { designation: row.properties.designation } }] })), total: rows.length, offset: 0, has_next: false });
    }
    const match = path.match(/^\/api\/(troncons-lit|elements-morphologiques-lit)(?:\/([^/]+))?(?:\/caracteristiques\/([^/]+)(?:\/([^/]+))?)?$/);
    if (match) {
      const [, resource, id, family, childId] = match;
      let feature = state.records[resource].find(row => row.properties.id === id);
      if (request.method() === 'GET') {
        if (id && state.delayDetail) await state.delayDetail;
        return send(id ? feature : { type: 'FeatureCollection', features: state.records[resource] });
      }
      const body = request.postDataJSON(), headers = request.headers();
      state.writes.push({ resource, id, family, childId, body, headers, method: request.method() });
      if (state.fail) return send({ detail: { code: state.fail === 409 ? 'version-conflict' : 'validation-error', message: state.fail === 409 ? 'Version dépassée' : 'Géométrie refusée' } }, state.fail);
      if (family) {
        const key = { largeurs: 'largeurs_lit', pentes: 'pentes_lit', domanialites: 'domanialites_lit',
          'regimes-ecoulement': 'regimes_ecoulement_lit', 'occupations-riveraines': 'occupations_riveraines_lit' }[family];
        const original = feature.properties[key].find(row => row.id === childId);
        if (original) Object.assign(original, body); else feature.properties[key].push({ ...body, segment_id: id });
        feature.properties.version++;
      } else if (!id) {
        feature = { type: 'Feature', geometry: body.geometry || null,
          properties: { largeurs_lit: [], pentes_lit: [], domanialites_lit: [], regimes_ecoulement_lit: [], occupations_riveraines_lit: [],
            ...body, version: 1 } };
        delete feature.properties.geometry; state.records[resource].push(feature);
      } else {
        feature.properties = { ...feature.properties, ...body, version: feature.properties.version + 1 };
        if (Object.hasOwn(body, 'geometry')) feature.geometry = body.geometry;
        delete feature.properties.geometry;
      }
      feature.mutation = mutation(resource, feature, id ? 'update' : 'create', headers);
      return send(feature);
    }
    if (path === '/api/desordres/selection') return send({ items: [], missing_ids: [] });
    if (path === '/api/desordres/search') return send({ items: [], next_cursor: null, has_more: false });
    if (['/api/troncons', '/api/desordres', '/api/territoire-administratif', '/api/mobilier', '/api/equipements-mesure',
      '/api/reseaux-techniques', '/api/ouvrages-particuliers', '/api/vegetation', '/api/cheminements', '/api/amenagements-hydrauliques', '/api/prestations'].includes(path)) return send(empty());
    return send({});
  });
  return state;
}
async function create(page, classId) {
  await page.goto('/carte');
  await page.waitForFunction(() => typeof mapViewportReady !== 'undefined' && mapViewportReady);
  await page.locator('#toggle-create-menu').click();
  await page.locator(`[data-create-type="${spec(classId).resource}"]`).click();
  const panel = page.locator(`#${spec(classId).resource}-editor-panel`);
  await expect(panel.locator('[data-role="status"]')).toContainText('Renseignez');
  await expect(panel.locator('[data-gps="start"]')).toHaveCount(0);
  await expect(panel.locator('[data-role="observations-tab"]')).toHaveCount(0);
  return panel;
}
async function draw(page, panel, type) {
  await panel.locator('[name="geometry_type"]').selectOption(type);
  await panel.locator('[data-role="draw"]').click();
  // Déclencher les événements Leaflet exerce le vrai moteur commun et ses couches.
  await page.evaluate(shape => {
    const points = [[2.34,48.84],[2.35,48.84],[2.35,48.85]];
    for (const [lng,lat] of points.slice(0, shape === 'Point' ? 1 : shape === 'LineString' ? 2 : 3)) map.fire('click', { latlng: L.latLng(lat,lng) });
  }, type);
  if (type !== 'Point') {
    const shared = page.locator('#confirm-editor-map-interaction');
    if (await shared.isVisible()) await shared.click();
    else await panel.locator('[data-role="confirm"]').click();
  }
}
for (const type of ['LineString', 'Polygon']) test(`Lit ${type} : création et relations N:N explicites`, async ({ context, page }) => {
  const state = await fixture(context);
  const panel = await create(page, 'troncons_lit');
  await expect(panel.locator('[name="geometry_type"] option')).toHaveText(['LineString', 'Polygon']);
  await panel.locator('[name="designation"]').fill(`Lit ${type}`);
  await panel.locator('[name="digue_ids"]').selectOption(DIGUES);
  await draw(page, panel, type);
  await panel.locator('[data-role="save"]').click();
  await expect(panel.locator('[data-role="status"]')).toContainText('enregistré');
  expect(state.writes[0].body.geometry.type).toBe(type);
  expect(state.writes[0].body.digue_ids).toEqual(DIGUES);
  expect(state.writes[0].headers['operation-id']).toBeTruthy();
  expect(state.writes[0].headers['geometry-contract-version']).toBe('a'.repeat(64));
});
for (const [subtype,type] of [['ILE','Polygon'],['BANC','LineString'],['ATTERISSEMENT','Point'],['INDETERMINE',null]]) {
  test(`Morphologie ${subtype} : ${type || 'absence invalide'}, parent facultatif et conservation`, async ({ context, page }) => {
    const state = await fixture(context, { standalone: subtype === 'INDETERMINE' });
    if (subtype === 'INDETERMINE') await page.setViewportSize({ width: 390, height: 844 });
    const panel = await create(page, 'elements_morphologiques_lit');
    await panel.locator('[name="type_morphologie"]').selectOption(subtype);
    await panel.locator('[name="longueur"]').fill('0');
    await panel.locator('[name="vegetalise"]').selectOption('false');
    if (subtype === 'ATTERISSEMENT') await panel.locator('[name="type_zone_id"]').selectOption(ZONE);
    if (type) await draw(page, panel, type);
    else {
      await panel.locator('[name="valid"]').uncheck();
      await page.setViewportSize({ width: 844, height: 390 });
      await expect(panel.locator('[name="type_morphologie"]')).toHaveValue(subtype);
    }
    await panel.locator('[data-role="save"]').click();
    await expect(panel.locator('[data-role="status"]')).toContainText('enregistré');
    expect(state.writes[0].body.type_morphologie).toBe(subtype);
    expect(state.writes[0].body.longueur).toBe('0'); expect(state.writes[0].body.vegetalise).toBe(false);
    expect(state.writes[0].body.segment_id).toBeNull();
    expect(state.writes[0].body.geometry?.type || null).toBe(type);
    if (!type) expect(state.writes[0].body.valid).toBe(false);
  });
}
async function openBase(page, classId = 'troncons_lit') {
  await page.goto('/base');
  await expect(page.locator('#identity-gate')).toBeHidden();
  await expect(page.locator('#base-summary-crs')).not.toHaveText('');
  await page.evaluate(({ classId, id }) => document.querySelector('main').dispatchEvent(new CustomEvent('digues-app:open-entity',
    { detail: { entityClass: classId, entityId: id, source: 'views' } })), { classId, id: classId === 'troncons_lit' ? LIT : MORPH });
  await expect(page.locator('#data-entity-detail-read')).toBeVisible();
  await expect(page.locator('#data-entity-detail-title')).toContainText(classId === 'troncons_lit' ? 'Lit initial' : 'Morphologie initiale');
}
test('Base : deux largeurs identiques, modification identitaire et PUT parent sans géométrie', async ({ context, page }) => {
  const state = await fixture(context);
  await openBase(page);
  for (let i=0; i<2; i++) {
    await page.getByRole('button', { name: 'Ajouter — Largeur moyenne', exact: true }).click();
    const form = page.locator('[data-role="lit-characteristic-form"]');
    await form.locator('[name="largeur_moyenne"]').fill('1.12345678901234567890');
    await form.locator('[name="pr_debut"]').fill('0');
    await form.getByRole('button', { name: 'Ajouter la caractéristique', exact: true }).click();
    await expect(form).toHaveCount(0);
  }
  const rows = state.records['troncons-lit'][0].properties.largeurs_lit;
  expect(rows).toHaveLength(2); expect(rows[0].id).not.toBe(rows[1].id);
  expect(rows[0].largeur_moyenne).toBe('1.12345678901234567890');
  expect(state.writes.map(row => row.headers['if-match'])).toEqual(['"1"', '"2"']);
  await page.locator('[data-family="largeurs"] button').filter({ hasText: 'Modifier' }).first().click();
  const child = page.locator('[data-role="lit-characteristic-form"]');
  await child.locator('[name="pr_debut"]').fill('');
  await child.getByRole('button', { name: 'Enregistrer la caractéristique' }).click();
  await expect(child).toHaveCount(0);
  expect(state.writes[2].body.pr_debut).toBeNull();
  await page.locator('#data-entity-detail-edit').click();
  await page.locator('#data-entity-detail .entity-form__form [name="designation"]').fill('Lit modifié');
  await page.locator('#data-entity-detail .entity-form__actions [data-role=save]').click();
  await expect(page.locator('#data-entity-detail-title')).toContainText('Lit modifié');
  expect(state.writes.at(-1).body).not.toHaveProperty('geometry');
  expect(state.writes.at(-1).headers['if-match']).toBe('"4"');
  expect(state.writes.at(-1).headers['operation-id']).toBeTruthy();
  await expect(page.locator('#data-entity-detail-map')).toBeVisible();
  await expect(page.locator('#data-entity-detail-tabs')).toBeHidden();
});
test('Carte : 422, 409 et transport incertain conservent saisie, géométrie et identifiant de rejeu', async ({ context, page }) => {
  const state = await fixture(context);
  await page.goto(`/carte?entity=elements_morphologiques_lit&id=${MORPH}`);
  const panel = page.locator('#elements-morphologiques-lit-editor-panel');
  await expect(panel.locator('[data-role="status"]')).toHaveText('État relu depuis le serveur.');
  await panel.locator('[name="type_morphologie"]').selectOption('ILE');
  await panel.locator('[name="designation"]').fill('Brouillon protégé');
  await draw(page, panel, 'Point');
  for (const failure of [422,409,500]) {
    state.fail = failure;
    await panel.locator('[data-role="save"]').click();
    await expect.poll(() => state.writes.length).toBe([422,409,500].indexOf(failure)+1);
    await expect(panel.locator('[data-role="status"]')).toContainText(failure === 500
      ? 'Résultat non confirmé' : failure === 409 ? 'brouillon est conservé' : 'Géométrie refusée');
    await expect(panel.locator('[name="designation"]')).toHaveValue('Brouillon protégé');
    expect(await page.evaluate(()=>{const values=[]; map.eachLayer(layer=>{
      if(layer instanceof L.Marker && layer.options.draggable) values.push(layer.dragging.enabled());
    }); return values;})).toEqual([failure !== 500]);
  }
  expect(new Set(state.writes.map(write => write.headers['operation-id'])).size).toBe(1);
  const operationId = state.writes.at(-1).headers['operation-id'];
  state.fail = null;
  await panel.locator('[data-role="save"]').click();
  await expect(panel.locator('[data-role="status"]')).toContainText('enregistré');
  expect(state.writes.at(-1).headers['operation-id']).toBe(operationId);
  expect(state.writes.at(-1).id).toBe(MORPH);
  expect(state.writes.at(-1).headers['if-match']).toBe('"1"');
  expect(state.writes.at(-1).body.geometry.type).toBe('Point');
});

test('Carte : une réponse tardive ne rouvre pas la fiche fermée ; annulation nettoie le dessin', async ({ context, page }) => {
  const state = await fixture(context);
  let release;
  state.delayDetail = new Promise(resolve => { release = resolve; });
  await page.goto(`/carte?entity=troncons_lit&id=${LIT}`);
  const panel = page.locator('#troncons-lit-editor-panel');
  await expect(panel).toBeVisible();
  await expect(panel.locator('[data-role="status"]')).toContainText('Chargement');
  await panel.locator('[data-role="cancel"]').click();
  await expect(page.locator('#editor-panel')).toBeHidden();
  const received = page.waitForResponse(response => new URL(response.url()).pathname === `/api/troncons-lit/${LIT}`);
  release(); await received;
  await expect(page.locator('#editor-panel')).toBeHidden();
  // Le parcours par URL publie son avis d’ouverture annulée : fermer ce
  // message par son contrôle avant de commencer une interaction distincte.
  await expect(page.locator('#status')).toContainText('ouverture annulée');
  await page.getByRole('button', { name: 'Fermer le message', exact: true }).click();
  state.delayDetail = null;
  await page.locator('#toggle-create-menu').click();
  await page.locator('[data-create-type="troncons-lit"]').click();
  await expect(panel.locator('[data-role="status"]')).toContainText('Renseignez');
  await page.setViewportSize({ width: 390, height: 844 });
  const count = () => page.evaluate(() => map._events.click?.length || 0);
  const before = await count();
  await panel.locator('[data-role="draw"]').click();
  await expect.poll(count).toBe(before + 1);
  await page.locator('#cancel-editor-map-interaction').click();
  await expect.poll(count).toBe(before);
  await expect(panel).toBeVisible();
  await panel.locator('[data-role="cancel"]').click();
  await expect(page.locator('#editor-panel')).toBeHidden();
  expect(state.writes).toHaveLength(0);
});

test('Base : une morphologie historique invalide garde sa géométrie et ses repérages', async ({ context, page }) => {
  const state = await fixture(context);
  const feature = state.records['elements-morphologiques-lit'][0];
  const originalGeometry = { type: 'MultiPoint', coordinates: [[2.123456789,48.84],[2.34,48.85]] };
  feature.geometry = structuredClone(originalGeometry);
  Object.assign(feature.properties, { valid: false, pr_debut: '1.234567890123456789', longueur: '0',
    largeur: '1.12345678901234567890', vegetalise: false });
  await openBase(page, 'elements_morphologiques_lit');
  await expect(page.locator('#data-entity-detail-read')).toContainText('MultiPoint');
  await page.locator('#data-entity-detail-edit').click();
  const form = page.locator('#data-entity-detail .entity-form__form');
  await expect(form.locator('[name="largeur"]')).toHaveValue('1.12345678901234567890');
  await form.locator('[name="commentaire"]').fill('Correction descriptive');
  await page.locator('#data-entity-detail .entity-form__actions [data-role=save]').click();
  await expect(form).toBeHidden();
  const body = state.writes[0].body;
  expect(body).not.toHaveProperty('geometry'); expect(body).not.toHaveProperty('pr_debut');
  expect(body.largeur).toBe('1.12345678901234567890'); expect(body.vegetalise).toBe(false);
  expect(feature.geometry).toEqual(originalGeometry);
  expect(feature.properties.pr_debut).toBe('1.234567890123456789');
});

test('Base : un refresh tardif préserve la caractéristique commencée pendant la lecture', async ({ context, page }) => {
  const state = await fixture(context);
  await openBase(page);
  let release;
  state.delayDetail = new Promise(resolve => { release = resolve; });
  const requested = page.waitForRequest(request => new URL(request.url()).pathname === `/api/troncons-lit/${LIT}`);
  await page.evaluate(({ id, eventId }) => {
    window.__litRefresh = ResourceMutationController.publishMutation({ schemaVersion: 1,
      resource: 'troncons-lit', action: 'update', ids: [id], eventId, versions: { [id]: 2 },
      occurredAt: Date.now(), aspects: ['attributes'] }, { applyLocally: true });
  }, { id: LIT, eventId: randomUUID() });
  await requested;
  await page.getByRole('button', { name: 'Ajouter — Largeur moyenne', exact: true }).click();
  const child = page.locator('[data-role="lit-characteristic-form"]');
  await child.locator('[name="largeur_moyenne"]').fill('9.125');
  Object.assign(state.records['troncons-lit'][0].properties, { version: 2, designation: 'Modification distante' });
  const received = page.waitForResponse(response => new URL(response.url()).pathname === `/api/troncons-lit/${LIT}`);
  release(); await received;
  await page.evaluate(() => window.__litRefresh);
  await expect(page.locator('#data-entity-detail-status')).toContainText('Votre saisie est conservée');
  await expect(child.locator('[name="largeur_moyenne"]')).toHaveValue('9.125');
  await expect(page.locator('#data-entity-detail-title')).toContainText('Lit initial');
  expect(state.writes).toHaveLength(0);
});

test('Base : la liste morphologique inverse ouvre uniquement son élément explicitement lié', async ({ context, page }) => {
  const state = await fixture(context);
  state.records['elements-morphologiques-lit'][0].properties.segment_id = LIT;
  state.records['troncons-lit'][0].properties.elements_morphologiques_lit = [
    structuredClone(state.records['elements-morphologiques-lit'][0].properties),
  ];
  await openBase(page);
  const link = page.locator('#data-entity-detail-read').getByRole('button', { name: 'Indéterminé · Morphologie initiale', exact: true });
  await expect(link).toBeVisible();
  await link.click();
  await expect(page.locator('#data-entity-detail-title')).toContainText('Morphologie initiale');
  await expect(page.locator('#data-entity-detail-read')).toContainText('Lit initial');
  expect(state.writes).toHaveLength(0);
});

test('Carte : deux relectures concurrentes ne régressent ni la version ni le catalogue', async ({ context, page }) => {
  const state = await fixture(context);
  await page.goto(`/carte?entity=troncons_lit&id=${LIT}`);
  const panel = page.locator('#troncons-lit-editor-panel');
  await expect(panel.locator('[data-role="status"]')).toHaveText('État relu depuis le serveur.');
  let releaseOlder, calls = 0;
  const older = new Promise(resolve => { releaseOlder = resolve; });
  const source = structuredClone(state.records['troncons-lit'][0]);
  await context.route(`**/api/troncons-lit/${LIT}`, async route => {
    const index = calls++;
    const feature = structuredClone(source);
    Object.assign(feature.properties, { version: index === 0 ? 2 : 3, designation: index === 0 ? 'Ancienne relecture' : 'Dernière relecture' });
    if (index === 0) await older;
    return route.fulfill({ json: feature });
  });
  await page.evaluate(() => { window.__litOlder = tronconLitEditor.refreshCurrent(); });
  await expect.poll(() => calls).toBe(1);
  await page.evaluate(() => { window.__litNewer = tronconLitEditor.refreshCurrent(); });
  await page.evaluate(() => window.__litNewer);
  await expect(panel.locator('[name="designation"]')).toHaveValue('Dernière relecture');
  releaseOlder();
  await page.evaluate(() => window.__litOlder);
  await expect(panel.locator('[name="designation"]')).toHaveValue('Dernière relecture');
  expect(await page.evaluate(() => tronconLitEditor.shell.state.snapshot.properties.designation)).toBe('Dernière relecture');
  expect(await page.evaluate(() => tronconLitEditor.shell.state.snapshot.properties.version)).toBe(3);
});
