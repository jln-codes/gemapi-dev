const { test, expect } = require('@playwright/test');
const { randomUUID } = require('node:crypto');
const { mockBusinessCRS } = require('./mock-business-crs');
const { mockResourceJournal } = require('./mock-resource-journal');
const { photoQualityPolicy } = require('./fixtures/photo-quality-policy');

test.use({ serviceWorkers: 'block' });

const PARENT = '00000000-0000-4000-8000-000000000701';
const OBSERVATION = '00000000-0000-4000-8000-000000000702';
const TRONCON = '00000000-0000-4000-8000-000000000703';
const TYPE = '00000000-0000-4000-8000-000000000704';
const PHOTO = '00000000-0000-4000-8000-000000000705';
const CASES = [
  ['mobilier', '/api/mobilier', 'mobilier'],
  ['equipements_mesure', '/api/equipements-mesure', 'equipements_mesure'],
  ['reseaux_techniques', '/api/reseaux-techniques', 'reseaux_techniques'],
  ['ouvrages_particuliers', '/api/ouvrages-particuliers', 'ouvrages_particuliers'],
  ['cheminements', '/api/cheminements', 'cheminements'],
  ['vegetation', '/api/vegetation', 'vegetation'],
  ['amenagements_hydrauliques', '/api/amenagements-hydrauliques', 'amenagements_hydrauliques'],
  ['prestations', '/api/prestations', 'prestations'],
];
const COLLECTION_PATHS = new Set(CASES.map(([, endpoint]) => endpoint));
const png = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAwAAAAICAIAAABChommAAAAFUlEQVR4nGNkYPjPQAgwEVQx7BUBAC5SAQ9y7SbzAAAAAElFTkSuQmCC',
  'base64',
);
const collection = features => ({ type: 'FeatureCollection', features });

function mutation(resource, action, id, version) {
  return { schemaVersion: 1, eventId: randomUUID(), resource, action, ids: [id],
    versions: { [id]: version }, occurredAt: Date.now(),
    originClientId: randomUUID(), originInstanceId: randomUUID() };
}

