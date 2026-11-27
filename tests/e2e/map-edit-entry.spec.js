const { test, expect } = require('@playwright/test');
const source = require('./fixtures/frontend-preview-data');
const { fixture, ready, clickFeature, errorsByContext, T, D, S } = require('./fixtures/map-editor-fixture');
// Niveau B (~2 min) : vrais contrôleurs et DOM Leaflet ; transport fictif mutable
// par scénario. Le backend/OCC réel reste couvert par les suites PostgreSQL.
// Aucun dessin ni remplacement géométrique n'est nécessaire pour ouvrir l'édition.
test.use({ serviceWorkers: 'block' });
test.afterEach(async ({ context }) => {
  expect(errorsByContext.get(context) || []).toEqual([]);
});
const classes = [
  ['mobilier', 'mobilier', 40], ['equipement-mesure', 'equipements_mesure', 41],
  ['reseau-technique', 'reseaux_techniques', 42], ['ouvrage-particulier', 'ouvrages_particuliers', 43],
  ['cheminement', 'cheminements', 44], ['vegetation', 'vegetation', 45],
  ['prestation', 'prestations', 47], ['troncons-lit', 'troncons_lit', 20],
  ['elements-morphologiques-lit', 'elements_morphologiques_lit', 30],
  ['zone-stockage', 'zones_stockage', 48],
];
for (const [type, resource, number] of classes) test(`${type} : clic métier ouvre l'édition et Créer reste vierge`, async ({ context, page }) => {
  await fixture(context); await ready(page);
  await clickFeature(page, source.fakeId(number));
  const panel = page.locator(`#${type}-editor-panel`);
  await expect(panel.locator('[name=designation]')).toHaveValue(new RegExp('ficti'));
  await expect(panel.locator('[data-role=save]')).toHaveText('Enregistrer');
  await expect(panel.locator('[data-role=save]')).toBeEnabled();
  expect(await page.evaluate(resource => mapEntityEditors.get(resource).shell.state.mode, resource)).toBe('edit');
  if (await panel.locator('[name=troncon_id]').count()) await expect(panel.locator('[name=troncon_id]')).toHaveValue(T);
  expect(await page.evaluate(() => editorMapInteraction)).toBeNull();
  await expect(panel.locator('[data-role=catalog]')).toHaveCount(0);
  await expect(panel.locator('[data-role=new]')).toHaveCount(0);
  await page.locator('#editor-panel .entity-form__header [data-role=close]:visible').click();
  await page.locator('#toggle-create-menu').click();
  await page.locator(`[data-create-type="${type}"]`).click();
  await expect(panel.locator('[name=designation]')).toHaveValue('');
  await expect(panel.locator('[data-role=catalog]')).toHaveCount(0);
});

test('Tronçon : vrai formulaire Base, attributs persistés et géométrie conservée', async ({ context, page }) => {
  const state = await fixture(context); const geometry = structuredClone(state.records.troncons[0].geometry);
  await ready(page); await clickFeature(page, T);
  await expect(page.locator('#data-entity-detail .entity-form__form')).toBeVisible();
  await expect(page.locator('#data-entity-detail .entity-form__fields [name=libelle]')).toHaveValue('T1');
  await expect(page.locator('#data-entity-detail-read')).toContainText('Digue 1');
  await page.locator('#data-entity-detail .entity-form__fields [name=designation]').fill('Tronçon modifié');
  await page.locator('#data-entity-detail .entity-form__actions [data-role=save]').click();
  await expect.poll(() => state.writes.length).toBe(1);
  expect(state.writes[0].headers['if-match']).toBe('"1"');
  expect(state.writes[0].body).not.toHaveProperty('geometry');
  expect(state.records.troncons[0].geometry).toEqual(geometry);
  await expect(page.locator('#data-entity-detail .entity-form__form')).toBeHidden();
  await clickFeature(page, T);
  await expect(page.locator('#data-entity-detail .entity-form__form')).toBeVisible();
  await expect(page.locator('#data-entity-detail .entity-form__fields [name=designation]')).toHaveValue('Tronçon modifié');
  await page.reload();
  await page.waitForFunction(() => typeof mapViewportReady !== 'undefined' && mapViewportReady);
  await clickFeature(page, T);
  await expect(page.locator('#data-entity-detail .entity-form__fields [name=designation]')).toHaveValue('Tronçon modifié');
});
for (const [kind, id, title] of [["Système d'endiguement", S, 'SE fictif'], ['Digue', D, 'Digue fictive'], ['Tronçon', T, 'Tronçon T1']]) {
  test(`Patrimoine ${kind} : édition, visibilité indépendante`, async ({ context, page }) => {
    await fixture(context); await ready(page);
    await page.locator('#toggle-heritage').click();
    const button = page.locator(`.tree-name[data-object-id="${id}"]`);
    await button.evaluate(node => { for (let el = node; el; el = el.parentElement) if (el.hidden) el.hidden = false; });
    await button.click();
    await expect(page.locator('#data-entity-detail .entity-form__fields [name=designation]')).toHaveValue(title);
    await expect(page.locator('#heritage-tree .tree-visibility').first()).toBeChecked();
  });
}

