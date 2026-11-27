// FORM-CORE-2, niveau B (~2 min) : vrais DOM/contrôleurs Carte, transport HTTP
// synthétique isolé par scénario. Les tests PostgreSQL existants prouvent SQL/OCC.
const {test, expect} = require('@playwright/test');
const {fixture, ready, clickFeature, errorsByContext, T} = require('./fixtures/map-editor-fixture');
const {fakeId} = require('./fixtures/frontend-preview-data');
test.use({serviceWorkers:'block'});
test.afterEach(async ({context}) => expect(errorsByContext.get(context) || []).toEqual([]));
const order = ['identification','classification','attachments','geometry','actions'];
async function create(page, family='mobilier') {
  await page.locator('#toggle-create-menu').click();
  await page.locator(`[data-create-type="${family}"]`).click();
  const view = page.locator(`#${family}-editor-panel`);
  await expect(view.locator('[name=designation]')).toBeEditable();
  return view;
}
async function placePoint(page, view) {
  await view.locator('[data-role=place-point]').click();
  const map = page.locator('#map'), bounds = await map.boundingBox();
  await map.click({position:{x:bounds.width*0.4,y:bounds.height*0.5}});
  // Coordinates now live in the shared spatial detail rather than a repeated
  // point-status sentence. Require actual numeric output, then retain the
  // save/reload assertions on the real Point geometry below.
  const longitude=view.locator('[data-spatial-read] dl > div')
    .filter({hasText:'Position — longitude (EPSG:4326)'}).locator('dd');
  await expect(longitude).toHaveText(/^-?\d+(\.\d+)?$/);
  await expect(view.locator('[data-role=cancel-drawing]').first()).toBeHidden();
}
async function fill(view, text='Mobilier composé') {
  await view.locator('[name=designation]').fill(text);
  await view.locator('[name=type_id]').selectOption({index:1});
  await view.locator('[name=troncon_id]').selectOption(T);
}

for (const viewport of [{width:1440,height:900},{width:390,height:844},{width:844,height:390}]) {
  test(`composition, création et relecture PWA ${viewport.width}×${viewport.height}`, async ({context,page}) => {
    await page.setViewportSize(viewport);
    if (viewport.width !== 1440) await page.addInitScript(() => {
      const match = window.matchMedia.bind(window);
      window.matchMedia = query => query.includes('display-mode: standalone')
        ? {...match(query),matches:true,addEventListener(){},removeEventListener(){}} : match(query);
    });
    await page.addInitScript(() => {
      window.__formCompositions = [];
      let api;
      Object.defineProperty(window, 'EntityFormView', {configurable:true, get:()=>api,
        set:value=>{ api={...value, create(options) {
          window.__formCompositions.push(options.descriptor.family);
          return value.create(options);
        }}; }});
    });
    const state = await fixture(context); await ready(page);
    const view = await create(page);
    await expect(view).toHaveClass('entity-form');
    expect(await page.evaluate(()=>window.__formCompositions)).toContain('mobilier');
    await expect(view.locator('.entity-form__header')).toHaveCount(1);
    await expect(page.locator('#editor-identity-port')).toBeHidden();
    await expect(view.getByRole('tablist')).toHaveCount(1);
    expect(await view.locator('[data-section]').evaluateAll(nodes=>nodes.map(node=>node.dataset.section))).toEqual(order);
    await expect(view.locator('[data-section=geometry] [data-role=place-point]')).toHaveCount(1);
    await expect(view.locator('[data-section=geometry] .entity-form__geometry-fixed-type')).toHaveText('Point');
    await expect(view.locator('[data-role=geometry-requirement]')).toHaveText('Géométrie obligatoire');
    await expect(view.getByRole('tab',{name:'Général',exact:true})).toHaveAttribute('aria-selected','true');
    await expect(view.getByRole('tab',{name:'Observations',exact:true})).toBeDisabled();
    await expect(view.locator('[name=designation]')).toHaveValue('');
    await fill(view); await placePoint(page,view);
    await view.locator('[data-role=save]').scrollIntoViewIfNeeded();
    await expect(view.locator('[data-role=save]')).toBeInViewport();
    await view.locator('[data-role=save]').click();
    await expect.poll(()=>state.creates.length).toBe(1);
    await expect(view.locator('[data-role=status]')).toContainText('enregistré');
    const id = state.records.mobilier.at(-1).properties.id;
    expect(await page.evaluate(()=>mapEntityEditors.get('mobilier').shell.state.mode)).toBe('edit');
    expect(await page.evaluate(()=>mapEntityEditors.get('mobilier').shell.state.objectId)).toBe(id);
    await expect(view.getByRole('tab',{name:'Observations',exact:true})).toBeEnabled();
    await expect(view.getByRole('tab',{name:'Général',exact:true})).toHaveAttribute('aria-selected','true');
    await expect(view.locator('[name=designation]')).toHaveValue('Mobilier composé');
    await view.locator('[name=commentaire]').fill('Édition persistée');
    await view.locator('[data-role=save]').click();
    await expect.poll(()=>state.writes.length).toBe(1);
    expect(state.writes[0].id).toBe(id); expect(state.writes[0].headers['if-match']).toBe('"1"');
    expect(state.writes[0].body).not.toHaveProperty('geometry');
    await expect(view.locator('[data-role=save]')).toBeEnabled();
    await view.locator('[data-role=close]').click(); await page.reload();
    await page.waitForFunction(()=>typeof mapViewportReady!=='undefined'&&mapViewportReady);
    await clickFeature(page,id);
    await expect(view.locator('[name=commentaire]')).toHaveValue('Édition persistée');
    await expect(view.locator('[name=troncon_id]')).toHaveValue(T);
    expect(state.records.mobilier.at(-1).geometry.type).toBe('Point');
    expect(state.creates).toHaveLength(1);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width+1);
    await page.screenshot({path:test.info().outputPath(`mobilier-${viewport.width}x${viewport.height}.png`)});
  });
}

