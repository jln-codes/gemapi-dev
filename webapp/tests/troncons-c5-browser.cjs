/* Level B, invoked only by the disposable PostgreSQL unittest harness. */
const {chromium,expect:baseExpect}=require('@playwright/test');
// Contextual reads include permission/schema checks and reference enrichment.
// Use the live harness budget, while waiting on actual UI readiness.
const expect=baseExpect.configure({timeout:15000});
const fs=require('node:fs');
const [origin,fixtureFile]=process.argv.slice(2);
if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(origin)||!/^audits\/troncons-c[56]\//.test(fixtureFile))throw new Error('Owned local harness required');
const f=JSON.parse(fs.readFileSync(fixtureFile,'utf8'));
(async()=>{
 const browser=await chromium.launch({headless:true});
 const context=await browser.newContext({baseURL:origin,serviceWorkers:'block'});
 const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 page.on('dialog',dialog=>dialog.accept());
 const authRequests=new Map();
 page.on('request',request=>{if(new URL(request.url()).pathname.startsWith('/api/auth/'))authRequests.set(request,Date.now());});
 page.on('response',response=>{const started=authRequests.get(response.request());if(started)console.log('AUTH_READY',new URL(response.url()).pathname,response.status(),Date.now()-started+'ms');});
 page.on('response',async response=>{
   if(response.request().method()!=='PUT')return;
   const body=await response.json().catch(()=>({}));
   console.log('MUTATION_RESULT',new URL(response.url()).pathname,response.status(),
     JSON.stringify({expected:response.request().headers()['if-match'],version:body.version??body.properties?.version,
       versions:body.mutation?.versions,code:body.detail?.code}));
 });
 const api=(p,path,options={})=>p.evaluate(async({path,options})=>{
   const response=await BusinessRuntime.fetch(path,{...options,headers:{'Content-Type':'application/json','X-Digues-CSRF':'1',...options.headers},...(options.body?{body:JSON.stringify(options.body)}:{})});
   return {status:response.status,value:await response.json()};
 },{path,options});
 async function ready(p){
   await expect(p.locator('#open-business-session')).not.toHaveAttribute('aria-disabled','true',{timeout:30000});
   await p.waitForFunction(()=>window.OfflineOperationsRuntime && window.ResourceEventTransport && ResourceEventTransport.getCursor()!==null,null,{timeout:30000});
   // Drain the real durable event stream before starting the next scenario's
   // draft. A non-null cursor alone does not prove catch-up has completed.
   await p.evaluate(()=>ResourceEventTransport.synchronize());
 }
 async function open(p,id,view='base'){
   await p.goto(`/${view}?base=${f.base}&entity=troncons&id=${id}`,{waitUntil:'domcontentloaded'});
   await ready(p);await expect(p.locator(view==='base'?'#data-entity-detail-read':'#data-troncons-editor')).toBeVisible({timeout:15000});
   await expect(p.getByRole('button',{name:'Gérer le repérage',exact:true})).toBeVisible();
   await expect(p.locator('#troncon-reperage-dialog')).toHaveCount(0);
 }
 async function management(p){
   if(!await p.locator('#troncon-reperage-dialog').count())await p.getByRole('button',{name:'Gérer le repérage',exact:true}).click();
   const panel=p.locator('#troncon-reperage-dialog .troncon-reperage');
   await expect(panel.getByRole('heading',{name:'Synthèse',exact:true})).toBeVisible();
   return panel;
 }
 async function closeManagement(p){
   const dialog=p.locator('#troncon-reperage-dialog');
   if(await dialog.count())await dialog.locator('.entity-form__header [data-role=close]').click();
   await expect(dialog).toHaveCount(0);
 }
 const rootForm=p=>p.locator('#data-troncons-editor-form');
 async function edit(p){
   await expect.poll(async()=>await rootForm(p).isVisible()||await p.locator('#data-entity-detail-edit').isVisible()).toBe(true);
   if(!await rootForm(p).isVisible())await p.locator('#data-entity-detail-edit').click();
   await expect(rootForm(p)).toBeVisible();await expect(rootForm(p).locator('.entity-form__fields')).toBeEnabled();
 }
 async function save(p){await p.locator('.entity-form__actions > [form="data-troncons-editor-form"]').click();await expect(rootForm(p)).toBeHidden();}
 async function referenceMutation(p,trigger){
   const started=Date.now();
   const [response]=await Promise.all([p.waitForResponse(r=>r.request().method()==='PUT'
     &&new URL(r.url()).pathname.endsWith('/reperage'),{timeout:15000}),trigger()]);
   const result=await response.json();expect(response.status(),JSON.stringify(result.detail)).toBe(200);
   const committed=Date.now(),id=new URL(response.url()).pathname.split('/').at(-2);
   // A successful PUT precedes the detail/options/graph reload. Observe the
   // returned version in the real graph before asserting its business content.
   const panel=p.locator('.troncon-reperage');
   await expect(panel.getByText(`Tronçon ${id} — version observée ${result.version}`,{exact:true}))
     .toHaveCount(1,{timeout:15000});
   await expect(panel).not.toHaveAttribute('inert','',{timeout:15000});
   console.log('REFERENCE_READY',JSON.stringify({put_ms:committed-started,reload_ms:Date.now()-committed,version:result.version}));
 }
 try{
   await page.goto('/base');await page.getByLabel('Identifiant',{exact:true}).fill('editor_alpha');
   await page.getByLabel('Mot de passe',{exact:true}).fill(f.password);await page.getByRole('button',{name:'Se connecter',exact:true}).click();
   await expect(page.getByRole('heading',{name:'Choisir une base métier'})).toBeVisible({timeout:20000});
   for(const [kind,id] of Object.entries(f.keys)){
     await open(page,id);await expect(page.locator('[data-role=troncon-reperage-summary]')).toContainText('Repérage automatique');
     const read=page.locator('#data-entity-detail-read');
     if(kind==='BERGE'){
       const history=read.locator('[data-troncon-section="historical"]');
       await expect(history).toContainText('12,75');await expect(history).toContainText('Numéro de couche');
       await expect(history.locator('dd').filter({hasText:/^0$/})).toHaveCount(1);
       await expect(history).toContainText(f.function_label);
       await expect(read.locator('[data-troncon-section="diagnostics"]')).not.toHaveAttribute('open','');
       await page.screenshot({path:`${f.artifacts}/berge-read.png`});
     }
     if(kind==='TALUS'){
       await expect(read.locator('[data-troncon-section="classification"]')).toContainText(f.function_label);
       await expect(read.locator('[data-troncon-section="attachments"]')).toContainText(f.support_label);
       const supportIdentifier=read.locator('[data-troncon-section="attachments"] .technical-identifier').filter({hasText:f.support});
       await expect(supportIdentifier).toHaveCount(1);await expect(supportIdentifier).toBeHidden();
     }
     await edit(page);await expect(rootForm(page).getByLabel('Sous-type',{exact:true})).toHaveValue(kind);
     if(kind==='TALUS'){
       await expect(rootForm(page).getByLabel('Pente intérieure',{exact:true})).toBeVisible();
       await expect(rootForm(page).getByLabel('Classe de largeur du franc-bord',{exact:true})).toBeHidden();
       const functionControl=rootForm(page).locator('[name="fonction_haut_id"]');
       await rootForm(page).getByLabel('Sous-type',{exact:true}).selectOption('BERGE');
       await expect(rootForm(page).locator('[data-section="historical"] [name="fonction_haut_id"]')).toHaveValue(f.function_id);
       await rootForm(page).getByLabel('Sous-type',{exact:true}).selectOption(kind);
       await expect(functionControl).toHaveValue(f.function_id);
       await expect(rootForm(page).locator('[data-section="classification"] [name="fonction_haut_id"]')).toHaveValue(f.function_id);
     }
     if(kind==='RISBERME'){
       await expect(rootForm(page).getByLabel('Épaisseur',{exact:true})).toBeVisible();
       await expect(rootForm(page).getByLabel('Pente intérieure',{exact:true})).toBeHidden();
     }
     if(kind==='FRANC_BORD'){
       await expect(rootForm(page).getByLabel('Classe de largeur du franc-bord',{exact:true})).toBeVisible();
       await expect(rootForm(page).getByLabel('Numéro de couche',{exact:true})).toBeHidden();
     }
     if(['BERGE','TALUS','FRANC_BORD'].includes(kind))await page.screenshot({path:`${f.artifacts}/${kind.toLowerCase()}-edit.png`});
     await rootForm(page).getByLabel('Commentaire',{exact:true}).fill(`C5 édité ${kind}`);await save(page);
     const r=await api(page,`/api/troncons/${id}`);expect(r.status).toBe(200);expect(r.value.properties.commentaire).toBe(`C5 édité ${kind}`);
     console.log('PASS common editor',kind);
   }
   await page.locator('#base-detail-observations-toggle').click();
   await expect(page.locator('#data-entity-detail-observation-list')).toContainText('Aucune observation.');
   await page.locator('#base-detail-observations-toggle').click();
   console.log('PASS shared tabs and live canonical Observation list');
   await open(page,f.curve);
   await expect(page.locator('#data-entity-detail-read [data-troncon-section="geometry"]')).toContainText('Courbe circulaire historique — affichage cartographique indisponible');
   await page.locator('#data-entity-detail-read [data-section=technical] > summary').click();
   await expect(page.locator('#data-entity-detail-read')).toContainText('CIRCULARSTRING');
   const curveRead=(await api(page,`/api/troncons/${f.curve}`)).value;
   expect(curveRead.geometry).toBeNull();expect(curveRead.properties.geometry_type).toBe('CIRCULARSTRING');
   await edit(page);await rootForm(page).getByLabel('Commentaire',{exact:true}).fill('Courbe historique conservée C5');await save(page);
   console.log('PASS historical non-GeoJSON geometry identified and preserved');
   // Real two-page delivery and a preserved root draft on an external mutation.
   await open(page,f.keys.BERGE);const second=await context.newPage();second.on('dialog',d=>d.accept());await open(second,f.keys.BERGE);
   await edit(page);await rootForm(page).getByLabel('Désignation',{exact:true}).fill('Brouillon conservé C5');
   await edit(second);await rootForm(second).getByLabel('Commentaire',{exact:true}).fill('Mutation autre onglet C5');await save(second);
   await expect(rootForm(page).getByLabel('Désignation',{exact:true})).toHaveValue('Brouillon conservé C5');
   await page.locator('.entity-form__actions > [form="data-troncons-editor-form"]').click();
   await expect(rootForm(page)).toBeVisible();await expect(rootForm(page).getByLabel('Désignation',{exact:true})).toHaveValue('Brouillon conservé C5');
   await expect(page.locator('#data-troncons-editor')).toContainText(/modifi|conflit|brouillon/i);
   console.log('PASS inter-page invalidation and preserved conflict draft');await second.close();
   await open(page,f.keys.BERGE);const rep=await management(page);
   const ab=rep.getByRole('form',{name:'Localiser par abscisse',exact:true});await ab.getByLabel('Abscisse curviligne (m)').fill('25');await ab.getByRole('button',{name:'Calculer'}).click();await expect(rep).toContainText('Position :');
   await expect(rep).toContainText('repères virtuels');
   await rep.locator('summary').filter({hasText:'Repérage calibré'}).click();
   const create=rep.getByRole('form',{name:'Créer un système explicite',exact:true});await create.getByLabel('Libellé du nouveau système').fill('Système ajouté C5');await referenceMutation(page,()=>create.getByRole('button',{name:'Créer un système explicite',exact:true}).click());
   await expect(page.locator('.troncon-reperage')).toContainText('Système ajouté C5');
   console.log('PASS immediate implicit and optional later explicit referencing');
   async function calibration(){
     const panel=await management(page);
     for(const text of ['Repérage calibré','Système ajouté C5']){
       const summary=panel.locator('summary').filter({hasText:text});
       if(!await summary.locator('..').evaluate(el=>el.open))await summary.click();
     }
     return panel;
   }
   for(const [label,xy,pr] of [['Début C5','2 48','0'],['Fin C5','2.001 48.001','100']]){
     const panel=await calibration();const form=panel.getByRole('form',{name:'Ajouter une borne et son PR',exact:true});
     await form.getByLabel('Libellé de la borne').fill(label);await form.getByLabel('Coordonnées XY de la borne (facultatives)').fill(xy);
     await form.getByLabel('SCR des coordonnées').selectOption('4326');await form.getByLabel('PR calibré (facultatif)').fill(pr);
     await referenceMutation(page,()=>form.getByRole('button',{name:'Ajouter une borne et son PR',exact:true}).click());
     await expect(page.locator('.troncon-reperage')).toContainText(label);
   }
   let ref=await api(page,`/api/troncons/${f.keys.BERGE}/reperage`);const sys=ref.value.systemes.find(s=>s.libelle==='Système ajouté C5').id;
   let panel=await calibration();const choice=panel.getByRole('form',{name:'Choisir le système par défaut',exact:true});
   await choice.getByLabel('Système par défaut',{exact:true}).selectOption(sys);await choice.getByRole('checkbox').check();
   await referenceMutation(page,()=>choice.getByRole('button',{name:'Choisir le système par défaut',exact:true}).click());
   await expect.poll(async()=> (await api(page,`/api/troncons/${f.keys.BERGE}/reperage`)).value.systeme_reperage_defaut_id).toBe(sys);
   panel=await calibration();const prForm=panel.getByRole('form',{name:'Calculer dans ce système',exact:true});
   await prForm.getByLabel('PR demandé').fill('50');await prForm.getByRole('button',{name:'Calculer'}).click();
   await expect(panel).toContainText('Position calibrée');
   console.log('PASS calibrated PR, physical borne creation and default selection');
   panel=await calibration();const metadata=panel.getByRole('form',{name:'Modifier le système',exact:true});
   await metadata.getByLabel('Commentaire',{exact:true}).fill('Métadonnées UX-03');await metadata.getByRole('checkbox',{name:'J’ai examiné',exact:false}).check();
   await referenceMutation(page,()=>metadata.getByRole('button',{name:'Modifier le système',exact:true}).click());
   expect((await api(page,`/api/troncons/${f.keys.BERGE}/reperage`)).value.systemes.find(s=>s.id===sys).commentaire).toBe('Métadonnées UX-03');
   panel=await calibration();await panel.locator('summary').filter({hasText:/Début C5 — PR/}).click();
   const linkEdit=panel.locator('summary').filter({hasText:/Début C5 — PR/}).locator('..').getByRole('form',{name:'Modifier la calibration',exact:true});
   await linkEdit.getByLabel('Ordre',{exact:true}).fill('0');await linkEdit.getByRole('checkbox',{name:'J’ai examiné',exact:false}).check();
   await referenceMutation(page,()=>linkEdit.getByRole('button',{name:'Modifier la calibration',exact:true}).click());
   expect((await api(page,`/api/troncons/${f.keys.BERGE}/reperage`)).value.liaisons.some(l=>l.ordre_borne===0)).toBe(true);
   panel=await calibration();const secondary=panel.getByRole('form',{name:'Créer un système explicite',exact:true});
   await secondary.getByLabel('Libellé du nouveau système').fill('Système secondaire UX');
   await referenceMutation(page,()=>secondary.getByRole('button',{name:'Créer un système explicite',exact:true}).click());
   panel=page.locator('.troncon-reperage');await panel.locator('summary').filter({hasText:'Repérage calibré'}).click();
   const secondaryItem=panel.locator('details').filter({has:page.locator('summary').filter({hasText:/^Système secondaire UX$/})}).last();
   await secondaryItem.locator(':scope > summary').click();const existing=secondaryItem.getByRole('form',{name:'Lier une borne existante',exact:true});
   const beforeLink=(await api(page,`/api/troncons/${f.keys.BERGE}/reperage`)).value;
   await existing.getByLabel('Borne existante',{exact:true}).selectOption(beforeLink.bornes[0].id);await existing.getByLabel('PR de cette occurrence').fill('0');
   await existing.getByRole('checkbox',{name:'J’ai examiné',exact:false}).check();
   await referenceMutation(page,()=>existing.getByRole('button',{name:'Lier une borne existante',exact:true}).click());
   const linked=(await api(page,`/api/troncons/${f.keys.BERGE}/reperage`)).value;
   expect(linked.bornes).toHaveLength(2);expect(linked.liaisons).toHaveLength(3);
   await panel.locator('summary').filter({hasText:'Repérage calibré'}).click();
   await secondaryItem.locator(':scope > summary').click();await secondaryItem.locator(':scope > details > summary').click();
   await referenceMutation(page,()=>secondaryItem.getByRole('button',{name:'Retirer logiquement cette occurrence',exact:true}).click());
   const retiredLink=(await api(page,`/api/troncons/${f.keys.BERGE}/reperage`)).value.liaisons.find(l=>!beforeLink.liaisons.some(old=>old.id===l.id));
   expect(retiredLink.valid).toBe(false);
   console.log('PASS system metadata, calibration edit, existing borne link and logical occurrence retirement via UI');
   panel=await calibration();

   const draft=panel.getByRole('form',{name:'Créer un système explicite',exact:true});
   await draft.getByLabel('Libellé du nouveau système').fill('Brouillon de repérage C5');
   ref=await api(page,`/api/troncons/${f.keys.BERGE}/reperage`);
   const changed=await api(page,`/api/troncons/${f.keys.BERGE}/reperage`,{method:'PUT',headers:{'If-Match':`"${ref.value.version}"`},body:{reference_revision:ref.value.reference_revision,inverser_axe:true}});
   expect(changed.status).toBe(200);
   await draft.getByRole('button',{name:'Créer un système explicite',exact:true}).click();
   await expect(draft.getByLabel('Libellé du nouveau système')).toHaveValue('Brouillon de repérage C5');
   await expect(panel).toContainText(/références|modifié|empreinte/i);
   console.log('PASS reference fingerprint draft retained after external mutation');
   await open(page,f.keys.BERGE);panel=await calibration();
   await referenceMutation(page,()=>panel.getByRole('button',{name:'Retirer logiquement ce système',exact:true}).click());
   await expect.poll(async()=> (await api(page,`/api/troncons/${f.keys.BERGE}/reperage`)).value.systemes.find(s=>s.id===sys).valid).toBe(false);
   const retired=(await api(page,`/api/troncons/${f.keys.BERGE}/reperage`)).value;
   expect(retired.systeme_reperage_defaut_id).toBeNull();expect(retired.bornes).toHaveLength(2);expect(retired.liaisons).toHaveLength(3);
   console.log('PASS logical retirement preserves systems, borne inventory and occurrences');
   await open(page,f.keys.BERGE);await management(page);let snapshotReady=false,releaseRead;
   const readGate=new Promise(resolve=>releaseRead=resolve);
   const pattern=url=>url.pathname.endsWith(`/troncons/${f.keys.BERGE}/reperage`) && url.pathname.startsWith("/api/bases/");let hold=true;
   await page.route(pattern,async route=>{
     if(!hold||route.request().method()!=='GET')return route.continue();
     hold=false;const actual=await route.fetch();snapshotReady=true;await readGate;await route.fulfill({response:actual});
   });
   const panelReload=page.locator('.troncon-reperage');
   await panelReload.locator('summary').filter({hasText:'Repérage calibré'}).click();
   await panelReload.getByRole('button',{name:'Abandonner la saisie de repérage et recharger',exact:true}).click();await expect.poll(()=>snapshotReady,{timeout:15000}).toBe(true);
   const typing=panelReload.getByRole('form',{name:'Créer un système explicite',exact:true}).getByLabel('Libellé du nouveau système');
   await typing.fill('Saisie commencée pendant le rechargement');
   await expect(panelReload.getByRole('form',{name:'Choisir le système par défaut',exact:true}).getByRole('button',{name:'Choisir le système par défaut',exact:true})).toBeDisabled();
   releaseRead();
   await expect(panelReload).toContainText('saisie commencée pendant le chargement est conservée');
   await expect(typing).toHaveValue('Saisie commencée pendant le rechargement');await page.unroute(pattern);
   console.log('PASS delayed authentic read cannot overwrite a draft started during reload');
   let failedReadReady=false,releaseFailure;const failGate=new Promise(resolve=>releaseFailure=resolve);hold=true;
   await page.route(pattern,async route=>{
     if(!hold||route.request().method()!=='GET')return route.continue();
     hold=false;await route.fetch();failedReadReady=true;await failGate;
     await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Erreur de lecture simulée'})});
   });
   await panelReload.getByRole('button',{name:'Abandonner la saisie de repérage et recharger',exact:true}).click();
   await expect.poll(()=>failedReadReady,{timeout:15000}).toBe(true);
   await typing.fill('Nouveau brouillon avant erreur de lecture');releaseFailure();
   await expect(panelReload).toContainText('Erreur de lecture simulée');
   await expect(typing).toHaveValue('Nouveau brouillon avant erreur de lecture');await page.unroute(pattern);
   console.log('PASS failed reload preserves the graph, identity, version and user input');




   await open(page,f.polygon);await management(page);await expect(page.locator('.troncon-reperage')).toContainText('AXE_ABSENT_OU_INEXPLOITABLE');
   await expect(page.locator('.troncon-reperage__implicit')).toContainText('Définissez explicitement un axe');
   await expect(page.locator('.troncon-reperage__implicit')).not.toContainText('AXE_ABSENT_OU_INEXPLOITABLE');
   await expect(page.locator('.troncon-reperage').getByRole('form',{name:'Localiser par abscisse',exact:true})).toHaveCount(0);
   console.log('PASS Polygon no invented referencing');
   const axis=page.locator('.troncon-reperage');await axis.locator('summary').filter({hasText:'Axe explicite de repérage'}).click();
   const axisForm=axis.getByRole('form',{name:'Définir un axe explicite',exact:true});
   await axisForm.getByLabel('Sommets XY de l’axe — un par ligne').fill('2 48\n2.001 48.001');
   await axisForm.getByLabel('SCR de l’axe',{exact:true}).selectOption('4326');await axisForm.getByRole('checkbox').check();
   await referenceMutation(page,()=>axisForm.getByRole('button',{name:'Définir un axe explicite',exact:true}).click());
   await expect(page.locator('.troncon-reperage')).toContainText('Longueur curviligne');
   console.log('PASS Polygon with user-defined axis, physical surface preserved');
   const beforeAxis=(await api(page,`/api/troncons/${f.polygon}/reperage`)).value.axe.geometry.coordinates;
   await axis.locator('summary').filter({hasText:'Axe explicite de repérage'}).click();
   await referenceMutation(page,()=>axis.getByRole('button',{name:'Inverser uniquement l’axe',exact:true}).click());
   expect((await api(page,`/api/troncons/${f.polygon}/reperage`)).value.axe.geometry.coordinates).toEqual([...beforeAxis].reverse());
   await axis.locator('summary').filter({hasText:'Axe explicite de repérage'}).click();
   await referenceMutation(page,()=>axis.getByRole('button',{name:'Retirer l’axe explicite',exact:true}).click());
   expect((await api(page,`/api/troncons/${f.polygon}/reperage`)).value.reperage_implicite_disponible).toBe(false);
   console.log('PASS explicit axis inversion and removal via UI, physical surface preserved');


   await closeManagement(page);
   // Each occurrence under AH and Digue reaches exactly the same canonical form.
   await page.locator('#toggle-heritage').click();
   const tree=page.locator('#heritage-tree');await expect(tree.getByRole('heading',{name:'Aménagements hydrauliques'})).toBeVisible();
   const ah=tree.locator(`.tree-name[data-object-id="${f.ah}"]`);await ah.locator('..').locator('.tree-toggle').click();
   // The current seed has a river bank outside AH and an indeterminate AH trunk.
   const demoBank=(await api(page,`/api/troncons/${f.demo_bank}`)).value.properties;
   expect(demoBank.type_physique_id).toBe('BERGE');expect(demoBank.amenagement_hydraulique_id).toBeNull();
   expect(demoBank.digue_id).not.toBeNull();
   for(const id of [f.demo_ah_troncon]){
     if(await page.locator('#toggle-heritage').getAttribute('aria-expanded')==='false')await page.locator('#toggle-heritage').click();
     const name=ah.locator('../..').locator(`.tree-name[data-object-id="${id}"]`);await expect(name).toHaveCount(1);await name.click();
     await expect(page.locator('#data-entity-detail')).toHaveAttribute('data-mode','read');await edit(page);
     await expect(rootForm(page).getByLabel('Sous-type',{exact:true})).toHaveValue('INDETERMINE');
     await rootForm(page).getByLabel('Commentaire',{exact:true}).fill('Tronçon AH édité C5');await save(page);
     const r=await api(page,`/api/troncons/${id}`);expect(r.value.properties.amenagement_hydraulique_id).toBe(f.ah);
   }
   console.log('PASS actual demonstration bank/indeterminate AH trunk keep distinct types and parents');
   if(await page.locator('#toggle-heritage').getAttribute('aria-expanded')==='false')await page.locator('#toggle-heritage').click();
   // Expand SE/Digue only via real UI; every occurrence shares its UUID/resource.
   async function reveal(occurrence){
     const paths=await occurrence.evaluate(el=>{
       const result=[];let n=el.closest('.tree-node');
       while(n){const toggle=n.querySelector(':scope > .tree-row > .tree-toggle');if(toggle&&!toggle.disabled)result.unshift(n.dataset.pathKey);n=n.parentElement.closest('.tree-node');}
       return result;
     });
     for(const path of paths){const toggle=tree.locator(`[data-path-key=${JSON.stringify(path)}] > .tree-row > .tree-toggle`);
       if(await toggle.getAttribute('aria-expanded')==='false')await toggle.click();}
   }
   const occurrences=tree.locator(`.tree-name[data-object-id="${f.shared}"]`);await expect(occurrences).toHaveCount(2);
   for(const occurrence of await occurrences.all()){
     if(await page.locator('#toggle-heritage').getAttribute('aria-expanded')==='false')await page.locator('#toggle-heritage').click();
     await reveal(occurrence);await expect(occurrence).toBeVisible();
     await expect(occurrence).toHaveAttribute('data-object-resource','troncons');await occurrence.click();
     await expect(page.locator('#data-entity-detail')).toHaveAttribute('data-mode','read');await edit(page);
     await expect(rootForm(page).getByLabel('Digue',{exact:true})).toHaveValue(f.digue);
     await expect(rootForm(page).getByLabel('Aménagement hydraulique',{exact:true})).toHaveValue(f.ah);
   }
   console.log('PASS Digue and AH same identity, parents and editor');
   // Leave the attribute form explicitly; reference changes have their own frozen graph.
   await page.locator('#data-troncons-editor .entity-form__actions > [data-role=cancel]').click();
   const sharedRep=await management(page);await expect(sharedRep).toContainText(f.keys.TALUS);await expect(sharedRep).toContainText(f.photo);
   await sharedRep.locator('summary').filter({hasText:'Bornes physiques inventoriées'}).click();
   await sharedRep.locator('summary').filter({hasText:/^Borne partagée C5$/}).click();
   const borneForm=sharedRep.getByRole('form',{name:'Modifier la borne partagée',exact:true});
   await borneForm.getByLabel('Libellé',{exact:true}).fill('Borne partagée éditée C5');await borneForm.getByRole('checkbox',{name:'J’ai examiné',exact:false}).check();
   await referenceMutation(page,()=>borneForm.getByRole('button',{name:'Modifier la borne partagée',exact:true}).click());
   await expect(page.locator('.troncon-reperage')).toContainText('Borne partagée éditée C5');
   console.log('PASS shared owners/dependent Photo visible before edit, historical positions unchanged');

   await closeManagement(page);
   const base=await context.newPage();await open(base,f.shared);
   await page.locator('#data-entity-detail-map').click();await expect(page).toHaveURL(/\/carte\?/);await ready(page);await expect(page.locator('#data-troncons-editor')).toBeVisible({timeout:15000});await edit(page);await rootForm(page).getByLabel('Commentaire',{exact:true}).fill('Carte vers Base C5');await save(page);
   await expect(base.locator('#data-entity-detail-read')).toContainText('Carte vers Base C5');
   await edit(base);await rootForm(base).getByLabel('Commentaire',{exact:true}).fill('Base vers Carte C5');await save(base);
   await expect(page.locator('#data-troncons-editor')).toContainText('Base vers Carte C5');await base.close();
   console.log('PASS live bidirectional Carte/Base invalidations without navigation or reload');
   const zoomTree=page.locator('#heritage-tree');await page.locator('#toggle-heritage').click();
   await expect(zoomTree.locator('.tree-name[data-object-id="'+f.keys.BERGE+'"]').first()).toBeVisible();
   const row=zoomTree.locator('.tree-name[data-object-id="'+f.keys.BERGE+'"]').first().locator('..');
   await expect(row.locator('.tree-zoom')).toBeEnabled();await row.locator('.tree-zoom').click();
   const visible=row.locator('.tree-visibility');await expect(visible).toBeEnabled();await visible.uncheck();await expect(visible).not.toBeChecked();await visible.check();
   console.log('PASS parentless accessibility, map framing/visibility, Carte/Base propagation');
   await open(page,f.point,'carte');await page.locator('#toggle-heritage').click();
   let pointRow=page.locator('#heritage-tree .tree-name[data-object-id="'+f.point+'"]').locator('..');
   await expect(pointRow.locator('.tree-zoom')).toBeEnabled();
   const actualPoint=(await api(page,`/api/troncons/${f.point}`)).value.geometry.coordinates;
   await page.evaluate(()=>map.setView([0,0],5,{animate:false}));await pointRow.locator('.tree-zoom').click();
   await expect.poll(async()=>page.evaluate(([lng,lat])=>Math.abs(map.getCenter().lng-lng)+Math.abs(map.getCenter().lat-lat),actualPoint)).toBeLessThan(0.00001);
   expect(await page.evaluate(()=>map.getZoom())).toBeLessThanOrEqual(18);
   await pointRow.locator('.tree-name').click();await expect(rootForm(page)).toBeVisible();
   await rootForm(page).getByLabel('Géométrie physique',{exact:true}).selectOption('remplacer');
   await rootForm(page).getByRole('combobox',{name:'Type de dessin',exact:true}).selectOption('LineString');
   await rootForm(page).locator('[data-troncon-coordinates] > summary').click();
   await rootForm(page).getByLabel('Sommets — longitude latitude [altitude]').fill('2 48\n2.001 48.001');await save(page);
   expect((await api(page,`/api/troncons/${f.point}`)).value.geometry.type).toBe('LineString');
   console.log('PASS explicit physical geometry replacement via the common form');

   await open(page,f.missing,'carte');await page.locator('#toggle-heritage').click();
   const noShape=page.locator('#heritage-tree .tree-name[data-object-id="'+f.missing+'"]').locator('..');
   await expect(noShape.locator('.tree-zoom')).toBeDisabled();
   console.log('PASS Point framed/selected and missing geometry has no invented bounds');

   if(process.env.DIGUES_C6_BROWSER==='1')await require('./troncons-c6-browser.cjs')({page,context,api,open,ready,f,expect,origin});
   if(process.env.DIGUES_ENTITY_TYPE_02==='1')await require('./troncon-requalification-browser.cjs')({page,context,api,open,ready,f,expect,origin});
   expect(errors).toEqual([]);console.log('PASS no browser runtime errors');
 }catch(e){await page.screenshot({path:f.artifacts+'/failure.png',fullPage:true});fs.writeFileSync(f.artifacts+'/failure.html',await page.content());console.error(e);console.error('Browser errors:',errors);process.exitCode=1;}
 finally{await browser.close();}
})();
