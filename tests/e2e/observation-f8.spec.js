const { test, expect } = require('@playwright/test');
const { randomUUID } = require('node:crypto');
const { mockBusinessCRS } = require('./mock-business-crs');
const { geometryCapabilities } = require('./mock-geometry-capabilities');
const { mockResourceJournal } = require('./mock-resource-journal');
const { photoQualityPolicy } = require('./fixtures/photo-quality-policy');

test.use({ serviceWorkers: 'block' });

const P = '00000000-0000-4000-8000-000000000081';
const O = '00000000-0000-4000-8000-000000000082';
const C = '00000000-0000-4000-8000-000000000083';
const T = '00000000-0000-4000-8000-000000000084';
const CAC = 'CAC';
const TR = '00000000-0000-4000-8000-000000000085';
const D = '00000000-0000-4000-8000-000000000042';
const FAMILIES = [
  ['mobilier', '/api/mobilier', 'mobilier'],
  ['equipement-mesure', '/api/equipements-mesure', 'equipements_mesure'],
  ['reseau-technique', '/api/reseaux-techniques', 'reseaux_techniques'],
  ['ouvrage-particulier', '/api/ouvrages-particuliers', 'ouvrages_particuliers'],
  ['cheminement', '/api/cheminements', 'cheminements'],
  ['vegetation', '/api/vegetation', 'vegetation'],
  ['amenagement-hydraulique', '/api/amenagements-hydrauliques', 'amenagements_hydrauliques'],
  ['prestation', '/api/prestations', 'prestations'],
];
const fc = features => ({ type: 'FeatureCollection', features });
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAwAAAAICAIAAABChommAAAAFUlEQVR4nGNkYPjPQAgwEVQx7BUBAC5SAQ9y7SbzAAAAAElFTkSuQmCC', 'base64');