test('Équipement garde son profil et les mêmes composants, sans sélectionner de Réseau',async({context,page})=>{
  const state=await fixture(context); await ready(page);
  const view=await create(page,'equipement-mesure'); await fill(view,'Instrument composé'); await placePoint(page,view);
  await view.locator('[data-role=save]').click();
  await expect.poll(()=>state.creates.length).toBe(1);
  expect(state.creates[0].resource).toBe('equipements-mesure');
  await expect(view.getByRole('tab',{name:'Observations',exact:true})).toBeEnabled();
  await expect(view.locator('[data-role=status]')).toContainText('enregistré');
  await view.locator('[data-role=close]').click();
  const network=await create(page,'reseau-technique');
  await expect(network.locator('[name=geometry_type]')).toHaveValue('Point');
  await network.locator('[name=geometry_type]').selectOption('LineString');
  await expect(network.locator('[data-role=geometry-requirement]')).toHaveText('Géométrie facultative');
  await expect(network.getByRole('group', { name: 'Mode de localisation' })).toBeVisible();
});

test('le composant compose le port de dessin multitype sans gabarit familial',async({context,page})=>{
  await fixture(context); await ready(page);
  const result=await page.evaluate(()=>{
    const descriptor=MobilierForm.composition(MobilierForm.profile('mobilier'));
    descriptor.geometry={types:['Point','LineString'],required:false,editable:true};
    // Composition-only probe, not a new business binding or an enabled editor.
    const view=EntityFormView.create({descriptor,id:'composition-probe'});
    const block=view.sections.get('geometry');
    return {controls:['type','draw','confirm','cancel','status','requirement','contractStatus']
      .every(name=>block.contains(view.geometry[name])),
      types:[...view.geometry.type.options].map(option=>option.value),
      sections:[...view.element.querySelectorAll('[data-section]')].map(node=>node.dataset.section)};
  });
  expect(result.controls).toBe(true); expect(result.types).toEqual(['Point','LineString']);
  expect(result.sections).toEqual(order);
});

