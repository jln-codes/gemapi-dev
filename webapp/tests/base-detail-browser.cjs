/* Niveau B : lancé exclusivement par le banc HTTP/PostgreSQL privé du dépôt. */
const {chromium,expect:baseExpect}=require('@playwright/test');
// Real contextual reads include permission/schema checks and reference labels.
// Match the existing live browser budget; readiness is still observable.
const expect=baseExpect.configure({timeout:15000});
const fs=require('node:fs');
const [origin,fixturePath]=process.argv.slice(2);
if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(origin)||!/^audits\/base-fiches-02\/live\/[a-f0-9]+\/fixture.json$/.test(fixturePath))throw new Error('Banc local privé requis');
const f=JSON.parse(fs.readFileSync(fixturePath,'utf8'));
const endpoints={systemes:'systemes-endiguement',digues:'digues',troncons:'troncons',desordres:'desordres',
 mobilier:'mobilier',equipements_mesure:'equipements-mesure',reseaux_techniques:'reseaux-techniques',ouvrages_particuliers:'ouvrages-particuliers',
 cheminements:'cheminements',vegetation:'vegetation',amenagements_hydrauliques:'amenagements-hydrauliques',zones_stockage:'zones-stockage',
 prestations:'prestations',troncons_lit:'troncons-lit',elements_morphologiques_lit:'elements-morphologiques-lit',observations:'observations'};
