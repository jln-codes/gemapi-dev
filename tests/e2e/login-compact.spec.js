// B presentation (~30 s): real bootstrap/CSS/Chromium, isolated synthetic auth
// responses. No claim about server authentication; its existing live tests remain separate.
const {test,expect}=require('@playwright/test');
const fs=require('node:fs');
test.use({serviceWorkers:'block'});
const phase=process.env.LOGIN_COMPACT_PHASE||'after';
const artifacts=`audits/login-compact-01/${phase}`;
async function fixture(context,demo){
 const state={user:null,logins:[]};
 await context.route('**/api/**',route=>{
  const req=route.request(),path=new URL(req.url()).pathname;
  if(path==='/api/config')return route.fulfill({json:{legacy_single_database_enabled:false,public_demo:demo?{slug:'base-demo',database_id:'00000000-0000-4000-8000-000000000011',login:'demo',password:'synthetic-demo'}:null}});
  if(path==='/api/auth/login'){
   const body=req.postDataJSON();state.logins.push(body);
   if(body.login!=='layout-user'||body.password!=='synthetic-password')return route.fulfill({status:401,json:{detail:'Invalid synthetic credentials'}});
   state.user=body.login;return route.fulfill({json:{id:'00000000-0000-4000-8000-000000000001'}});
  }
  if(path==='/api/auth/logout'){state.user=null;return route.fulfill({status:204});}
  if(!state.user)return route.fulfill({status:401,json:{detail:'Authentication required'}});
  if(path==='/api/auth/me')return route.fulfill({json:{id:'00000000-0000-4000-8000-000000000001',login:state.user}});
  if(path==='/api/auth/databases')return route.fulfill({json:{user_id:'00000000-0000-4000-8000-000000000001',instance_id:'00000000-0000-4000-8000-000000000002',expires_at:new Date(Date.now()+3600000).toISOString(),databases:[1,2].map(n=>({slug:'layout-'+n,structure_name:'Structure de contrôle',display_name:'Base '+n}))}});
  return route.fulfill({status:404,json:{detail:'Unexpected request'}});
 });
 return state;
}
const dialog=page=>page.locator('#identity-gate > [role=dialog]');
async function metrics(page,name,target=dialog(page)){
 const value=await target.evaluate(el=>{
  const box=n=>{if(!n)return null;const r=n.getBoundingClientRect();return{x:r.x,y:r.y,width:r.width,height:r.height};};
  const form=el.querySelector('form'),status=el.querySelector('[role=status]'),action=el.querySelector('.app-modal-actions'),link=el.querySelector('.identity-demo-access');
  return {dialog:box(el),header:box(el.querySelector('header')),inputs:[...el.querySelectorAll('input')].map(box),button:box(el.querySelector('button')),status:box(status),statusText:status?.textContent,link:box(link),actionToLink:link?link.getBoundingClientRect().top-action.getBoundingClientRect().bottom:null,overflow:document.documentElement.scrollWidth>innerWidth,padding:getComputedStyle(el).padding,formGap:form?getComputedStyle(form).gap:null,actionDirection:action?getComputedStyle(action).flexDirection:null};
 });
 fs.mkdirSync(artifacts,{recursive:true});fs.writeFileSync(`${artifacts}/${name}.json`,JSON.stringify(value,null,2));await page.screenshot({path:`${artifacts}/${name}.png`});return value;
}
for(const demo of [true,false])test(`connexion desktop, démonstration ${demo?'disponible':'absente'}`,async({context,page})=>{
 await page.setViewportSize({width:1440,height:900});const state=await fixture(context,demo);await page.goto('/base?login=1');
 await expect(page.getByRole('heading',{name:'Connexion à digues.app',exact:true})).toBeVisible();
 await expect(page.getByRole('link',{name:'Découvrir la démonstration'})).toHaveCount(demo?1:0);
 const m=await metrics(page,demo?'desktop-demo':'desktop-no-demo');
 expect(m.dialog.width).toBe(phase==='before'?512:420);expect(m.overflow).toBe(false);expect(m.actionDirection).toBe('row');
 if(phase!=='before'){
  expect(m.status.height).toBe(0);expect(m.dialog.height).toBeLessThanOrEqual(300);
  if(demo){expect(m.dialog.height).toBeGreaterThanOrEqual(280);expect(m.actionToLink).toBeLessThanOrEqual(9);}
  for(const input of m.inputs)expect(input.height).toBeGreaterThanOrEqual(44);expect(m.button.height).toBeGreaterThanOrEqual(44);
 }
 const action=await dialog(page).locator('.app-modal-actions').boundingBox();expect(m.button.x+m.button.width).toBeCloseTo(action.x+action.width,0);
 await page.getByLabel('Identifiant',{exact:true}).fill('layout-user');await page.getByLabel('Mot de passe',{exact:true}).fill('wrong');await page.getByRole('button',{name:'Se connecter',exact:true}).click();
 await expect(dialog(page).locator('[role=status]')).toHaveText('Identifiant ou mot de passe incorrect.');
 const error=await metrics(page,demo?'desktop-error-demo':'desktop-error-no-demo');expect(error.status.height).toBeGreaterThan(0);expect(error.dialog.height).toBeGreaterThan(m.dialog.height);
 await page.getByLabel('Mot de passe',{exact:true}).fill('synthetic-password');await page.getByRole('button',{name:'Se connecter',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Choisir une base métier',exact:true})).toBeVisible();
 const bases=await metrics(page,demo?'base-selector-demo':'base-selector-no-demo');expect(bases.dialog.width).toBe(512);await expect(dialog(page)).not.toHaveClass(/identity-login-dialog/);
 expect(state.logins).toHaveLength(2);await page.getByRole('button',{name:'Se connecter avec un autre compte',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Connexion à digues.app',exact:true})).toBeVisible();expect(state.user).toBeNull();
});
test('connexion mobile, clavier et petit écran défilable',async({context,page})=>{
 await fixture(context,true);await page.setViewportSize({width:390,height:844});await page.goto('/base?login=1');
 await expect(page.getByLabel('Identifiant',{exact:true})).toBeFocused();
 for(const target of [page.getByLabel('Mot de passe',{exact:true}),page.getByRole('button',{name:'Se connecter',exact:true}),page.getByRole('link',{name:'Découvrir la démonstration'})]){await page.keyboard.press('Tab');await expect(target).toBeFocused();}
 const m=await metrics(page,'mobile');expect(m.overflow).toBe(false);expect(m.dialog.x).toBeGreaterThanOrEqual(0);expect(m.dialog.x+m.dialog.width).toBeLessThanOrEqual(390);expect(m.actionDirection).toBe('column');
 await page.setViewportSize({width:320,height:240});await metrics(page,'small-screen');
 await page.getByRole('link',{name:'Découvrir la démonstration'}).scrollIntoViewIfNeeded();
 const box=await page.getByRole('link',{name:'Découvrir la démonstration'}).boundingBox();expect(box.y).toBeGreaterThanOrEqual(0);expect(box.y+box.height).toBeLessThanOrEqual(240);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

// B: control modal without the login class retains the common foundation.
test('une autre modale conserve ses proportions communes',async({context,page})=>{
 await fixture(context,false);await page.setViewportSize({width:1440,height:900});await page.goto('/base?login=1');
 await expect(page.getByLabel('Identifiant',{exact:true})).toBeVisible();
 await page.evaluate(()=>{
  const host=document.createElement('div');host.className='app-modal-backdrop';
  host.innerHTML='<section id="login-compact-control" class="app-modal-dialog" role="dialog"><header class="app-modal-header"><h1>Modale témoin</h1></header><div class="app-modal-body"><p>Contenu du dialogue commun.</p><div class="app-modal-actions"><button class="app-modal-primary">Confirmer</button></div></div></section>';document.body.append(host);
 });
 const m=await metrics(page,'other-modal',page.locator('#login-compact-control'));
 expect(m.dialog.width).toBe(512);expect(m.padding).toBe('16px');expect(m.header.height).toBeCloseTo(39.375,1);expect(m.button.height).toBeLessThan(44);
});
