// B: real DOM/Leaflet, isolated synthetic transport. SQL fidelity and actual C4
// effects are qualified separately by the disposable PostgreSQL scenarios.
const {test,expect}=require('@playwright/test');
const {fixture,ready}=require('./fixtures/map-editor-fixture');
const {fakeId}=require('./fixtures/frontend-preview-data');
const fs=require('node:fs');const T=fakeId(1);
async function setup(context){
 const state=await fixture(context,{ah:true});
 Object.assign(state.records.troncons[0].properties,{type_physique_id:'BERGE',type_physique_libelle:'Berge',geometry_type:'LINESTRING',geometry_srid:3950,geometry_dimensions:2,geometry_valide:true,hauteur_murette:12.75,num_couche:0});
 state.referenceReads=0;state.referenceWrites=[];state.calculations=0;
 const graph={id:T,version:1,reference_revision:'a'.repeat(64),systeme_reperage_defaut_id:null,
  axe:{source:'PHYSIQUE',type:'LINESTRING',srid:3950,longueur_m:1000,exploitable:true,simple:true,fermee:false,geometry:{type:'LineString',coordinates:[[1700000,9200000],[1701000,9200000]]}},
  reperes_virtuels:[{role:'DEBUT',abscisse_m:0,coordinates:[1700000,9200000]},{role:'FIN',abscisse_m:1000,coordinates:[1701000,9200000]}],
  reperage_implicite_disponible:true,crs:{srid:3950},systemes:[],bornes:[],liaisons:[],troncon_versions:{[T]:1},localisations_dependantes:[]};
 state.referenceGraph=graph;
 await context.route('**/api/troncons/'+T+'/reperage/calcul',route=>{
  state.calculations++;return route.fulfill({json:{troncon_id:T,version:1,reference:'implicite',statut:'OK',abscisse_m:0,longueur_m:1000,srid:3950,geometry:{type:'Point',coordinates:[1700000,9200000]}}});
 });
 await context.route('**/api/troncons/'+T+'/reperage',route=>{
  if(route.request().method()==='GET'){state.referenceReads++;return route.fulfill({json:graph});}
  state.referenceWrites.push(route.request().postDataJSON());return route.fulfill({status:422,json:{detail:'No reference mutation expected'}});
 });
 return state;
}
function form(page){return page.locator('#data-troncons-editor-form');}
function view(page){return page.locator('#data-troncons-editor');}
async function open(page,surface){
 if(surface==='carte'){await ready(page);await page.evaluate(id=>MapEntityNavigation.open('troncons',id),T);}
 else{await page.goto('/base');await expect(page.locator('main')).toHaveJSProperty('inert',false);await page.evaluate(id=>document.querySelector('main').dispatchEvent(new CustomEvent('digues-app:open-entity',{detail:{entityClass:'troncons',entityId:id,source:'home',entities:[]}})),T);await page.locator('#data-entity-detail-edit').click();}
 await expect(form(page).locator('[name=type_physique_id]')).toHaveValue('BERGE');
}
async function signature(page){return view(page).evaluate(el=>({shell:[...el.children].map(n=>n.className),
 sections:[...el.querySelectorAll('.entity-form__fields > [data-section]')].map(n=>({key:n.dataset.section,hidden:n.hidden})),
 controls:[...el.querySelector('form').elements].filter(n=>n.name).map(n=>({name:n.name,value:n.type==='checkbox'?n.checked:n.value,hidden:n.closest('.entity-form__field')?.hidden})),
 headers:el.querySelectorAll(':scope > .entity-form__header').length,tabs:[...el.querySelectorAll(':scope > .entity-form__tabs > button')].map(n=>({name:n.textContent,selected:n.getAttribute('aria-selected')}))}));}
for(const surface of ['carte','base'])test(`annulations successives du Tronçon ${surface}`,async({context,page})=>{
 test.setTimeout(90000);page.on('dialog',d=>d.accept());const state=await setup(context);const before=JSON.stringify(state.records.troncons[0]);await open(page,surface);
 const initial=await signature(page),artifacts=process.env.DIGUES_TRONCON_UX_ARTIFACTS||'audits/digues-troncon-ux-03/before';fs.mkdirSync(artifacts,{recursive:true});
 await page.screenshot({path:artifacts+'/'+surface+'-edit.png'});
 for(let round=0;round<2;round++){
  await form(page).locator('[name=commentaire]').fill('Brouillon abandonné '+round);
  await form(page).locator('[name=type_physique_id]').selectOption('CRETE');
  await form(page).locator('[name=geometry_mode]').selectOption('remplacer');
  await form(page).locator('[data-troncon-coordinates] > summary').click();
  await form(page).locator('[name=geometry_sommets]').fill('-1.55 47.21\n-1.54 47.22');
  await view(page).locator(':scope > .entity-form__body > .entity-form__actions > [data-role=cancel]').click();
  await expect(page.locator('#data-entity-detail-read')).toBeVisible();
  await page.screenshot({path:artifacts+'/'+surface+'-cancel-'+round+'.png'});
  await page.locator('#data-entity-detail-edit').click();await expect(form(page)).toBeVisible();
  expect(await signature(page)).toEqual(initial);
 }
 expect(state.writes).toEqual([]);expect(state.referenceWrites).toEqual([]);expect(JSON.stringify(state.records.troncons[0])).toBe(before);
 fs.writeFileSync(artifacts+'/'+surface+'-cancel-signature.json',JSON.stringify(initial,null,2));
});

