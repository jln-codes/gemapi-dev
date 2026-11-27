/* ENTITY-TYPE-02: real Carte/Base form, HTTP refusals and concurrent versions. */
module.exports=async function({page,context,api,open,ready,f,expect}){
 const rootForm=p=>p.locator('#data-troncons-editor-form');
 async function edit(p){
   // Runtime/event readiness precedes asynchronous Carte form assembly.
   // Wait for either actionable consultation or the already-open editor.
   await expect.poll(async()=>await rootForm(p).isVisible()||await p.locator('#data-entity-detail-edit').isVisible()).toBe(true);
   if(!await rootForm(p).isVisible())await p.locator('#data-entity-detail-edit').click();
   await expect(rootForm(p)).toBeVisible();
   await expect(rootForm(p).locator('.entity-form__fields')).toBeEnabled();
 }
 async function save(p){
   await p.locator('.entity-form__actions > [form="data-troncons-editor-form"]').click();
   await expect(rootForm(p)).toBeHidden();
 }
 const key=f.keys.BERGE;
 await open(page,key);await edit(page);
 const draft=rootForm(page),type=draft.getByLabel('Sous-type',{exact:true});
 await expect(draft.getByLabel('Hauteur de murette',{exact:true})).toHaveValue('12.75');
 await type.selectOption('TALUS');
 await expect(draft.getByLabel('Fonction — haut',{exact:true})).toBeVisible();
 await type.selectOption('INDETERMINE');await type.selectOption('BERGE');
 await expect(draft.getByLabel('Hauteur de murette',{exact:true})).toHaveValue('12.75');
 await type.selectOption('CRETE');
 await draft.getByLabel('Commentaire',{exact:true}).fill('Brouillon après refus ENTITY-TYPE-02');
 const rejected=page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/troncons/'+key)&&r.request().method()==='PUT');
 await page.locator('.entity-form__actions > [form="data-troncons-editor-form"]').click();
 expect((await rejected).status()).toBe(422);
 await expect(page.locator('#data-troncons-editor')).toContainText('Hauteur de murette');
 await expect(type).toHaveValue('CRETE');
 await expect(draft.getByLabel('Commentaire',{exact:true})).toHaveValue('Brouillon après refus ENTITY-TYPE-02');
 await expect(draft.getByLabel('Hauteur de murette',{exact:true})).toHaveValue('12.75');
 await type.selectOption('INDETERMINE');await save(page);
 expect((await api(page,`/api/troncons/${key}`)).value.properties.type_physique_id).toBe('INDETERMINE');
 console.log('PASS ENTITY-TYPE-02 Base requalification, historical fields, subtype detour and refusal draft');

 await page.locator('#data-entity-detail-map').click();await expect(page).toHaveURL(/\/carte\?/);await ready(page);await edit(page);
 await rootForm(page).getByLabel('Sous-type',{exact:true}).selectOption('TALUS');await save(page);
 expect((await api(page,`/api/troncons/${key}`)).value.properties.type_physique_id).toBe('TALUS');
 console.log('PASS ENTITY-TYPE-02 Carte uses the same subtype editor');

 // Actual second browser page and version advance; no fabricated 409 response.
 const second=await context.newPage();second.on('dialog',dialog=>dialog.accept());
 await open(second,key);await edit(page);
 await rootForm(page).getByLabel('Sous-type',{exact:true}).selectOption('INDETERMINE');
 await rootForm(page).getByLabel('Désignation',{exact:true}).fill('Brouillon conflit ENTITY-TYPE-02');
 const current=(await api(second,`/api/troncons/${key}`)).value;
 const changed=await api(second,`/api/troncons/${key}`,{method:'PUT',headers:{'If-Match':`"${current.properties.version}"`},body:{type_physique_id:'TALUS_RISBERME'}});
 expect(changed.status).toBe(200);
 const conflicted=page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/troncons/'+key)&&r.request().method()==='PUT');
 await page.locator('.entity-form__actions > [form="data-troncons-editor-form"]').click();
 expect((await conflicted).status()).toBe(409);
 await expect(rootForm(page).getByLabel('Sous-type',{exact:true})).toHaveValue('INDETERMINE');
 await expect(rootForm(page).getByLabel('Désignation',{exact:true})).toHaveValue('Brouillon conflit ENTITY-TYPE-02');
 await expect(page.locator('#data-troncons-editor')).toContainText(/brouillon|changé|conflit/i);
 expect((await api(second,`/api/troncons/${key}`)).value.properties.type_physique_id).toBe('TALUS_RISBERME');
 await second.close();
 console.log('PASS ENTITY-TYPE-02 real version conflict retains selected subtype and draft');
};
