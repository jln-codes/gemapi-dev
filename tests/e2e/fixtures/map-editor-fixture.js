// Fixtures Carte partagées : état HTTP synthétique neuf par scénario.
const { expect } = require('@playwright/test');
const { randomUUID } = require('node:crypto');
const source = require('./frontend-preview-data');
const references = require('./frontend-preview-references.json').payloads;
const { mockBusinessCRS } = require('../mock-business-crs');
const { mockResourceJournal } = require('../mock-resource-journal');
const { geometryCapabilities } = require('../mock-geometry-capabilities');
const { photoQualityPolicy } = require('./photo-quality-policy');
const T = source.fakeId(1), D = source.fakeId(2), S = source.fakeId(3);
const errorsByContext = new WeakMap();
function copy(value) {
  return JSON.parse(JSON.stringify(value).replaceAll('"t1"', `"${T}"`)
    .replaceAll('"t2"', `"${source.fakeId(4)}"`).replaceAll('"d1"', `"${D}"`).replaceAll('"s1"', `"${S}"`));
}
async function fixture(context, { conflict = false, delay = null, postStatus = 201, ah = false } = {}) {
  const errors = [];
  errorsByContext.set(context, errors);
  for (const page of context.pages()) page.on('pageerror', error => errors.push(error.message));
  context.on('page', page => page.on('pageerror', error => errors.push(error.message)));
  const records = copy({ ...source.records, troncons: source.troncons.features });
  records['zones-stockage'] = [{ type: 'Feature', geometry: copy(source.records.vegetation[0].geometry),
    properties: { id: source.fakeId(48), version: 1, designation: 'Zone fictive', valid: true } }];
  const heritage = copy(source.previewHeritage);
  const system = { ...heritage.systemes[0], version: 1, designation: 'SE fictif', population_protegee: 12 };
  const digue = { ...system.digues[0], version: 1, designation: 'Digue fictive', systeme_endiguement_id: S,
    systeme_endiguement_libelle: system.libelle };
  records.troncons[0].properties = { ...records.troncons[0].properties, systeme_endiguement_id: S, systeme_endiguement_libelle: system.libelle };
  heritage.amenagements_hydrauliques = [];
  if (ah) {
    const a=source.fakeId(90), z=source.fakeId(92);
    const trunks=[{...records.troncons[0].properties,resource:'troncons',type_troncon_unifie_id:'INDETERMINE',type_troncon_unifie_libelle:'Indéterminé',amenagement_hydraulique_id:a},
      {id:source.fakeId(93),designation:'Tronçon sans extension',resource:'troncons_unifies',type_troncon_unifie_id:'INDETERMINE',type_troncon_unifie_libelle:'Indéterminé',amenagement_hydraulique_id:a,valid:false},
      {id:source.fakeId(94),designation:'Berge associée',resource:'troncons_unifies',type_troncon_unifie_id:'BERGE',type_troncon_unifie_libelle:'Berge',amenagement_hydraulique_id:a,valid:false}];
    const zones=[{id:z,designation:'Zone AH',libelle:'Zone AH',amenagement_hydraulique_id:a,valid:false}];
    const assembly={id:a,designation:'AH autonome',libelle:'AH A',valid:true,troncons_unifies:trunks,zones_stockage:zones};
    const empty={id:source.fakeId(91),designation:'AH vide',libelle:'AH B',valid:null,troncons_unifies:[],zones_stockage:[]};
    heritage.amenagements_hydrauliques=[assembly,empty];
    records['amenagements-hydrauliques']=[assembly,empty].map(row=>({type:'Feature',geometry:null,properties:{...row,version:1,zone_ids:row.zones_stockage.map(z=>z.id),troncon_ids:row.troncons_unifies.map(t=>t.id),desordre_ids:[]}}));
    records['zones-stockage'].push({type:'Feature',geometry:{type:'Polygon',coordinates:[[[-1.55,47.21],[-1.54,47.21],[-1.54,47.22],[-1.55,47.21]]]},properties:{...zones[0],version:1,reseau_hydraulique_ids:[],gestionnaire_ids:[],geometry_status:'CONFORME'}});
  }
  const writes = [], creates = [], observationReads = [];
  const createdRecords = new Map();
  await context.route('**/api/**', async route => {
    if (await mockBusinessCRS(route) || await mockResourceJournal(route)) return;
    const request = route.request(), path = new URL(request.url()).pathname;
    const send = (json, status = 200) => route.fulfill({ status, json });
    if (path === '/api/config') return send({ legacy_single_database_enabled: true, show_uuid: false,
      photo_quality_policy: photoQualityPolicy(), build_id: 'map-edit-entry' });
    if (request.method() === 'POST' && ['/api/systemes-endiguement', '/api/digues', '/api/troncons', '/api/desordres', '/api/mobilier', '/api/equipements-mesure',...Object.keys(records).map(resource=>`/api/${resource}`)].includes(path)) {
      const resource = path.split('/').pop(), body = request.postDataJSON(), id = body.id || source.fakeId(80 + creates.length);
      creates.push({resource, body});
      if (delay) await delay;
      if (postStatus !== 201) return send({detail: 'Création refusée'}, postStatus);
      const values = { ...body, id: body.id || id, version: 1 };
      const row = !['systemes-endiguement','digues'].includes(resource) ? { type: 'Feature', properties: values, geometry: body.geometry } : values;
      if (records[resource]) row.mutation = {
        schemaVersion: 1, eventId: randomUUID(), resource, action: 'create', ids: [id],
        versions: { [values.id]: 1 }, occurredAt: Date.now(), originClientId: request.headers()['x-client-id'],
        originInstanceId: request.headers()['x-instance-id'], aspects: ['attributes', 'geometry'] };
      if (records[resource]) records[resource].push(row);
      createdRecords.set(`${resource}/${values.id}`, row);
      return send(row, 201);
    }
    if (path === '/api/systemes-endiguement') return send(heritage);
    if (path.startsWith('/api/referentiels/')) return send(references[path.split('/').pop()] || { references: [], types: [], categories: [] });
    if (path.startsWith('/api/geometry-capabilities/')) return send(geometryCapabilities({ resource: path.split('/').pop(),
      status: 'approved', allowedTypes: path.endsWith('/troncons-lit') ? ['LineString','Polygon'] : ['Point', 'LineString', 'Polygon'] }));
    if (path === '/api/troncons/options') return send({ troncons: records.troncons.map(row => row.properties) });
    if (path === '/api/troncons-lit/options') return send({ digues: [digue], troncons_lit: records['troncons-lit'].map(row => row.properties) });
    if (path.endsWith('/relations/options')) return send({ amenagements: ah ? records['amenagements-hydrauliques'].map(row=>row.properties) : [], reseaux: [], organismes: [], zones: ah ? records['zones-stockage'].map(row=>row.properties) : [], troncons: records.troncons.map(row => row.properties),
      amenagements_hydrauliques: records['amenagements-hydrauliques'].map(row => row.properties), contacts: [],
      desordres: records.desordres.map(row => row.properties), ouvrages_hydrauliques: [],
      ouvrages_particuliers: [], mobilier: [], equipements_mesure: [], reseaux_techniques: [], cheminements: [], vegetation: [] });
    if (path === '/api/desordres/search' || path === '/api/desordres/selection') return send({ items: records.desordres.map(row => ({
      id: row.properties.id, label: row.properties.designation, valid: true })), missing_ids: [], has_more: false, next_cursor: null });
    if (path.endsWith('/observations')) {
      const [, , resource, id] = path.split('/'); observationReads.push({resource,id});
      return send({parent: {resource:resource.replaceAll('-','_'),id}, observations: []});
    }
    const [, resource, id] = path.match(/^\/api\/([^/]+)(?:\/([^/]+))?$/) || [];
    let row = createdRecords.get(`${resource}/${id}`) || (resource === 'systemes-endiguement' && id === S ? system : resource === 'digues' && id === D ? digue
      : records[resource]?.find(item => item.properties.id === id));
    if (row) {
      if (request.method() === 'PUT') {
        const body = request.postDataJSON();
        writes.push({ resource, id, body, headers: request.headers() });
        if (conflict) return send({ detail: { code: 'version-conflict', message: 'Version obsolète' } }, 409);
        if (delay) await delay;
        const values = row.properties || row;
        Object.assign(values, body, { version: values.version + 1 });
        if (['mobilier', 'equipements-mesure'].includes(resource) && body.geometry) row.geometry = body.geometry;
        row.mutation = { schemaVersion: 1, eventId: randomUUID(), resource: resource === 'systemes-endiguement' ? 'systemes' : resource,
          action: 'update', ids: [id], versions: { [id]: values.version }, occurredAt: Date.now(),
          originClientId: request.headers()['x-client-id'], originInstanceId: request.headers()['x-instance-id'], aspects: ['attributes'] };
      }
      return send(row);
    }
    if (records[resource]) return send({ type: 'FeatureCollection', features: records[resource] });
    return send({ type: 'FeatureCollection', features: [] });
  });
  return { records, writes, creates, observationReads, system, digue, heritage };
}
async function ready(page) {
  await page.goto('/carte');
  await expect(page.locator('main')).toHaveJSProperty('inert', false);
  await page.waitForFunction(() => typeof mapViewportReady !== 'undefined' && mapViewportReady);
}
async function clickFeature(page, id) {
  // Dispatch a DOM click on the real Leaflet path, identified by source UUID.
  const found = await page.evaluate(id => {
    let found = false;
    map.eachLayer(layer => {
      if (layer.feature?.properties?.id === id && layer.getElement?.()) {
        layer.getElement().dataset.editEntry = id; found = true;
      }
    });
    return found;
  }, id);
  expect(found, `Géométrie cliquable ${id}`).toBe(true);
  await page.locator(`[data-edit-entry="${id}"]`).dispatchEvent('click');
}

module.exports = { fixture, ready, clickFeature, errorsByContext, T, D, S };