for(const surface of ['carte','base'])test(`abandonner et recharger le repérage ${surface}`,async({context,page})=>{
 let discard=true,confirmations=0;page.on('dialog',d=>{confirmations++;return discard?d.accept():d.dismiss();});const state=await setup(context);await open(page,surface);
 await view(page).locator(':scope > .entity-form__body > .entity-form__actions > [data-role=cancel]').click();
 await page.getByRole('button',{name:'Gérer le repérage',exact:true}).first().click();
 const rep=page.locator('#troncon-reperage-dialog .troncon-reperage');await expect(rep).toContainText('Longueur curviligne');
 const structure=()=>rep.evaluate(el=>[...el.querySelectorAll('form')].map(f=>({label:f.getAttribute('aria-label'),fields:[...f.elements].filter(c=>c.name).map(c=>({name:c.name,value:c.type==='checkbox'?c.checked:c.value}))})));
 const before=await structure();
 await rep.locator('summary').filter({hasText:'Repérage calibré'}).click();
 await rep.getByLabel('Libellé du nouveau système',{exact:true}).fill('Saisie de repérage abandonnée');
 const reads=state.referenceReads;await rep.getByRole('button',{name:'Abandonner la saisie de repérage et recharger',exact:true}).click();
 await expect.poll(()=>state.referenceReads).toBe(reads+1);await expect.poll(structure).toEqual(before);
 await rep.locator('summary').filter({hasText:'Repérage calibré'}).click();
 await rep.getByLabel('Libellé du nouveau système',{exact:true}).fill('Brouillon avancé protégé');
 discard=false;const prompts=confirmations;await page.keyboard.press('Escape');
 await expect(rep).toBeVisible();await expect(rep.getByLabel('Libellé du nouveau système',{exact:true})).toHaveValue('Brouillon avancé protégé');
 expect(confirmations).toBe(prompts+1);discard=true;await page.keyboard.press('Escape');await expect(page.locator('#troncon-reperage-dialog')).toHaveCount(0);
 expect(state.writes).toEqual([]);expect(state.referenceWrites).toEqual([]);
});

for(const surface of ['carte','base'])test(`repérage automatique compact et gestion séparée ${surface}`,async({context,page})=>{
 const state=await setup(context);await open(page,surface);
 await expect(view(page).locator('[data-role=troncon-reperage-summary]')).toHaveText('Repérage automatique — 0 à 1\u202f000 m');
 expect(state.referenceReads).toBe(0);await expect(page.locator('#troncon-reperage-dialog')).toHaveCount(0);
 await expect(view(page).getByLabel('Libellé du nouveau système',{exact:true})).toHaveCount(0);
 await form(page).locator('[name=commentaire]').fill('Brouillon physique protégé');
 await view(page).getByRole('button',{name:'Gérer le repérage',exact:true}).click();
 await expect(page.locator('#troncon-reperage-dialog')).toHaveCount(0);await expect(form(page).locator('[name=commentaire]')).toHaveValue('Brouillon physique protégé');
 page.on('dialog',d=>d.accept());await view(page).locator(':scope > .entity-form__body > .entity-form__actions > [data-role=cancel]').click();
 await page.getByRole('button',{name:'Gérer le repérage',exact:true}).click();
 const dialog=page.locator('#troncon-reperage-dialog');await expect(dialog).toBeVisible();
 await expect(dialog.locator(':scope > .entity-form > .entity-form__header')).toContainText('Gestion du repérage');
 await expect(dialog.getByLabel('Libellé du nouveau système',{exact:true})).toHaveCount(1);
 expect(state.referenceReads).toBe(1);expect(state.referenceWrites).toEqual([]);expect(state.writes).toEqual([]);
 await dialog.locator(':scope > .entity-form > .entity-form__header [data-role=close]').click();await expect(dialog).toHaveCount(0);
 await expect(page.getByRole('button',{name:'Gérer le repérage',exact:true})).toBeFocused();
});