// FORM-LOCALISATION-2, niveau B (~15 s au total), HTTP synthétique isolé.
// Le lien utilise la navigation Carte réelle et sa garde d'abandon du brouillon.
for (const viewport of [{width:1440,height:900},{width:390,height:844},{width:844,height:390}]) {
  test(`rattachement unique compact et navigation protégée ${viewport.width}×${viewport.height}`, async ({context,page}) => {
    await page.setViewportSize(viewport);
    if (viewport.width !== 1440) await page.addInitScript(() => {
      const match = window.matchMedia.bind(window);
      window.matchMedia = query => query.includes('display-mode: standalone')
        ? {...match(query),matches:true,addEventListener(){},removeEventListener(){}} : match(query);
    });
    const state = await fixture(context);
    await ready(page);
    const view = await create(page);
    const parent = view.locator('[name=troncon_id]');
    const link = view.locator('[data-role=open-relation]');
    await expect(link).toBeHidden();
    await parent.selectOption(T);
    await expect(link).toBeVisible();
    const [selectBox, linkBox] = await Promise.all([parent.boundingBox(), link.boundingBox()]);
    expect(Math.abs(selectBox.y + selectBox.height / 2 - linkBox.y - linkBox.height / 2)).toBeLessThan(1);
    expect(selectBox.width).toBeGreaterThan(linkBox.width * 3);
    await parent.selectOption('');
    await expect(link).toBeHidden();
    await parent.selectOption(T);
    await view.locator('[name=designation]').fill('Brouillon terrain');
    await expect(view.locator('[name=desordre_ids]').locator('..').locator('[data-role=open-relation]')).toHaveCount(0);
    page.once('dialog', dialog => dialog.dismiss());
    await link.click();
    await expect(view.locator('[name=designation]')).toHaveValue('Brouillon terrain');
    await expect(parent).toHaveValue(T);
    await view.screenshot({path:test.info().outputPath('rattachement.png')});
    page.once('dialog', dialog => dialog.accept());
    await link.click();
    await expect(view).toBeHidden();
    await expect(page.locator('#heritage-edit-view [name=libelle]')).toHaveValue(state.records.troncons[0].properties.libelle);
    expect(state.creates).toHaveLength(0);
    expect(state.writes).toHaveLength(0);
  });
}

test('un parent indisponible conserve son identité sans lien de navigation', async ({context,page}) => {
  const state = await fixture(context);
  const record = state.records.mobilier[0];
  record.properties.troncon_id = fakeId(999);
  await ready(page);
  await clickFeature(page, record.properties.id);
  const view = page.locator('#mobilier-editor-panel');
  await expect(view.locator('[name=troncon_id]')).toHaveValue(fakeId(999));
  await expect(view.locator('[data-role=open-relation]')).toBeHidden();
});

for(const status of [403,422]) test(`POST ${status} conserve le brouillon et refuse Observations`,async({context,page})=>{
  const state=await fixture(context,{postStatus:status}); await ready(page);
  const view=await create(page); await fill(view,'Création refusée'); await placePoint(page,view);
  await view.locator('[data-role=save]').click();
  await expect(view.locator('[data-role=status]')).toContainText('Création refusée');
  await expect(view).toHaveAttribute('data-state',status===403?'forbidden':'error');
  await expect(view.locator('[name=designation]')).toHaveValue('Création refusée');
  await expect(view.getByRole('tab',{name:'Observations',exact:true})).toBeDisabled();
  page.once('dialog',dialog=>dialog.dismiss()); await view.locator('[data-role=close]').click();
  await expect(view).toBeVisible(); await expect(view.locator('[name=designation]')).toHaveValue('Création refusée');
  expect(state.creates).toHaveLength(1); expect(state.writes).toHaveLength(0);
});

