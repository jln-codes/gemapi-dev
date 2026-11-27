/* Level A DOM fixture (~1 s): real shared form mounting, no network or SQL.
 * It complements live browser tests; it does not simulate session security.
 */
const {chromium}=require('@playwright/test');
const fs=require('node:fs');
(async()=>{
 const browser=await chromium.launch({headless:true});
 try {
  const page=await browser.newPage();
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.setContent(fs.readFileSync('webapp/frontend/map.html','utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,''));
  await page.evaluate(fields=>{window.DataEntityDetail={ENTITY_CONFIGS:{desordres:{fields}}};},
    require('../frontend/js/data-entity-detail.js').ENTITY_CONFIGS.desordres.fields);
  for(const name of ['entity-form-view','observation-form','map-form-hosts'])
   await page.addScriptTag({path:'webapp/frontend/js/'+name+'.js'});
  const ids=await page.locator('[id]').evaluateAll(nodes=>nodes.map(n=>n.getAttribute('id')));
  if(errors.length)throw new Error(errors.join('\n'));
  if(new Set(ids).size!==ids.length)throw new Error('Mounted form identities must be unique: '+ids.filter((id,index)=>ids.indexOf(id)!==index).join(','));
  if(process.argv[2]==='modes') {
   const source=fs.readFileSync('webapp/frontend/js/map.js','utf8');
   await page.addScriptTag({content:source.slice(source.indexOf('function creationBornageChoiceState'),source.indexOf('function canonicalDisorderBornageAvailable'))});
   const result=await page.evaluate(()=>{
    const view=MapFormViews.disorder.geometry, choice=view.modeChoices.get('bornage');
    const cardinality=[0,1,2,3,2,1].map(count=>{
     const available=availableDisorderModes('LineString',count,true).includes('bornage');
     view.setModeAvailability({bornage:creationBornageChoiceState('LineString',count,true)});
     return {count,available,hidden:choice.choice.hidden,disabled:choice.input.disabled};
    });
    const pending=[true,false,true,true].map(loaded=>{
     view.setModeAvailability({bornage:creationBornageChoiceState('LineString',1,loaded)});
     return {visible:!choice.choice.hidden,enabled:!choice.input.disabled};
    });
    return {cardinality,pending,zero:creationBornageChoiceState('LineString',0,false),many:creationBornageChoiceState('LineString',2,true),polygon:creationBornageChoiceState('Polygon',1,true)};
   });
   process.stdout.write(JSON.stringify(result));return;
  }
  process.stdout.write(await page.content());
 } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1});