for(const width of [1440,390])test(`dessin physique, annulations et gestion dédiée ${width}px`,async({context,page})=>{
 // B: actual Leaflet acquisition and common shell; mutations prohibited by fixture.
 await page.setViewportSize({width,height:900});page.on('dialog',d=>d.accept());const state=await setup(context);await open(page,'carte');
 const initial=await signature(page);const geometry=form(page).locator('[data-section=geometry]');
 await geometry.getByRole('button',{name:'Modifier',exact:true}).click();
 await expect(page.locator('#cancel-editor-map-interaction')).toBeVisible();
 await page.locator('#map').click({position:{x:80,y:130}});
 await page.locator('#cancel-editor-map-interaction').click();
 await expect(form(page).locator('[name=geometry_mode]')).toHaveValue('garder');
 await expect(view(page).locator('[data-role=save]')).toBeEnabled();
 await geometry.getByRole('button',{name:'Modifier',exact:true}).click();
 await page.locator('#map').click({position:{x:90,y:140}});await page.locator('#map').click({position:{x:120,y:160}});
 await page.locator('#finish-editor-map-interaction').click();
 await expect(form(page).locator('[name=geometry_mode]')).toHaveValue('remplacer');
 await expect(view(page).locator('[data-role=troncon-reperage-summary]')).toContainText('après l’enregistrement');
 await view(page).locator(':scope > .entity-form__body > .entity-form__actions > [data-role=cancel]').click();
 await page.locator('#data-entity-detail-edit').click();expect(await signature(page)).toEqual(initial);
 await expect(page.locator('#cancel-editor-map-interaction')).toBeHidden();
 await view(page).getByRole('button',{name:'Gérer le repérage',exact:true}).click();
 const dialog=page.locator('#troncon-reperage-dialog');await expect(dialog).toBeVisible();
 await expect(dialog.getByRole('heading',{name:'Synthèse',exact:true})).toBeVisible();
 expect(await dialog.evaluate(el=>el.scrollWidth<=el.clientWidth+1)).toBe(true);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
 const folder=process.env.DIGUES_TRONCON_UX_ARTIFACTS||'audits/digues-troncon-ux-03/after';fs.mkdirSync(folder,{recursive:true});
 await page.screenshot({path:`${folder}/gestion-${width}.png`});await dialog.locator('.entity-form__header [data-role=close]').click();
 await page.locator('#data-entity-detail-edit').click();await page.screenshot({path:`${folder}/ordinaire-${width}.png`});
 expect(state.writes).toEqual([]);expect(state.referenceWrites).toEqual([]);
});

test('un succès C4 reste acquis si la publication des invalidations échoue',async({context,page})=>{
 // A/B DOM boundary: acknowledged mutation versus failed consumer, no real SQL effect claimed.
 await setup(context);await ready(page);
 const result=await page.evaluate(async id=>{
  const host=document.createElement('div');document.body.append(host);let writes=0;
  const graph=await BusinessRuntime.fetch('/api/troncons/'+id+'/reperage').then(r=>r.json());
  const component=TronconReperage.create({container:host,id,canWrite:()=>true,
   fetchJson:async(path,options)=>{if(options?.method==='PUT'){writes++;return {...graph,version:2,systemes:[{id:'system',libelle:'Succès acquis',valid:true}]};}return graph;},
   onMutation:async()=>{throw new Error('Publication refusée');}});
  await component.load();host.querySelector('details:not(.troncon-diagnostics)').open=true;
  const form=[...host.querySelectorAll('form')].find(f=>f.getAttribute('aria-label')==='Créer un système explicite');
  form.elements.libelle.value='Succès acquis';form.elements.libelle.dispatchEvent(new Event('input',{bubbles:true}));form.requestSubmit();
  await new Promise(resolve=>{const observer=new MutationObserver(()=>{if(host.textContent.includes('Actualisation des autres vues incomplète')){observer.disconnect();resolve();}});observer.observe(host,{subtree:true,childList:true,characterData:true});});
  const result={writes,dirty:component.hasDraft(),version:component.getState().version,text:host.textContent};component.dispose();host.remove();return result;
 },T);
 expect(result.writes).toBe(1);expect(result.dirty).toBe(false);expect(result.version).toBe(2);expect(result.text).toContain('ne renvoyez pas cette opération');
});

