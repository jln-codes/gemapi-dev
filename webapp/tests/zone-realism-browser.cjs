/* B: real Leaflet/HTTP, reader and disposable seed. No mocked security boundary. */
const {chromium,expect:baseExpect}=require('@playwright/test'),fs=require('node:fs');
const expect=baseExpect.configure({timeout:20000});
const [origin,path]=process.argv.slice(2);
if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(origin)||!/^audits\/demo-ah-realism-01\/browser\/[a-f0-9]+\/fixture.json$/.test(path))throw new Error('Owned local harness required');
const f=JSON.parse(fs.readFileSync(path,'utf8'));
(async()=>{const browser=await chromium.launch({headless:true});let page;const errors=[],writes=[];
try{
 const ctx=await browser.newContext({viewport:{width:1440,height:900},serviceWorkers:'block'});page=await ctx.newPage();
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(['PUT','PATCH','DELETE'].includes(r.method()))writes.push(new URL(r.url()).pathname);});
 await page.goto(origin+'/carte',{waitUntil:'domcontentloaded'});
 await page.getByLabel('Identifiant',{exact:true}).fill('reader_alpha');await page.getByLabel('Mot de passe',{exact:true}).fill(f.password);
 await page.getByRole('button',{name:'Se connecter',exact:true}).click();await expect(page.getByRole('heading',{name:'Choisir une base métier'})).toBeVisible();
 await page.goto(origin+`/carte?base=${f.base}&entity=zones_stockage&id=${f.zone}`,{waitUntil:'domcontentloaded'});
 await page.waitForFunction(()=>typeof mapEntityNavigationComplete!=='undefined'&&mapEntityNavigationComplete,null,{timeout:60000});
 const state=()=>page.evaluate(ids=>{const result={};map.eachLayer(layer=>{const id=layer.feature?.properties?.id,e=layer.getElement?.();if(ids.includes(id)&&e)result[id]={stroke:e.getAttribute('stroke'),fill:e.getAttribute('fill'),fillOpacity:e.getAttribute('fill-opacity'),width:e.getAttribute('stroke-width'),weight:layer.options.weight};});return result;},[f.zone,f.trunk,f.line,f.point]);
 await expect.poll(async()=>Object.keys(await state()).length).toBe(4);
 const styles=await state();expect(styles[f.zone]).toMatchObject({stroke:'none',fill:'#1769aa',fillOpacity:'0.4',weight:4});
 expect(styles[f.trunk]).toMatchObject({stroke:'#5b6470',width:'4',weight:4});
 expect(styles[f.line]).toMatchObject({stroke:'#1769aa',width:'4',weight:4});
 expect(styles[f.point]).toMatchObject({stroke:'#1769aa',fillOpacity:'0.9',weight:4});
 const panel=page.locator('#zone-stockage-editor-panel');await expect(panel).toBeVisible();
 await expect(panel).toHaveAttribute('data-state','readonly');
 await panel.locator('[data-role=close]').click();await expect(page.locator('#editor-panel')).toBeHidden();
 await page.evaluate(id=>{let layer;map.eachLayer(l=>{if(l.feature?.properties?.id===id&&l.getBounds)layer=l;});map.invalidateSize();map.fitBounds(layer.getBounds(),{padding:[100,100],animate:false});},f.zone);
 // Use an unobstructed interior point, away from the local canal at the centre.
 const interior=()=>page.evaluate(id=>{let layer;map.eachLayer(l=>{if(l.feature?.properties?.id===id&&l.getBounds)layer=l;});const b=layer.getBounds(),lat=b.getSouth()+.7*(b.getNorth()-b.getSouth()),lng=b.getWest()+.7*(b.getEast()-b.getWest()),p=map.latLngToContainerPoint([lat,lng]),r=map.getContainer().getBoundingClientRect(),x=p.x+r.left,y=p.y+r.top;return{x,y,zoneIsTarget:document.elementFromPoint(x,y)===layer.getElement()};},f.zone);
 await expect.poll(async()=>(await interior()).zoneIsTarget).toBe(true);
 const point=await interior();await page.mouse.click(point.x,point.y);await expect(panel).toBeVisible();await expect(panel).toHaveAttribute('data-state','readonly');
 await expect(panel.locator('[name="designation"]')).toHaveValue('Zone de stockage des Prés');await page.screenshot({path:f.artifacts+'/zone-click-readonly.png'});
 expect(writes).toEqual([]);expect(errors).toEqual([]);
 fs.writeFileSync(f.artifacts+'/styles.json',JSON.stringify(styles,null,2));console.log('PASS Zone fill/stroke, real filled-area click, read-only session, unchanged AH/legacy line/point styles');
}catch(error){console.log(String(error));if(page)try{await page.screenshot({path:f.artifacts+'/failure.png'});fs.writeFileSync(f.artifacts+'/failure.txt',await page.locator('body').innerText());}catch(_){}process.exitCode=1;}
finally{await browser.close();}})();
