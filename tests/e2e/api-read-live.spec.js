// B: actual login/HTTP/SQL; fixture owned by test_api_read_postgres, no route mocks.
const {test,expect}=require('@playwright/test');
const fs=require('node:fs');
test.skip(!process.env.DIGUES_API_READ_FIXTURE,'Cluster API-READ-01 jetable requis');
test.use({serviceWorkers:'block'});
test.setTimeout(90000);
const fixture=process.env.DIGUES_API_READ_FIXTURE
  ? JSON.parse(fs.readFileSync(process.env.DIGUES_API_READ_FIXTURE,'utf8')):null;

test('fiches réelles depuis les listes Base : système facultatif et géométrie absente',async({page})=>{
  const errors=[],mutations=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{
    const path=new URL(r.url()).pathname;
    // The view builder sends its read-only query payload by POST. The SQL
    // fixture also verifies unchanged rows, versions, eligibility and journal.
    const viewQuery=r.method()==='POST'&&path===`/api/bases/${fixture.base}/data/view-query`;
    if(path.startsWith('/api/bases/')&&r.method()!=='GET'&&!viewQuery)mutations.push(r.method()+' '+path);
  });
  await page.goto('/base');
  await page.getByLabel('Identifiant',{exact:true}).fill('reader_alpha');
  await page.getByLabel('Mot de passe',{exact:true}).fill(fixture.password);
  await page.getByRole('button',{name:'Se connecter',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Choisir une base métier'})).toBeVisible();
  for(const width of [1280,720])for(const resource of ['troncons','desordres']){
    await page.setViewportSize({width,height:1000});
    await page.goto(`/base?base=${fixture.base}`);
    await expect(page.locator('#identity-gate')).toBeHidden();
    const table=page.locator('#data-view-table');
    if(!await table.isVisible())await page.locator('#toggle-views').click();
    await expect(page.locator('#data-view-field-list button').first()).toBeVisible();
    while(await page.locator('#data-view-field-list button').count())await page.locator('#data-view-field-list button').first().click();
    await table.selectOption(resource);
    await page.locator('#data-view-field').selectOption('designation');
    await page.locator('#data-view-add-field').click();
    await expect(page.locator('#data-view-result-body button').first()).toBeVisible();
    const ids=resource==='troncons'?[fixture.troncons[0]]:fixture.desordres;
    for(const id of ids){
      const item=page.locator(`#data-view-result-body button[data-entity-class="${resource}"][data-entity-id="${id}"]`);
      await expect(item).toBeVisible();
      const response=page.waitForResponse(r=>new URL(r.url()).pathname===`/api/bases/${fixture.base}/${resource}/${id}`);
      await item.click();
      const r=await response;expect(r.status()).toBe(200);
      const data=await r.json();
      const read=page.locator('#data-entity-detail-read');await expect(read).toBeVisible();
      if(resource==='desordres'){
        expect(data.geometry).toBeNull();expect(data.properties.valid).toBe(false);
        await expect(read).toContainText('Géométrie non renseignée');
        await expect(read).toContainText('Catégorie conservée');
        await expect(read).toContainText('Zone(s) de stockage');
        await expect(read).toContainText(data.properties.zones_stockage.length?'Zone conservée':'Aucune');
        await expect(page.getByRole('button',{name:'Voir sur Carte',exact:true})).toBeHidden();
        await expect(read.locator('input[name=valid]')).toHaveCount(0);
      }else{
        expect(data.properties.systeme_endiguement_id).toBeNull();expect(data.properties.valid).toBe(true);
        await expect(read).toContainText(data.properties.digue_libelle);
        await expect(read).not.toContainText('Système témoin');
      }
      await page.screenshot({path:test.info().outputPath(`${resource}-${id}-${width}.png`)});
      await page.locator('#data-entity-detail-back').click();
    }
  }
  expect(errors).toEqual([]);expect(mutations).toEqual([]);
});