test('Brouillon Tronçon : refus d’abandon et conflit conservent saisie/version', async ({ context, page }) => {
  const state = await fixture(context, { conflict: true });
  await ready(page); await clickFeature(page, T);
  const field = page.locator('#data-entity-detail .entity-form__fields [name=designation]');
  await field.fill('Brouillon conservé');
  page.once('dialog', dialog => dialog.dismiss());
  await page.locator('#toggle-create-menu').click(); await page.locator('[data-create-type=mobilier]').click();
  await expect(field).toHaveValue('Brouillon conservé');
  page.once('dialog', dialog => dialog.dismiss()); await clickFeature(page, source.fakeId(40));
  await expect(field).toHaveValue('Brouillon conservé');
  await page.locator('#data-entity-detail .entity-form__actions [data-role=save]').click();
  await expect(page.locator('#data-entity-detail .entity-form__messages [role=alert]')).toHaveText(require('../../webapp/frontend/js/resource-concurrency').CONFLICT_MESSAGE);
  await expect(field).toHaveValue('Brouillon conservé');
  expect(state.writes[0].headers['if-match']).toBe('"1"');
  page.once('dialog', dialog => dialog.dismiss()); await page.locator('#editor-panel .entity-form__header [data-role=close]:visible').click();
  await expect(field).toBeVisible();
});
for (const viewport of [{ width: 390, height: 844 }, { width: 844, height: 390 }]) test(`Tronçon mobile ${viewport.width}×${viewport.height}`, async ({ context, page }) => {
  await page.setViewportSize(viewport); await fixture(context); await ready(page); await clickFeature(page, T);
  await expect(page.locator('#data-entity-detail .entity-form__form')).toBeVisible();
  await page.locator('#data-entity-detail .entity-form__actions [data-role=save]').scrollIntoViewIfNeeded();
  await expect(page.locator('#data-entity-detail .entity-form__actions [data-role=save]')).toBeInViewport();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width + 1);
  await page.screenshot({ path: `audits/ux-form-3b/troncon-${viewport.width}x${viewport.height}.png` });
});


test('Tronçon en lecture seule : fiche accessible et édition refusée', async ({ context, page }) => {
  const state = await fixture(context); await ready(page);
  await page.evaluate(() => { const runtime = window.BusinessRuntime;
    window.BusinessRuntime = { ...runtime, can: permission => permission === 'business.read' }; });
  await clickFeature(page, T);
  await expect(page.locator('#data-entity-detail-read')).toBeVisible();
  await expect(page.locator('#data-entity-detail-edit')).toBeHidden();
  await expect(page.locator('#data-entity-detail .entity-form__form')).toBeHidden();
  expect(state.writes).toEqual([]);
});

test('Enregistrement Tronçon : navigation et fermeture refusées pendant le PUT', async ({ context, page }) => {
  let release; const delay = new Promise(resolve => { release = resolve; });
  const state = await fixture(context, { delay }); await ready(page); await clickFeature(page, T);
  const field = page.locator('#data-entity-detail .entity-form__fields [name=designation]');
  await field.fill('Écriture en cours');
  await page.locator('#data-entity-detail .entity-form__actions [data-role=save]').click();
  await expect.poll(() => state.writes.length).toBe(1);
  await page.locator('#editor-panel .entity-form__header [data-role=close]:visible').click();
  await expect(field).toBeVisible(); await expect(field).toBeDisabled();
  await clickFeature(page, source.fakeId(40));
  await expect(field).toHaveValue('Écriture en cours');
  release();
  await expect(page.locator('#data-entity-detail-read')).toContainText('Écriture en cours');
  expect(state.writes).toHaveLength(1);
});

test('Désordre : clic en édition, brouillon protégé vers Tronçon, puis création vierge', async ({ context, page }) => {
  await fixture(context); await ready(page); await clickFeature(page, source.fakeId(10));
  const designation = page.locator('#desordre-create-designation');
  await expect(designation).toHaveValue('Désordre fictif');
  await designation.fill('Brouillon Désordre');
  page.once('dialog', dialog => dialog.dismiss()); await clickFeature(page, T);
  await expect(designation).toHaveValue('Brouillon Désordre');
  await expect(page.locator('#heritage-edit-view')).toHaveCount(0);
  page.once('dialog', dialog => dialog.accept()); await clickFeature(page, T);
  await expect(page.locator('#data-entity-detail .entity-form__form')).toBeVisible();
  await page.locator('#toggle-create-menu').click(); await page.locator('[data-create-type=desordre]').click();
  await expect(designation).toHaveValue('');
  expect(await page.evaluate(() => editorState.mode)).toBe('create');
});

