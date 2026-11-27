// Niveau B : DOM/Leaflet, vraie API et PostgreSQL/PostGIS jetable du runner.
// Chaque objet créé est propre au scénario ; aucune cible persistante autorisée.
const {test,expect}=require('@playwright/test');
const {randomUUID}=require('node:crypto');
test.use({serviceWorkers:'block',actionTimeout:10000});
test.skip(process.env.DIGUES_E2E_USE_TEST_DATABASE!=='true','À lancer avec tests.run_postgres --browser sur PostgreSQL jetable.');
async function ready(page){await page.goto('/carte');await expect(page.locator('#map')).toBeVisible();await expect(page.locator('#toggle-create-menu')).toBeEnabled();await page.waitForFunction(()=>typeof mobilierEditor!=='undefined' && mobilierEditor);}
async function createEntry(page,type){await page.locator('#toggle-create-menu').click();await page.locator(`[data-create-type="${type}"]`).click();return page.locator('#editor-panel .entity-form:visible').first();}
async function clickMap(page,positions){const box=await page.locator('#map').boundingBox();for(const [x,y]of positions)await page.locator('#map').click({position:{x:box.width*x,y:box.height*y}});}
async function draw(page,view,type,shift=0){
 if(await page.locator('#heritage-panel').isVisible())await page.locator('#close-heritage').click();
 const selector=view.locator('[name=geometry_type]');if(await selector.isVisible())await selector.selectOption(type);
 const button=view.locator('[data-role=draw],[data-role=place-point]');await expect(button).toBeVisible();
 const leafDraw=['start-troncon-draw','start-desordre-draw'].includes(await button.getAttribute('id'));await button.click();
 const narrow=page.viewportSize().width<=900;
 const points=type==='Point'?[[.55,.4]]:narrow?[[.52,.23],[.80,.23],[.80,.34]]:[[.55,.4],[.61,.4],[.61,.47]];
 await clickMap(page,points.map(([x,y])=>[x+shift,y]));
 if(type!=='Point'){
  if(leafDraw){const box=await page.locator('#map').boundingBox();await page.locator('#map').dblclick({position:{x:box.width*.61,y:box.height*.47}});}
  const finish=page.locator('#finish-editor-map-interaction');if(await finish.isVisible()||!await view.isVisible())await finish.click();
  else if(!leafDraw)await view.locator('[data-role=confirm],[data-role=confirm-geometry]').click();
 }
 if(type==='Point'&&await page.locator('#finish-editor-map-interaction').isVisible())await page.locator('#finish-editor-map-interaction').click();
}

test('création Zone : dessin requis, POST réel et relecture',async({page,request})=>{
 await ready(page);const view=await createEntry(page,'zone-stockage');const label=`Zone régression ${randomUUID()}`;
 const observations=view.locator('[data-role=observations-tab]');
 await expect(observations).toBeDisabled();
 await field(view,'designation').fill(label);
 let posts=0;page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname==='/api/zones-stockage')posts++;});
 await view.locator('[data-role=save]').click();await expect(view.locator('[data-role=status]')).toContainText('Dessinez un Polygon avant de créer la Zone.');expect(posts).toBe(0);
 await draw(page,view,'Polygon');
 const saved=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname==='/api/zones-stockage');
 await view.locator('[data-role=save]').click();const response=await saved;expect(response.status(),await response.text()).toBe(201);
 const feature=await response.json();expect(feature.geometry.type).toBe('Polygon');
 await expect(observations).toBeEnabled();
 await expect(view.locator('[data-role=general-tab]')).toHaveAttribute('aria-selected','true');
 await expect(field(view,'designation')).toHaveValue(label);
 expect(posts).toBe(1);
 const actual=await request.get(`/api/zones-stockage/${feature.properties.id}`);expect(actual.status()).toBe(200);
 const reread=await actual.json();
 expect(reread.properties.designation).toBe(label);expect(reread.geometry).toEqual(feature.geometry);
});

test('création Désordre : type et commande de dessin accessibles sans repérage informatif',async({page})=>{
 await ready(page);const view=await createEntry(page,'desordre');
 await page.screenshot({path:test.info().outputPath('desordre.png'),fullPage:true});
 await expect(view.locator('[name=geometry_type]')).toBeVisible();
 await view.locator('#desordre-xy-choice').click();
 await expect(view.locator('.entity-form__geometry-entry #desordre-create-x')).toBeEditable();
 await expect(view.locator('[data-role=geometry-requirement]')).toHaveText('Géométrie obligatoire');
 await expect(view.locator('[name=desordre-mode][value=map]').locator('..')).toBeVisible();
 await view.locator('[name=desordre-mode][value=map]').locator('..').click();
 await draw(page,view,'Point');
});

