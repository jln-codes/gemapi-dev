/* C6 extension of the real C5 browser/database harness, no business transport mocks. */
const fs=require('node:fs');
module.exports=async({page,context,api,open,ready,f,expect,origin})=>{
 await open(page,f.keys.BERGE,'carte');
 const selected=[...Object.values(f.keys),...f.banks,f.shared,f.polygon,f.curve,f.empty];
 const roots=(await api(page,'/api/troncons')).value.features;
 const hidden=roots.map(row=>row.properties.id).filter(id=>!selected.includes(id));
 const definition={root:'troncons',fields:['libelle','type_physique_id','digue_id','amenagement_hydraulique_id'].map(field=>({table:'troncons',field})),hidden_troncon_ids:hidden};
 async function downloadApi(path,body,name){
  const download=page.waitForEvent('download');
  const status=await page.evaluate(async({path,body,name})=>{
   const response=await BusinessRuntime.fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Digues-CSRF':'1'},body:JSON.stringify(body)});
   if(!response.ok)throw new Error('Export '+response.status+': '+await response.text());
   const url=URL.createObjectURL(await response.blob());const a=document.createElement('a');a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),0);
   return response.status;
  },{path,body,name});
  expect(status).toBe(200);const file=await download;expect(await file.failure()).toBeNull();
  const target=f.artifacts+'/'+name;await file.saveAs(target);return fs.readFileSync(target);
 }
 const csv=await downloadApi('/api/data/export-csv',definition,'troncons.csv');
 for(const type of Object.keys(f.keys))expect(csv.toString('utf8')).toContain(type);
 const gpkg=await downloadApi('/api/data/export-gpkg',{...definition,entity_classes:['troncons']},'troncons.gpkg');
 expect(gpkg.subarray(0,16).toString()).toBe('SQLite format 3\0');
 console.log('PASS actual CSV/GeoPackage routes and canonical seven-type exports');
 const inventory=await page.evaluate(()=>currentMapPrintState().tronconsNonRepresentables);
 expect(inventory.filter(row=>row.id===f.curve)).toHaveLength(1);
 expect(inventory.filter(row=>row.id===f.empty)).toHaveLength(1);
 expect(inventory.some(row=>row.id===f.missing)).toBe(false);
 const empty=(await api(page,'/api/troncons/'+f.empty)).value;
 expect(empty.geometry).toBeNull();expect(empty.properties.geometry_vide).toBe(true);
 // A private deterministic background image exercises the real print engine,
 // downloads and browser image decode without external provider availability.
 const tile=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jL1sAAAAASUVORK5CYII=','base64');
 await page.route(url=>url.pathname.startsWith('/__c6_print_tile/'),route=>route.fulfill({status:200,contentType:'image/png',body:tile}));
 for(const output of ['png','pdf']){
  const download=page.waitForEvent('download');
  await page.evaluate(async({origin,output})=>MapPrint.exportMap({L,sourceBounds:map.getBounds(),state:currentMapPrintState(),
   options:{format:'A4',output,orientation:'landscape',legend:true,scale:true,north:true},
   tileUrl:origin+'/__c6_print_tile/{z}/{x}/{y}.png',tileOptions:{minZoom:0,maxZoom:19},attribution:'Fond synthétique de test C6',tileFormat:'png'}),{origin,output});
  const file=await download;expect(await file.failure()).toBeNull();await file.saveAs(f.artifacts+'/carte.'+output);
  const bytes=fs.readFileSync(f.artifacts+'/carte.'+output);
  if(output==='pdf')expect(bytes.subarray(0,4).toString()).toBe('%PDF');else expect(bytes.subarray(1,4).toString()).toBe('PNG');
 }
 await page.unroute(url=>url.pathname.startsWith('/__c6_print_tile/'));
 console.log('PASS real PNG/PDF print, unique physical-geometry diagnostic, no fictitious rendering');
 await page.evaluate(async ids=>OfflineRuntime.prepare(ids),[f.keys.BERGE,f.shared,f.keys.CRETE]);
 const stored=await page.evaluate(async()=>{const view=await OfflineRuntime.inspect();return {scope:view.scope,rows:view.records};});
 expect(stored.scope.tronconIds).toEqual(expect.arrayContaining([f.keys.BERGE,f.shared,f.keys.CRETE]));
 expect(stored.rows.filter(row=>row.resource==='troncons').map(row=>row.id)).toEqual(expect.arrayContaining([f.keys.BERGE,f.keys.CRETE]));
 // Real read fallback over prepared IndexedDB, without adding an offline CRUD.
 await context.setOffline(true);
 const fallback=await page.evaluate(async()=>{
  await OfflineRuntime.networkError(new TypeError('Browser offline'));
  const hierarchy=await (await OfflineRuntime.fetch('/api/systemes-endiguement')).json();
  const view=await (await OfflineRuntime.fetch('/api/data/desordres/query',{method:'POST',body:'{}'})).json();
  return {hierarchy,view};
 });
 expect(fallback.hierarchy.troncons_hors_se.map(row=>row.id)).toEqual(expect.arrayContaining([f.keys.BERGE,f.keys.CRETE]));
 expect(fallback.view.rows).toBeInstanceOf(Array);
 await context.setOffline(false);
 console.log('PASS real IndexedDB preparation and offline parentless/SE paths without queue remapping');
};