// UX-FORM-04 : même contrat visuel que Mobilier et cycle POST → édition.
// État SQL simulé par scénario ; les tests live couvrent la persistance réelle.
const heritageFamilies = [
  ['systeme', 'systemes-endiguement', S], ['digue', 'digues', D], ['troncon', 'troncons', T],
];
async function createEntry(page, type) {
  await page.locator('#toggle-create-menu').click();
  await page.locator(`[data-create-type="${type}"]`).click();
  await expect(page.locator(type === 'mobilier' ? '#mobilier-editor-panel form' : type === 'desordre' ? '#desordre-editor' : '#heritage-object-editor')).toBeVisible();
}
async function editEntry(page, type, id) {
  if (type === 'troncon' || type === 'desordre') return clickFeature(page, id);
  const toggle = page.locator('#toggle-heritage');
  if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
  for (const parentId of type === 'digue' ? [S] : []) {
    const expand = page.locator(`.tree-row:has(.tree-name[data-object-id="${parentId}"]) > .tree-toggle`);
    if (await expand.getAttribute('aria-expanded') !== 'true') await expand.click();
  }
  const button = page.locator(`.tree-name[data-object-id="${id}"]`);
  await button.click();
}
async function fillCreation(page, type) {
  await page.locator(type === 'desordre' ? '#desordre-create-designation' : '#heritage-object-label').fill('Objet UX04');
  if (type === 'digue') await page.locator('#heritage-parent').selectOption(S);
  if (type === 'troncon') await page.locator('#heritage-parent').selectOption(D);
  if (type === 'troncon' || type === 'desordre') await page.evaluate(type => {
    if (type === 'troncon') { provisionalTronconLayer = L.polyline([[46,2],[46.01,2.01]]).addTo(map); }
    else { provisionalDesordreLayer = L.marker([46,2]).addTo(map); }
  }, type);
}
for (const [type, resource] of [...heritageFamilies, ['desordre', 'desordres']]) {
  test(`UX04 ${type} : POST unique, identité relue, Général puis Observations légitimes et PUT`, async ({ context, page }) => {
    let release; const delay = new Promise(resolve => { release = resolve; });
    const state = await fixture(context, {delay}); await ready(page);
    await createEntry(page, type);
    await expect(page.locator('#editor-panel .entity-form [data-role=general-tab]:visible')).toHaveAttribute('aria-selected', 'true');
    const observations = page.locator('#editor-panel .entity-form:visible > .entity-form__tabs [data-role=observations-tab]');
    if (['troncon','desordre'].includes(type)) await expect(observations).toBeDisabled();
    else await expect(observations).toBeHidden();
    await fillCreation(page, type);
    const form = type === 'desordre' ? '#desordre-editor' : '#heritage-object-editor';
    await page.locator(form).evaluate(form => {form.requestSubmit(); form.requestSubmit();});
    await expect.poll(() => state.creates.length).toBe(1);
    await expect(page.locator('#editor-panel .entity-form__header [data-role=close]:visible')).toBeDisabled();
    await expect(page.locator(`${form} [name=designation]`)).toBeDisabled();
    release();
    const fields = type === 'desordre' ? page.locator('#desordre-editor') : page.locator('#data-entity-detail .entity-form__fields');
    const save = page.locator(type === 'desordre' ? '#submit-desordre-create' : '#data-entity-detail .entity-form__actions [data-role=save]');
    await expect(save).toBeVisible(); await expect(save).toHaveText('Enregistrer'); await expect(save).toBeEnabled();
    await expect(fields.locator(type === 'desordre' ? '[name=designation]' : '[name=libelle]')).toHaveValue('Objet UX04');
    const general = type === 'desordre' ? page.locator('#editor-panel .entity-form [data-role=general-tab]:visible') : page.locator('#data-entity-detail [data-role=general-tab]');
    await expect(general).toHaveAttribute('aria-selected', 'true');
    expect(state.observationReads).toEqual([]);
    if (['troncon','desordre'].includes(type)) {
      const tab = type === 'desordre' ? observations : page.locator('#data-entity-detail [data-role=observations-tab]');
      await expect(tab).toBeEnabled(); await tab.click();
      await expect.poll(() => state.observationReads.length).toBe(1);
      expect(state.observationReads[0]).toEqual({resource,id:source.fakeId(80)});
      await expect(page.locator(type === 'desordre' ? '#observations-message' : '#data-entity-detail-observation-list')).toContainText('Aucune observation');
      const createObservation = page.locator(type === 'desordre' ? '#create-desordre-observation' : '#data-entity-detail-create-observation');
      await expect(createObservation).toBeVisible(); await createObservation.click();
      await expect(page.locator('#observation-create-dialog')).toBeVisible();
      await expect(page.locator('#observation-create-submit')).toBeEnabled();
      await page.locator('#observation-create-close').click();
      await general.click();
    } else await expect(page.locator('#data-entity-detail [data-role=observations-tab]')).toBeHidden();
    await fields.locator('[name=designation]').fill('Modification UX04'); await save.click();
    await expect.poll(() => state.writes.length).toBe(1);
    expect(state.writes[0].id).toBe(source.fakeId(80));
    expect(state.writes[0].headers['if-match']).toBe('"1"');
    expect(state.writes[0].body.designation).toBe('Modification UX04');
    expect(state.writes[0].body).not.toHaveProperty('geometry');
    expect(state.creates).toHaveLength(1);
  });
  test(`UX04 ${type} : création refusée conserve brouillon et Observations indisponibles`, async ({context,page}) => {
    const state = await fixture(context,{postStatus:422}); await ready(page); await createEntry(page,type); await fillCreation(page,type);
    await page.locator(type === 'desordre' ? '#submit-desordre-create' : '#submit-create').click();
    await expect(page.locator(type === 'desordre' ? '#desordre-create-message' : '#heritage-create-message')).toContainText('Création refusée');
    const field = page.locator(type === 'desordre' ? '#desordre-create-designation' : '#heritage-object-label');
    await expect(field).toHaveValue('Objet UX04');
    page.once('dialog', dialog => dialog.dismiss()); await page.locator('#editor-panel .entity-form__header [data-role=close]:visible').click();
    await expect(field).toBeVisible(); await expect(field).toHaveValue('Objet UX04');
    if (['troncon','desordre'].includes(type)) await expect(page.locator('#editor-panel .entity-form:visible > .entity-form__tabs [data-role=observations-tab]')).toBeDisabled();
    expect(state.writes).toEqual([]); expect(state.observationReads).toEqual([]);
  });
}
async function presentation(page, formSelector) {
  return page.locator(formSelector).evaluate(form => {
    const style = el => { const s = getComputedStyle(el); return {font:s.font, padding:s.padding, gap:s.gap}; };
    return {form:style(form), fields:style(form.querySelector('.entity-form__fields')),
      label:style(form.querySelector('[name=designation]').closest('label')),
      input:style(form.querySelector('[name=designation]')),
      section:style(form.closest('.entity-form__body'))};
  });
}
for (const viewport of [{width:1440,height:900}, {width:390,height:844}, {width:844,height:390}]) {
  test.describe(`Présentation ${viewport.width}×${viewport.height}`, () => {
  test.use({viewport, hasTouch:viewport.width!==1440, isMobile:viewport.width!==1440});
  test(`UX04 structure des quatre familles non converties, création/édition ${viewport.width}×${viewport.height}`, async ({context,page}) => {
    await page.setViewportSize(viewport);
    if (viewport.width !== 1440) await page.addInitScript(() => {
      const original = window.matchMedia.bind(window);
      window.matchMedia = query => query.includes('display-mode: standalone') ? {...original(query),matches:true,addEventListener(){},removeEventListener(){}} : original(query);
    });
    await fixture(context); await ready(page);
    if (viewport.width !== 1440) await expect(page.locator('body')).toHaveClass(/pwa-standalone/);
    await createEntry(page, 'systeme');
    await expect(page.locator('#heritage-object-editor [name=libelle]')).toBeEditable();
    const reference = await presentation(page, '#heritage-object-editor');
    await page.locator('#editor-panel .entity-form__header [data-role=close]:visible').click();
    for (const [type,,id] of [...heritageFamilies, ['desordre','desordres',source.fakeId(10)]]) {
      await createEntry(page,type);
      const createForm = type === 'desordre' ? '#desordre-editor' : '#heritage-object-editor';
      expect(await presentation(page,createForm)).toEqual(reference);
      await expect(page.locator("#editor-panel .entity-form__actions:visible")).toHaveCount(1);
      await page.screenshot({path:`audits/ux-form-04/${type}-creation-${viewport.width}x${viewport.height}.png`});
      await page.locator('#editor-panel .entity-form__header [data-role=close]:visible').click(); await editEntry(page,type,id);
      const editForm = type === 'desordre' ? '#desordre-editor' : '#data-entity-detail .entity-form__form';
      await expect(page.locator(editForm)).toBeVisible();
      expect(await presentation(page,editForm)).toEqual(reference);
      await expect(page.locator("#editor-panel .entity-form__actions:visible")).toHaveCount(1);
      await expect(page.locator('#editor-panel .entity-form__header:visible')).toHaveCount(1);
      await expect(page.locator('#general-tab > h2, #data-entity-detail-general > h2')).toHaveCount(0);
      await expect(page.locator('#editor-panel .entity-form:visible > .entity-form__header .entity-form__title')).not.toHaveText('Fiche métier');
      await page.screenshot({path:`audits/ux-form-04/${type}-edition-${viewport.width}x${viewport.height}.png`});
      const button = page.locator(type === 'desordre' ? '#submit-desordre-create' : '#data-entity-detail .entity-form__actions [data-role=save]');
      await button.scrollIntoViewIfNeeded(); await expect(button).toBeInViewport();
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width+1);
      await page.screenshot({path:`audits/ux-form-04/${type}-${viewport.width}x${viewport.height}.png`});
      await page.locator('#editor-panel .entity-form__header [data-role=close]:visible').click();
    }
  });
  });
}

