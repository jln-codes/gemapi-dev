/* ENTITY-TYPE-03 level B: owned fixture, real API and DOM on Carte and Base. */
const {chromium,expect}=require('@playwright/test');
const fs=require('node:fs');
const [origin,fixtureFile]=process.argv.slice(2);
if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(origin)||!/^audits\/entity-type-03\/browser\//.test(fixtureFile))throw Error('Owned harness required');
const f=JSON.parse(fs.readFileSync(fixtureFile,'utf8'));
(async()=>{
 const browser=await chromium.launch({headless:true});
 const context=await browser.newContext({baseURL:origin,serviceWorkers:'block'});
 const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 page.on('response',async response=>{if(response.status()>=400&&new URL(response.url()).pathname.includes('/vegetation'))console.log('VEGETATION_ERROR',response.status(),new URL(response.url()).pathname,await response.text());});page.on('dialog',d=>d.accept());
 const api=(resource,id,options={})=>page.evaluate(async({resource,id,options})=>{
  const response=await BusinessRuntime.fetch(`/api/${resource}/${id}`,{...options,headers:{'Content-Type':'application/json','X-Digues-CSRF':'1',...options.headers},...(options.body?{body:JSON.stringify(options.body)}:{})});
  return {status:response.status,value:await response.json()};
 },{resource,id,options});
 const submitFor=async form=>page.locator(`.entity-form__actions > [form="${await form.getAttribute('id')}"]`);
 async function open(entity,id,host='base'){
  await page.goto(`/${host}?base=${f.base}&entity=${entity}&id=${id}`,{waitUntil:'domcontentloaded'});
  await expect(page.locator('#open-business-session')).not.toHaveAttribute('aria-disabled','true',{timeout:30000});
  await page.waitForFunction(()=>window.ResourceEventTransport&&ResourceEventTransport.getCursor()!==null,null,{timeout:30000});
  const mapForms={vegetation:'vegetation',ouvrages_particuliers:'ouvrage-particulier',zones_stockage:'zone-stockage',cheminements:'cheminement'};
  const form=page.locator(host==='base'?`#data-${entity}-editor-form`:`#${mapForms[entity]}-editor-form`);
  if(host==='base'){await expect(page.locator('#data-entity-detail-read')).toBeVisible({timeout:15000});await page.locator('#data-entity-detail-edit').click();}
  await expect(form).toBeVisible({timeout:15000});
  // The Carte panel is mounted before its object/choices finish loading.
  // Enabled Save is the observable readiness gate, separate from assertions
  // about the historical business value itself.
  await expect(await submitFor(form)).toBeEnabled({timeout:30000});
  console.log('READY',host,entity);return form;
 }
 async function save(form,resource,id){
  const response=page.waitForResponse(r=>r.request().method()==='PUT'&&new URL(r.url()).pathname.endsWith(`/${resource}/${id}`));
  await (await submitFor(form)).click();
  const r=await response;expect(r.status(),await r.text()).toBe(200);return r.request().postDataJSON();
 }
 try{
  await page.goto('/base');await page.getByLabel('Identifiant',{exact:true}).fill('editor_alpha');
  await page.getByLabel('Mot de passe',{exact:true}).fill(f.password);await page.getByRole('button',{name:'Se connecter',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Choisir une base métier'})).toBeVisible({timeout:20000});
  for(const host of ['base','carte'])for(const [entity,resource,name,field] of [['ouvrages_particuliers','ouvrages-particuliers','ouvrage','type_id'],['zones_stockage','zones-stockage','zone','type_id'],['cheminements','cheminements','chemin','type_cheminement_id']]){
   const form=await open(entity,f.keys[name],host);await expect(form.locator(`[name=${field}]`)).toHaveValue('ET03_BROWSER');
   // Active-only catalogues preserve an option for the current historical ID.
   // The API suite separately proves a new inactive/unknown choice is refused.
   await expect(form.locator(`[name=${field}] option[value=ET03_BROWSER]`)).toHaveCount(1);
   await form.getByLabel('Commentaire',{exact:true}).fill('Retouche '+host+' ET03');const body=await save(form,resource,f.keys[name]);
   expect(body[field]).toBeUndefined();expect((await api(resource,f.keys[name])).value.properties[field]).toBe('ET03_BROWSER');
   console.log('PASS inactive retained',host,entity);
  }
  for(const host of ['base','carte']){
   let form=await open('vegetation',f.keys.historical,host);
   await expect(form.locator('[name=famille_vegetation]')).toHaveValue('');await expect(form.locator('[name=famille_vegetation]')).not.toHaveAttribute('required');
   await form.getByLabel('Commentaire',{exact:true}).fill('Retouche historique '+host);
   const body=await save(form,'vegetation',f.keys.historical);expect(body).toEqual({commentaire:'Retouche historique '+host});
   console.log('PASS incomplete historical edit without geometry',host);
   const photo=(await api('photos',f.keys.photo)).value;
   await page.evaluate(photo=>PhotoMedia.openEdit(photo,{id:photo.observation_id,designation:'Observation ET03'}),photo);
   const photoForm=page.locator('#photo-editor-form');await expect(photoForm).toBeVisible();
   await expect(photoForm.locator('[name=orientation_photo_id]')).toHaveValue('ET03_BROWSER');
   await expect(photoForm.locator('[name=cote_id]')).toHaveValue('ET03_BROWSER');
   await expect(await submitFor(photoForm)).toBeEnabled();
   await photoForm.locator('[name=libelle]').fill('Photo conservée '+host);
   const photoSaved=page.waitForResponse(r=>r.request().method()==='PUT'&&new URL(r.url()).pathname.endsWith('/photos/'+f.keys.photo));
   await (await submitFor(photoForm)).click();
   const photoResponse=await photoSaved;expect(photoResponse.status(),await photoResponse.text()).toBe(200);
   expect(photoResponse.request().postDataJSON()).toEqual({libelle:'Photo conservée '+host});
   await expect(page.locator('#photo-editor-dialog')).toContainText('Photo enregistrée.');await page.locator('#photo-editor-dialog [data-close]').click();
   console.log('PASS Photo inactive references retained',host);
   form=await open('vegetation',f.keys.tree,host);
   await form.locator('[name=famille_vegetation]').selectOption('HERBACEES');
   await expect(form.locator('[name=sous_type]')).toHaveValue('SOUCHE');await expect(form.locator('[name=sous_type]')).toBeVisible();
   await (await submitFor(form)).click();
   await expect(page.locator(host==='base'?'#data-entity-detail':'#vegetation-editor-panel')).toContainText(/retirez explicitement/);
   await form.locator('[name=famille_vegetation]').selectOption('ARBRES');await expect(form.locator('[name=sous_type]')).toHaveValue('SOUCHE');
   await form.getByLabel('Commentaire',{exact:true}).fill('Souche conservée '+host);await save(form,'vegetation',f.keys.tree);
   console.log('PASS subtype roundtrip and explicit incompatible save refusal',host);
   form=await open('vegetation',f.keys.tree,host);await form.getByLabel('Commentaire',{exact:true}).fill('Brouillon conflit '+host);
   const current=(await api('vegetation',f.keys.tree)).value;
   const r=await api('vegetation',f.keys.tree,{method:'PUT',headers:{'If-Match':`"${current.properties.version}"`,'Operation-Id':crypto.randomUUID()},body:{designation:'Autre auteur '+host}});expect(r.status).toBe(200);
   const refused=page.waitForResponse(r=>r.request().method()==='PUT'&&new URL(r.url()).pathname.endsWith('/vegetation/'+f.keys.tree));
   await (await submitFor(form)).click();expect((await refused).status()).toBe(409);
   await expect(form.getByLabel('Commentaire',{exact:true})).toHaveValue('Brouillon conflit '+host);await expect(form.locator('[name=sous_type]')).toHaveValue('SOUCHE');
   console.log('PASS real version conflict keeps draft',host);
   // The next host is a new scenario using the same SQL rows/session. Finish
   // this rejected draft through its owner before navigating; a new document
   // alone does not resolve a preserved conflict or drain its invalidations.
   const authoritative=(await api('vegetation',f.keys.tree)).value.properties;
   await form.locator('xpath=ancestor::*[contains(concat(" ",normalize-space(@class)," ")," entity-form ")][1]').locator('.entity-form__actions > [data-role=cancel]').click();
   await expect(form).toBeHidden();await page.evaluate(()=>ResourceEventTransport.synchronize());
   const afterCancel=(await api('vegetation',f.keys.tree)).value.properties;
   expect(afterCancel.version).toBe(authoritative.version);expect(afterCancel.designation).toBe(authoritative.designation);
   console.log('PASS explicit conflict abandonment before next host, no extra write',host);

  }
  expect(errors).toEqual([]);await page.screenshot({path:f.artifacts+'/final.png'});
 }catch(e){await page.screenshot({path:f.artifacts+'/failure.png'});fs.writeFileSync(f.artifacts+'/failure.html',await page.content());throw e;}
 finally{await context.close();await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