const CASES=[
 ['systeme','systemes','systemes-endiguement',null],['digue','digues','digues',null],['troncon','troncons','troncons','LineString'],
 ['mobilier','mobilier','mobilier','Point'],['equipement-mesure','equipements_mesure','equipements-mesure','Point'],
 ['reseau-technique','reseaux_techniques','reseaux-techniques','LineString'],['ouvrage-particulier','ouvrages_particuliers','ouvrages-particuliers','Point'],
 ['cheminement','cheminements','cheminements','LineString'],['vegetation','vegetation','vegetation','Point'],
 ['amenagement-hydraulique','amenagements_hydrauliques','amenagements-hydrauliques',null],['zone-stockage','zones_stockage','zones-stockage','Polygon'],
 ['prestation','prestations','prestations','Point'],['troncons-lit','troncons_lit','troncons-lit','LineString'],['elements-morphologiques-lit','elements_morphologiques_lit','elements-morphologiques-lit','Point'],
];
test('composition géométrique sur Carte : titres, types, modes et commandes', async ({ page }) => {
  test.setTimeout(90000);
  let commonButtonStyle;
  const cases = [
    ['desordre', 'Nouveau désordre', ['Point']],
    ['mobilier', 'Nouveau mobilier', ['Point']],
    ['ouvrage-particulier', 'Nouvel ouvrage particulier', ['LineString']],
    ['reseau-technique', 'Nouveau réseau technique', ['Point', 'LineString']],
    ['vegetation', 'Nouvelle végétation', ['Point', 'LineString', 'Polygon']],
    ['zone-stockage', 'Nouvelle zone de stockage', ['Polygon']],
    ['troncon', 'Nouveau tronçon', ['LineString']],
  ];
  for (const [family, title, types] of cases) await test.step(family, async () => {
    await ready(page);
    const view = await createEntry(page, family);
    await expect(field(view, 'designation')).toBeEditable();
    await expect(view.locator('.entity-form__title').first()).toHaveText(title);
    await expect(view.locator('[data-role=status]').first()).toBeEmpty();
    const geometry = view.locator('[data-section=geometry]');
    const selector = geometry.locator('[name=geometry_type]');
    for (const type of types) {
      if (await selector.isVisible()) await selector.selectOption(type);
      else await expect(geometry.locator('.entity-form__geometry-fixed-type')).toHaveText(type);
      const drawButton = geometry.getByRole('button', { name: 'Dessiner', exact: true });
      await expect(drawButton).toBeEnabled();
      const style = await drawButton.evaluate(button => {
        const { backgroundImage, borderRadius, minHeight } = getComputedStyle(button);
        return { backgroundImage, borderRadius, minHeight };
      });
      commonButtonStyle ??= style;
      expect(style).toEqual(commonButtonStyle);
      await geometry.scrollIntoViewIfNeeded();
      await page.screenshot({ path: test.info().outputPath(`${family}-${type}.png`) });
    }
  });
});

function properties(body){return body.properties||body;}
function field(view,name){return view.locator(`:scope > .entity-form__body > form [name="${name}"]`);}
async function prepareFields(view,type,label){
 await expect(field(view,'designation')).toBeEditable();await field(view,'designation').fill(label);
 if(['systeme','digue','troncon'].includes(type))await view.locator('[name=libelle]').fill(label);
 for(const field of await view.locator('select[required]:visible').all())if(!await field.inputValue())await field.selectOption({index:1});
 if(type==='prestation')await view.locator('[name=date_fin]').fill('2026-11-10');
}
async function saveRequest(page,view,resource,method,id){
 const path=`/api/${resource}${id?'/'+id:''}`;
 const pending=page.waitForResponse(r=>r.request().method()===method&&new URL(r.url()).pathname===path);
 await view.locator(':scope > .entity-form__body > form [data-role=save]').click();const response=await pending;
 expect(response.status(),await response.text()).toBe(method==='POST'?201:200);return {body:await response.json(),sent:response.request().postDataJSON()};
}

async function selectLocationMode(view, mode) {
  const button = view.getByRole('group', { name: 'Mode de localisation' }).getByText(mode, { exact: true });
  await button.click();
}

async function enterCoordinates(view, pairs) {
  for (const [index, pair] of pairs.entries()) {
    const endpoint = index === 0 ? 'start' : 'end';
    for (const [axis, value] of pair.entries()) {
      await field(view, `coordinate_${endpoint}_${axis + 1}`).fill(String(value));
    }
  }
}

function expectPosition(position, expected) {
  expect(position[0]).toBeCloseTo(expected[0], 7);
  expect(position[1]).toBeCloseTo(expected[1], 7);
}

