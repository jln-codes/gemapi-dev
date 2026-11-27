// B (~6 min): macrostructure with real Chromium/DOM/Leaflet, isolated synthetic
// HTTP per scenario. No persistent resources or claim about server permissions.
const {test,expect}=require('@playwright/test');
const {fixture,ready,clickFeature,errorsByContext}=require('./fixtures/map-editor-fixture');
const {fakeId}=require('./fixtures/frontend-preview-data');
const fs=require('node:fs');
const families=[['systemes',3],['digues',2],['troncons',1],['desordres',10],['mobilier',40],['equipements_mesure',41],
 ['reseaux_techniques',42],['ouvrages_particuliers',43],['cheminements',44],['vegetation',45],['prestations',47],
 ['zones_stockage',48],['amenagements_hydrauliques',90],['troncons_lit',20],['elements_morphologiques_lit',30],['observations',99]];
const phase=process.env.DIGUES_FORM_SHELL_PHASE||'after';
const artifacts=`audits/digues-form-shell-02/${phase}`;
test.use({serviceWorkers:'block'});
for(const width of [1440,390])for(const surface of ['base','carte'])
for(const [batchIndex,batch] of (surface==='base'&&width===390?[[]]:[families.slice(0,8),families.slice(8)]).entries())
test(`enveloppe 16 familles ${surface} ${width} groupe ${batchIndex+1}`,async({context,page})=>{
 test.setTimeout(180000);await page.setViewportSize({width,height:900});fs.mkdirSync(artifacts,{recursive:true});
 await page.addInitScript(()=>{
  window.__shellElements=new WeakSet();let api;
  Object.defineProperty(window,'EntityFormView',{configurable:true,get:()=>api,set:value=>{
   api={...value,create(options){const view=value.create(options);window.__shellElements.add(view.element);return view;}};
  }});
 });
 await fixture(context,{ah:true});
 if(surface==='base'&&width===390){
  await page.goto('/base');await expect(page).toHaveURL(/\/carte(?:[?#]|$)/);
  await expect(page.locator('main')).toHaveJSProperty('inert',false);
  await expect(page.locator('#map')).toBeVisible();return;
 }
 const observation={id:fakeId(99),version:1,designation:'Observation de contrôle',date:'2026-11-21',photos:[],parent:{resource:'troncons',id:fakeId(1),designation:'Tronçon de contrôle'}};
 await context.route('**/api/observations/'+observation.id,route=>route.fulfill({json:observation}));
 if(surface==='carte')await ready(page);else{await page.goto('/base');await expect(page.locator('main')).toHaveJSProperty('inert',false);}
 const matrix=[];const save=()=>fs.writeFileSync(`${artifacts}/${surface}-${width}-${batchIndex+1}.json`,JSON.stringify(matrix,null,2));
 async function capture(family,mode,view){
  await expect(view).toBeVisible();
  const metrics=await view.evaluate(el=>{
   const rect=n=>{const r=n?.getBoundingClientRect();return r?{x:r.x,y:r.y,w:r.width,h:r.height,bottom:r.bottom}:null;};
   const visible=n=>n&&n.getClientRects().length>0&&getComputedStyle(n).display!=='none';
   const header=el.querySelector(':scope > .entity-form__header'),tabs=el.querySelector(':scope > .entity-form__tabs'),body=el.querySelector(':scope > .entity-form__body:not([hidden])');
   const host=el.closest('#editor-panel')||el.closest('.base-detail__editor-host')||el.parentElement;
   return{composed:window.__shellElements.has(el),children:[...el.children].map(n=>n.className),header:rect(header),tabs:rect(tabs),body:rect(body),host:rect(host),
    contentPaddingLeft:body?parseFloat(getComputedStyle(body).paddingLeft):null,headerVisible:visible(header),root:rect(el),paddingTop:parseFloat(getComputedStyle(el).paddingTop),padding:getComputedStyle(el).padding,headerPadding:header&&getComputedStyle(header).padding,
    tabsMargin:tabs&&getComputedStyle(tabs).marginTop,headerTabGap:header&&tabs?tabs.getBoundingClientRect().top-header.getBoundingClientRect().bottom:null,
    outsideActions:[...host.querySelectorAll('button')].filter(n=>visible(n)&&n.textContent==='Voir sur Carte'&&!el.contains(n)).length,
    overflow:el.scrollWidth>el.clientWidth+1||document.documentElement.scrollWidth>innerWidth+1};
  });
  matrix.push({family,mode,...metrics});save();
  if(phase!=='before' && await view.evaluate(el=>el.classList.contains('entity-form'))){
   await expect(view.locator(':scope > .entity-form__header')).toBeVisible();
   expect(metrics.composed).toBe(true);
   expect(metrics.children.slice(0,4)).toEqual(['entity-form__header','entity-form__tabs','entity-form__context-actions','entity-form__messages']);
   expect(metrics.outsideActions).toBe(0);expect(metrics.headerTabGap).toBe(12);expect(metrics.contentPaddingLeft).toBe(12);
   expect(metrics.header.x).toBeCloseTo(metrics.tabs.x,0);expect(metrics.header.w).toBeCloseTo(metrics.tabs.w,0);
   expect(metrics.overflow).toBe(false);
   expect(metrics.header.y-metrics.root.y).toBeCloseTo(metrics.paddingTop,0);
   await expect(view.locator(':scope > .entity-form__body > .entity-form__actions')).toHaveCount(1);
   expect(await view.locator(':scope > .entity-form__body > .entity-form__actions > [data-role=save]').getAttribute('form')).toBe(await view.locator(':scope > .entity-form__body > form').getAttribute('id'));
  }
  await page.screenshot({path:`${artifacts}/${surface}-${width}-${family}-${mode}.png`});
 }
 for(const [family,n]of batch){
  let view;
  if(surface==='base'){
   await page.evaluate(({family,id})=>document.querySelector('main').dispatchEvent(new CustomEvent('digues-app:open-entity',{detail:{entityClass:family,entityId:id,source:'home',entities:[]}})),{family,id:fakeId(n)});
   await expect(page.locator('#data-entity-detail-read')).toBeVisible();
   await capture(family,'read',page.locator('#data-entity-detail'));
   await page.locator('#data-entity-detail-edit').click();view=page.locator('#base-detail-editor > .entity-form');
  }else if(family==='observations'){
   await page.evaluate(row=>ObservationDetail.open({id:row.id,parent:row.parent,fetchJson:async(path,options)=>{const response=await fetch(path,options);return response.json();}}),observation);
   view=page.locator('#observation-detail-dialog > .entity-form');
  }else{
   if(family==='desordres')await clickFeature(page,fakeId(n));else await page.evaluate(({family,id})=>MapEntityNavigation.open(family,id),{family,id:fakeId(n)});
   view=page.locator('#editor-panel .entity-form:visible').first();
  }
  await expect(view.locator('[name=designation]').first()).toHaveValue(/.+/);await capture(family,'edit',view);
  if(surface==='base'){
   await view.locator('[data-role=cancel]').first().click();await expect(page.locator('#data-entity-detail-read')).toBeVisible();
   await page.locator('#base-detail-close').click();
  }else{
   if(['systemes','digues','troncons'].includes(family)){
    await view.locator('[data-role=cancel]').first().click();await capture(family,'read',page.locator('#editor-panel .entity-form:visible').first());
   }
   await view.locator(':scope > .entity-form__header [data-role=close]').click();
  }
 }
 expect(errorsByContext.get(context)).toEqual([]);
});

// Native validation and form association must survive host-specific form IDs.
test('footer commun, validation native et composants enfants indépendants',async({context,page})=>{
 await fixture(context);await ready(page);
 const result=await page.evaluate(()=>{
  let submits=0;
  const v=EntityFormView.create({id:'shell-probe',descriptor:{family:'troncons',fields:{identification:[{name:'designation',label:'Désignation',kind:'text',required:true}]},children:['details']},onSubmit:e=>{e.preventDefault();submits++;}});
  document.body.append(v.element);v.setFormId('host-specific-form');v.sync({mode:'edit',objectId:'probe'});
  const nested=document.createElement('form');nested.innerHTML='<input name="independent" required>';
  v.children.get('details').append(nested);
  v.save.click();const invalid=submits;v.controls.get('designation').value='Tronçon';v.save.click();
  const out={invalid,submits,associated:v.save.form===v.form,independent:nested.parentElement.closest('form')===null,
   order:[...v.general.children].map(n=>n.className),childrenBeforeFooter:!!(v.extensions.compareDocumentPosition(v.actions)&Node.DOCUMENT_POSITION_FOLLOWING)};
  v.element.remove();return out;
 });
 expect(result).toEqual({invalid:0,submits:1,associated:true,independent:true,order:['entity-form__form','entity-form__extensions','entity-form__section entity-form__actions'],childrenBeforeFooter:true});
});

test('les actions empruntées à Base reviennent à leur propriétaire à la fermeture du contexte',async({context,page})=>{
 await fixture(context);await page.goto('/base');await expect(page.locator('main')).toHaveJSProperty('inert',false);
 async function open(){await page.evaluate(id=>document.querySelector('main').dispatchEvent(new CustomEvent('digues-app:open-entity',{detail:{entityClass:'systemes',entityId:id,source:'home',entities:[]}})),fakeId(3));await expect(page.locator('#data-entity-detail-edit')).toBeVisible();}
 await open();await page.locator('#data-entity-detail-edit').click();await expect(page.locator('#base-detail-editor > .entity-form')).toBeVisible();
 // Exercise DOM disposal only. Real authorization/isolation is tested by the
 // disposable PostgreSQL browser harness, not by this synthetic signal.
 await page.evaluate(()=>dispatchEvent(new Event('digues-app:context-closed')));
 await expect(page.locator('#data-entity-detail')).toBeHidden();
 await expect(page.locator('.base-detail__actions > #data-entity-detail-back')).toHaveCount(1);
 await expect(page.locator('.base-detail__actions > #data-entity-detail-map')).toHaveCount(1);
 await open();await page.locator('#data-entity-detail-edit').click();await expect(page.locator('#base-detail-editor > .entity-form')).toBeVisible();
});


// B (<10 s): legacy map hosts rename the generated select's ID. Its accessible
// name must survive without depending on another labelable hidden output.
test('le type de dessin reste nommé quand un hôte renomme son identifiant',async({page})=>{
 await page.setContent('<main></main>');await page.addScriptTag({path:'webapp/frontend/js/entity-form-view.js'});
 await page.evaluate(()=>{
  const view=EntityFormView.create({id:'geometry-shell-probe',descriptor:{family:'troncons',fields:{},
   geometry:{types:['LineString','Polygon'],required:true,editable:true,controls:'draft'}}});
  document.querySelector('main').append(view.element);view.sync({mode:'edit',objectId:'probe'});
  view.geometry.type.id='legacy-host-geometry';
 });
 await expect(page.getByRole('combobox',{name:'Type de dessin',exact:true})).toHaveAttribute('id','legacy-host-geometry');
});
