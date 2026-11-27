// GPS-PWA-UX-02, niveau B : GNSS synthétique, Leaflet réel, API/PostGIS jetables.
// Un objet unique par scénario, aucune base d'opérateur. ~5 min pour huit objets.
const {test,expect}=require('@playwright/test');
const {randomUUID}=require('node:crypto');
test.use({serviceWorkers:'block'});
test.skip(process.env.DIGUES_E2E_USE_TEST_DATABASE!=='true','Runner PostgreSQL jetable requis.');
const cases=[
 ['troncon','troncons','LineString','troncon-create-geometry'],
 ['desordre','desordres','Polygon','desordre-create-geometry'],
 ['reseau-technique','reseaux-techniques','LineString','reseau-technique-gps'],
 ['cheminement','cheminements','LineString','cheminement-gps'],
 ['zone-stockage','zones-stockage','Polygon','zone-stockage-gps'],
 ['prestation','prestations','Polygon','prestation-gps'],
 ['vegetation','vegetation','Polygon','vegetation-gps'],
 ['ouvrage-particulier','ouvrages-particuliers','LineString','ouvrage-particulier-gps'],
];
async function ready(page){
 await page.goto('/carte');await page.waitForFunction(()=>typeof mapViewportReady!=='undefined'&&mapViewportReady);
 if(await page.locator('#heritage-panel').isVisible())await page.locator('#close-heritage').click();
}
async function collect(page,tracker,type,index,offset=0){
 await tracker.locator('[data-gps=start]').click();
 const points=[[2.35+offset,50.5],[2.3503+offset,50.5],[2.3503+offset,50.5003]];
 await expect.poll(()=>page.evaluate(()=>__gpsWatches.length)).toBe(index+1);
 for(let i=0;i<(type==='Polygon'?3:2);i++) await page.evaluate(({p,index,i})=>{
  __gpsWatches[index]({coords:{longitude:p[0],latitude:p[1],accuracy:7},timestamp:Date.now()-20000+i*5000});
 },{p:points[i],index,i});
 await expect(page.locator('#editor-map-interaction')).toBeHidden();
 await tracker.locator('[data-gps=finish]').click();
 await expect(tracker.locator('button:visible')).toHaveText(['Valider','Annuler']);
 await expect(page.locator('.leaflet-vertex-icon')).not.toHaveCount(0);
 return {type,coordinates:type==='Polygon'?[[...points,points[0]]]:points.slice(0,2)};
}
async function save(page,view,family,resource,method,id=''){
 const response=page.waitForResponse(r=>r.request().method()===method&&new URL(r.url()).pathname===`/api/${resource}${id?'/'+id:''}`);
 if(family==='desordre')await page.locator('#submit-desordre-create').click();
 else await view.locator(':scope > .entity-form__body > form [data-role=save]').click();
 const result=await response;expect(result.status(),await result.text()).toBe(method==='POST'?201:200);return result.json();
}
function expectGeometry(actual, expected) {
 expect(actual.type).toBe(expected.type);
 const a=actual.type==='Polygon'?actual.coordinates[0]:actual.coordinates;
 const b=expected.type==='Polygon'?expected.coordinates[0]:expected.coordinates;
 expect(a).toHaveLength(b.length);
 // The server round-trips through the projected business CRS.
 a.forEach((point,i)=>point.forEach((coordinate,axis)=>expect(coordinate).toBeCloseTo(b[i][axis],9)));
}
for(const [family,resource,type,owner] of cases)test(`${family} : GPS commun en création/modification, annulation du deuxième brouillon`,async({context,page,request})=>{
 await context.addInitScript(()=>{const real=matchMedia;window.matchMedia=q=>q==='(display-mode: standalone)'?{matches:true,addEventListener(){},removeEventListener(){}}:real(q);
  window.__gpsWatches=[];Object.defineProperty(navigator,'geolocation',{value:{getCurrentPosition(){},watchPosition(resolve){__gpsWatches.push(resolve);return __gpsWatches.length},clearWatch(){}}});});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await ready(page);await page.locator('#toggle-create-menu').click();await page.locator(`[data-create-type="${family}"]`).click();
 let view=page.locator('#editor-panel .entity-form:visible').first();await expect(view).toHaveAttribute('aria-busy','false');
 const label='GPS '+randomUUID();
 if(family==='desordre'){
  await page.locator('#desordre-create-designation').fill(label);await page.locator('#desordre-create-geometry-type').selectOption(type);
 }else{
  await view.locator('[name=designation]').fill(label);
  if(family==='troncon')await view.locator('[name=libelle]').fill(label);
  for(const select of await view.locator('select[required]:visible').all())if(!await select.inputValue())await select.selectOption({index:1});
  if(family==='prestation')await view.locator('[name=date_fin]').fill('2026-11-10');
  const selector=view.locator('[name=geometry_type]');if(await selector.isVisible())await selector.selectOption(type);
 }
 const writes=[];page.on('request',r=>{if(['POST','PUT','PATCH','DELETE'].includes(r.method())&&new URL(r.url()).pathname.startsWith(`/api/${resource}`))writes.push(r)});
 let tracker=page.locator(`[data-gps-owner="${owner}"]`);
 const geometry=await collect(page,tracker,type,0);
 await expect(view.locator('form').first()).toHaveJSProperty('inert',true);
 // An already queued/programmatic submit must respect the GPS lock too.
 await view.locator('form').first().evaluate(form=>form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
 await tracker.locator('[data-gps=confirm]').click();await expect(tracker.locator('[data-gps=start]')).toBeEnabled();
 await expect(view.locator('form').first()).toHaveJSProperty('inert',false);
 await collect(page,tracker,type,1,.001);await tracker.locator('[data-gps=cancel]').click();
 expect(writes).toHaveLength(0);
 const created=await save(page,view,family,resource,'POST');expectGeometry(created.geometry,geometry);
 const id=created.properties.id;expect(writes).toHaveLength(1);
 await ready(page);
 if(family==='troncon')await page.evaluate(id=>openHeritageEdit('troncons',id),id);
 else {
  // The self-mounted editors deliberately keep their controller private.
  // Open their real Leaflet feature, as the map user does.
  await expect.poll(()=>page.evaluate(id=>{
   let found=false;map.eachLayer(layer=>{
    if(layer.feature?.properties?.id===id&&layer.getElement?.()){
     layer.getElement().dataset.gpsConsumer=id;found=true;
    }
   });return found;
  },id)).toBe(true);
  await page.locator(`[data-gps-consumer="${id}"]`).dispatchEvent('click');
 }
 view=page.locator('#editor-panel .entity-form:visible').first();await expect(view).toHaveAttribute('aria-busy','false');
 tracker=page.locator(`[data-gps-owner="${family==='troncon'?'troncon-edit-geometry':owner}"]`);
 const edited=await collect(page,tracker,type,0,.002);
 await view.locator('form').first().evaluate(form=>form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
 await tracker.locator('[data-gps=confirm]').click();await expect(tracker.locator('[data-gps=start]')).toBeEnabled();
 await collect(page,tracker,type,1,.003);await tracker.locator('[data-gps=cancel]').click();
 expect(writes).toHaveLength(1);
 const updated=await save(page,view,family,resource,'PUT',id);expectGeometry(updated.geometry,edited);
 expect(writes.at(-1).headers()['if-match']).toBe(`"${created.properties.version}"`);
 const actual=await(await request.get(`/api/${resource}/${id}`)).json();expect(actual.geometry).toEqual(updated.geometry);
 expect(errors).toEqual([]);
});