test('coordonnées communes : création et édition avec relecture PostgreSQL', async ({ page, request }) => {
  test.setTimeout(180000);
  const positions = [[2.4, 50.4], [2.41, 50.405]];
  async function projected([longitude, latitude]) {
    const response = await request.get('/api/coordinates/transform', { params: { longitude, latitude } });
    expect(response.ok()).toBe(true);
    const { x, y } = await response.json();
    return [x, y];
  }
  const xy = await Promise.all(positions.map(projected));
  const cases = [
    ...CASES.filter(([, , , shape]) => ['Point', 'LineString'].includes(shape)),
    ['reseau-technique', 'reseaux_techniques', 'reseaux-techniques', 'Point'],
    ['ouvrage-particulier', 'ouvrages_particuliers', 'ouvrages-particuliers', 'LineString'],
    ['vegetation', 'vegetation', 'vegetation', 'LineString'],
  ];
  for (const [type, family, resource, geometry] of cases) {
    await test.step(`${type} ${geometry}`, async () => {
      await ready(page);
      const view = await createEntry(page, type);
      const label = `Coordonnées ${type} ${randomUUID()}`;
      await prepareFields(view, type, label);
      const selector = field(view, 'geometry_type');
      if (await selector.isVisible()) await selector.selectOption(geometry);
      const isLine = geometry === 'LineString';
      await selectLocationMode(view, 'X/Y');
      await expect(view.getByRole('spinbutton', { name: 'X (EPSG:3950)', exact: true })).toHaveCount(isLine ? 2 : 1);
      await enterCoordinates(view, xy.slice(0, isLine ? 2 : 1));
      if (type === 'mobilier') {
        await view.locator('[data-role=save]').click();
        await expect(view.getByRole('status').filter({ hasText: 'Confirmez ou annulez' })).toBeVisible();
        await view.locator('[data-role=cancel-coordinates]').click();
        await expect(field(view, 'designation')).toHaveValue(label);
        await expect(field(view, 'coordinate_start_1')).toBeEmpty();
        await enterCoordinates(view, xy.slice(0, 1));
      }
      await view.locator('[data-role=confirm-coordinates]').click();
      await expect(view.getByText('Coordonnées confirmées dans le brouillon.')).toBeVisible();
      const saved = await saveRequest(page, view, resource, 'POST');
      const id = properties(saved.body).id;
      const reread = await (await request.get(`/api/${resource}/${id}`)).json();
      expect(properties(reread).designation).toBe(label);
      expect(reread.geometry).toEqual(saved.body.geometry);
      expect(reread.geometry.type).toBe(geometry);
      const coords = isLine ? reread.geometry.coordinates : [reread.geometry.coordinates];
      expect(coords).toHaveLength(isLine ? 2 : 1);
      coords.forEach((position, index) => expectPosition(position, positions[index]));

      await ready(page);
      await page.evaluate(({ family, id }) => MapEntityNavigation.open(family, id), { family, id });
      const edit = page.locator('#editor-panel .entity-form:visible').first();
      await expect(field(edit, 'designation')).toHaveValue(label);
      if (type === 'troncon') {
        await expect(edit.getByRole('group', { name: 'Mode de localisation' })).toBeHidden();
        return;
      }
      await selectLocationMode(edit, 'Lat/Lon');
      expect(Number(await field(edit, 'coordinate_start_1').inputValue())).toBeCloseTo(2.4, 7);
      const replacement = [2.401, 50.401];
      await enterCoordinates(edit, [replacement]);
      await edit.locator('[data-role=confirm-coordinates]').click();
      const changed = await saveRequest(page, edit, resource, 'PUT', id);
      const actual = await (await request.get(`/api/${resource}/${id}`)).json();
      expect(actual.geometry).toEqual(changed.body.geometry);
      expectPosition(isLine ? actual.geometry.coordinates[0] : actual.geometry.coordinates, replacement);
      if (isLine) expectPosition(actual.geometry.coordinates.at(-1), positions[1]);
    });
  }
});

test('Réseau technique : édition numérique des extrémités avec sommet intermédiaire', async ({ page, request }) => {
  const catalog = await (await request.get('/api/referentiels/types-reseau-technique')).json();
  const type = (catalog.types || catalog.references || [])[0];
  expect(type).toBeDefined();
  const positions = [[2.4, 50.4], [2.404, 50.404], [2.41, 50.405]];
  const response = await request.post('/api/reseaux-techniques', { data: {
    designation: `Sommet ${randomUUID()}`, type_id: type.id,
    geometry: { type: 'LineString', coordinates: positions },
  } });
  expect(response.status(), await response.text()).toBe(201);
  const before = await response.json();
  const id = properties(before).id;
  await ready(page);
  await page.evaluate(id => MapEntityNavigation.open('reseaux_techniques', id), id);
  const view = page.locator('#reseau-technique-editor-panel');
  await selectLocationMode(view, 'Lat/Lon');
  const replacement = [2.401, 50.401];
  await enterCoordinates(view, [replacement]);
  await view.locator('[data-role=confirm-coordinates]').click();
  const saved = await saveRequest(page, view, 'reseaux-techniques', 'PUT', id);
  expect(saved.sent.geometry.coordinates).toEqual([replacement, ...before.geometry.coordinates.slice(1)]);
  const after = await (await request.get(`/api/reseaux-techniques/${id}`)).json();
  expect(after.geometry.coordinates).toHaveLength(3);
  after.geometry.coordinates.forEach((position, index) =>
    expectPosition(position, index === 0 ? replacement : positions[index]));
});