test('le GPS du Tronçon édite le brouillon physique et son annulation le préserve',async({context,page})=>{
 // B: same installed-PWA simulator as the GPS suite, actual widget/draft lifetime.
 await context.addInitScript(()=>{
  const match=window.matchMedia.bind(window);window.matchMedia=q=>q==='(display-mode: standalone)'?{matches:true,addEventListener(){}}:match(q);
  window.__tronconGps=[];Object.defineProperty(navigator,'geolocation',{configurable:true,value:{
   getCurrentPosition(){},watchPosition(resolve){window.__tronconGps.push(resolve);return window.__tronconGps.length;},clearWatch(){}}});
 });
 page.on('dialog',d=>d.accept());const state=await setup(context);await open(page,'carte');
 const initial=await signature(page),tracker=page.locator('[data-gps-owner=troncon-edit-geometry]');
 await tracker.locator('[data-gps=start]').click();
 await expect.poll(()=>page.evaluate(()=>window.__tronconGps.length)).toBe(1);
 await page.evaluate(()=>{const time=Date.now();for(const [i,longitude]of [2.34,2.3403].entries())window.__tronconGps[0]({coords:{longitude,latitude:48.84,accuracy:7},timestamp:time-10000+i*5000});});
 await tracker.locator('[data-gps=finish]').click();await tracker.locator('[data-gps=confirm]').click();
 await expect(form(page).locator('[name=geometry_mode]')).toHaveValue('remplacer');
 const first=await form(page).locator('[name=geometry_sommets]').inputValue();expect(first).toContain('2.3403');
 await tracker.locator('[data-gps=start]').click();await tracker.locator('[data-gps=cancel]').click();
 await expect(form(page).locator('[name=geometry_sommets]')).toHaveValue(first);
 await view(page).locator(':scope > .entity-form__body > .entity-form__actions > [data-role=cancel]').click();
 await page.locator('#data-entity-detail-edit').click();expect(await signature(page)).toEqual(initial);
 await form(page).locator('[data-section=geometry]').scrollIntoViewIfNeeded();
 const folder=process.env.DIGUES_TRONCON_UX_ARTIFACTS||'audits/digues-troncon-ux-03/after';fs.mkdirSync(folder,{recursive:true});
 await page.screenshot({path:folder+'/geometry-edit.png'});
 expect(state.writes).toEqual([]);expect(state.referenceWrites).toEqual([]);
});

test('consultation spécialisée lecteur et destruction lors du changement de contexte',async({context,page})=>{
 // B DOM permission projection; actual authorization is covered by C4 multi-base PostgreSQL tests.
 const state=await setup(context);await ready(page);
 await page.evaluate(()=>{const runtime=BusinessRuntime;BusinessRuntime=Object.freeze({...runtime,can:permission=>permission==='business.read'});});
 await page.evaluate(id=>MapEntityNavigation.open('troncons',id),T);
 await expect(page.locator('#data-entity-detail-edit')).toBeHidden();
 await page.getByRole('button',{name:'Gérer le repérage',exact:true}).click();
 const dialog=page.locator('#troncon-reperage-dialog');await expect(dialog).toBeVisible();
 await expect(dialog.getByLabel('Libellé du nouveau système',{exact:true})).toBeDisabled();
 const calc=dialog.getByRole('form',{name:'Localiser par abscisse',exact:true});
 await expect(calc.getByLabel('Abscisse curviligne (m)')).toBeEnabled();
 await calc.getByRole('button',{name:'Calculer',exact:true}).click();await expect(dialog).toContainText('Position :');
 await page.evaluate(()=>dispatchEvent(new CustomEvent('digues-app:context-closed')));await expect(dialog).toHaveCount(0);
 expect(state.referenceWrites).toEqual([]);expect(state.writes).toEqual([]);
});


test('une courbe historique non affichable reste propre et ouvre sa gestion sans faux brouillon',async({context,page})=>{
 const state=await setup(context);state.records.troncons[0].geometry=null;
 Object.assign(state.records.troncons[0].properties,{geometry_type:'CIRCULARSTRING',geometry_srid:3950});
 Object.assign(state.referenceGraph,{reperage_implicite_disponible:false,reperes_virtuels:[]});
 Object.assign(state.referenceGraph.axe,{type:'CIRCULARSTRING',geometry:null,longueur_m:null,exploitable:false});
 await open(page,'carte');await expect(form(page).locator('[name=geometry_mode]')).toHaveValue('garder');
 await expect(form(page).locator('[name=geometry_type_input]')).toHaveValue('LineString');
 await view(page).getByRole('button',{name:'Gérer le repérage',exact:true}).click();
 await expect(page.locator('#troncon-reperage-dialog')).toBeVisible();expect(state.writes).toEqual([]);expect(state.referenceWrites).toEqual([]);
});