(async()=>{
 const browser=await chromium.launch({headless:true});const contexts=[];const errors=[];const matrix=[];
 async function makeContext(login){
  const context=await browser.newContext({baseURL:origin,serviceWorkers:'block',viewport:{width:1440,height:900}});contexts.push(context);
  context.on('page',page=>{
   page.on('pageerror',e=>errors.push(e.message));
   const started=new WeakMap();page.on('request',request=>started.set(request,Date.now()));
   page.on('response',response=>{
    const elapsed=Date.now()-(started.get(response.request())||Date.now());
    if(elapsed>500)console.log('HTTP_READY',new URL(response.url()).pathname,response.status(),elapsed+'ms');
   });
  });
  const page=await context.newPage();page.on('dialog',d=>d.dismiss());
  await page.goto('/base');await page.getByLabel('Identifiant',{exact:true}).fill(login);await page.getByLabel('Mot de passe',{exact:true}).fill(f.password);
  await page.getByRole('button',{name:'Se connecter',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Choisir une base métier'})).toBeVisible({timeout:30000});return {context,page};
 }
 async function ready(page){
  await expect(page.locator('#open-business-session')).not.toHaveAttribute('aria-disabled','true',{timeout:30000});
  await page.waitForFunction(()=>window.BaseDetailController&&window.ResourceEventTransport?.getCursor()!==null);
  await page.evaluate(()=>ResourceEventTransport.synchronize());
 }
 async function select(page,resource,id=f.ids[resource],source='views'){
  await page.evaluate(({resource,id,source})=>document.querySelector('main').dispatchEvent(new CustomEvent('digues-app:open-entity',
   {detail:{entityClass:resource,entityId:id,source,entities:[]}})),{resource,id,source});
  await expect(page.locator('#data-entity-detail')).toHaveAttribute('aria-busy','false');
  await expect(page.locator('#data-entity-detail-read')).toBeVisible();
  await expect(page.locator('#data-entity-detail-read .base-detail__properties')).not.toHaveCount(0);
 }
 const form=page=>page.locator('#base-detail-editor form:visible').first();
 async function edit(page){await page.locator('#data-entity-detail-edit').click();await expect(form(page)).toBeVisible();}
 async function save(page){await page.locator('#base-detail-editor .entity-form__actions > [data-role=save]').click();await expect(page.locator('#data-entity-detail')).toHaveAttribute('data-mode','read');await expect(page.locator('#data-entity-detail')).toHaveAttribute('aria-busy','false');}
 try{
  const {context,page}=await makeContext('editor_alpha');
  await page.goto(`/base?base=${f.base}`);await ready(page);
  // Real Vues cell, no injected opening for this entry/return assertion.
  await page.locator('#toggle-views').click();const cell=page.locator('.data-entity-link').first();await expect(cell).toBeVisible();
  await cell.focus();await cell.click();await expect(page.locator('#data-entity-detail-read .base-detail__properties')).not.toHaveCount(0);
  await page.locator('#base-detail-close').click();await expect(page.locator('main')).toHaveAttribute('data-central-view','views');await expect(cell).toBeFocused();
  console.log('PASS Vues, origine et focus');
  for(const resource of Object.keys(f.ids)){
   await select(page,resource);
   const header=page.locator('#data-entity-detail > .base-detail__header');await expect(header.locator('h1')).toHaveCount(1);
   await expect(header.locator('button')).toHaveText(['Retour','Voir sur Carte','Modifier','×']);
   await expect(page.locator('#data-entity-detail > .data-entity-detail-actions')).toHaveCount(0);
   await expect(page.locator('#data-entity-detail-read > form')).toHaveCount(0);
   if(resource==='desordres')await expect(page.getByRole('button',{name:'Fiche PDF',exact:true})).toBeVisible();
   if(resource==='troncons'){
    await expect(page.locator('[data-troncon-section=historical]')).toContainText('12,75');
    await expect(page.locator('[data-troncon-section=historical] [data-field=num_couche]')).toHaveText('0');
    await expect(page.locator('[data-section=geometry-details]')).not.toHaveAttribute('open','');
    await expect(page.locator('[data-role=troncon-reperage-summary]')).toContainText('Repérage automatique');
    await expect(page.getByRole('button',{name:'Gérer le repérage',exact:true})).toBeVisible();
    await expect(page.locator('#troncon-reperage-dialog')).toHaveCount(0);
   }
   if(resource==='troncons_lit')await expect(page.locator('.lit-characteristics')).toBeVisible();
   matrix.push({resource,sections:await page.locator('#data-entity-detail-read [data-section]').evaluateAll(nodes=>nodes.map(n=>n.dataset.section))});
  }
  console.log('PASS 16 familles et extensions métier');
  // A→B→C→B→A, using the actual relation buttons.
  await select(page,'systemes');
  await page.locator(`[data-relation-resource=digues][data-relation-id="${f.ids.digues}"]`).click();
  await page.locator(`[data-relation-resource=troncons][data-relation-id="${f.ids.troncons}"]`).click();
  await expect(page.locator('main')).toHaveAttribute('data-selected-entity-class','troncons');
  await page.locator('#data-entity-detail-back').click();await expect(page.locator('main')).toHaveAttribute('data-selected-entity-class','digues');
  await page.locator('#data-entity-detail-back').click();await expect(page.locator('main')).toHaveAttribute('data-selected-entity-class','systemes');
  await page.locator(`[data-relation-resource=digues][data-relation-id="${f.ids.digues}"]`).click();await page.locator('#base-detail-close').click();
  await expect(page.locator('main')).toHaveAttribute('data-central-view','views');
  console.log('PASS pile Retour et Fermer direct');
  // Patrimoine opens read mode; editing requires a separate click.
  await page.locator('#toggle-heritage').click();await page.locator('#heritage-panel .tree-name').first().click();
  await expect(page.locator('#data-entity-detail')).toHaveAttribute('data-mode','read');
  await expect(page.locator('#data-entity-detail-edit')).toBeVisible();
  console.log('PASS entrée Patrimoine en consultation');
  for(const width of [840,1024,1440,1920])for(const withAi of [false,true]){
   await page.setViewportSize({width,height:900});
   const aiOpen=await page.locator('#toggle-ai').getAttribute('aria-expanded')==='true';
   if(aiOpen!==withAi)await page.locator('#toggle-ai').click();
   for(const resource of ['systemes','digues','troncons','desordres']){
    await select(page,resource);
    const measurements=await page.locator('#data-entity-detail').evaluate(panel=>{
     const grid=panel.querySelector('.base-detail__grid'),read=panel.querySelector('.base-detail__scroll');
     const tab=document.querySelector('#toggle-heritage').getBoundingClientRect();const title=panel.querySelector('h1').getBoundingClientRect();
     const style=getComputedStyle(panel);
     return {width:panel.clientWidth,availableWidth:panel.clientWidth-parseFloat(style.paddingLeft)-parseFloat(style.paddingRight),columns:getComputedStyle(grid).gridTemplateColumns.split(' ').length,
      overflow:read.scrollWidth>read.clientWidth+1,heritageOverlap:tab.left<title.right&&tab.right>title.left&&tab.top<title.bottom&&tab.bottom>title.top,
      scrollOwners:[...panel.querySelectorAll('*')].filter(n=>getComputedStyle(n).overflowY==='auto'&&n.scrollHeight>n.clientHeight+1&&n.getClientRects().length).length};
    });
    expect(measurements.overflow,`${resource} ${width}/${withAi}: débordement`).toBe(false);
    expect(measurements.heritageOverlap).toBe(false);expect(measurements.scrollOwners).toBeLessThanOrEqual(1);
    expect(measurements.columns).toBe(measurements.availableWidth>=1024?2:1);
    matrix.push({resource,width,withAi,...measurements});
   }
   if(width===1440)await page.screenshot({path:`${f.artifacts}/desktop-${width}-ai-${withAi}.png`});
  }
  fs.writeFileSync(`${f.artifacts}/matrix.json`,JSON.stringify(matrix,null,2));
  console.log('PASS 32 dispositions réelles, largeur disponible et scroll');
  await page.setViewportSize({width:1440,height:900});if(await page.locator('#toggle-ai').getAttribute('aria-expanded')==='true')await page.locator('#toggle-ai').click();
  await select(page,'systemes');await edit(page);await form(page).getByLabel('Commentaire',{exact:true}).fill('Brouillon Base protégé');
  await page.locator('#base-detail-editor > .entity-form > .entity-form__header [data-role=close]').click();await expect(form(page).getByLabel('Commentaire',{exact:true})).toHaveValue('Brouillon Base protégé');
  // Mutation from a second page reaches the first through the real journal.
  const second=await context.newPage();await second.goto(`/base?base=${f.base}&entity=systemes&id=${f.ids.systemes}`);await ready(second);
  await expect(second.locator('#data-entity-detail-edit')).toBeEnabled();await edit(second);
  await form(second).getByLabel('Commentaire',{exact:true}).fill('Autre onglet Base');await save(second);
  await page.locator('#base-detail-editor .entity-form__actions > [data-role=save]').click();
  await expect(form(page)).toBeVisible();await expect(form(page).getByLabel('Commentaire',{exact:true})).toHaveValue('Brouillon Base protégé');
  await expect(page.locator('#base-detail-editor')).toContainText(/conflit|modifié ailleurs|Version/i);
  page.removeAllListeners('dialog');page.on('dialog',d=>d.accept());await page.locator('#base-detail-editor > .entity-form > .entity-form__header [data-role=close]').click();await second.close();
  console.log('PASS brouillon, conflit réel et invalidation inter-onglets');
  const reader=await makeContext('reader_alpha');await reader.page.goto(`/base?base=${f.base}&entity=systemes&id=${f.ids.systemes}`);await ready(reader.page);
  await expect(reader.page.locator('#data-entity-detail-edit')).toBeHidden();
  const beta=await makeContext('admin_beta');await beta.page.goto(`/base?base=${f.beta}&entity=systemes&id=${f.ids.systemes}`);await ready(beta.page);
  await expect(beta.page.locator('#data-entity-detail-title')).toContainText('Système Beta exclusif');
  await expect(reader.page.locator('#data-entity-detail-title')).not.toContainText('Beta exclusif');
  const forbidden=await reader.page.evaluate(async({beta,id})=>{
   const u=new URL(`/api/bases/${beta}/systemes-endiguement/${id}`,location.origin);
   const c=BusinessRuntime.context;u.searchParams.set('context_user_id',c.user_id);u.searchParams.set('context_instance_id',c.instance_id);
   u.searchParams.set('context_data_epoch',c.data_epoch);return (await fetch(u)).status;
  },{beta:f.beta,id:f.ids.systemes});expect(forbidden).toBe(403);
  console.log('PASS lecteur/éditeur et même UUID Alpha/Beta, refus serveur réel');
  // Supported map destination and existing independent editor.
  await select(page,'digues');await page.locator('#data-entity-detail-map').click();await expect(page).toHaveURL(/\/carte\?/);
  await expect(page.locator('#editor-panel')).toBeVisible();await expect(page.locator('#data-digues-editor-form')).toBeVisible();
  await expect(page.locator('#editor-panel .base-detail__header')).toHaveCount(0);
  console.log('PASS lien Carte et éditeur patrimonial inchangé');
  expect(errors).toEqual([]);
 }catch(error){
  for(const [index,context]of contexts.entries())for(const [pageIndex,page]of context.pages().entries())
   await page.screenshot({path:`${f.artifacts}/failure-${index}-${pageIndex}.png`}).catch(()=>{});
  fs.writeFileSync(`${f.artifacts}/matrix.json`,JSON.stringify(matrix,null,2));
  throw error;
 }finally{for(const context of contexts)await context.close();await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1});