test('Désordre : X/Y, Lat/Lon et Bornage enregistrés avec leurs contrats existants', async ({ page, request }) => {
  // Le Point X/Y a une précision de saisie au centimètre. L'origine du
  // CC50 est exprimable exactement avec ce pas.
  const positions = [[3, 50], [3.01, 50.005]];
  const result = await request.get('/api/coordinates/transform', {
    params: { longitude: positions[0][0], latitude: positions[0][1] },
  });
  const { x, y } = await result.json();
  expect(result.ok()).toBe(true);
  for (const [type, mode, inputs, coordinates] of [
    ['Point', 'X/Y', [['x', x.toFixed(2)], ['y', y.toFixed(2)]], positions[0]],
    ['LineString', 'Lat/Lon', [
      ['line-start-1', positions[0][0]], ['line-start-2', positions[0][1]],
      ['line-end-1', positions[1][0]], ['line-end-2', positions[1][1]],
    ], positions],
  ]) {
    await ready(page);
    const view = await createEntry(page, 'desordre');
    await field(view, 'designation').fill(`Désordre ${mode} ${randomUUID()}`);
    await field(view, 'geometry_type').selectOption(type);
    await selectLocationMode(view, mode);
    for (const [key, value] of inputs) await view.locator(`#desordre-create-${key}`).fill(String(value));
    const created = await saveRequest(page, view, 'desordres', 'POST');
    const id = properties(created.body).id;
    const actual = await (await request.get(`/api/desordres/${id}`)).json();
    expect(actual.geometry.type).toBe(type);
    if (type === 'Point') expectPosition(actual.geometry.coordinates, coordinates);
    else actual.geometry.coordinates.forEach((point, index) => expectPosition(point, coordinates[index]));
  }

  const { troncons } = await (await request.get('/api/troncons/options')).json();
  const options = await (await request.get(`/api/troncons/${troncons[0].id}/reperage-options`)).json();
  expect(options.bornes.length).toBeGreaterThanOrEqual(2);
  await ready(page);
  const view = await createEntry(page, 'desordre');
  await field(view, 'designation').fill(`Désordre Bornage ${randomUUID()}`);
  await field(view, 'geometry_type').selectOption('LineString');
  const bornage = view.locator('#desordre-bornage-choice');
  await expect(bornage).toBeHidden();
  await field(view, 'troncon_ids').selectOption(troncons[0].id);
  await selectLocationMode(view, 'Bornage');
  for (const [index, endpoint] of ['start', 'end'].entries()) {
    await view.locator(`#desordre-create-borne-${endpoint}`).selectOption(options.bornes[index].id);
    const distance = view.locator(`#desordre-create-distance-${endpoint}`);
    await expect(distance).toHaveAttribute('step', '1');
    await distance.fill('12.34');
    await distance.press('ArrowUp');
    await expect(distance).toHaveValue('13.34');
    await view.locator(`#desordre-create-sense-${endpoint}`).selectOption(index === 0 ? 'APRES_BORNE' : 'AVANT_BORNE');
  }
  const created = await saveRequest(page, view, 'desordres', 'POST');
  const actual = await (await request.get(`/api/desordres/${properties(created.body).id}`)).json();
  expect(actual.geometry.type).toBe('LineString');
  expect(actual.properties.troncon_ids).toEqual([troncons[0].id]);
  expect(actual.properties.reperage).toMatchObject({
    borne_debut_id: options.bornes[0].id, borne_fin_id: options.bornes[1].id,
    distance_debut_m: 13.34, distance_fin_m: 13.34,
  });
});

test('Végétation Polygon : dessin Carto et relecture sans saisie d’extrémités', async ({ page, request }) => {
  await ready(page);
  const view = await createEntry(page, 'vegetation');
  const label = `Végétation surfacique ${randomUUID()}`;
  await prepareFields(view, 'vegetation', label);
  await field(view, 'geometry_type').selectOption('Polygon');
  await expect(view.getByRole('group', { name: 'Mode de localisation' })).toBeHidden();
  await draw(page, view, 'Polygon');
  const { body } = await saveRequest(page, view, 'vegetation', 'POST');
  const actual = await (await request.get(`/api/vegetation/${properties(body).id}`)).json();
  expect(actual.properties.designation).toBe(label);
  expect(actual.geometry.type).toBe('Polygon');
  expect(actual.geometry).toEqual(body.geometry);
});

