// Niveau B, parcours transversal intégré (~4 min). Contrôleurs/DOM/Leaflet réels,
// état HTTP synthétique propre par contexte ; aucune ressource persistante.
// Ce contrôle technique ne constitue pas une validation ergonomique utilisateur.
const {test,expect}=require('@playwright/test');
const {fixture,ready,clickFeature,errorsByContext,T,D,S}=require('./fixtures/map-editor-fixture');
const {fakeId}=require('./fixtures/frontend-preview-data');
const {randomUUID}=require('node:crypto');
test.use({serviceWorkers:'block',actionTimeout:10000});
const families=[['mobilier','mobilier',40],['equipement-mesure','equipements_mesure',41],['reseau-technique','reseaux_techniques',42],
 ['ouvrage-particulier','ouvrages_particuliers',43],['cheminement','cheminements',44],['vegetation','vegetation',45],
 ['prestation','prestations',47],['zone-stockage','zones_stockage',48],['amenagement-hydraulique','amenagements_hydrauliques',90],
 ['troncons-lit','troncons_lit',20],['elements-morphologiques-lit','elements_morphologiques_lit',30],
 ['systeme','systemes',3],['digue','digues',2],['troncon','troncons',1],['desordre','desordres',10]];
const characteristicNames=['largeurs','pentes','domanialites','regimes-ecoulement','occupations-riveraines'];
const order=['identification','classification','characteristics','attachments','geometry','historical','actions'];
async function structure(view){
 await expect(view).toBeVisible();await expect(view.locator(':scope > .entity-form__header')).toHaveCount(1);
 await expect(view.locator(':scope > [role=tablist]')).toHaveCount(1);
 const keys=await view.locator(':scope > .entity-form__body > form > fieldset > [data-section], :scope > .entity-form__body > [data-section]').evaluateAll(nodes=>nodes.map(node=>node.dataset.section));
 expect(keys).toEqual(order.filter(key=>keys.includes(key)));
 const names=await view.locator(':scope > .entity-form__body > form [name]:not([type=radio])').evaluateAll(nodes=>nodes.map(n=>n.name));
 expect(new Set(names).size).toBe(names.length);
 await expect(view.locator(':scope > [role=tablist] [data-role=general-tab]')).toHaveAttribute('aria-selected','true');
}
async function createEntry(page,type){await page.locator('#toggle-create-menu').click();await page.locator(`[data-create-type="${type}"]`).click();return page.locator('#editor-panel .entity-form:visible').first();}
async function draw(page,view){
 const section=view.locator('[data-section=geometry]');if(!await section.isVisible())return;
 if(await page.locator('#heritage-panel').isVisible())await page.locator('#close-heritage').click();
 const draw=section.locator('[data-role=draw], [data-role=place-point]');
 await draw.click(); const box=await page.locator('#map').boundingBox();
 const type=await section.locator('[name=geometry_type]').inputValue();
 const positions=type==='Point'?[[.3,.5]]:[[.3,.5],[.35,.5],[.35,.55]];
 for(const [x,y]of positions)await page.locator('#map').click({position:{x:box.width*x,y:box.height*y}});
 if(type!=='Point'){
  // Existing drawing interaction can collapse the panel; the map's confirmation
  // command belongs to the same geometry draft and is not another save.
  const finish=page.locator('#finish-editor-map-interaction');
  if(await finish.isVisible())await finish.click();
  else await section.locator('[data-role=confirm], [data-role=confirm-geometry]').click();
 }
}
async function installChildren(context,state){
 const observations=new Map();const photos=new Map();
 const mutation=(resource,id,version,req,action='update')=>({schemaVersion:1,eventId:randomUUID(),resource,action,ids:[id],versions:{[id]:version},occurredAt:Date.now(),originClientId:req.headers()['x-digues-app-client-id'],originInstanceId:req.headers()['x-digues-app-instance-id']});
 await context.route('**/api/observations**',async route=>{
  const req=route.request(),path=new URL(req.url()).pathname,id=path.split('/')[3];
  if(req.method()==='POST' && path==='/api/observations'){const b=req.postDataJSON(),row={...b,version:1,photos:[],mutation:mutation('observations',b.id,1,req,'create')};observations.set(row.id,row);return route.fulfill({status:201,json:row});}
  const row=observations.get(id);if(!row)return route.fallback();
  if(req.method()==='PUT')Object.assign(row,req.postDataJSON(),{version:row.version+1});
  row.mutation=mutation('observations',id,row.version,req);return route.fulfill({json:row});
 });
 await context.route('**/api/troncons-lit/*/caracteristiques/**',async route=>{
  const req=route.request(),parts=new URL(req.url()).pathname.split('/'),id=parts[3],family=parts[5],child=parts[6],body=req.postDataJSON();
  const feature=state.records['troncons-lit'].find(f=>f.properties.id===id);
  const collection={largeurs:'largeurs_lit',pentes:'pentes_lit',domanialites:'domanialites_lit','regimes-ecoulement':'regimes_ecoulement_lit','occupations-riveraines':'occupations_riveraines_lit'}[family];
  feature.properties[collection]??=[];
  if(child)Object.assign(feature.properties[collection].find(row=>row.id===child),body);
  else feature.properties[collection].push(body);
  feature.properties.version++;feature.mutation=mutation('troncons-lit',id,feature.properties.version,req);
  return route.fulfill({json:feature});
 });
 return {observations,photos};
}
async function heritageEdit(page,id){
 if(await page.locator('#toggle-heritage').getAttribute('aria-expanded')!=='true')await page.locator('#toggle-heritage').click();
 if(id===D){const toggle=page.locator(`.tree-row:has(.tree-name[data-object-id="${S}"]) > .tree-toggle`);if(await toggle.getAttribute('aria-expanded')!=='true')await toggle.click();}
 const button=page.locator(`.tree-name[data-object-id="${id}"]`);
 await button.click();return page.locator('#editor-panel .entity-form:visible').first();
}
for(const viewport of [{width:1440,height:900},{width:390,height:844},{width:844,height:390}])test(`22 familles composées, parcours intégrés ${viewport.width}×${viewport.height}`,async({context,page})=>{
 test.setTimeout(300000);await page.setViewportSize(viewport);
 if(viewport.width!==1440)await page.addInitScript(()=>{const m=window.matchMedia.bind(window);window.matchMedia=q=>q.includes('display-mode: standalone')?{...m(q),matches:true,addEventListener(){},removeEventListener(){}}:m(q);});
 await page.addInitScript(()=>{window.__composed=JSON.parse(sessionStorage.getItem("form-core-composed")||"[]");let api;Object.defineProperty(window,'EntityFormView',{configurable:true,get:()=>api,set:value=>{api={...value,create(options){window.__composed.push(options.descriptor.family);sessionStorage.setItem("form-core-composed",JSON.stringify(window.__composed));return value.create(options);}};}});});
 const state=await fixture(context,{ah:true});const children=await installChildren(context,state);await ready(page);
 for(const [type,family,number]of families)await test.step(family,async()=>{
  console.log(`PARCOURS ${viewport.width} ${family}`);
  let view=await createEntry(page,type);await structure(view);
  const designation=view.locator(':scope > .entity-form__body > form [name=designation]');await expect(designation).toHaveValue('');
  // Desktop exercises POST/PUT/reopen; mobile exercises each actual composed
  // entry and keyboard/touch reachability without repeating SQL-like fixtures.
  if(viewport.width===1440 && !['systeme','digue','troncon','desordre'].includes(type)){
   await designation.fill(`Création ${family}`);
   for(const select of await view.locator(':scope > .entity-form__body > form select[required]:visible').all())if(!await select.inputValue())await select.selectOption({index:1});
   if(type==='prestation')await view.locator('[name=date_fin]').fill('2026-11-09');
   await draw(page,view);const n=state.creates.length;await view.locator(':scope > .entity-form__body > form [data-role=save]').click();
   await expect.poll(()=>state.creates.length, {message:`POST ${family}`}).toBe(n+1);
   await expect(view.locator(':scope > .entity-form__body > form [data-role=save]')).toBeEnabled();
   await expect(designation).toHaveValue(`Création ${family}`);
   await expect(view).not.toHaveAttribute('data-state','saving');
  }
  // Guarded discard of a pristine or confirmed form; no forced loss of draft.
  await view.locator(':scope > .entity-form__header [data-role=close]').click();
  if(['systeme','digue'].includes(type)||type==='amenagement-hydraulique')view=await heritageEdit(page,fakeId(number));
  else{await clickFeature(page,fakeId(number));view=page.locator('#editor-panel .entity-form:visible').first();}
  await expect(view.locator(':scope > .entity-form__body > form [name=designation]')).toBeEditable();await structure(view);
  if(viewport.width===1440){
   const original=family==='systemes'?state.system:family==='digues'?state.digue:state.records[family.replaceAll('_','-')]?.find(r=>r.properties.id===fakeId(number));
   const geometry=structuredClone(original?.geometry);const before=state.writes.length;
   await view.locator(':scope > .entity-form__body > form [name=designation]').fill(`Édition ${family}`);
   // Preview fixtures intentionally contain incomplete classifications; supply
   // the mandatory reference exactly as a user completing that object would.
   for(const select of await view.locator(':scope > .entity-form__body > form select[required]:visible').all())if(!await select.inputValue())await select.selectOption({index:1});
   await view.locator(':scope > .entity-form__body > form [data-role=save]').click();
   await expect.poll(()=>state.writes.length).toBe(before+1);
   expect(state.writes.at(-1).body).not.toHaveProperty('geometry');
   expect(original?.geometry).toEqual(geometry);
   // Heritage controllers deliberately return to consultation after PUT.
   if(['systeme','digue','troncon'].includes(type))await page.locator('#data-entity-detail-edit').click();
   await expect(view.locator(':scope > .entity-form__body > form [name=designation]')).toHaveValue(`Édition ${family}`);
  }
  if(type==='troncons-lit'){
   const collections=['largeurs_lit','pentes_lit','domanialites_lit','regimes_ecoulement_lit','occupations_riveraines_lit'];
   for(const [index,name] of characteristicNames.entries()){
    const host=view.locator('[data-role=lit-characteristics]');await host.locator(`[data-family="${name}"] [data-action=add-characteristic]`).click();
    const child=host.locator('.entity-form');await structure(child);
    await child.locator('[name=designation]').fill(`Occurrence ${name}`);
    if(viewport.width===1440){await child.locator('[data-role=save]').click();await expect(child).toHaveCount(0);
     expect(state.records['troncons-lit'][0].properties[collections[index]].at(-1).designation).toBe(`Occurrence ${name}`);
    }else await child.locator('[data-role=cancel]').click();
   }
  }
  if(['vegetation','desordre','troncon'].includes(type))await page.screenshot({path:`audits/form-core-final/captures/${family}-${viewport.width}x${viewport.height}.png`});
  await view.locator(':scope > .entity-form__header [data-role=close]').click();
 });
 if(viewport.width===1440){
  const roots=families.map(([,id])=>({id,label:id}));
  await context.route('**/api/data/view-catalog',route=>route.fulfill({json:{default_root:'mobilier',roots,relations:[],tables:roots.map(({id,label})=>({id,label,compatible_roots:[id],fields:[{id:'designation',label:'Désignation',type:'text',source_class:id,source_field:'designation',navigable:true}]}))}}));
  await page.goto('/base');await expect(page.locator('main')).toHaveJSProperty('inert',false);
  for(const [,family,number]of families)await test.step(`Base ${family}`,async()=>{
   await page.evaluate(({family,id})=>document.querySelector('main').dispatchEvent(new CustomEvent('digues-app:open-entity',{detail:{entityClass:family,entityId:id,source:'home',entities:[]}})),{family,id:fakeId(number)});
   await expect(page.locator('#data-entity-detail-edit')).toBeVisible();await page.locator('#data-entity-detail-edit').click();
   const view=page.locator('#base-detail-editor > .entity-form');await expect(view.locator(':scope > .entity-form__body > form [name=designation]')).toBeEditable();await structure(view);
   await view.locator(':scope > .entity-form__body > form [name=designation]').fill(`Base ${family}`);const before=state.writes.length;
   await view.locator(':scope > .entity-form__body > form [data-role=save]').click();await expect.poll(()=>state.writes.length).toBe(before+1);
   expect(state.writes.at(-1).body).not.toHaveProperty('geometry');
   await expect(page.locator('#data-entity-detail-read')).toContainText(`Base ${family}`);
  });
 }
 // Observation and Photo are contextual editors, never invented as map geometries.
 await page.evaluate(({id})=>ObservationCreate.open({resource:'desordres',id,designation:'Parent synthétique'}),{id:fakeId(10)});
 const observation=page.locator('#observation-create-dialog > .entity-form');await expect(observation.locator('[type=submit]')).toBeEnabled();await structure(observation);
 await observation.locator('[name=designation]').fill('Observation composée');
 await observation.locator('[type=submit]').click();await expect(observation.locator('[data-role=result]')).toContainText('Observation composée');
 const row=[...children.observations.values()][0];expect(row.parent.id).toBe(fakeId(10));
 await observation.locator('[data-role=cancel]').click();
 await page.evaluate(row=>ObservationDetail.open({id:row.id,parent:row.parent,fetchJson:async(path,options)=>{const res=await fetch(path,options);return res.json();}}),row);
 const obsEdit=page.locator('#observation-detail-dialog > .entity-form');await expect(obsEdit.locator('[name=designation]')).toHaveValue('Observation composée');await structure(obsEdit);
 await obsEdit.locator('[name=suite]').fill('Suite persistée');await obsEdit.locator('[type=submit]').click();await expect(obsEdit.locator('[data-role=status]')).toContainText('enregistrée');
 await obsEdit.locator('[data-role=close]').click();
 await page.evaluate(row=>PhotoMedia.openCreate(row),row);
 const photo=page.locator('#photo-editor-dialog > .entity-form');await structure(photo);
 await expect(photo.locator('[name=file]')).toHaveAttribute('accept','image/jpeg,image/png,image/webp');
 await photo.locator('[data-role=close]').click();
 await page.screenshot({path:`audits/form-core-final/captures/application-${viewport.width}x${viewport.height}.png`});
 expect([...new Set(await page.evaluate(()=>__composed))].sort()).toEqual([...families.map(([,family])=>family),...characteristicNames,'observations','photos','patrimoine'].sort());
 expect(errorsByContext.get(context)).toEqual([]);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width+1);
});