async function setup(context, family, options = {}) {
  const [, path, resource] = family;
  let releaseParentRead;
  const parentReadGate = options.delayParentRead
    ? new Promise(resolve => { releaseParentRead = resolve; }) : null;
  let releaseReferenceRead;
  const referenceReadGate = options.delayReferenceRead
    ? new Promise(resolve => { releaseReferenceRead = resolve; }) : null;
  let referenceReads = 0;
  const existingGeometry = ['mobilier', 'equipement-mesure', 'ouvrage-particulier', 'vegetation']
    .includes(family[0])
    ? { type: 'Point', coordinates: [2, 46] }
    : family[0] === 'cheminement'
      ? { type: 'LineString', coordinates: [[2, 46], [2.01, 46.01]] }
      : null;
  let feature = { type: 'Feature', properties: { id: P, designation: 'Parent F8', type_id: T,
    type_cheminement_id: T, date_fin: '2026-10-09',
    ...(family[0] === 'vegetation' ? { famille_vegetation: 'ARBRES',
      disposition: 'ISOLE', sous_type: 'ARBRE_SUR_PIED', invasif: false } : {}),
    version: 1, valid: true,
    troncon_ids: [], desordre_ids: [], ...(options.parentProperties || {}) },
    geometry: Object.hasOwn(options, 'parentGeometry') ? options.parentGeometry : existingGeometry };
  let disorder = options.includeDisorder ? { type: 'Feature', properties: {
    id: D, designation: 'Désordre initial', version: 1, valid: true, troncon_ids: [],
  }, geometry: { type: 'Point', coordinates: [2, 46] } } : null;
  const disorderPuts = [];
  let parentCreated = null;
  const parentWrites = [];
  const photo = { id: randomUUID(), observation_id: O, version: 1, libelle: 'Photo existante',
    content_available: true, content_url: '/api/photos/content/existing', photos: [] };
  let observation = { id: O, parent: { resource, id: P, designation: 'Parent F8' }, version: 1,
    designation: 'Observation F8', date: '2026-10-09', evolution: 'Stable', suite: null,
    suite_apporter_id: null, photos: [photo], valid: true };
  const puts = [];
  let created = null;
  const photoPosts = [];
  const uploads = new Map();
  await context.route(/^https:\/\//, route => route.abort());
  await context.route('**/api/**', async route => {
    if (await mockBusinessCRS(route)) return;
    if (await mockResourceJournal(route)) return;
    const request = route.request(), url = new URL(request.url()), pathname = url.pathname;
    if (pathname === '/api/photos/content/existing') {
      await route.fulfill({ contentType: 'image/png', body: png }); return;
    }
    if (pathname.startsWith('/api/photo-uploads/')) {
      const id = pathname.split('/')[3];
      if (request.method() === 'GET') {
        await route.fulfill({ status: uploads.has(id) ? 200 : 404,
          json: uploads.get(id) || { detail: 'absent' } }); return;
      }
      const descriptor = { id, observation_id: new URL(request.url()).searchParams.get('observation_id'),
        upload_ref: randomUUID(), file_sha256: pathname.split('/')[4],
        file_size: request.postDataBuffer().length, mime_type: request.headers()['content-type'] };
      uploads.set(id, descriptor);
      await route.fulfill({ json: descriptor }); return;
    }
    if (pathname === '/api/photos' && request.method() === 'POST') {
      const body = request.postDataJSON();
      photoPosts.push(body);
      const saved = { ...body, version: 1, content_available: true,
        content_url: `/api/photos/${body.id}/content`, mutation: {
          schemaVersion: 1, eventId: randomUUID(), resource: 'photos', action: 'create',
          ids: [body.id], versions: { [body.id]: 1 }, occurredAt: Date.now(),
          originClientId: request.headers()['x-digues-app-client-id'],
          originInstanceId: request.headers()['x-digues-app-instance-id'] } };
      if (body.observation_id === O) observation.photos.push(saved);
      if (body.observation_id === created?.id) created.photos.push(saved);
      await route.fulfill({ status: 201, json: saved }); return;
    }
    if (pathname.startsWith('/api/photos/') && pathname.endsWith('/content')) {
      await route.fulfill({ contentType: 'image/png', body: png }); return;
    }
    let json;
    if (pathname === '/api/config') json = { legacy_single_database_enabled: true, show_uuid: false,
      basemap_tilejson_url: '', photo_quality_policy: photoQualityPolicy() };
    else if (pathname === '/api/systemes-endiguement') json = { systemes: [] };
    else if (pathname === '/api/troncons/options') {
      if (++referenceReads === 1 && referenceReadGate) await referenceReadGate;
      json = { troncons: [{ id: TR, libelle: 'Tronçon pilote', valid: true }] };
    }
    else if (pathname === '/api/desordres/search') json = { items: [
      { id: D, label: 'Désordre lié', valid: true }],
      next_cursor: null, has_more: false };
    else if (pathname === '/api/desordres/selection') {
      const ids = url.searchParams.getAll('ids');
      json = { items: ids.includes(D)
        ? [{ id: D, label: 'Désordre lié', valid: true }] : [],
      missing_ids: ids.filter(id => id !== D) };
    }
    else if (pathname === '/api/prestations/relations/options') json = {
      troncons: [{ id: TR, libelle: 'Tronçon pilote', valid: true }],
      contacts: [], desordres: [{ id: D, libelle: 'Désordre lié', valid: true }],
    };
    else if (pathname.startsWith('/api/referentiels/')) json = {
      references: [
        { id: T, libelle: 'Type pilote', valid: true,
          native_selectable: true, compatible_point: true, compatible_linestring: true,
          compatible_polygon: true },
        { id: CAC, libelle: "Chemin d'accès technique", valid: true },
      ], types: [], categories: [] };
    else if (pathname === `${path}/${P}/observations`) json = { parent: observation.parent,
      observations: [observation, created].filter(Boolean).map(row =>
        ({ ...row, photos: undefined, photo_count: row.photos.length })) };
    else if (pathname === `${path}/${P}`) {
      if (request.method() === 'GET' && parentReadGate) await parentReadGate;
      if (request.method() === 'PUT') {
        parentWrites.push({ method: 'PUT', headers: request.headers(), body: request.postDataJSON() });
        if (options.conflictOnParentPut) {
          await route.fulfill({ status: 409, json: { detail: 'Conflit de version' } }); return;
        }
        feature = { ...feature, properties: { ...feature.properties, ...request.postDataJSON(), version: 2 } };
      }
      json = feature;
    } else if (pathname === `${path}/${C}`) {
      if (request.method() === 'PUT') {
        parentWrites.push({ method: 'PUT', headers: request.headers(), body: request.postDataJSON() });
        parentCreated = { ...parentCreated, properties: {
          ...parentCreated.properties, ...request.postDataJSON(), version: 2 } };
      }
      json = parentCreated;
    }
    else if (pathname === `${path}/${C}/observations`) json = {
      parent: { resource, id: C },
      observations: created?.parent?.id === C ? [{ ...created, photos: undefined,
        photo_count: created.photos.length }] : [],
    };
    else if (pathname === path && request.method() === 'POST') {
      parentWrites.push({ method: 'POST', headers: request.headers(), body: request.postDataJSON() });
      if (options.failParentPost) {
        await route.fulfill({ status: 500, json: { detail: 'Création refusée' } }); return;
      }
      parentCreated = { type: 'Feature', properties: { ...request.postDataJSON(), id: C, version: 1 },
        geometry: request.postDataJSON().geometry || null };
      json = parentCreated;
    } else if (pathname === path) json = fc([feature, parentCreated].filter(Boolean));
    else if (pathname === `/api/desordres/${D}/observations` && disorder) json = {
      parent: { resource: 'desordres', id: D, designation: disorder.properties.designation },
      observations: [],
    };
    else if (pathname === `/api/desordres/${D}` && disorder) {
      if (request.method() === 'PUT') {
        disorderPuts.push({ headers: request.headers(), body: request.postDataJSON() });
        disorder = { ...disorder, properties: {
          ...disorder.properties, ...request.postDataJSON(), version: disorder.properties.version + 1,
        } };
      }
      json = disorder;
    }
    else if (pathname === '/api/desordres') json = fc(disorder ? [disorder] : []);
    else if (pathname === '/api/observations' && request.method() === 'POST') {
      const body = request.postDataJSON();
      created = { ...body, version: 1, parent: body.parent || observation.parent, photos: [] };
      json = { ...created, mutation: { schemaVersion: 1, eventId: randomUUID(), resource: 'observations',
        action: 'create', ids: [body.id], versions: { [body.id]: 1 }, occurredAt: Date.now(),
        originClientId: request.headers()['x-digues-app-client-id'], originInstanceId: request.headers()['x-digues-app-instance-id'] } };
    } else if (created && pathname === `/api/observations/${created.id}`) json = created;
    else if (pathname === `/api/observations/${O}`) {
      if (request.method() === 'PUT') {
        puts.push({ headers: request.headers(), body: request.postDataJSON() });
        if (options.conflictOnPut) {
          await route.fulfill({ status: 409, json: { detail: 'Conflit de version' } }); return;
        }
        observation = { ...observation, ...request.postDataJSON(), version: observation.version + 1 };
        json = { ...observation, mutation: { schemaVersion: 1, eventId: randomUUID(), resource: 'observations',
          action: 'update', ids: [O], versions: { [O]: observation.version }, occurredAt: Date.now(),
          originClientId: request.headers()['x-digues-app-client-id'], originInstanceId: request.headers()['x-digues-app-instance-id'] } };
      } else json = options.wrongParent
        ? { ...observation, parent: { ...observation.parent, id: randomUUID() } } : observation;
    } else if (pathname.endsWith('/relations/options')) json = { troncons: [], zones: [], desordres: [], amenagements_hydrauliques: [] };
    else if (pathname === '/api/territoire-administratif' || pathname === '/api/troncons'
        || FAMILIES.some(([, other]) => other === pathname)) json = fc([]);
    else json = {};
    await route.fulfill({ json });
  });
  return { puts, photoPosts, parentWrites, disorderPuts, getObservation: () => observation,
    getCreated: () => created, getParentCreated: () => parentCreated,
    releaseParentRead: () => releaseParentRead?.(),
    releaseReferenceRead: () => releaseReferenceRead?.(),
    referenceReads: () => referenceReads };
}

async function openParent(page, family) {
  await page.goto(`/carte?entity=${family[2]}&id=${P}`);
  const parent = page.locator(`#${family[0]}-editor-panel`);
  await expect(parent.locator('[data-role=status]')).toContainText('État relu');
  await expect(parent.locator('[data-role=observations-tab]')).toBeEnabled();
  await parent.locator('[data-role=observations-tab]').click();
  await expect(parent.locator('[data-role=observations] button')).toHaveCount(1);
  return parent;
}

for (const family of FAMILIES) {
  test(`${family[0]} : ouvrir, modifier, voir la photo et revenir au parent`, async ({ context, page }) => {
    const { puts, photoPosts, getCreated } = await setup(context, family);
    const parent = await openParent(page, family);
    await parent.locator('[data-role=observations] button').click();
    const detail = page.locator('#observation-detail-dialog');
    await expect(detail).toBeVisible();
    await expect(detail.locator('[name=designation]')).toHaveValue('Observation F8');
    await expect(detail).toContainText('Parent F8');
    await expect(detail).toContainText('Photo existante');
    await detail.getByText('Voir la photo').click();
    await expect(page.locator('.photo-viewer')).toBeVisible();
    await page.locator('.photo-viewer').getByText('Fermer').click();
    await detail.getByText('+ Ajouter une photo').click();
    const photoDialog = page.locator('#photo-editor-dialog');
    await photoDialog.locator('[name=file]').setInputFiles({ name: 'terrain.png', mimeType: 'image/png', buffer: png });
    await photoDialog.locator('[type=submit]').click();
    await expect(photoDialog.locator('[role=status]')).toContainText('Photo ajoutée');
    expect(photoPosts).toHaveLength(1);
    expect(photoPosts[0].observation_id).toBe(O);
    await photoDialog.locator('[data-close]').click();
    await detail.locator('[name=designation]').fill('Observation modifiée');
    await detail.locator('[type=submit]').click();
    await expect(detail.locator('[name=designation]')).toHaveValue('Observation modifiée');
    expect(puts).toHaveLength(1);
    expect(puts[0].headers['if-match']).toBe('"1"');
    expect(puts[0].body).toEqual({ designation: 'Observation modifiée' });
    await detail.locator('[data-close]').click();
    await expect(parent).toBeVisible();
    await expect(parent.locator('[data-role=observations]')).toContainText('Observation modifiée');
    await parent.locator('[data-role=create-observation]').click();
    const create = page.locator('#observation-create-dialog');
    await create.locator('[name=designation]').fill('Observation créée');
    await create.locator('[type=submit]').click();
    await expect(create.locator('#observation-create-status')).toContainText('Observation créée et enregistrée');
    expect(getCreated()?.parent).toMatchObject({ resource: family[2], id: P });
    await create.locator('.photo-add').click();
    await photoDialog.locator('[name=file]').setInputFiles({ name: 'apres-creation.png', mimeType: 'image/png', buffer: png });
    await photoDialog.locator('[type=submit]').click();
    await expect(photoDialog.locator('[role=status]')).toContainText('Photo ajoutée');
    expect(photoPosts).toHaveLength(2);
    expect(photoPosts[1].observation_id).toBe(getCreated().id);
    await photoDialog.locator('[data-close]').click();
    await create.locator('#observation-create-close').click();
    await expect(parent.locator('[data-role=observations]')).toContainText('Observation créée');
    await expect(parent.locator('[data-role=observations]')).toContainText('1 photo(s)');
  });
}

for (const family of FAMILIES) {
  test(`${family[0]} : panneau, création puis mise à jour sans second POST`, async ({ context, page }) => {
    const { parentWrites, getParentCreated, getCreated } = await setup(context, family);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/carte');
    await page.waitForFunction(() => typeof mobilierEditor !== 'undefined');
    const mapWidth = await page.locator('#map').evaluate(node => node.clientWidth);
    await page.locator('#toggle-create-menu').click();
    await page.locator(`[data-create-type="${family[0]}"]`).click();
    const panel = page.locator('#editor-panel');
    const form = panel.locator(`#${family[0]}-editor-panel`);
    await expect(panel).toBeVisible();
    await expect(page.locator(`#${family[0]}-editor-dialog`)).toHaveCount(0);
    await expect(form.locator('[data-role=observations-tab]')).toBeDisabled();
    await expect.poll(() => page.locator('#map').evaluate(node => node.clientWidth)).toBeLessThan(mapWidth);
    await form.locator('[name=designation]').fill('Objet pilote créé');
    const requiredChoice = {
      'mobilier': 'type_id', 'equipement-mesure': 'type_id', 'reseau-technique': 'type_id',
      'cheminement': 'type_cheminement_id',
    }[family[0]];
    if (requiredChoice) await form.locator(`[name=${requiredChoice}]`).selectOption(T);
    if (family[0] === 'vegetation') {
      await form.locator('[name=famille_vegetation]').selectOption('ARBRES');
      await form.locator('[name=disposition]').selectOption('ISOLE');
      await form.locator('[name=sous_type]').selectOption('ARBRE_SUR_PIED');
      await form.locator('[name=invasif]').check();
    }
    const disorderFamilies = ['mobilier', 'equipement-mesure', 'reseau-technique',
      'ouvrage-particulier', 'vegetation', 'amenagement-hydraulique'];
    if (disorderFamilies.includes(family[0])) {
      await form.getByRole('button', { name: 'Désordre lié' }).click();
    }
    if (family[0] === 'prestation') await form.locator('[name=date_fin]').fill('2026-10-09');
    if (['mobilier', 'equipement-mesure'].includes(family[0])) {
      await form.locator('[data-role=place-point]').click();
      const box = await page.locator('#map').boundingBox();
      await page.mouse.click(box.x + box.width * 0.5, box.y + box.height * 0.5);
      await expect(panel).toBeVisible();
    } else if (['ouvrage-particulier', 'vegetation'].includes(family[0])) {
      await form.locator('[name=geometry_type]').selectOption('Point');
      await form.locator('[data-role=draw]').click();
      const box = await page.locator('#map').boundingBox();
      await page.mouse.click(box.x + box.width * 0.5, box.y + box.height * 0.5);
      await expect(panel).toBeVisible();
    } else if (family[0] === 'cheminement') {
      await form.locator('[data-role=draw]').click();
      const box = await page.locator('#map').boundingBox();
      await page.mouse.click(box.x + box.width * 0.4, box.y + box.height * 0.4);
      await page.mouse.click(box.x + box.width * 0.6, box.y + box.height * 0.6);
      await form.locator('[data-role=confirm]').click();
      await expect(panel).toBeVisible();
    }
    await form.locator('[data-role=save]').click();
    await expect(form.locator('[data-role=observations-tab]')).toBeEnabled();
    expect(getParentCreated()?.properties).toMatchObject({ id: C, version: 1 });
    expect(parentWrites.map(write => write.method)).toEqual(['POST']);
    if (family[0] === 'vegetation') {
      expect(parentWrites[0].body).toMatchObject({ famille_vegetation: 'ARBRES',
        disposition: 'ISOLE', sous_type: 'ARBRE_SUR_PIED', invasif: true });
      expect(parentWrites[0].body).not.toHaveProperty('nature_id');
    }
    if (disorderFamilies.includes(family[0])) {
      expect(parentWrites[0].body.desordre_ids).toEqual([D]);
    }
    await form.locator('[data-role=observations-tab]').click();
    await expect(form.locator('[data-role=observations]')).toContainText('Aucune observation');
    await form.locator('[data-role=create-observation]').click();
    const createObservation = page.locator('#observation-create-dialog');
    await createObservation.locator('[name=designation]').fill('Après création du parent');
    await createObservation.locator('[type=submit]').click();
    await expect(createObservation.locator('#observation-create-status'))
      .toContainText('Observation créée et enregistrée');
    expect(getCreated()?.parent).toMatchObject({ resource: family[2], id: C });
    await createObservation.locator('#observation-create-close').click();
    await expect(form.locator('[data-role=observations]'))
      .toContainText('Après création du parent');
    await form.locator('[data-role=general-tab]').click();
    await form.locator('[name=designation]').fill('Objet pilote modifié');
    await form.locator('[data-role=save]').click();
    await expect.poll(() => parentWrites.map(write => write.method)).toEqual(['POST', 'PUT']);
    expect(parentWrites[1].headers['if-match']).toBe('"1"');
    if (['mobilier', 'equipement-mesure', 'reseau-technique', 'vegetation',
      'amenagement-hydraulique'].includes(family[0])) {
      expect(parentWrites[1].body).not.toHaveProperty('desordre_ids');
    }
    await expect(form.locator('[data-role=save]')).toBeEnabled();
    await page.locator('#editor-panel .entity-form__header [data-role=close]:visible').click();
    await expect(panel).toBeHidden();
    await expect.poll(() => page.locator('#map').evaluate(node => node.clientWidth)).toBe(mapWidth);
  });
}

for (const family of [FAMILIES[0], FAMILIES[1], FAMILIES[3], FAMILIES[4], FAMILIES[5]]) {
  test(`${family[0]} : une création sans géométrie garde le brouillon et ne lance aucun POST`, async ({ context, page }) => {
    const { parentWrites } = await setup(context, family);
    await page.goto('/carte');
    await page.waitForFunction(() => typeof mobilierEditor !== 'undefined');
    await page.locator('#toggle-create-menu').click();
    await page.locator(`[data-create-type="${family[0]}"]`).click();
    const form = page.locator(`#${family[0]}-editor-panel`);
    await form.locator('[name=designation]').fill('Brouillon sans géométrie');
    const requiredChoice = {
      'mobilier': 'type_id', 'equipement-mesure': 'type_id',
      'cheminement': 'type_cheminement_id',
    }[family[0]];
    if (requiredChoice) await form.locator(`[name=${requiredChoice}]`).selectOption(T);
    if (family[0] === 'vegetation') {
      await form.locator('[name=famille_vegetation]').selectOption('ARBRES');
      await form.locator('[name=disposition]').selectOption('ISOLE');
      await form.locator('[name=sous_type]').selectOption('ARBRE_SUR_PIED');
    }
    if (['ouvrage-particulier', 'cheminement'].includes(family[0])) {
      await form.getByRole('button', { name: 'Désordre lié' }).click();
    }

    await form.locator('[data-role=save]').click();

    await expect(form.locator('[data-role=status]')).toContainText('obligatoire');
    await expect(form.locator('[name=designation]')).toHaveValue('Brouillon sans géométrie');
    await expect(form.locator('[data-role=observations-tab]')).toBeDisabled();
    expect(parentWrites).toHaveLength(0);
    if (['ouvrage-particulier', 'cheminement'].includes(family[0])) {
      await expect(form.getByRole('button', { name: 'Retirer Désordre lié' })).toBeVisible();
    }
  });
}

for (const family of FAMILIES.slice(2)) {
  test(`${family[0]} : lecture seule et conflit conservent le contexte`, async ({ context, page }) => {
    const { parentWrites } = await setup(context, family, { conflictOnParentPut: true });
    const parent = await openParent(page, family);
    await parent.locator('[data-role=general-tab]').click();
    await parent.locator('[name=designation]').fill('Brouillon concurrent');
    await parent.locator('[data-role=save]').click();
    await expect.poll(() => parentWrites.length).toBe(1);
    expect(parentWrites[0].headers['if-match']).toBe('"1"');
    await expect(parent.locator('[name=designation]')).toHaveValue('Brouillon concurrent');
    page.once('dialog', dialog => dialog.dismiss());
    await page.locator('#editor-panel .entity-form__header [data-role=close]:visible').click();
    await expect(parent).toBeVisible();
    page.once('dialog', dialog => dialog.accept());
    await page.locator('#editor-panel .entity-form__header [data-role=close]:visible').click();
    await expect(page.locator('#editor-panel')).toBeHidden();

    await page.locator('#toggle-create-menu').click();
    await page.locator(`[data-create-type="${family[0]}"]`).click();
    await page.evaluate(() => {
      const runtime = window.BusinessRuntime;
      window.BusinessRuntime = { ...runtime, can: permission => permission === 'business.read' };
      document.body.dataset.businessCreate = 'false';
      document.body.dataset.businessUpdate = 'false';
    });
    // Switching away from the freshly opened draft requires explicit consent.
    page.once('dialog', dialog => dialog.accept());
    await page.evaluate(({ resource, id }) => { void MapEntityNavigation.open(resource, id); }, { resource: family[2], id: P });
    await expect(parent.locator('[data-role=status]')).toContainText('État relu');
    await expect(parent.locator('[name=designation]')).toBeDisabled();
    await expect(parent.locator('[data-role=save]')).toBeHidden();
    await expect(parent.locator('[data-role=new]')).toHaveCount(0);
    await parent.locator('[data-role=observations-tab]').click();
    await expect(parent.locator('[data-role=create-observation]')).toBeHidden();
  });
}

test('Cheminement : dessin linéaire mobile et relations avec Tronçon et Désordre', async ({ context, page }) => {
  const { parentWrites } = await setup(context, FAMILIES[4]);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/carte');
  await page.waitForFunction(() => typeof cheminementEditor !== 'undefined');
  await page.locator('#toggle-create-menu').click();
  await page.locator('[data-create-type=cheminement]').click();
  const form = page.locator('#cheminement-editor-panel');
  await form.locator('[name=type_cheminement_id]').selectOption(T);
  await form.locator('[name=troncon_ids]').selectOption([TR]);
  await form.getByRole('button', { name: 'Désordre lié' }).click();
  await form.locator('[name=geometry_type]').selectOption('LineString');
  await form.locator('[data-role=draw]').click();
  await expect(page.locator('#editor-panel')).toBeHidden();
  const box = await page.locator('#map').boundingBox();
  await page.mouse.click(box.x + box.width * 0.35, box.y + box.height * 0.35);
  await page.mouse.click(box.x + box.width * 0.65, box.y + box.height * 0.65);
  await expect(page.locator('#finish-editor-map-interaction')).toBeVisible();
  await page.locator('#finish-editor-map-interaction').click();
  await expect(page.locator('#editor-panel')).toBeVisible();
  await form.locator('[data-role=save]').click();
  await expect.poll(() => parentWrites.length).toBe(1);
  expect(parentWrites[0].body.geometry.type).toBe('LineString');
  expect(parentWrites[0].body.geometry.coordinates).toHaveLength(2);
  expect(parentWrites[0].body.troncon_ids).toEqual([TR]);
  expect(parentWrites[0].body.desordre_ids).toEqual([D]);
});

test("Cheminement CAC : Polygon créé puis conservé lors d'une édition attributaire", async ({ context, page }) => {
  const { parentWrites } = await setup(context, FAMILIES[4]);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/carte');
  await page.waitForFunction(() => typeof cheminementEditor !== 'undefined');
  await page.locator('#toggle-create-menu').click();
  await page.locator('[data-create-type=cheminement]').click();
  const form = page.locator('#cheminement-editor-panel');
  await form.locator('[name=type_cheminement_id]').selectOption(CAC);
  await expect(form.locator('[name=geometry_type] option[value=Polygon]')).toBeEnabled();
  await form.locator('[name=designation]').fill('Accès polygonal');
  await form.locator('[name=geometry_type]').selectOption('Polygon');
  await form.locator('[data-role=draw]').click();
  await expect(page.locator('#editor-panel')).toBeHidden();
  const box = await page.locator('#map').boundingBox();
  for (const [x, y] of [[0.3, 0.3], [0.65, 0.35], [0.5, 0.7]]) {
    await page.mouse.click(box.x + box.width * x, box.y + box.height * y);
  }
  await page.locator('#finish-editor-map-interaction').click();
  await expect(page.locator('#editor-panel')).toBeVisible();
  await form.locator('[data-role=save]').click();
  await expect.poll(() => parentWrites.length).toBe(1);
  expect(parentWrites[0].method).toBe('POST');
  expect(parentWrites[0].body.type_cheminement_id).toBe(CAC);
  expect(parentWrites[0].body.geometry.type).toBe('Polygon');
  expect(parentWrites[0].body.geometry.coordinates[0]).toHaveLength(4);

  await form.locator('[name=type_cheminement_id]').selectOption(T);
  await expect(form.locator('[data-role=geometry-status]')).toContainText('incompatible');
  await form.locator('[data-role=save]').click();
  await expect(form.locator('[data-role=status]')).toContainText('LineString');
  expect(parentWrites).toHaveLength(1);
  await expect(form.locator('[name=designation]')).toHaveValue('Accès polygonal');

  await form.locator('[name=type_cheminement_id]').selectOption(CAC);
  await form.locator('[name=commentaire]').fill('Attribut sans réécriture géométrique');
  await form.locator('[data-role=save]').click();
  await expect.poll(() => parentWrites.length).toBe(2);
  expect(parentWrites[1].method).toBe('PUT');
  expect(parentWrites[1].body).not.toHaveProperty('geometry');
});

for (const [geometryType, familyValue, disposition, vertices] of [
  ['LineString', 'ARBRES', 'LIGNE', [[0.35, 0.12], [0.65, 0.24]]],
  ['Polygon', 'HERBACEES', 'SURFACE', [[0.35, 0.12], [0.65, 0.12], [0.5, 0.24]]],
]) {
  test(`Végétation : famille, sous-type et dessin ${geometryType}`, async ({ context, page }) => {
    const { parentWrites } = await setup(context, FAMILIES[5]);
    await page.goto('/carte');
    await page.waitForFunction(() => Boolean(window.MapCreateActions));
    await page.locator('#toggle-create-menu').click();
    await page.locator('[data-create-type=vegetation]').click();
    const form = page.locator('#vegetation-editor-panel');
    await form.locator('[name=designation]').fill('Brouillon végétation conservé');
    await form.locator('[name=famille_vegetation]').selectOption('ARBRES');
    await form.locator('[name=disposition]').selectOption(disposition);
    await form.locator('[name=sous_type]').selectOption('SOUCHE');
    await expect(form.locator('[data-context=sous_type]')).toBeVisible();
    await form.locator('[name=famille_vegetation]').selectOption('HERBACEES');
    await expect(form.locator('[data-context=sous_type]')).toBeHidden();
    await expect(form.locator('[name=sous_type]')).toHaveValue('');
    await form.locator('[name=famille_vegetation]').selectOption(familyValue);
    if (familyValue === 'ARBRES') {
      await expect(form.locator('[data-context=sous_type]')).toBeVisible();
      await form.locator('[name=sous_type]').selectOption('ARBRE_SUR_PIED');
    }
    await form.locator('[name=geometry_type]').selectOption(geometryType);
    await expect(form.locator('[name=designation]')).toHaveValue('Brouillon végétation conservé');
    await form.locator('[data-role=draw]').click();
    const map = page.locator('#map');
    const box = await map.boundingBox();
    for (const [index, [x, y]] of vertices.entries()) {
      // Real pointer clicks in the upper map, clear of the expanded legend.
      // Locator actionability and the vertex count detect intercepted clicks.
      await map.click({ position: { x: box.width * x, y: box.height * y } });
      await expect(form.locator('[data-role=geometry-status]')).toContainText(`${index + 1} sommet`);
    }
    await form.locator('[data-role=confirm]').click();
    await form.locator('[data-role=save]').click();
    await expect.poll(() => parentWrites.length).toBe(1);
    expect(parentWrites[0].body).toMatchObject({ famille_vegetation: familyValue,
      disposition, sous_type: familyValue === 'ARBRES' ? 'ARBRE_SUR_PIED' : null });
    expect(parentWrites[0].body).not.toHaveProperty('nature_id');
    expect(parentWrites[0].body.geometry.type).toBe(geometryType);
  });
}

for (const [family, geometry, vertices] of [
  [FAMILIES[2], 'LineString', 2], [FAMILIES[3], 'LineString', 2],
  [FAMILIES[3], 'Polygon', 3],
]) {
  test(`${family[0]} : le dessin ${geometry} confirme depuis la carte mobile`, async ({ context, page }) => {
    const { parentWrites } = await setup(context, family);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/carte');
    await page.waitForFunction(() => Boolean(window.MapCreateActions));
    await page.locator('#toggle-create-menu').click();
    await page.locator(`[data-create-type=${family[0]}]`).click();
    const form = page.locator(`#${family[0]}-editor-panel`);
    if (family[0] === 'reseau-technique') await form.locator('[name=type_id]').selectOption(T);
    await form.locator('[name=geometry_type]').selectOption(geometry);
    await form.locator('[data-role=draw]').click();
    await expect(page.locator('#editor-panel')).toBeHidden();
    const box = await page.locator('#map').boundingBox();
    for (const [x, y] of [[0.3, 0.3], [0.55, 0.35], [0.5, 0.65]].slice(0, vertices)) {
      await page.mouse.click(box.x + box.width * x, box.y + box.height * y);
    }
    await expect(page.locator('#finish-editor-map-interaction')).toBeVisible();
    await page.locator('#finish-editor-map-interaction').click();
    await expect(page.locator('#editor-panel')).toBeVisible();
    await form.locator('[data-role=save]').click();
    await expect.poll(() => parentWrites.length).toBe(1);
    expect(parentWrites[0].body.geometry.type).toBe(geometry);
    await page.locator('#editor-panel .entity-form__header [data-role=close]:visible').click();
    await expect(page.locator('#map')).toHaveJSProperty('inert', false);
  });
}

test('Prestation : le sélecteur Désordre reste utilisable dans le panneau mobile', async ({ context, page }) => {
  const { parentWrites } = await setup(context, FAMILIES[7]);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/carte');
  await page.waitForFunction(() => Boolean(window.MapCreateActions));
  await page.locator('#toggle-create-menu').click();
  await page.locator('[data-create-type=prestation]').click();
  const form = page.locator('#prestation-editor-panel');
  const desordres = form.locator('[name=objects_desordres]');
  await desordres.selectOption([D]);
  const bounds = await desordres.boundingBox();
  expect(bounds.x).toBeGreaterThanOrEqual(0);
  expect(bounds.x + bounds.width).toBeLessThanOrEqual(390);
  await form.locator('[name=date_fin]').fill('2026-10-09');
  await form.locator('[data-role=save]').click();
  await expect.poll(() => parentWrites.length).toBe(1);
  expect(parentWrites[0].body.object_ids.desordres).toEqual([D]);
});

test('le panneau Réseau efface son contexte entre deux objets et une fermeture de base', async ({ context, page }) => {
  const { releaseParentRead } = await setup(context, FAMILIES[2], { delayParentRead: true });
  await page.goto('/carte');
  await page.waitForFunction(() => typeof reseauTechniqueEditor !== 'undefined');
  await page.locator('#toggle-create-menu').click();
  await page.locator('[data-create-type=reseau-technique]').click();
  const form = page.locator('#reseau-technique-editor-panel');
  await form.locator('[name=designation]').fill('Nouveau réseau');
  await form.locator('[name=type_id]').selectOption(T);
  await form.locator('[data-role=save]').click();
  await expect(form.locator('[data-role=observations-tab]')).toBeEnabled();
  await page.evaluate(id => { void MapEntityNavigation.open('reseaux_techniques', id); }, P);
  await expect(form.locator('[data-role=status]')).toContainText('Chargement');
  await page.evaluate(() => window.dispatchEvent(new Event('digues-app:context-closed')));
  await expect(page.locator('#editor-panel')).toBeHidden();
  releaseParentRead();
  await expect(page.locator('#editor-panel')).toBeHidden();
  await page.locator('#toggle-create-menu').click();
  await page.locator('[data-create-type=reseau-technique]').click();
  page.once('dialog', dialog => dialog.accept());
  await page.evaluate(id => { void MapEntityNavigation.open('reseaux_techniques', id); }, C);
  await expect(form.locator('[name=designation]')).toHaveValue('Nouveau réseau');
  await page.evaluate(id => { void MapEntityNavigation.open('reseaux_techniques', id); }, P);
  await expect(form.locator('[name=designation]')).toHaveValue('Parent F8');
});

test('un ancien chargement de référentiels ne remplace pas le Réseau sélectionné', async ({ context, page }) => {
  const { releaseReferenceRead, referenceReads } = await setup(context, FAMILIES[2],
    { delayReferenceRead: true });
  await page.goto('/carte');
  await page.waitForFunction(() => typeof reseauTechniqueEditor !== 'undefined');
  await page.locator('#toggle-create-menu').click();
  await page.locator('[data-create-type=reseau-technique]').click();
  await expect.poll(referenceReads).toBe(1);
  const form = page.locator('#reseau-technique-editor-panel');
  await page.evaluate(id => { void MapEntityNavigation.open('reseaux_techniques', id); }, P);
  await expect(form.locator('[name=designation]')).toHaveValue('Parent F8');
  const delayedResponse = page.waitForResponse(response =>
    response.url().endsWith('/api/troncons/options') && response.status() === 200);
  releaseReferenceRead();
  await delayedResponse;
  await page.waitForTimeout(50);
  await expect(form.locator('[name=designation]')).toHaveValue('Parent F8');
  expect(await page.evaluate(() => reseauTechniqueEditor.shell.state.objectId)).toBe(P);
  await expect(form.locator('[name=type_id]')).toHaveValue(T);
});

test('un brouillon Réseau protège le passage à Ouvrage particulier', async ({ context, page }) => {
  await setup(context, FAMILIES[2]);
  await page.goto('/carte');
  await page.waitForFunction(() => typeof reseauTechniqueEditor !== 'undefined');
  await page.locator('#toggle-create-menu').click();
  await page.locator('[data-create-type=reseau-technique]').click();
  const reseau = page.locator('#reseau-technique-editor-panel');
  await reseau.locator('[name=designation]').fill('Réseau non enregistré');
  const blocksExit = () => page.evaluate(() => {
    const event = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(event);
    return event.defaultPrevented;
  });
  expect(await blocksExit()).toBe(true);
  await page.locator('#toggle-create-menu').click();
  page.once('dialog', dialog => dialog.dismiss());
  await page.locator('[data-create-type=ouvrage-particulier]').click();
  await expect(reseau).toBeVisible();
  await expect(reseau.locator('[name=designation]')).toHaveValue('Réseau non enregistré');
  await page.locator('#toggle-create-menu').click();
  page.once('dialog', dialog => dialog.accept());
  await page.locator('[data-create-type=ouvrage-particulier]').click();
  await expect(page.locator('#ouvrage-particulier-editor-panel')).toBeVisible();
  await expect(reseau).toBeHidden();
  expect(await blocksExit()).toBe(false);
});

test('un POST Réseau refusé conserve le mode création et le brouillon', async ({ context, page }) => {
  const { parentWrites } = await setup(context, FAMILIES[2], { failParentPost: true });
  await page.goto('/carte');
  await page.waitForFunction(() => typeof reseauTechniqueEditor !== 'undefined');
  await page.locator('#toggle-create-menu').click();
  await page.locator('[data-create-type=reseau-technique]').click();
  const form = page.locator('#reseau-technique-editor-panel');
  await form.locator('[name=designation]').fill('Réseau à reprendre');
  await form.locator('[name=type_id]').selectOption(T);
  await form.locator('[data-role=save]').click();
  await expect.poll(() => parentWrites.length).toBe(1);
  await expect(form.locator('[name=designation]')).toHaveValue('Réseau à reprendre');
  await expect(form.locator('[data-role=observations-tab]')).toBeDisabled();
  await expect(page.locator('#editor-panel .entity-form:visible > .entity-form__header .entity-form__title')).toContainText('Nouveau réseau');
});

test('le brouillon Mobilier bloque la fermeture et le passage à Équipement', async ({ context, page }) => {
  await setup(context, FAMILIES[0]);
  await page.goto('/carte');
  await page.waitForFunction(() => typeof mobilierEditor !== 'undefined');
  await page.locator('#toggle-create-menu').click();
  await page.locator('[data-create-type=mobilier]').click();
  const mobilier = page.locator('#mobilier-editor-panel');
  await mobilier.locator('[name=designation]').fill('Brouillon conservé');
  page.once('dialog', dialog => dialog.dismiss());
  await page.locator('#editor-panel .entity-form__header [data-role=close]:visible').click();
  await expect(mobilier).toBeVisible();
  await expect(mobilier.locator('[name=designation]')).toHaveValue('Brouillon conservé');
  await page.locator('#toggle-create-menu').click();
  page.once('dialog', dialog => dialog.dismiss());
  await page.locator('[data-create-type=equipement-mesure]').click();
  await expect(mobilier).toBeVisible();
  await expect(page.locator('#equipement-mesure-editor-panel')).toBeHidden();
  await page.locator('#toggle-create-menu').click();
  page.once('dialog', dialog => dialog.accept());
  await page.locator('[data-create-type=equipement-mesure]').click();
  await expect(page.locator('#equipement-mesure-editor-panel')).toBeVisible();
  await expect(page.locator('#mobilier-editor-panel')).toBeHidden();
});

test('le conflit Mobilier conserve la saisie et la version initiale', async ({ context, page }) => {
  const { parentWrites } = await setup(context, FAMILIES[0], { conflictOnParentPut: true });
  const parent = await openParent(page, FAMILIES[0]);
  await parent.locator('[data-role=general-tab]').click();
  await parent.locator('[name=designation]').fill('Brouillon en conflit');
  await parent.locator('[data-role=save]').click();
  await expect(parent.locator('[data-role=status]')).toContainText('Votre brouillon est conservé');
  await expect(parent.locator('[name=designation]')).toHaveValue('Brouillon en conflit');
  expect(parentWrites).toHaveLength(1);
  expect(parentWrites[0].headers['if-match']).toBe('"1"');
  page.once('dialog', dialog => dialog.dismiss());
  await page.locator('#editor-panel .entity-form__header [data-role=close]:visible').click();
  await expect(parent).toBeVisible();
  await expect(parent.locator('[name=designation]')).toHaveValue('Brouillon en conflit');
  page.once('dialog', dialog => dialog.accept());
  await page.keyboard.press('Escape');
  await expect(page.locator('#editor-panel')).toBeHidden();
  await expect(page.locator('#toggle-create-menu')).toBeFocused();
});

test('le Point Mobilier suspend le panneau en mobile puis le restaure', async ({ context, page }) => {
  await setup(context, FAMILIES[0]);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/carte');
  await page.waitForFunction(() => typeof mobilierEditor !== 'undefined');
  await page.locator('#toggle-create-menu').click();
  await page.locator('[data-create-type=mobilier]').click();
  await expect(page.locator('main')).toHaveAttribute('data-layout', 'single-tool');
  await expect(page.locator('#editor-panel')).toBeVisible();
  await page.locator('#mobilier-editor-panel [data-role=place-point]').click();
  await expect(page.locator('main')).toHaveClass(/editor-map-mode/);
  await expect(page.locator('#editor-map-interaction')).toBeVisible();
  await expect(page.locator('#editor-panel')).toBeHidden();
  const box = await page.locator('#map').boundingBox();
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
  await expect(page.locator('#editor-panel')).toBeVisible();
  await expect(page.locator('#mobilier-editor-panel [data-role=point-status]')).toContainText('Point ');
  page.once('dialog', dialog => dialog.accept());
  await page.locator('#editor-panel .entity-form__header [data-role=close]:visible').click();
  await expect(page.locator('#editor-panel')).toBeHidden();
  await expect(page.locator('#map')).toHaveJSProperty('inert', false);
});

test('Désordre existant : édition versionnée et retour depuis Mobilier', async ({ context, page }) => {
  const { disorderPuts } = await setup(context, FAMILIES[0], { includeDisorder: true });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/carte');
  await page.waitForFunction(() => typeof mobilierEditor !== 'undefined');
  const mapWidth = await page.locator('#map').evaluate(node => node.clientWidth);
  await page.locator('.desordre-point-marker').click();
  await expect(page.locator('#editor-panel')).toBeVisible();
  await expect(page.locator('#desordre-editor')).toBeVisible();
  await expect(page.locator('#editor-panel .entity-form:visible > .entity-form__header .entity-form__title')).toContainText('Désordre');
  await expect.poll(() => page.locator('#map').evaluate(node => node.clientWidth))
    .toBeLessThan(mapWidth);
  await page.locator('#desordre-create-designation').fill('Désordre revu');
  await page.locator('#submit-desordre-create').click();
  await expect.poll(() => disorderPuts.length).toBe(1);
  expect(disorderPuts[0].headers['if-match']).toBe('"1"');
  expect(disorderPuts[0].body.designation).toBe('Désordre revu');
  await expect(page.locator('#desordre-create-designation')).toHaveValue('Désordre revu');
  await page.locator('#editor-panel .entity-form:visible > .entity-form__tabs [data-role=observations-tab]').click();
  await expect(page.locator('#observations-tab')).toBeVisible();
  await expect(page.locator('#general-tab')).toBeHidden();
  await page.locator('#editor-panel .entity-form [data-role=general-tab]:visible').click();
  await expect(page.locator('#general-tab')).toBeVisible();
  await page.locator('#editor-panel .entity-form__header [data-role=close]:visible').click();
  await expect(page.locator('#editor-panel')).toBeHidden();
  await expect.poll(() => page.locator('#map').evaluate(node => node.clientWidth))
    .toBe(mapWidth);

  await page.locator('#toggle-create-menu').click();
  await page.locator('[data-create-type=mobilier]').click();
  const mobilier = page.locator('#mobilier-editor-panel');
  page.once('dialog', dialog => dialog.accept());
  await page.evaluate(id => { void MapEntityNavigation.open('mobilier', id); }, P);
  await expect(mobilier).toBeVisible();
  await expect(page.locator('#desordre-editor')).toBeHidden();
  await expect(mobilier.locator('[data-role=observations-tab]')).toBeEnabled();

  // The editor asks before abandoning its current context when switching back
  // to the map object. Confirm the intended navigation explicitly.
  page.once('dialog', dialog => dialog.accept());
  await page.locator('.desordre-point-marker').click();
  await expect(page.locator('#desordre-editor')).toBeVisible();
  await expect(page.locator('#business-editor-content')).toBeHidden();
  await expect(page.locator('#general-tab')).toBeVisible();
  await expect(page.locator('#desordre-create-designation')).toHaveValue('Désordre revu');
  await page.locator('#desordre-create-designation').fill('Désordre revu deux fois');
  await page.locator('#submit-desordre-create').click();
  await expect.poll(() => disorderPuts.length).toBe(2);
  expect(disorderPuts[1].headers['if-match']).toBe('"2"');
  await page.locator('#editor-panel .entity-form__header [data-role=close]:visible').click();
  await expect(page.locator('#editor-panel')).toBeHidden();
  await expect.poll(() => page.locator('#map').evaluate(node => node.clientWidth))
    .toBe(mapWidth);
});

test('un détail d’un autre parent est refusé sans quitter la fiche', async ({ context, page }) => {
  const family = FAMILIES[0];
  await setup(context, family, { wrongParent: true });
  const parent = await openParent(page, family);
  await parent.locator('[data-role=observations] button').click();
  const detail = page.locator('#observation-detail-dialog');
  await expect(detail.locator('[role=status]')).toContainText('ne correspond pas');
  await expect(detail.locator('[data-content]')).toBeHidden();
  await detail.locator('[data-close]').click();
  await expect(parent).toBeVisible();
});

test('la lecture seule masque les actions Observation et Photo', async ({ context, page }) => {
  const family = FAMILIES[0];
  await setup(context, family);
  const parent = await openParent(page, family);
  await page.evaluate(() => {
    const runtime = window.BusinessRuntime;
    window.BusinessRuntime = { ...runtime, can: permission => permission === 'business.read' };
    document.body.dataset.businessCreate = 'false';
    document.body.dataset.businessUpdate = 'false';
  });
  await parent.locator('[data-role=general-tab]').click();
  await expect(parent.locator('[name=designation]')).toBeDisabled();
  await expect(parent.locator('[data-role=save]')).toBeHidden();
  await expect(parent.locator('[data-role=new]')).toHaveCount(0);
  await parent.locator('[data-role=observations-tab]').click();
  await expect(parent.locator('[data-role=create-observation]')).toBeHidden();
  await parent.locator('[data-role=observations] button').click();
  const detail = page.locator('#observation-detail-dialog');
  await expect(detail.locator('[name=designation]')).toBeDisabled();
  await expect(detail.locator('[type=submit]')).toBeHidden();
  await expect(detail.locator('.photo-add')).toBeHidden();
  await expect(detail.getByText('Modifier les métadonnées')).toBeHidden();
  await expect(detail.getByText('Voir la photo')).toBeVisible();
});

test('une liste arrivée après un changement d’objet ou de base est ignorée', async ({ context, page }) => {
  await setup(context, FAMILIES[0]);
  await page.goto('/carte');
  await page.waitForFunction(() => Boolean(window.ObservationParentList && window.BusinessRuntime));
  const result = await page.evaluate(async () => {
    const container = document.createElement('div');
    document.body.append(container);
    const original = window.BusinessRuntime;
    let resolveOld;
    const delayed = new Promise(resolve => { resolveOld = resolve; });
    let selected = 'parent-a';
    const request = (id, fetchJson) => ObservationParentList.load({
      container, parent: { resource: 'mobilier', id }, path: '/api/mock', fetchJson,
      openObservation: () => {}, isCurrent: () => selected === id,
    });
    const old = request('parent-a', () => delayed);
    selected = 'parent-b';
    await request('parent-b', async () => ({ parent: { resource: 'mobilier', id: 'parent-b' },
      observations: [{ id: 'observation-b', designation: 'Observation B' }] }));
    resolveOld({ parent: { resource: 'mobilier', id: 'parent-a' },
      observations: [{ id: 'observation-a', designation: 'Observation A' }] });
    await old;
    const afterObjectChange = container.textContent;
    let resolveBase;
    const delayedBase = new Promise(resolve => { resolveBase = resolve; });
    const fromOldBase = request('parent-b', () => delayedBase);
    window.BusinessRuntime = { ...original };
    resolveBase({ parent: { resource: 'mobilier', id: 'parent-b' },
      observations: [{ id: 'old-base', designation: 'Ancienne base' }] });
    await fromOldBase;
    const afterBaseChange = container.textContent;
    window.BusinessRuntime = original;
    container.remove();
    return { afterObjectChange, afterBaseChange };
  });
  expect(result.afterObjectChange).toContain('Observation B');
  expect(result.afterObjectChange).not.toContain('Observation A');
  expect(result.afterBaseChange).not.toContain('Ancienne base');
});

test('un brouillon et sa version restent présents après conflit ou fermeture refusée', async ({ context, page }) => {
  const family = FAMILIES[0];
  const { puts } = await setup(context, family, { conflictOnPut: true });
  const parent = await openParent(page, family);
  await parent.locator('[data-role=observations] button').click();
  const detail = page.locator('#observation-detail-dialog');
  await expect(detail.locator('[name=designation]')).toHaveValue('Observation F8');
  await detail.locator('[name=designation]').fill('Brouillon concurrent');
  page.once('dialog', dialog => dialog.dismiss());
  await detail.locator('[data-close]').click();
  await expect(detail).toBeVisible();
  await detail.locator('[type=submit]').click();
  await expect(detail.locator('[role=status]')).toContainText('brouillon est conservé');
  await expect(detail.locator('[name=designation]')).toHaveValue('Brouillon concurrent');
  expect(puts).toHaveLength(1);
  expect(puts[0].headers['if-match']).toBe('"1"');
});

// UX-GEO-1, niveau B : métadonnées synthétiques approuvées et API de mutation
// interceptée. On prouve la propagation aux contrôles et au brouillon réel
// Leaflet, pas la validation SQL (couverte séparément par les tests backend).
test('UX-GEO : les formes approuvées changent à la réouverture sans recoder Ouvrage', async ({ context, page }) => {
  const { parentWrites } = await setup(context, FAMILIES[3]);
  let contract = geometryCapabilities({ status: 'approved', allowedTypes: ['Polygon'] });
  await context.route('**/api/geometry-capabilities/ouvrages-particuliers', route => route.fulfill({ json: contract }));
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/carte');
  await page.waitForFunction(() => typeof mobilierEditor !== 'undefined' && mapViewportReady);
  await page.locator('#toggle-create-menu').click();
  await page.locator('[data-create-type=ouvrage-particulier]').click();
  const form = page.locator('#ouvrage-particulier-editor-panel');
  await expect(form.locator('[name=geometry_type] option')).toHaveText(['Polygon']);
  // Approved types come from the capability response; no decorative success banner.
  await expect(form.locator('[data-role=geometry-contract-status]')).toBeEmpty();
  await form.locator('[name=designation]').fill('Emprise pilote');
  await form.locator('[data-role=draw]').click();
  const box = await page.locator('#map').boundingBox();
  for (const [x, y] of [[0.3, 0.3], [0.65, 0.35], [0.5, 0.7]]) {
    await page.mouse.click(box.x + box.width * x, box.y + box.height * y);
  }
  await form.locator('[data-role=confirm]').click();
  await form.locator('[data-role=save]').click();
  await expect.poll(() => parentWrites.length).toBe(1);
  expect(parentWrites[0].body.geometry.type).toBe('Polygon');
  expect(parentWrites[0].headers['geometry-contract-version']).toBe(contract.contractVersion);
  await expect(form.locator('[data-role=status]')).toContainText('enregistré');

  contract = geometryCapabilities({ status: 'approved', allowedTypes: ['Point'], contractVersion: 'c'.repeat(64) });
  await page.locator('#toggle-create-menu').click();
  await page.locator('[data-create-type=ouvrage-particulier]').click();
  await expect(form.locator('[name=geometry_type] option')).toHaveText(['Point']);
  await form.locator('[name=designation]').fill('Localisation pilote');
  await form.locator('[data-role=draw]').click();
  await page.mouse.click(box.x + box.width * 0.4, box.y + box.height * 0.5);
  await expect(form.locator('[data-role=geometry-status]')).toBeEmpty();
  await form.locator('[data-role=save]').click();
  await expect.poll(() => parentWrites.length).toBe(2);
  expect(parentWrites[1].body.geometry.type).toBe('Point');
  expect(parentWrites[1].headers['geometry-contract-version']).toBe(contract.contractVersion);
});

test('UX-GEO : une divergence de contrat 409 garde le brouillon et son empreinte', async ({ context, page }) => {
  const { parentWrites } = await setup(context, FAMILIES[3], { conflictOnParentPut: true });
  const form = await openParent(page, FAMILIES[3]);
  await form.locator('[data-role=general-tab]').click();
  await expect(form.locator('[data-role=geometry-contract-status]')).toContainText('à qualifier');
  await form.locator('[name=designation]').fill('Brouillon contrat concurrent');
  await context.route(`**/api/ouvrages-particuliers/${P}`, async route => {
    if (route.request().method() !== 'PUT') return route.fallback();
    parentWrites.push({ headers: route.request().headers(), body: route.request().postDataJSON() });
    await route.fulfill({ status: 409, json: { detail: {
      code: 'geometry-contract-changed', message: 'Le contrat géométrique a changé. Rechargez la fiche.' } } });
  });
  await form.locator('[data-role=save]').click();
  await expect(form.locator('[data-role=status]')).toContainText('contrat géométrique a changé');
  await expect(form.locator('[name=designation]')).toHaveValue('Brouillon contrat concurrent');
  expect(parentWrites).toHaveLength(1);
  expect(parentWrites[0].headers['geometry-contract-version']).toBe('a'.repeat(64));
  expect(parentWrites[0].body).not.toHaveProperty('geometry');
});

for (const [label, historical, status, error] of [
  ['MultiPoint', { type: 'MultiPoint', coordinates: [[2, 46], [2.01, 46.01]] }, 'existante hors contrat', 'doit être de type'],
  ['Point EMPTY', { type: 'Point', coordinates: [] }, 'vide, non dessinable', 'est vide'],
  ['NULL', null, 'Aucune géométrie', 'obligatoire'],
]) {
test(`UX-GEO : un ouvrage historique ${label} reste corrigeable sans réécrire sa géométrie`, async ({ context, page }) => {
  const { parentWrites } = await setup(context, FAMILIES[3], {
    parentGeometry: historical, parentProperties: { valid: false },
  });
  const form = await openParent(page, FAMILIES[3]);
  await form.locator('[data-role=general-tab]').click();
  await expect(form.locator('[data-role=geometry-status]')).toContainText(status);
  await form.locator('[name=designation]').fill('Attribut historique corrigé');
  await form.locator('[name=valid]').check();
  await form.locator('[data-role=save]').click();
  await expect(form.locator('[data-role=status]')).toContainText(error);
  expect(parentWrites).toHaveLength(0);
  await form.locator('[name=valid]').uncheck();
  await form.locator('[data-role=save]').click();
  await expect.poll(() => parentWrites.length).toBe(1);
  expect(parentWrites[0].body).not.toHaveProperty('geometry');
  expect(parentWrites[0].body.valid).toBe(false);
  await expect(form.locator('[data-role=geometry-status]')).toContainText(status);
});
}

test('UX-GEO : une réponse de capacités tardive ne remplace pas celle de la nouvelle ouverture', async ({ context, page }) => {
  await setup(context, FAMILIES[3]);
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  let reads = 0;
  await context.route('**/api/geometry-capabilities/ouvrages-particuliers', async route => {
    const first = ++reads === 1;
    if (first) await gate;
    await route.fulfill({ json: geometryCapabilities({ status: 'approved',
      allowedTypes: first ? ['Polygon'] : ['Point'], contractVersion: (first ? 'a' : 'c').repeat(64) }) });
  });
  await page.goto('/carte');
  await page.waitForFunction(() => typeof mobilierEditor !== 'undefined' && mapViewportReady);
  await page.locator('#toggle-create-menu').click();
  await page.locator('[data-create-type=ouvrage-particulier]').click();
  const form = page.locator('#ouvrage-particulier-editor-panel');
  await expect.poll(() => reads).toBe(1);
  await form.locator('[data-role=cancel]').click();
  await expect(form).toBeHidden();
  await page.locator('#toggle-create-menu').click();
  await page.locator('[data-create-type=ouvrage-particulier]').click();
  await expect(form.locator('[name=geometry_type] option')).toHaveText(['Point']);
  await form.locator('[name=designation]').fill('Nouvelle ouverture');
  const response = page.waitForResponse(response => response.url().includes('/api/geometry-capabilities/'));
  release();
  await response;
  await expect(form.locator('[name=geometry_type] option')).toHaveText(['Point']);
  await expect(form.locator('[name=designation]')).toHaveValue('Nouvelle ouverture');
});

test('UX-GEO : un refus géométrique 422 conserve attributs et tracé à corriger', async ({ context, page }) => {
  await setup(context, FAMILIES[3]);
  const writes = [];
  await context.route(`**/api/ouvrages-particuliers/${P}`, async route => {
    if (route.request().method() !== 'PUT') return route.fallback();
    writes.push(route.request().postDataJSON());
    await route.fulfill({ status: 422, json: { detail: 'Géométrie non conforme au contrat courant.' } });
  });
  const form = await openParent(page, FAMILIES[3]);
  await form.locator('[data-role=general-tab]').click();
  await form.locator('[name=designation]').fill('Correction conservée');
  await form.locator('[name=geometry_type]').selectOption('Point');
  await form.locator('[data-role=draw]').click();
  const box = await page.locator('#map').boundingBox();
  await page.mouse.click(box.x + box.width * 0.4, box.y + box.height * 0.5);
  await expect(form.locator('[data-role=geometry-status]')).toBeEmpty();
  await form.locator('[data-role=save]').click();
  await expect(form.locator('[data-role=status]')).toContainText('non conforme');
  await expect(form.locator('[name=designation]')).toHaveValue('Correction conservée');
  await expect(form.locator('[data-role=geometry-status]')).toBeEmpty();
  expect(writes).toHaveLength(1);
  expect(writes[0].geometry.type).toBe('Point');
  // A canceled replacement keeps the preceding local Point and the independent server error.
  await form.locator('[data-role=draw]').click();
  await form.locator('[data-role=cancel-drawing]').click();
  await expect(form.locator('[data-role=geometry-status]')).toBeEmpty();
  await expect(form.locator('[data-role=status]')).toContainText('non conforme');
  await expect(form.locator('[name=designation]')).toHaveValue('Correction conservée');
  await form.locator('[data-role=save]').click();
  await expect.poll(() => writes.length).toBe(2);
  expect(writes[1].geometry).toEqual(writes[0].geometry);
});

for (const family of [FAMILIES[3], FAMILIES[5]]) {
  test(`UX-GEO GPS ${family[0]} : pause, reprise, confirmation et annulation gardent le contrat de sauvegarde`, async ({ context, page }) => {
    await context.addInitScript(() => {
      const native = window.matchMedia.bind(window);
      window.matchMedia = query => query === '(display-mode: standalone)'
        ? { matches: true, addEventListener() {}, removeEventListener() {} } : native(query);
      window.__gpsWatches = [];
      window.__gpsClears = [];
      Object.defineProperty(navigator, 'geolocation', { configurable: true, value: {
        getCurrentPosition() {},
        watchPosition(resolve, reject) {
          window.__gpsWatches.push({ resolve, reject });
          return window.__gpsWatches.length;
        },
        clearWatch(id) { window.__gpsClears.push(id); },
      } });
    });
    const { parentWrites } = await setup(context, family);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/carte');
    await page.waitForFunction(() => typeof mobilierEditor !== 'undefined' && mapViewportReady);
    await page.locator('#toggle-create-menu').click();
    await page.locator(`[data-create-type=${family[0]}]`).click();
    const form = page.locator(`#${family[0]}-editor-panel`);
    await form.locator('[name=designation]').fill('Tracé GPS conservé');
    if (family[0] === 'vegetation') {
      await form.locator('[name=famille_vegetation]').selectOption('ARBRES');
      await form.locator('[name=disposition]').selectOption('LIGNE');
      await form.locator('[name=sous_type]').selectOption('ARBRE_SUR_PIED');
    }
    await form.locator('[name=geometry_type]').selectOption('LineString');
    await form.locator('[data-gps=start]').click();
    const gps = page.locator('.gps-geometry-tracking--floating');
    const now = Date.now();
    const emit = async (index, longitude, timestamp) => {
      await expect.poll(() => page.evaluate(() => window.__gpsWatches.length)).toBeGreaterThan(index);
      await page.evaluate(({ index, longitude, timestamp }) => {
        window.__gpsWatches[index].resolve({ coords: { longitude, latitude: 48.84, accuracy: 7 }, timestamp });
      }, { index, longitude, timestamp });
    };
    await emit(0, 2.34, now - 10000);
    await gps.locator('[data-gps=pause]').click();
    await expect.poll(() => page.evaluate(() => window.__gpsClears.includes(1))).toBe(true);
    await gps.locator('[data-gps=pause]').click();
    await emit(1, 2.3403, now - 5000);
    await gps.locator('[data-gps=finish]').click();
    await expect(gps.locator('[data-gps=status]')).toContainText('Aperçu prêt');
    expect(parentWrites).toHaveLength(0);
    await gps.locator('[data-gps=confirm]').click();
    await expect(form).toBeVisible();
    await expect(form.locator('[data-role=geometry-status]')).toContainText('LineString prête');
    await expect(form.locator('[name=designation]')).toHaveValue('Tracé GPS conservé');
    expect(parentWrites).toHaveLength(0);
    await form.locator('[data-role=save]').click();
    await expect.poll(() => parentWrites.length).toBe(1);
    expect(parentWrites[0].method).toBe('POST');
    expect(parentWrites[0].body.geometry).toEqual({ type: 'LineString',
      coordinates: [[2.34, 48.84], [2.3403, 48.84]] });
    await expect(form.locator('[data-role=status]')).toContainText('enregistr');
    await form.locator('[data-gps=start]').click();
    await emit(2, 2.3406, Date.now());
    await gps.locator('[data-gps=pause]').click();
    await gps.locator('[data-gps=cancel]').click();
    await expect(form.locator('[data-role=geometry-status]')).toContainText('LineString prête');
    await expect.poll(() => page.evaluate(() => window.__gpsClears.includes(3))).toBe(true);
    expect(parentWrites).toHaveLength(1);
  });
}