test('Bornage partagé : Points et lignes créés, modifiés et relus par les API métier', async ({ page, request }) => {
  test.setTimeout(180000);
  const { troncons } = await (await request.get('/api/troncons/options')).json();
  const options = await (await request.get(`/api/troncons/${troncons[0].id}/reperage-options`)).json();
  expect(options.bornes.length).toBeGreaterThanOrEqual(2);
  for (const [type, family, resource, shape] of CASES.filter(([, family]) =>
    ['mobilier', 'equipements_mesure', 'ouvrages_particuliers', 'reseaux_techniques', 'vegetation', 'cheminements', 'prestations'].includes(family))) {
    await test.step(type, async () => {
      await ready(page);
      const view = await createEntry(page, type);
      const label = `Bornage ${type} ${randomUUID()}`;
      await prepareFields(view, type, label);
      const selector = field(view, 'geometry_type');
      if (await selector.isVisible()) await selector.selectOption(shape);
      const parent = view.locator('[name=troncon_id],[name=troncon_ids]').first();
      await parent.selectOption(troncons[0].id);
      await selectLocationMode(view, 'Bornage');
      async function locate(distance) {
        for (const [index, endpoint] of (shape === 'LineString' ? ['start', 'end'] : ['start']).entries()) {
          await field(view, `bornage_${endpoint}_borne`).selectOption(options.bornes[index].id);
          await field(view, `bornage_${endpoint}_distance`).fill(String(distance));
          await field(view, `bornage_${endpoint}_sense`).selectOption(distance ? (index ? 'AVANT_BORNE' : 'APRES_BORNE') : 'SUR_BORNE');
        }
        const calculation = page.waitForResponse(response => new URL(response.url()).pathname.endsWith('/geometrie-bornage'));
        await view.locator('[data-role=confirm-bornage]').click();
        const response = await calculation;
        expect(response.status(), await response.text()).toBe(200);
        await expect(view.getByText('Bornage confirmé dans le brouillon.')).toBeVisible();
        return (await response.json()).geometry;
      }
      const expected = await locate(0);
      const created = await saveRequest(page, view, resource, 'POST');
      const id = properties(created.body).id;
      const initial = await (await request.get(`/api/${resource}/${id}`)).json();
      expect(initial.geometry.type).toBe(shape);
      const actualPoints = shape === 'Point' ? [initial.geometry.coordinates] : initial.geometry.coordinates;
      const expectedPoints = shape === 'Point' ? [expected.coordinates] : expected.coordinates;
      actualPoints.forEach((point, index) => expectPosition(point, expectedPoints[index]));
      expect(properties(initial)[await parent.getAttribute('name')]).toEqual(
        await parent.getAttribute('name') === 'troncon_id' ? troncons[0].id : [troncons[0].id]);
      await ready(page);
      await page.evaluate(({ family, id }) => MapEntityNavigation.open(family, id), { family, id });
      await expect(field(view, 'designation')).toHaveValue(label);
      await selectLocationMode(view, 'Bornage');
      const editedGeometry = await locate(1);
      await saveRequest(page, view, resource, 'PUT', id);
      const reread = await (await request.get(`/api/${resource}/${id}`)).json();
      expect(properties(reread).version).toBeGreaterThan(properties(initial).version);
      const points = shape === 'Point' ? [reread.geometry.coordinates] : reread.geometry.coordinates;
      const targets = shape === 'Point' ? [editedGeometry.coordinates] : editedGeometry.coordinates;
      points.forEach((point, index) => expectPosition(point, targets[index]));
    });
  }
});

test('familles autonomes : dessin utilisateur, POST/PUT, relations et relecture SQL indépendante',async({page,request})=>{
 test.setTimeout(300000);const errors=[];const results=[];
 for(const [type,family,resource,geometryType]of CASES){
  try{
   await ready(page);let view=await createEntry(page,type);const label=`Régression ${type} ${randomUUID()}`;
   await prepareFields(view,type,label);
   if(geometryType)await draw(page,view,geometryType);
   const created=await saveRequest(page,view,resource,'POST');const original=created.body,id=properties(original).id;
   expect(id).toMatch(/^[0-9a-f-]{36}$/);if(geometryType)expect(original.geometry.type).toBe(geometryType);
   const before=await (await request.get(`/api/${resource}/${id}`)).json();expect(properties(before).designation).toBe(label);
   expect(before.geometry).toEqual(original.geometry);
   for(const [key,value]of Object.entries(created.sent))if(key.endsWith('_id')||key.endsWith('_ids'))expect(properties(before)[key],key).toEqual(value);
   await page.reload();await page.waitForFunction(()=>typeof mobilierEditor!=='undefined');
   await page.evaluate(({family,id})=>MapEntityNavigation.open(family,id),{family,id});view=page.locator('#editor-panel .entity-form:visible').first();
   await expect(field(view,'designation')).toHaveValue(label);
   await field(view,'designation').fill(label+' modifié');
   const edited=await saveRequest(page,view,resource,'PUT',id);expect(edited.sent).not.toHaveProperty('geometry');
   const after=await (await request.get(`/api/${resource}/${id}`)).json();expect(properties(after).designation).toBe(label+' modifié');expect(after.geometry).toEqual(before.geometry);
   expect(properties(after).version).toBeGreaterThan(properties(before).version);
   const replace=geometryType && family!=='troncons';
   if(replace){
    if(['ouvrages_particuliers','troncons_lit','elements_morphologiques_lit'].includes(family)){
     const contract=await(await request.get(`/api/geometry-capabilities/${resource}`)).json();expect(contract).toMatchObject({edit:true,replace:true});
    }
    if(family==='zones_stockage'){
     await view.locator('[name=commentaire]').fill('Attribut préservé après annulation');
     await view.locator('[data-role=draw]').click();await clickMap(page,[[.55,.4]]);
     await view.locator('[data-role=cancel-drawing]').click();
     await expect(view.locator('[name=commentaire]')).toHaveValue('Attribut préservé après annulation');
     expect((await(await request.get(`/api/${resource}/${id}`)).json()).geometry).toEqual(before.geometry);
    }
    await draw(page,view,geometryType,.08);const spatial=await saveRequest(page,view,resource,'PUT',id);
    expect(spatial.sent.geometry.type).toBe(geometryType);
    const reread=await(await request.get(`/api/${resource}/${id}`)).json();expect(reread.geometry).toEqual(spatial.body.geometry);expect(reread.geometry).not.toEqual(before.geometry);
   }
   if(family==='troncons_lit')await childrenLit(page,view,request,id);
   await baseEdit(page,request,family,resource,id,label);
   results.push({family,id,creation:true,edition:true,base:true,replacement:Boolean(replace),geometry:geometryType||'sans contrat spatial',relations:Object.keys(created.sent).filter(k=>k.endsWith('_id')||k.endsWith('_ids'))});
  }catch(error){errors.push({family,error:error.message});console.log('REGRESSION',family,error.message);}
 }
 require('node:fs').writeFileSync(test.info().outputPath('matrice-execution.json'),JSON.stringify({results,errors},null,2));expect(errors).toEqual([]);
});