for (const [type,,id] of [['systeme','systemes-endiguement',S], ['digue','digues',D], ['desordre','desordres',source.fakeId(10)]]) {
  test(`UX04 ${type} : conflit et abandon refusé gardent la saisie et la version`, async ({context,page}) => {
    const state = await fixture(context,{conflict:true}); await ready(page); await editEntry(page,type,id);
    const field = page.locator(type === 'desordre' ? '#desordre-create-designation' : '#data-entity-detail .entity-form__fields [name=designation]');
    await expect(field).toBeVisible(); await field.fill('Brouillon UX04');
    page.once('dialog',dialog => dialog.dismiss()); await page.locator('#editor-panel .entity-form__header [data-role=close]:visible').click();
    await expect(field).toBeVisible(); await expect(field).toHaveValue('Brouillon UX04');
    await page.locator(type === 'desordre' ? '#submit-desordre-create' : '#data-entity-detail .entity-form__actions [data-role=save]').click();
    await expect.poll(() => state.writes.length).toBe(1);
    await expect(page.locator(type === 'desordre' ? '#editor-panel .entity-form:visible > .entity-form__header .entity-form__subtitle' : '#data-entity-detail .entity-form__messages [role=alert]')).toContainText(/(Conflit|modifi|version|Version|obsol)/);
    await expect(field).toHaveValue('Brouillon UX04');
    expect(state.writes[0].headers['if-match']).toBe('"1"');
    expect(state.creates).toEqual([]);
  });
}
test('UX04 : détail Désordre périmé ignoré après ouverture de la création SE', async ({context,page}) => {
  await fixture(context); await ready(page);
  let release, requested = false;
  const gate = new Promise(resolve => {release=resolve;});
  await page.route(`**/api/desordres/${source.fakeId(10)}`,async route => {
    requested=true; await gate; await route.fallback();
  });
  await clickFeature(page,source.fakeId(10)); await expect.poll(() => requested).toBe(true);
  await createEntry(page,'systeme'); await page.locator('#heritage-object-label').fill('Nouvelle saisie');
  release();
  await expect(page.locator('#heritage-object-label')).toHaveValue('Nouvelle saisie');
  await expect(page.locator('#desordre-editor')).toBeHidden();
  await expect(page.locator('#editor-panel .entity-form:visible > .entity-form__tabs [data-role=observations-tab]')).toBeHidden();
});