test('double soumission bloquée jusqu’à confirmation, puis PUT sur la même identité',async({context,page})=>{
  let release; const delay=new Promise(resolve=>{release=resolve;});
  const state=await fixture(context,{delay}); await ready(page);
  const view=await create(page); await fill(view); await placePoint(page,view);
  await view.locator('[data-role=save]').click(); await expect.poll(()=>state.creates.length).toBe(1);
  await expect(view.locator('[data-role=save]')).toBeDisabled();
  expect(await page.evaluate(()=>{const values=[]; map.eachLayer(layer=>{
    if(layer instanceof L.Marker && layer.options.draggable) values.push(layer.dragging.enabled());
  }); return values;})).toEqual([false]);
  await view.locator('form').evaluate(form=>form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
  expect(state.creates).toHaveLength(1); release();
  await expect(view.locator('[data-role=status]')).toContainText('enregistré');
  await view.locator('[name=commentaire]').fill('Après confirmation'); await view.locator('[data-role=save]').click();
  await expect.poll(()=>state.writes.length).toBe(1); expect(state.creates).toHaveLength(1);
});

test('409 et abandon refusé conservent la version, les champs et les rattachements',async({context,page})=>{
  const state=await fixture(context,{conflict:true}); await ready(page); await clickFeature(page,fakeId(40));
  const view=page.locator('#mobilier-editor-panel'); await fill(view,'Brouillon en conflit');
  await view.locator('[data-role=save]').click();
  await expect(view.locator('[data-role=status]')).toContainText('brouillon');
  await expect(view).toHaveAttribute('data-state','conflict');
  await expect(view.locator('[name=designation]')).toHaveValue('Brouillon en conflit');
  expect(state.writes[0].headers['if-match']).toBe('"1"');
  page.once('dialog',dialog=>dialog.dismiss()); await clickFeature(page,T);
  await expect(view).toBeVisible(); await expect(view.locator('[name=troncon_id]')).toHaveValue(T);
  expect(await page.evaluate(()=>mapEntityEditors.get('mobilier').shell.state.snapshot.properties.version)).toBe(1);
});

test('lecture seule conserve la consultation Observations sans autoriser son ajout',async({context,page})=>{
  const state=await fixture(context); await ready(page);
  await page.evaluate(()=>{const runtime=BusinessRuntime; BusinessRuntime=Object.freeze({...runtime,
    can:permission=>permission==='business.read'});});
  await clickFeature(page,fakeId(40)); const view=page.locator('#mobilier-editor-panel');
  await expect(view.locator('[name=designation]')).toBeDisabled();
  await expect(view.locator('[data-role=save]')).toBeHidden();
  await expect(view.getByRole('group', { name: 'Mode de localisation' })).toBeHidden();
  await view.getByRole('tab',{name:'Observations',exact:true}).click();
  await expect(view.locator('[data-role=observations]')).toContainText('Aucune observation');
  await expect(view.locator('[data-role=create-observation]')).toBeHidden();
  expect(state.writes).toHaveLength(0); expect(state.creates).toHaveLength(0);
});

test('une réponse périmée ne remplace pas la nouvelle création',async({context,page})=>{
  const state=await fixture(context); await ready(page);
  let release; const gate=new Promise(resolve=>{release=resolve;});
  await page.route(`**/api/mobilier/${fakeId(40)}`,async route=>{await gate; await route.fulfill({json:state.records.mobilier[0]});});
  const requested=page.waitForRequest(request=>request.url().endsWith(`/api/mobilier/${fakeId(40)}`));
  await clickFeature(page,fakeId(40)); await requested;
  const view=page.locator('#mobilier-editor-panel'); await view.locator('[data-role=close]').click();
  await create(page); await view.locator('[name=designation]').fill('Nouvelle saisie');
  const response=page.waitForResponse(response=>response.url().endsWith(`/api/mobilier/${fakeId(40)}`));
  release(); await response;
  await expect(view.locator('[name=designation]')).toHaveValue('Nouvelle saisie');
  expect(await page.evaluate(()=>mapEntityEditors.get('mobilier').shell.state.mode)).toBe('create');
});

test('type et parent inactifs, association hors recherche : aucune perte au PUT',async({context,page})=>{
  const state=await fixture(context), associated=fakeId(777);
  Object.assign(state.records.mobilier[0].properties,{type_id:'MRE-INACTIVE',type_libelle:'Type inactif conservé',desordre_ids:[associated]});
  state.records.troncons[0].properties.valid=false;
  await context.route('**/api/desordres/selection*',route=>route.fulfill({json:{
    items:[{id:associated,label:'Désordre hors page',valid:false}],missing_ids:[]}}));
  await ready(page); await clickFeature(page,fakeId(40));
  const view=page.locator('#mobilier-editor-panel');
  await expect(view.locator('[name=type_id]')).toHaveValue('MRE-INACTIVE');
  await expect(view.locator('[name=troncon_id]')).toHaveValue(T);
  await expect(view.locator('[name=desordre_ids]')).toHaveValues([associated]);
  await view.locator('[name=commentaire]').fill('Attribut seul'); await view.locator('[data-role=save]').click();
  await expect.poll(()=>state.writes.length).toBe(1);
  expect(state.writes[0].body.type_id).toBe('MRE-INACTIVE');
  expect(state.writes[0].body.troncon_id).toBe(T);
  expect(state.writes[0].body).not.toHaveProperty('desordre_ids');
  expect(state.records.mobilier[0].properties.desordre_ids).toEqual([associated]);
});

test('onglets accessibles au clavier, sans perdre les valeurs du Général',async({context,page})=>{
  await fixture(context); await ready(page); await clickFeature(page,fakeId(40));
  const view=page.locator('#mobilier-editor-panel');
  await expect(view.locator('[name=designation]')).toBeEditable();
  await view.locator('[data-role=close]').click(); await clickFeature(page,fakeId(40));
  await expect(view.locator('[name=designation]')).toBeEditable();
  await expect(view.locator('[data-role=close]')).toBeFocused();
  await view.locator('[name=designation]').fill('Brouillon conservé entre onglets');
  const general=view.getByRole('tab',{name:'Général',exact:true}); await general.focus();
  await general.press('ArrowRight');
  const observations=view.getByRole('tab',{name:'Observations',exact:true});
  await expect(observations).toBeFocused(); await expect(observations).toHaveAttribute('aria-selected','true');
  await observations.press('ArrowLeft'); await expect(general).toBeFocused();
  await expect(view.locator('[name=designation]')).toHaveValue('Brouillon conservé entre onglets');
});

for(const [family,number] of [['mobilier',40],['ouvrage-particulier',43],['troncons-lit',20]])
test(`${family} : changement de contexte pendant les en-têtes, aucune écriture dans la nouvelle base`,async({context,page})=>{
  const state=await fixture(context); await ready(page); await clickFeature(page,fakeId(number));
  const view=page.locator(`#${family}-editor-panel`);
  if(family==='mobilier') await fill(view,'Ancien contexte');
  else await view.locator('[name=designation]').fill('Ancien contexte');
  await page.evaluate(()=>{
    const original=ResourceMutationController.requestHeaders;
    ResourceMutationController.requestHeaders=()=>new Promise(resolve=>{
      window.__releaseHeaders=async()=>resolve(await original());
    });
  });
  await view.locator('[data-role=save]').click();
  await expect.poll(()=>page.evaluate(()=>Boolean(window.__releaseHeaders))).toBe(true);
  await page.evaluate(async()=>{
    const previous=BusinessRuntime; BusinessRuntime=Object.freeze({...previous,can:()=>true});
    window.dispatchEvent(new CustomEvent('digues-app:context-closed'));
    await window.__releaseHeaders();
  });
  await expect(view).toBeHidden();
  expect(state.writes).toHaveLength(0); expect(state.creates).toHaveLength(0);
});

// FORM-CORE-3 : résultats ambigus, état réel des actions et invalidations.
test('POST incertain : aucune seconde création, fermeture bloquée et brouillon conservé',async({context,page})=>{
  await fixture(context); await ready(page);
  let attempts=0;
  await context.route('**/api/mobilier',route=>{
    if(route.request().method()!=='POST') return route.fallback();
    attempts++; return route.abort('failed');
  });
  const view=await create(page); await fill(view,'Intention non confirmée'); await placePoint(page,view);
  await view.locator('[data-role=save]').click();
  await expect(view).toHaveAttribute('data-state','uncertain');
  await expect(view.locator('[data-role=status]')).toContainText('Résultat non confirmé');
  await expect(view.locator('[name=designation]')).toHaveValue('Intention non confirmée');
  await expect(view.locator('[data-role=save]')).toBeDisabled();
  await expect(view.locator('[data-role=close]')).toBeDisabled();
  await view.locator('form').evaluate(form=>form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
  expect(attempts).toBe(1);
});

test('PUT incertain : seul le même enregistrement versionné est rejoué',async({context,page})=>{
  const state=await fixture(context); await ready(page); await clickFeature(page,fakeId(40));
  let firstHeaders=null;
  await context.route(`**/api/mobilier/${fakeId(40)}`,route=>{
    if(route.request().method()!=='PUT'||firstHeaders) return route.fallback();
    firstHeaders=route.request().headers(); return route.abort('failed');
  });
  const view=page.locator('#mobilier-editor-panel'); await fill(view,'Commande identique');
  await expect(view).toHaveAttribute('data-state','dirty');
  await view.locator('[data-role=save]').click();
  await expect(view).toHaveAttribute('data-state','uncertain');
  await expect(view.locator('[name=designation]')).toBeDisabled();
  await expect(view.locator('[data-role=save]')).toHaveText("Réessayer l'enregistrement");
  await view.locator('[data-role=save]').click();
  await expect(view).toHaveAttribute('data-state','ready');
  expect(state.writes).toHaveLength(1); expect(state.creates).toHaveLength(0);
  expect(state.writes[0].headers['operation-id']).toBe(firstHeaders['operation-id']);
  expect(state.writes[0].headers['if-match']).toBe(firstHeaders['if-match']);
});

test('une invalidation distante garde le brouillon et la version de départ',async({context,page})=>{
  const state=await fixture(context); await ready(page); await clickFeature(page,fakeId(40));
  const view=page.locator('#mobilier-editor-panel'); await fill(view,'Brouillon local protégé');
  Object.assign(state.records.mobilier[0].properties,{version:2,designation:'Valeur distante'});
  await page.evaluate(async id=>mapFeatureStore.invalidate({resource:'mobilier',ids:[id],action:'update',
    versions:{[id]:2}}),fakeId(40));
  await expect(view).toHaveAttribute('data-state','conflict');
  await expect(view.locator('[name=designation]')).toHaveValue('Brouillon local protégé');
  expect(await page.evaluate(()=>mapEntityEditors.get('mobilier').shell.state.snapshot.properties.version)).toBe(1);
});