test('présentation commune finale : UUID optionnel, checkbox et commandes des hôtes',async({context,page})=>{
 await page.setViewportSize({width:390,height:844});const state=await fixture(context);await ready(page);
 await clickFeature(page,fakeId(10));await expect(page.locator('#desordre-create-id-field')).toBeHidden();
 await page.evaluate(()=>document.body.classList.add('show-uuid'));await expect(page.locator('#desordre-create-id-field')).toBeVisible();
 const box=await page.locator('#desordre-editor [name=valid]').boundingBox();expect(box.height).toBeLessThan(25);expect(box.width).toBeLessThan(25);
 await page.evaluate(()=>document.body.classList.remove('show-uuid'));
 await page.screenshot({path:'audits/form-core-final/captures/desordre-final-390x844.png'});
 await page.locator('#editor-panel .entity-form__header [data-role=close]:visible').click();
 await clickFeature(page,T);await expect(page.locator('#data-entity-detail [name=designation]')).toBeEditable();
 await expect(page.locator('#data-entity-detail-back')).toBeHidden();
 await page.setViewportSize({width:844,height:390});
 await page.screenshot({path:'audits/form-core-final/captures/troncon-final-844x390.png'});
 const row=state.records.mobilier[0];
 await page.setViewportSize({width:1440,height:900});
 await page.goto('/base');await expect(page.locator('main')).toHaveJSProperty('inert',false);
 await page.evaluate(id=>document.querySelector('main').dispatchEvent(new CustomEvent('digues-app:open-entity',{detail:{entityClass:'mobilier',entityId:id,source:'home',entities:[]}})),row.properties.id);
 await expect(page.locator('#data-entity-detail-map')).toBeVisible();await expect(page.locator('#data-entity-detail-back')).toBeVisible();
 await expect(page.locator('#data-entity-detail')).toHaveAttribute('data-mode','read');
 await expect(page.locator('#data-entity-detail > .base-detail__header')).toHaveCount(1);
 await expect(page.locator('#data-entity-detail-read > form')).toHaveCount(0);
 expect(errorsByContext.get(context)).toEqual([]);
});