test('UX04 lecture seule : quatre fiches consultables, écriture et ajout Observation interdits', async ({context,page}) => {
  const state = await fixture(context); await ready(page);
  await page.evaluate(() => {const runtime=window.BusinessRuntime; window.BusinessRuntime={...runtime, can:permission=>permission==='business.read'};});
  for (const [type,,id] of [...heritageFamilies,['desordre','desordres',source.fakeId(10)]]) {
    await editEntry(page,type,id);
    if (type === 'desordre') {
      await expect(page.locator('#desordre-create-designation')).toBeDisabled();
      await expect(page.locator('#editor-panel .entity-form:visible > .entity-form__header .entity-form__subtitle')).toContainText('Consultation');
      await expect(page.locator('#submit-desordre-create')).toBeHidden();
      await page.locator('#editor-panel .entity-form:visible > .entity-form__tabs [data-role=observations-tab]').click();
      await expect(page.locator('#create-desordre-observation')).toBeHidden();
    } else {
      await expect(page.locator('#data-entity-detail-read')).toBeVisible();
      await expect(page.locator('#data-entity-detail-edit')).toBeHidden();
      if (type === 'troncon') {
        await page.locator('#data-entity-detail [data-role=observations-tab]').click();
        await expect(page.locator('#data-entity-detail-create-observation')).toBeHidden();
      }
    }
    await page.locator('#editor-panel .entity-form__header [data-role=close]:visible').click();
  }
  expect(state.writes).toEqual([]); expect(state.creates).toEqual([]);
});