const CHARACTERISTICS={largeurs:'largeurs_lit',pentes:'pentes_lit',domanialites:'domanialites_lit','regimes-ecoulement':'regimes_ecoulement_lit','occupations-riveraines':'occupations_riveraines_lit'};
async function childrenLit(page,view,request,id){
 for(const [name,collection]of Object.entries(CHARACTERISTICS)){
  const host=view.locator('[data-role=lit-characteristics]');await host.locator(`[data-family="${name}"] [data-action=add-characteristic]`).click();
  const child=host.locator('.entity-form');await expect(child.locator('[name=designation]')).toBeEditable();const label=`Enfant ${name} ${randomUUID()}`;
  await child.locator('[name=designation]').fill(label);
  const created=await saveRequest(page,child,`troncons-lit/${id}/caracteristiques/${name}`,'POST');
  const row=created.body.properties[collection].find(c=>c.designation===label);expect(row.id).toBe(created.sent.id);
  await host.locator(`[data-family="${name}"] li[data-id="${row.id}"]`).getByRole('button',{name:'Modifier',exact:true}).click();
  await child.locator('[name=commentaire]').fill('Occurrence modifiée indépendamment');
  await saveRequest(page,child,`troncons-lit/${id}/caracteristiques/${name}`,'PUT',row.id);
  const parent=await (await request.get(`/api/troncons-lit/${id}`)).json();
  expect(parent.properties[collection].find(c=>c.id===row.id)).toMatchObject({designation:label,commentaire:'Occurrence modifiée indépendamment'});
 }
}
async function baseEdit(page,request,family,resource,id,label){
 const before=await(await request.get(`/api/${resource}/${id}`)).json();
 await page.goto('/base');await expect(page.locator('main')).toHaveJSProperty('inert',false);
 await page.evaluate(({family,id})=>document.querySelector('main').dispatchEvent(new CustomEvent('digues-app:open-entity',{detail:{entityClass:family,entityId:id,source:'home',entities:[]}})),{family,id});
 await page.locator('#data-entity-detail-edit').click();const view=page.locator('#data-entity-detail > .entity-form');
 await expect(field(view,'designation')).toBeEditable();await field(view,'designation').fill(label+' Base');
 const result=await saveRequest(page,view,resource,'PUT',id);expect(result.sent).not.toHaveProperty('geometry');
 const after=await(await request.get(`/api/${resource}/${id}`)).json();expect(properties(after).designation).toBe(label+' Base');expect(after.geometry).toEqual(before.geometry);
}