async function mockApplication(context, selected, {
  geometry = { type: 'Point', coordinates: [2.4, 46.2] },
  detailStatus = 200,
  photoState = 'available',
} = {}) {
  const [classId, endpoint, observationResource] = selected;
  let feature = { type: 'Feature', id: PARENT, geometry, properties: {
    id: PARENT, version: 1, designation: `Parent ${classId}`, valid: true,
    type_id: TYPE, type_cheminement_id: TYPE,
    ...(classId === 'vegetation' ? { famille_vegetation: 'ARBRES',
      disposition: 'ISOLE', sous_type: 'ARBRE_SUR_PIED', invasif: false } : {}),
    date_fin: '2026-10-09', troncon_ids: [], desordre_ids: [],
    amenagement_hydraulique_ids: [], problematique_ids: [], intervenant_ids: [], object_ids: {},
  } };
  let observation = { id: OBSERVATION, version: 1, designation: 'Observation existante',
    date: '2026-10-09', valid: true, parent: {
      resource: observationResource, id: PARENT, designation: feature.properties.designation,
    }, photos: [{ id: PHOTO, observation_id: OBSERVATION, version: 1,
      libelle: 'Photo existante', content_available: photoState === 'available',
      content_state: photoState,
      content_url: photoState === 'available' ? `/api/photos/${PHOTO}/content` : null }] };
  let createdObservation = null;
  const observationWrites = [];
  const photoWrites = [];
  const photoUpdates = [];
  const uploads = new Map();

  await context.route(/^https:\/\//, route => route.abort());
  await context.route('**/api/**', async route => {
    if (await mockBusinessCRS(route) || await mockResourceJournal(route)) return;
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const send = (json, status = 200) => route.fulfill({ status, json });
    if (path === '/api/config') return send({ legacy_single_database_enabled: true,
      show_uuid: false, basemap_tilejson_url: '', photo_quality_policy: photoQualityPolicy() });
    if (path === '/api/systemes-endiguement') return send({ systemes: [] });
    if (path === '/api/data/desordres/query') return send({ columns: [], rows: [], total: 0,
      offset: 0, has_next: false });
    if (path === '/api/territoire-administratif' || path === '/api/troncons'
        || path === '/api/desordres') return send(collection([]));
    if (path === '/api/troncons/options') return send({ troncons: [
      { id: TRONCON, libelle: 'Tronçon test', valid: true },
    ] });
    if (path === '/api/desordres/search') return send({ items: [], next_cursor: null, has_more: false });
    if (path === '/api/desordres/selection') return send({ items: [],
      missing_ids: url.searchParams.getAll('ids') });
    if (path === '/api/vegetation/relations/options') return send({ troncons: [],
      amenagements_hydrauliques: [] });
    if (path === '/api/prestations/relations/options') return send({ troncons: [], contacts: [],
      desordres: [], ouvrages_hydrauliques: [], ouvrages_particuliers: [], mobilier: [],
      equipements_mesure: [], reseaux_techniques: [], cheminements: [], vegetation: [],
      amenagements_hydrauliques: [] });
    if (path.endsWith('/relations/options')) return send({ troncons: [], zones: [], desordres: [],
      amenagements_hydrauliques: [] });
    if (path.startsWith('/api/referentiels/')) return send({ references: [
      { id: TYPE, libelle: 'Type test', valid: true, native_selectable: true,
        compatible_point: true, compatible_linestring: true, compatible_polygon: true },
    ], types: [], categories: [] });
    if (path === `${endpoint}/${PARENT}/observations`) return send({ parent: observation.parent,
      observations: [observation, createdObservation].filter(Boolean).map(row => ({
        ...row, photos: undefined, photo_count: row.photos.length,
      })) });
    if (path === `${endpoint}/${PARENT}`) {
      return detailStatus === 200 ? send(feature) : send({ detail: detailStatus === 403
        ? 'Accès refusé.' : 'Objet introuvable.' }, detailStatus);
    }
    if (path === `/api/observations/${OBSERVATION}`) {
      if (request.method() === 'PUT') {
        observationWrites.push({ headers: request.headers(), body: request.postDataJSON() });
        observation = { ...observation, ...request.postDataJSON(), version: observation.version + 1 };
        return send({ ...observation,
          mutation: mutation('observations', 'update', OBSERVATION, observation.version) });
      }
      return send(observation);
    }
    if (path === '/api/observations' && request.method() === 'POST') {
      const body = request.postDataJSON();
      createdObservation = { ...body, version: 1, photos: [],
        parent: { ...body.parent, designation: feature.properties.designation },
        mutation: mutation('observations', 'create', body.id, 1) };
      return send(createdObservation, 201);
    }
    if (path === `/api/photos/${PHOTO}/content`) {
      return route.fulfill({ contentType: 'image/png', body: png });
    }
    if (path === `/api/photos/${PHOTO}` && request.method() === 'PUT') {
      photoUpdates.push({ headers: request.headers(), body: request.postDataJSON() });
      const index = observation.photos.findIndex(photo => photo.id === PHOTO);
      observation.photos[index] = { ...observation.photos[index], ...request.postDataJSON(), version: 2,
        mutation: mutation('photos', 'update', PHOTO, 2) };
      return send(observation.photos[index]);
    }
    if (path.startsWith('/api/photo-uploads/')) {
      const parts = path.split('/');
      const photoId = parts[3];
      if (request.method() === 'GET') {
        return uploads.has(photoId) ? send(uploads.get(photoId)) : send({ detail: 'absent' }, 404);
      }
      const prepared = { upload_ref: randomUUID(), file_sha256: parts[4],
        file_size: request.postDataBuffer().length, observation_id: url.searchParams.get('observation_id') };
      uploads.set(photoId, prepared);
      return send(prepared);
    }
    if (path === '/api/photos' && request.method() === 'POST') {
      const body = request.postDataJSON();
      photoWrites.push({ headers: request.headers(), body });
      const saved = { ...body, version: 1, content_available: true,
        content_url: `/api/photos/${body.id}/content`,
        mutation: mutation('photos', 'create', body.id, 1) };
      observation.photos.push(saved);
      return send(saved, 201);
    }
    if (/^\/api\/photos\/[^/]+\/content$/.test(path)) {
      return route.fulfill({ contentType: 'image/png', body: png });
    }
    if (COLLECTION_PATHS.has(path)) {
      return send(collection(path === endpoint && detailStatus === 200 ? [feature] : []));
    }
    return send({});
  });
  return { feature: () => feature, observation: () => observation,
    createdObservation: () => createdObservation, observationWrites, photoWrites, photoUpdates };
}

async function openBaseDetail(page, selected, { readOnly = false } = {}) {
  const [classId] = selected;
  await page.goto('/base');
  await expect(page.locator('#identity-gate')).toBeHidden();
  await expect(page.locator('#base-summary-crs')).not.toHaveText('');
  if (readOnly) {
    await page.evaluate(() => {
      const runtime = BusinessRuntime;
      BusinessRuntime = Object.freeze({ ...runtime,
        can: permission => permission === 'business.read',
      });
      document.body.dataset.businessCreate = 'false';
      document.body.dataset.businessUpdate = 'false';
    });
  }
  await page.evaluate(({ entityClass, entityId }) => {
    document.querySelector('main').dispatchEvent(new CustomEvent('digues-app:open-entity', {
      detail: { entityClass, entityId, source: 'views', entities: [] },
    }));
  }, { entityClass: classId, entityId: PARENT });
  await expect(page.locator('#data-entity-detail-title')).toContainText('Parent');
}

for (const selected of CASES) {
  const [classId, , resource] = selected;
  test(`${classId} : Observations et Photos partagées dans Base`, async ({ context, page }) => {
    const state = await mockApplication(context, selected);
    await openBaseDetail(page, selected);
    await expect(page.locator('#base-detail-observations-toggle')).toBeVisible();
    await expect(page.locator('#base-detail-observations-toggle')).toHaveAttribute('aria-expanded', 'false');
    await expect(page.getByRole('button', { name: 'Voir sur Carte' })).toBeVisible();
    const url = await page.evaluate(({ entityClass, id }) => EntityNavigation.mapUrl(entityClass, id),
      { entityClass: classId, id: PARENT });
    expect(url).toBe(`/carte?entity=${classId}&id=${PARENT}`);

    await page.locator('#base-detail-observations-toggle').click();
    await expect(page.locator('#data-entity-detail-observation-list')).toContainText('Observation existante');
    await page.locator('#data-entity-detail-observation-list button').click();
    await expect(page.locator('#observation-detail-dialog')).toBeVisible();
    await expect(page.locator('#observation-detail-dialog [data-photos]')).toContainText('Photo existante');
    await page.locator('#observation-detail-dialog').getByText('Voir la photo').click();
    await expect(page.locator('.photo-viewer')).toBeVisible();
    await expect(page.locator('.photo-viewer img')).toHaveAttribute('src', new RegExp(`/api/photos/${PHOTO}/content$`));
    await page.locator('.photo-viewer button').click();
    await page.locator('#observation-detail-dialog').getByText('Modifier les métadonnées').click();
    await page.locator('#photo-editor-dialog [name=libelle]').fill('Photo existante modifiée');
    await page.locator('#photo-editor-dialog [type=submit]').click();
    await expect(page.locator('#photo-editor-dialog [role=status]')).toContainText('Photo enregistrée');
    expect(state.photoUpdates).toHaveLength(1);
    expect(state.photoUpdates[0].headers['if-match']).toBe('"1"');
    await page.locator('#photo-editor-dialog [data-close]').click();
    await expect(page.locator('#observation-detail-dialog [data-photos]')).toContainText('Photo existante modifiée');
    await page.locator('#observation-detail-dialog [name=designation]').fill('Observation modifiée');
    await page.locator('#observation-detail-dialog [type=submit]').click();
    await expect(page.locator('#observation-detail-dialog').getByText('Observation — Observation modifiée', { exact: true })).toBeVisible();
    expect(state.observationWrites).toHaveLength(1);
    expect(state.observationWrites[0].headers['if-match']).toBe('"1"');

    await page.locator('#observation-detail-dialog .photo-add').click();
    await page.locator('#photo-editor-dialog [name=file]').setInputFiles({
      name: 'nouvelle.png', mimeType: 'image/png', buffer: png,
    });
    await page.locator('#photo-editor-dialog [name=libelle]').fill('Photo ajoutée dans Base');
    await page.locator('#photo-editor-dialog [type=submit]').click();
    await expect(page.locator('#photo-editor-dialog [role=status]')).toContainText('Photo ajoutée');
    expect(state.photoWrites).toHaveLength(1);
    expect(state.photoWrites[0].body.observation_id).toBe(OBSERVATION);
    await page.locator('#photo-editor-dialog [data-close]').click();
    await expect(page.locator('#observation-detail-dialog [data-photos]')).toContainText('Photo ajoutée dans Base');
    await page.locator('#observation-detail-dialog').getByRole('button', { name: 'Fermer', exact: true }).click();
    await expect(page.locator('#data-entity-detail-observation-list')).toContainText('Observation modifiée');

    await page.locator('#data-entity-detail-create-observation').click();
    await expect(page.locator('#observation-create-parent')).toContainText('Parent');
    await page.locator('#observation-create-dialog [name=designation]').fill('Nouvelle observation Base');
    await page.locator('#observation-create-dialog [type=submit]').click();
    await expect(page.locator('#observation-create-status')).toContainText('créée');
    const createdParent = await page.locator('#observation-create-dialog').evaluate(dialog => ({
      id: dialog.dataset.observationId,
    }));
    expect(createdParent.id).toBeTruthy();
    await page.locator('#observation-create-close').click();
    await expect(page.locator('#data-entity-detail-observation-list')).toContainText('Nouvelle observation Base');
    expect(state.createdObservation().parent).toEqual(expect.objectContaining({ resource, id: PARENT }));
  });

  test(`${classId} : lecture seule conserve la consultation`, async ({ context, page }) => {
    await mockApplication(context, selected);
    await openBaseDetail(page, selected, { readOnly: true });
    await page.locator('#base-detail-observations-toggle').click();
    await expect(page.locator('#data-entity-detail-observation-list')).toContainText('Observation existante');
    await expect(page.locator('#data-entity-detail-create-observation')).toBeHidden();
    await page.locator('#data-entity-detail-observation-list button').click();
    await expect(page.locator('#observation-detail-dialog')).toBeVisible();
    await expect(page.locator('#observation-detail-dialog [type=submit]')).toBeHidden();
    await expect(page.locator('#observation-detail-dialog .photo-add')).toBeHidden();
  });

  test(`${classId} : navigation Base vers Carte et centrage`, async ({ context, page }) => {
    await mockApplication(context, selected);
    await openBaseDetail(page, selected);
    await page.getByRole('button', { name: 'Voir sur Carte' }).click();
    await expect(page).toHaveURL(new RegExp(`/carte\\?entity=${classId}&id=${PARENT}$`));
    await expect(page.locator('#editor-panel')).toBeVisible();
    await expect(page.locator('#status')).toContainText('Objet ouvert et localisé');
    const center = await page.evaluate(() => ({ lat: map.getCenter().lat, lng: map.getCenter().lng }));
    expect(center.lat).toBeCloseTo(46.2, 2);
    expect(center.lng).toBeCloseTo(2.4, 2);
  });
}

test('une Photo historique sans chemin reste consultable dans Base', async ({ context, page }) => {
  await mockApplication(context, CASES[0], { photoState: 'historical_no_path' });
  await openBaseDetail(page, CASES[0]);
  await page.locator('#base-detail-observations-toggle').click();
  const observation = page.locator('#data-entity-detail-observation-list button');
  await observation.focus();
  await page.keyboard.press('Enter');
  const gallery = page.locator('#observation-detail-dialog [data-photos]');
  await expect(gallery).toContainText('Photo existante');
  await expect(gallery.getByText('Photo sans fichier associé — chemin non renseigné dans SIRS')).toBeVisible();
  await expect(gallery.getByRole('button', { name: 'Voir la photo' })).toHaveCount(0);
  await expect(gallery.getByRole('button', { name: 'Modifier les métadonnées' })).toBeVisible();
});

test('navigation directe : objet non localisé et paramètres invalides restent explicites', async ({ context, page }) => {
  await mockApplication(context, CASES[0], { geometry: null });
  await page.goto(`/carte?entity=mobilier&id=${PARENT}`);
  await expect(page.locator('#editor-panel')).toBeVisible();
  await expect(page.locator('#status')).toContainText('Objet ouvert, mais non localisé');
  await page.goto('/carte?entity=inconnue&id=00000000-0000-4000-8000-000000000799');
  await expect(page.locator('#status')).toContainText('Catégorie de navigation Carte inconnue');
  await expect(page.locator('#map')).toBeVisible();
});

for (const [detailStatus, expected] of [[404, 'introuvable'], [403, 'inaccessible']]) {
  test(`navigation directe : réponse ${detailStatus} ne sélectionne aucun autre objet`, async ({ context, page }) => {
    await mockApplication(context, CASES[0], { detailStatus });
    await page.goto(`/carte?entity=mobilier&id=${PARENT}`);
    await expect(page.locator('#status')).toContainText(expected);
    await expect(page.locator('#map')).toBeVisible();
  });
}

test('navigation directe : le panneau reste accessible sur écran étroit', async ({ context, page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await mockApplication(context, CASES[0]);
  await page.goto(`/carte?entity=mobilier&id=${PARENT}`);
  await expect(page.locator('#editor-panel')).toBeVisible();
  await expect(page.locator('#status')).toContainText('Objet ouvert et localisé');
});