test('Base : les composants conditionnels restent raccordés au formulaire composé',async({context,page})=>{
 await fixture(context);await page.goto('/base');await expect(page.locator('main')).toHaveJSProperty('inert',false);
 async function edit(family,id){
  await page.evaluate(({family,id})=>document.querySelector('main').dispatchEvent(new CustomEvent('digues-app:open-entity',{detail:{entityClass:family,entityId:id,source:'home',entities:[]}})),{family,id});
  await expect(page.locator('#data-entity-detail-edit')).toBeVisible();await page.locator('#data-entity-detail-edit').click();
  return page.locator('#data-entity-detail .entity-form__form');
 }
 let form=await edit('elements_morphologiques_lit',fakeId(30));
 await expect(form.locator('[name=type_morphologie]')).toBeEditable();
 const initialType=await form.locator('[name=type_morphologie]').inputValue();
 const zone=form.locator('[name=type_zone_id]');await expect(zone).toBeHidden();
 await form.locator('[name=type_morphologie]').selectOption('ATTERISSEMENT');await expect(zone).toBeVisible();
 await form.locator('[name=type_morphologie]').selectOption(initialType);await expect(zone).toBeHidden();
 await page.locator('#data-entity-detail-back').click();
 form=await edit('vegetation',fakeId(45));
 await expect(form.locator('[name=sous_type]')).toBeVisible();
 const originalSubtype=await form.locator('[name=sous_type]').inputValue();
 await form.locator('[name=famille_vegetation]').selectOption('HERBACEES');
 await expect(form.locator('[name=sous_type]')).toBeVisible();
 await expect(form.locator('[name=sous_type]')).toHaveValue(originalSubtype);
 await form.locator('[name=famille_vegetation]').selectOption('ARBRES');await expect(form.locator('[name=sous_type]')).toBeVisible();
 await expect(form.locator('[name=sous_type]')).toHaveValue(originalSubtype);
 expect(errorsByContext.get(context)).toEqual([]);
});