for (const viewport of [{width:390,height:844},{width:844,height:390}]) test.describe(`Cycle PWA ${viewport.width}×${viewport.height}`, () => {
  test.use({viewport,isMobile:true,hasTouch:true});
  test('UX04 quatre créations confirmées restent éditables en PWA',async ({context,page}) => {
    await page.addInitScript(() => {
      const original = window.matchMedia.bind(window);
      window.matchMedia = query => query.includes('display-mode: standalone')
        ? {...original(query),matches:true,addEventListener(){},removeEventListener(){}} : original(query);
    });
    const state = await fixture(context); await ready(page);
    await expect(page.locator('body')).toHaveClass(/pwa-standalone/);
    for (const [index,[type]] of [...heritageFamilies,['desordre']].entries()) {
      await createEntry(page,type); await fillCreation(page,type);
      await page.locator(type==='desordre'?'#submit-desordre-create':'#submit-create').click();
      const form=page.locator(type==='desordre'?'#desordre-editor':'#data-entity-detail .entity-form__form');
      await expect(form).toBeVisible();
      await expect(page.locator(type==='desordre'?'#submit-desordre-create':'#data-entity-detail .entity-form__actions [data-role=save]')).toHaveText('Enregistrer');
      const identifier = await page.evaluate(type => type==='desordre' ? editorState.objectId : heritageAttributeEditor.getState().id,type);
      expect(identifier).toBe(source.fakeId(80+index));
      await expect(page.locator(type==='desordre'?'#editor-panel .entity-form [data-role=general-tab]:visible':'#data-entity-detail [data-role=general-tab]')).toHaveAttribute('aria-selected','true');
      await page.locator('#editor-panel .entity-form__header [data-role=close]:visible').click();
    }
    expect(state.creates).toHaveLength(4); expect(state.writes).toEqual([]); expect(state.observationReads).toEqual([]);
  });
});