test('Observation et Photo : parent réel, création/édition, fichier et localisation du photographe persistés',async({page,request})=>{
 test.setTimeout(120000);
 const parentResponse=await request.post('/api/zones-stockage',{data:{designation:`Parent photo ${randomUUID()}`,geometry:{type:'Polygon',coordinates:[[[2.4,50.4],[2.41,50.4],[2.41,50.41],[2.4,50.4]]]}}});
 expect(parentResponse.status()).toBe(201);const parent=(await parentResponse.json()).properties;
 await ready(page);await page.evaluate(id=>MapEntityNavigation.open('zones_stockage',id),parent.id);
 const view=page.locator('#zone-stockage-editor-panel');await view.locator('[data-role=observations-tab]').click();await view.locator('[data-role=create-observation]').click();
 const create=page.locator('#observation-create-dialog > .entity-form');const label=`Observation ${randomUUID()}`;
 await expect(create.locator('[name=designation]')).toBeEditable();await create.locator('[name=designation]').fill(label);
 const made=await saveRequest(page,create,'observations','POST');const obs=made.body;expect(obs.parent).toMatchObject({resource:'zones_stockage',id:parent.id});
 await create.locator('[data-role=close]').click();await view.locator('.observation-list-item',{hasText:label}).click();
 const detail=page.locator('#observation-detail-dialog > .entity-form');await expect(detail.locator('[name=designation]')).toHaveValue(label);
 await detail.locator('[name=suite]').fill('Suite réellement sauvegardée');await saveRequest(page,detail,'observations','PUT',obs.id);
 await expect(detail.locator('[data-role=status]')).toContainText('enregistrée');
 await detail.locator('.photo-add').click();const photo=page.locator('#photo-editor-dialog > .entity-form');
 await photo.locator('[name=file]').setInputFiles({name:'synthese.png',mimeType:'image/png',buffer:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAwAAAAICAIAAABChommAAAAFUlEQVR4nGNkYPjPQAgwEVQx7BUBAC5SAQ9y7SbzAAAAAElFTkSuQmCC','base64')});
 await photo.locator('[name=libelle]').fill('Photo régression');const uploaded=await saveRequest(page,photo,'photos','POST');expect(uploaded.body.observation_id).toBe(obs.id);
 await expect(photo.locator('[data-role=status]')).toContainText('ajoutée');await photo.locator('[data-role=close]').click();
 await detail.getByRole('button',{name:'Modifier les métadonnées'}).click();await photo.locator('[name=libelle]').fill('Photo modifiée');await saveRequest(page,photo,'photos','PUT',uploaded.body.id);
 await expect(photo.locator('[data-role=status]')).toContainText('enregistrée');await photo.locator('[data-role=close]').click();
 // The specialized existing localization editor owns its own map and draft.
 await detail.getByRole('button',{name:'Localisation du photographe',exact:true}).click();const location=page.locator('#photo-localisation-dialog');
 await expect(location.getByRole('group', { name: 'Mode de localisation' })).toBeVisible();
 await location.locator('[data-section=geometry]').scrollIntoViewIfNeeded();
 await page.screenshot({ path: test.info().outputPath('photo-localisation.png') });
 await location.locator('[data-map-start]').click();await expect(location.locator('[data-map]')).toBeVisible();
 await location.locator('[data-map]').click({position:{x:150,y:100}});await location.locator('[data-map-accept]').click();
 const located=page.waitForResponse(r=>r.request().method()==='PUT'&&new URL(r.url()).pathname.includes(`/api/photos/${uploaded.body.id}/localisation`));
 await location.locator('[type=submit]').click();expect((await located).status()).toBe(200);
 const reread=await(await request.get(`/api/photos/${uploaded.body.id}`)).json();expect(reread.libelle).toBe('Photo modifiée');expect(reread.localisation.geometry_4326.type).toBe('Point');
 expect((await request.get(reread.content_url)).status()).toBe(200);
 const observation=await(await request.get(`/api/observations/${obs.id}`)).json();expect(observation.parent.id).toBe(parent.id);expect(observation.suite).toBe('Suite réellement sauvegardée');
});

async function openDisorder(page,id){
 await ready(page);
 await page.waitForFunction(id=>mapViewportReady && desordreLayersById.get(id)?.getElement(),id);
 await page.evaluate(id=>{
  const layer=desordreLayersById.get(id);
  layer.getElement().dataset.testDisorder=id;
  if(layer.getBounds)map.fitBounds(layer.getBounds(),{padding:[55,160],maxZoom:18,animate:false});
 },id);
 await page.locator(`[data-test-disorder="${id}"]`).dispatchEvent('click');
 const view=page.locator('#disorder-form-view');await expect(field(view,'designation')).toBeEditable();return view;
}
async function moveVertex(page){
 const handle=await page.waitForFunction(()=>{
  for(const node of document.querySelectorAll('.leaflet-vertex-icon')){
   const r=node.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2;
   if(document.elementFromPoint(x,y)===node)return true;
  }
  return false;
 },null,{timeout:5000});
 await handle.dispose();
 const vertices=page.locator('.leaflet-vertex-icon');
 const index=await vertices.evaluateAll(nodes=>nodes.findIndex(node=>{
  const r=node.getBoundingClientRect();return document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)===node;
 }));
 const vertex=vertices.nth(index);
 await vertex.hover();
 const box=await vertex.boundingBox();const x=box.x+box.width/2,y=box.y+box.height/2;
 await page.mouse.down();await page.mouse.move(x+12,y+20,{steps:4});await page.mouse.up();
}
test('Désordre : Point, ligne et polygone dessinés, modifiés et relus dans PostgreSQL',async({page,request})=>{
 test.setTimeout(150000);
 for(const type of ['Point','LineString','Polygon'])await test.step(type,async()=>{
  await page.setViewportSize({width:1280,height:720});
  await ready(page);let view=await createEntry(page,'desordre');const label=`Désordre ${type} ${randomUUID()}`;
  await field(view,'designation').fill(label);await draw(page,view,type);
  const created=await saveRequest(page,view,'desordres','POST');const original=created.body,id=original.properties.id;
  expect(original.geometry.type).toBe(type);
  if(type!=='Point')await page.setViewportSize({width:390,height:844});
  view=await openDisorder(page,id);await expect(field(view,'designation')).toHaveValue(label);
  await expect(view.getByText('Repérage actuel', { exact: true })).toHaveCount(0);
  if (type === 'Polygon') {
    const details = view.locator('#polygon-representative-point');
    await expect(details).toBeVisible();
    await expect(details).toHaveJSProperty('open', false);
  }
  const change=type==='Point'?'#start-map-position':type==='LineString'?'#start-line-edit':'#start-desordre-draw';
  const finish=type==='Point'?view.locator('#validate-map-position'):page.locator('#finish-editor-map-interaction');
  await view.locator(change).click();
  if(type==='Point')await clickMap(page,[[.67,.41]]);
  else {
   await expect(page.locator('#editor-panel')).toBeHidden();
   // Le panneau se replie avant que la carte ne retrouve ses dimensions.
   await page.evaluate(id => {
    map.closePopup();
    map.stop();
    map.invalidateSize({ pan: false, animate: false });
    map.fitBounds(desordreLayersById.get(id).getBounds(), { padding: [55, 160], maxZoom: 18, animate: false });
   }, id);
   await page.waitForFunction(() => !map._animatingZoom && !map._panAnim?._inProgress);
   const path=page.locator(`[data-test-disorder="${id}"]`);
   const initialPath=await path.getAttribute('d');
   await moveVertex(page);
   await expect(path).not.toHaveAttribute('d',initialPath);
  }
  if(type!=='Point') { await expect(finish).toBeVisible(); await expect(finish).toBeEnabled(); await finish.click(); }
  const unchanged=await(await request.get(`/api/desordres/${id}`)).json();expect(unchanged.geometry).toEqual(original.geometry);
  const result=await saveRequest(page,view,'desordres','PUT',id);expect(result.body.properties.id).toBe(id);
  const current=await(await request.get(`/api/desordres/${id}`)).json();expect(current.geometry).not.toEqual(original.geometry);expect(current.geometry.type).toBe(type);
  expect(current.properties.designation).toBe(label);expect(current.properties.version).toBeGreaterThan(original.properties.version);
  if(type==='Point')await baseEdit(page,request,'desordres','desordres',id,label);
 });
});

for(const [width,height] of [[390,844],[844,390]])test(`PWA ${width}×${height} : dessiner, annuler et enregistrer une Zone`,async({page,request})=>{
 await page.setViewportSize({width,height});
 await page.addInitScript(()=>{const match=window.matchMedia.bind(window);window.matchMedia=q=>q==='(display-mode: standalone)'?{matches:true,media:q,addEventListener(){},removeEventListener(){}}:match(q);});
 await ready(page);const view=await createEntry(page,'zone-stockage');const label=`Zone PWA ${width} ${randomUUID()}`;
 await field(view,'designation').fill(label);await view.locator('[data-role=draw]').click();
 const cancel=page.locator('#cancel-editor-map-interaction');
 if(await cancel.isVisible())await cancel.click();else await view.locator('[data-role=cancel-drawing]').click();
 await expect(field(view,'designation')).toHaveValue(label);await draw(page,view,'Polygon');
 const result=await saveRequest(page,view,'zones-stockage','POST');const reread=await(await request.get(`/api/zones-stockage/${result.body.properties.id}`)).json();
 expect(reread.geometry).toEqual(result.body.geometry);expect(reread.properties.designation).toBe(label);
 await page.screenshot({path:test.info().outputPath('pwa.png')});
});

// The optional geometry path must remain a real save, not an empty map draft.
test('Réseau technique sans géométrie : rattachement existant et POST autorisé',async({page,request})=>{
 await ready(page);const view=await createEntry(page,'reseau-technique');const label=`Réseau non localisé ${randomUUID()}`;
 await prepareFields(view,'reseau-technique',label);await field(view,'troncon_id').selectOption({index:1});
 const parent=await field(view,'troncon_id').inputValue();expect(parent).toMatch(/^[0-9a-f-]{36}$/);
 const saved=await saveRequest(page,view,'reseaux-techniques','POST');expect(saved.sent).not.toHaveProperty('geometry');
 const actual=await(await request.get(`/api/reseaux-techniques/${saved.body.properties.id}`)).json();
 expect(actual.geometry).toBeNull();expect(actual.properties).toMatchObject({designation:label,troncon_id:parent});
});