// B: real shared DOM/controllers; HTTP fixtures are synthetic. Persistence
// and security are qualified separately by the existing PostgreSQL browser gate.
for(const route of ['/base','/carte'])test(`fiches métier : structure des 16 familles sur ${route}`, async ({ context, page }) => {
 test.setTimeout(180000);
 const fs=require('node:fs');
 const artifacts=process.env.DIGUES_FORM_REPAIR_ARTIFACTS || 'audits/digues-form-repair-01/render';
 fs.mkdirSync(artifacts,{recursive:true});
 const state=await fixture(context,{ah:true});const children=await installChildren(context,state);
 Object.assign(state.records.troncons[0].properties,{type_physique_id:'INDETERMINE',type_physique_libelle:'Indéterminé',
  geometry_type:'LINESTRING',geometry_srid:3950,geometry_dimensions:2,geometry_valide:true});
 const observationId=fakeId(99);
 children.observations.set(observationId,{id:observationId,version:1,designation:'Observation de contrôle',date:'2026-11-21',
  evolution:'Suivi du terrain',suite:'Contrôle prévu',photos:[],parent:{resource:'troncons',id:T,designation:'Tronçon de contrôle'}});
 const matrix=[];
 const save=()=>fs.writeFileSync(`${artifacts}/family-matrix-${route.slice(1)}.json`,JSON.stringify(matrix,null,2));
 async function consultation(family,route){
  const read=page.locator('#data-entity-detail-read');await expect(read).toBeVisible();
  await expect(read.locator('dl')).not.toHaveCount(0);
  const layout=await read.evaluate(node=>({className:node.className,padding:getComputedStyle(node).paddingLeft,
   headingBorder:getComputedStyle(node.querySelector('h2')).borderBottomWidth,
   headings:[...node.querySelectorAll('h2')].map(n=>n.textContent),overflow:node.scrollWidth>node.clientWidth+1}));
  expect.soft(layout.padding,`${route} ${family}: padding de consultation`).toBe(route==='/base'?'20px':'16px');
  if(route==='/carte')expect.soft(layout.headingBorder,`${route} ${family}: hiérarchie des sections`).toBe('1px');
  else await expect(page.locator('#data-entity-detail-read .base-detail__section')).not.toHaveCount(0);
  expect.soft(layout.overflow,`${route} ${family}: débordement horizontal`).toBe(false);
  if(['troncons','mobilier','systemes'].includes(family))await page.screenshot({path:`${artifacts}/${family}-${route.slice(1)}-read.png`});
  return layout;
 }
  if(route==='/carte')await ready(page);else{await page.goto(route);await expect(page.locator('main')).toHaveJSProperty('inert',false);}
  for(const [,family,number]of [...families,['observation','observations',99]]){
   const item={family,route};matrix.push(item);save();let view;
   if(route==='/base'){
    await page.evaluate(({family,id})=>document.querySelector('main').dispatchEvent(new CustomEvent('digues-app:open-entity',
     {detail:{entityClass:family,entityId:id,source:'home',entities:[]}})),{family,id:fakeId(number)});
    item.consultation=await consultation(family,route);save();
    await page.locator('#data-entity-detail-edit').click();view=page.locator('#base-detail-editor > .entity-form');
   }else{
    if(family==='observations'){
     await page.evaluate(row=>ObservationDetail.open({id:row.id,parent:row.parent,
      fetchJson:async(path,options)=>{const response=await fetch(path,options);return response.json();}}),children.observations.get(observationId));
     view=page.locator('#observation-detail-dialog > .entity-form');
    }else if(family==='desordres'){
     await clickFeature(page,fakeId(number));
     view=page.locator('#disorder-editor-host > .entity-form');
    }else{
     await page.evaluate(({family,id})=>MapEntityNavigation.open(family,id),{family,id:fakeId(number)});
     view=page.locator('#editor-panel .entity-form:visible').first();
    }
    item.consultation={mode:'Ouverture directe en édition, intentionnelle'};
   }
   await structure(view);
   item.edition=await view.evaluate(node=>({sections:[...node.querySelectorAll('form [data-section], .entity-form__body > [data-section]')].map(n=>n.dataset.section),
    fields:[...node.querySelectorAll('form label:not([hidden])')].map(n=>n.textContent.trim()),overflow:node.scrollWidth>node.clientWidth+1}));save();
   if(family==='troncons')await page.screenshot({path:`${artifacts}/${family}-${route.slice(1)}-edit.png`});
   if(route==='/base'||['troncons','systemes','digues'].includes(family)){
    await view.locator('[data-role="cancel"]').first().click();
    if(route==='/carte'){item.consultation=await consultation(family,route);save();}
   }
   if(route==='/carte')await view.locator(':scope > .entity-form__header [data-role="close"]').click();
  }
 save();expect(errorsByContext.get(context)).toEqual([]);
});