// UX-PATRIMOINE-AH-1 : réutilise les contrôleurs et fixtures Carte du fichier.
const AH = source.fakeId(90), EMPTY_AH=source.fakeId(91), ZONE=source.fakeId(92);
const ahBranch = page => page.locator('#heritage-tree [role=group][aria-label="Aménagements hydrauliques"]');
const ahName = (page,id) => ahBranch(page).locator(`.tree-name[data-object-id="${id}"]`);
async function ahTree(page) {
  const toggle=page.locator('#toggle-heritage');
  if(await toggle.getAttribute('aria-expanded')!=='true') await toggle.click();
  await expect(ahName(page,AH)).toBeVisible();
}
async function expandAH(page) {
  await ahTree(page);
  const toggle=ahName(page,AH).locator('..').locator('> .tree-toggle');
  if(await toggle.getAttribute('aria-expanded')!=='true') await toggle.click();
  await expect(ahName(page,ZONE)).toBeVisible();
}
test('Patrimoine AH : deux branches, enfants directs typés, aucun rattachement inventé', async ({context,page}) => {
  await fixture(context,{ah:true}); await ready(page); await expandAH(page);
  await expect(page.locator('#heritage-tree > .heritage-branch')).toHaveCount(2);
  const children=ahName(page,AH).locator('../..').locator('> .tree-node-children > .tree-node');
  await expect(children).toHaveCount(4);
  expect(await children.locator('> .tree-row > .tree-name').evaluateAll(nodes=>nodes.map(n=>n.dataset.objectId))).toEqual([ZONE,T,source.fakeId(93),source.fakeId(94)]);
  await expect(ahBranch(page).locator('.tree-visibility')).toHaveCount(0);
  await expect(ahName(page,source.fakeId(48))).toHaveCount(0);
  await expect(ahName(page,source.fakeId(4))).toHaveCount(0);
  await expect(ahName(page,EMPTY_AH)).toBeVisible();
  await expect(ahName(page,EMPTY_AH).locator('../..').locator('> .tree-node-children')).toHaveCount(0);
  await expect(ahBranch(page)).not.toContainText('Sans AH');
});
for (const [id,selector,expected] of [[AH,'#amenagement-hydraulique-editor-panel','AH autonome'],[ZONE,'#zone-stockage-editor-panel','Zone AH'],[T,'#data-entity-detail .entity-form__form','Tronçon T1'],[EMPTY_AH,'#amenagement-hydraulique-editor-panel','AH vide']]) {
  test(`Patrimoine AH : identité ${id} ouvre le véritable éditeur`,async ({context,page})=>{
    const state=await fixture(context,{ah:true}); await ready(page); await expandAH(page); await ahName(page,id).click();
    await expect(page.locator(`${selector} [name=designation]`)).toHaveValue(expected);
    await expect(page.locator(`${selector} [name=designation]`)).toBeEnabled();
    if(id===AH || id===EMPTY_AH) expect(state.records['amenagements-hydrauliques'].find(row=>row.properties.id===id).geometry).toBeNull();
    await expect(ahName(page,id)).toHaveClass(/selected/);
    expect(state.creates).toEqual([]);
  });
}
test('Patrimoine AH : types sans éditeur consultables sans requête au mauvais Tronçon',async ({context,page})=>{
  await fixture(context,{ah:true}); const wrong=[];
  page.on('request',r=>{if(/\/api\/troncons\/99990000-0000-4000-8000-00000000009[34]/.test(r.url()))wrong.push(r.url());});
  await ready(page); await expandAH(page);
  for(const id of [source.fakeId(93),source.fakeId(94)]) {
    await ahName(page,id).click();
    await expect(ahName(page,id).locator('../..').locator('> .tree-consultation')).toContainText('Aucun éditeur compatible');
    await expect(page.locator('#data-entity-detail .entity-form__form')).toHaveCount(0);
    await expect(ahName(page,id)).toHaveAttribute('data-object-resource','troncons_unifies');
  }
  expect(wrong).toEqual([]);
});
test('Patrimoine AH : même UUID sous Digue et AH, modification canonique unique',async ({context,page})=>{
  const state=await fixture(context,{ah:true}); await ready(page); await expandAH(page); await ahName(page,T).click();
  await page.locator('#data-entity-detail .entity-form__fields [name=designation]').fill('Modification depuis AH');
  await page.locator('#data-entity-detail .entity-form__actions [data-role=save]').click(); await expect.poll(()=>state.writes.length).toBe(1);
  expect(state.writes[0].id).toBe(T); expect(state.writes[0].body).not.toHaveProperty('geometry');
  await page.locator('#editor-panel .entity-form__header [data-role=close]:visible').click();
  const systems=page.locator('#heritage-tree [aria-label="Systèmes d\'endiguement"]');
  for(const id of [S,D]) await systems.locator(`.tree-row:has(>.tree-name[data-object-id="${id}"]) > .tree-toggle`).click();
  await systems.locator(`.tree-name[data-object-id="${T}"]`).click();
  await expect(page.locator('#data-entity-detail .entity-form__fields [name=designation]')).toHaveValue('Modification depuis AH');
  expect(state.records.troncons.filter(row=>row.properties.id===T)).toHaveLength(1);
  expect(state.creates).toEqual([]);
});
test('Patrimoine AH : clavier, dépliage, sélection et fermeture',async ({context,page})=>{
  await fixture(context,{ah:true}); await ready(page); await ahTree(page);
  await ahName(page,AH).focus(); await page.keyboard.press('ArrowRight'); await expect(ahName(page,ZONE)).toBeVisible();
  await page.keyboard.press('ArrowRight'); await expect(ahName(page,ZONE)).toBeFocused();
  await page.keyboard.press('ArrowDown'); await expect(ahName(page,T)).toBeFocused();
  await page.keyboard.press('ArrowLeft'); await expect(ahName(page,AH)).toBeFocused();
  await page.keyboard.press('ArrowLeft'); await expect(ahName(page,ZONE)).toBeHidden();
  await page.keyboard.press('End'); await expect(ahName(page,EMPTY_AH)).toBeFocused();
  await page.keyboard.press('Enter'); await expect(page.locator('#amenagement-hydraulique-editor-panel [name=designation]')).toHaveValue('AH vide');
  await page.locator('#close-heritage').click(); await expect(page.locator('#toggle-heritage')).toHaveAttribute('aria-expanded','false');
});
test('Patrimoine AH : brouillon Zone protège sa fiche et la sélection active',async ({context,page})=>{
  await fixture(context,{ah:true}); await ready(page); await expandAH(page); await ahName(page,ZONE).click();
  const field=page.locator('#zone-stockage-editor-panel [name=designation]'); await expect(field).toBeEnabled(); await field.fill('Brouillon Zone');
  page.once('dialog',d=>d.dismiss()); await ahName(page,AH).click();
  await expect(field).toHaveValue('Brouillon Zone'); await expect(ahName(page,ZONE)).toHaveClass(/selected/);
  await expect(ahName(page,AH)).not.toHaveClass(/selected/);
});
test('Patrimoine AH : ancienne réponse de fiche ignorée et aucune sélection périmée',async ({context,page})=>{
  await fixture(context,{ah:true}); await ready(page); await ahTree(page);
  let release,requested=false; const gate=new Promise(r=>release=r);
  await page.route(`**/api/amenagements-hydrauliques/${AH}`,async route=>{requested=true;await gate;await route.fallback();});
  await ahName(page,AH).click(); await expect.poll(()=>requested).toBe(true);
  await ahName(page,EMPTY_AH).click(); await expect(page.locator('#amenagement-hydraulique-editor-panel [name=designation]')).toHaveValue('AH vide');
  release(); await expect(ahName(page,EMPTY_AH)).toHaveClass(/selected/);
  await expect(ahName(page,AH)).not.toHaveClass(/selected/);
});
test('Patrimoine AH : invalidation Zone actualise les liens sans rouvrir un brouillon',async ({context,page})=>{
  const state=await fixture(context,{ah:true}); await ready(page); await expandAH(page); await ahName(page,AH).click();
  const field=page.locator('#amenagement-hydraulique-editor-panel [name=designation]'); await expect(field).toBeEnabled(); await field.fill('Brouillon conservé');
  const [a,b]=state.heritage.amenagements_hydrauliques; b.zones_stockage=a.zones_stockage.splice(0); b.zones_stockage[0].amenagement_hydraulique_id=b.id;
  await page.evaluate(async id=>ResourceMutationController.publishMutation({schemaVersion:1,eventId:crypto.randomUUID(),resource:'zones-stockage',action:'update',ids:[id],versions:{[id]:2},occurredAt:Date.now(),originClientId:'peer',originInstanceId:'peer'}, {applyLocally:true}),ZONE);
  await expect(ahName(page,AH).locator('../..').locator('> .tree-node-children .tree-name[data-object-id="'+ZONE+'"]')).toHaveCount(0);
  await expect(ahName(page,AH).locator('..').locator('> .tree-toggle')).toHaveAttribute('aria-expanded','true');
  await expect(ahName(page,AH)).toHaveClass(/selected/); await expect(field).toHaveValue('Brouillon conservé');
});
for(const viewport of [{width:390,height:844},{width:844,height:390}]) test.describe(`Patrimoine AH PWA ${viewport.width}`,()=>{
  test.use({viewport,isMobile:true,hasTouch:true});
  test('AH mobile : navigation, brouillon Désordre préservé et panneau utilisable',async ({context,page})=>{
    await page.addInitScript(()=>{const original=window.matchMedia.bind(window);window.matchMedia=q=>q.includes('display-mode: standalone')?{...original(q),matches:true,addEventListener(){},removeEventListener(){}}:original(q);});
    await fixture(context,{ah:true}); await ready(page); await clickFeature(page,source.fakeId(10));
    const draft=page.locator('#desordre-create-designation'); await draft.fill('Brouillon mobile');
    await ahTree(page); page.once('dialog',d=>d.dismiss()); await ahName(page,AH).click();
    await expect(draft).toBeVisible(); await expect(draft).toHaveValue('Brouillon mobile');
    await ahTree(page); page.once('dialog',d=>d.accept()); await ahName(page,AH).click();
    await expect(page.locator('#amenagement-hydraulique-editor-panel [name=designation]')).toHaveValue('AH autonome');
    await expandAH(page); await expect(ahName(page,AH)).toBeInViewport();
    await ahName(page,ZONE).scrollIntoViewIfNeeded();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width+1);
    await page.screenshot({path:`audits/ux-patrimoine-ah-1/patrimoine-${viewport.width}.png`});
  });
});

test('Patrimoine AH : lecture seule, aucun contournement de droit par la navigation',async ({context,page})=>{
  const state=await fixture(context,{ah:true}); await ready(page);
  await page.evaluate(()=>{const runtime=window.BusinessRuntime;window.BusinessRuntime={...runtime,can:p=>p==='business.read'};});
  await expandAH(page);
  for(const [id,panel] of [[AH,'#amenagement-hydraulique-editor-panel'],[ZONE,'#zone-stockage-editor-panel']]) {
    await ahName(page,id).click(); await expect(page.locator(`${panel} [name=designation]`)).toBeDisabled();
    await expect(page.locator(`${panel} [data-role=save]`)).toBeHidden();
  }
  expect(state.writes).toEqual([]); expect(state.creates).toEqual([]);
});

test('Patrimoine AH : une consultation non éditable respecte la révocation du contexte',async ({context,page})=>{
  await fixture(context,{ah:true}); await ready(page); await expandAH(page);
  await page.evaluate(()=>{const r=window.BusinessRuntime;window.BusinessRuntime={...r,can:()=>false};});
  await ahName(page,source.fakeId(94)).click();
  await expect(ahName(page,source.fakeId(94)).locator('../..').locator('> .tree-consultation')).toBeHidden();
  await expect(ahName(page,source.fakeId(94))).not.toHaveClass(/selected/);
});

test('Patrimoine AH : un rafraîchissement pendant une ouverture conserve la dernière identité demandée',async ({context,page})=>{
  await fixture(context,{ah:true}); await ready(page); await ahTree(page); await ahName(page,AH).click();
  await expect(ahName(page,AH)).toHaveClass(/selected/);
  let release,requested=false;const gate=new Promise(r=>release=r);
  await page.route(`**/api/amenagements-hydrauliques/${EMPTY_AH}`,async route=>{requested=true;await gate;await route.fallback();});
  await ahName(page,EMPTY_AH).click(); await expect.poll(()=>requested).toBe(true);
  await page.evaluate(()=>heritagePanelController.reload({propagateError:true}));
  release(); await expect(page.locator('#amenagement-hydraulique-editor-panel [name=designation]')).toHaveValue('AH vide');
  await expect(ahName(page,EMPTY_AH)).toHaveClass(/selected/); await expect(ahName(page,AH)).not.toHaveClass(/selected/);
});
