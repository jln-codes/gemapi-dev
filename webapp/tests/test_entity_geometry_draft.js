const assert = require("node:assert/strict");
const test = require("node:test");
const GeometryDraft = require("../frontend/js/entity-geometry-draft.js");

test("les coordonnées refusent une saisie incomplète, une position hors domaine et une ligne dégénérée", () => {
  const { readCoordinatePair, geometryFromCoordinates } = GeometryDraft;
  assert.deepEqual(readCoordinatePair(["0", "-0.5"]), [0, -0.5]);
  assert.throws(() => readCoordinatePair(["", "0"]), /deux coordonnées/);
  assert.throws(() => geometryFromCoordinates("Point", [[3, 91]]), /hors du domaine/);
  assert.throws(() => geometryFromCoordinates("LineString", [[3, 50], [3, 50]]), /distincts/);
  assert.throws(() => geometryFromCoordinates("Polygon", [[3, 50], [4, 51]]), /hors du domaine/);
});

function harness(allowedGeometryTypes = ["Point", "LineString", "Polygon"], contractResource = null, observeDirty = null) {
  const original = { type: "Point", coordinates: [2, 48] };
  let source = original;
  let mode = "edit", editable = true;
  const handlers = new Set(), layers = new Set(), messages = [];
  let begun = 0, finished = 0, dirty = 0, gpsCancelled = 0;
  const map = {
    on(type, handler) { if (type === "click") handlers.add(handler); },
    off(type, handler) { if (type === "click") handlers.delete(handler); },
    removeLayer(layer) { layers.delete(layer); },
  };
  const makeLayer = (kind, position) => ({
    kind, position, handlers: {},
    dragging: { enabled: true, enable() { this.enabled = true; }, disable() { this.enabled = false; } },
    addTo() { layers.add(this); return this; },
    on(type, handler) { this.handlers[type] = handler; },
    getLatLng() { return this.position; },
  });
  const L = {
    marker: position => makeLayer("marker", position),
    polyline: position => makeLayer("polyline", position),
    polygon: position => makeLayer("polygon", position),
  };
  const panel = {
    beginGeometryDrawing(options) { begun++; this.options = options; },
    updateGeometryDrawing() {},
    finishGeometryDrawing() { finished++; },
  };
  const controls = {
    type: { value: "Point", children: [],
      ownerDocument: { createElement: () => ({}) },
      replaceChildren(...options) { this.children = options; } }, status: { textContent: "" },
    draw: { textContent: "" }, confirm: { hidden: true }, restore: { hidden: true },
    setRequirement(required, allowMissingWhenInvalid) { this.requirement = { required, allowMissingWhenInvalid }; }, contractStatus: {}, cancel: Object.assign(new EventTarget(), { hidden: true }),
  };
  const draft = GeometryDraft.create({ map, L, panel, pathStyle: {}, controls,
    allowedGeometryTypes, contractResource, getMode: () => mode,
    getOriginal: () => source, canStart: () => editable,
    onDirty: () => { dirty++; observeDirty?.(draft); },
    setStatus: (message, error) => messages.push({ message, error }),
    getGpsTracking: () => ({ cancel() { gpsCancelled++; }, refresh() {} }),
    emptyMessage: "Aucune géométrie.",
  });
  const click = (lng, lat) => {
    for (const handler of [...handlers]) handler({ latlng: { lng, lat } });
  };
  return { draft, controls, panel, handlers, layers, messages, click, original,
    setOriginal(value) { source = value; },
    setMode(value) { mode = value; },
    setEditable(value) { editable = value; },
    counts: () => ({ begun, finished, dirty, gpsCancelled }) };
}

// FORM-CORE-3, niveau A : aucun changement du contrat spatial ; le verrou de
// session interdit aussi le drag du Point sur la carte, pas seulement le bouton.
test("un Point en brouillon ne peut pas être déplacé pendant une opération verrouillée", () => {
  const h = harness(); h.draft.start(); h.click(2, 48);
  const marker = [...h.layers][0];
  assert.equal(marker.dragging.enabled, true);
  h.setEditable(false); h.draft.updateStatus();
  assert.equal(marker.dragging.enabled, false);
  assert.deepEqual(h.draft.draftGeometry(), h.original);
  h.setEditable(true); h.draft.updateStatus();
  assert.equal(marker.dragging.enabled, true);
});

test("Point, LineString et Polygon produisent le même GeoJSON et libèrent les couches", () => {
  const h = harness();
  h.draft.start();
  h.click(2, 48);
  assert.deepEqual(h.draft.draftGeometry(), h.original);
  assert.equal(h.draft.isChanged(), true);
  assert.equal(h.handlers.size, 0);
  assert.equal([...h.layers][0].kind, "marker");

  h.controls.type.value = "LineString";
  h.draft.changeType();
  assert.equal(h.layers.size, 0);
  h.draft.start();
  h.click(2, 48);
  h.click(3, 49);
  h.draft.confirm();
  assert.deepEqual(h.draft.draftGeometry(),
    { type: "LineString", coordinates: [[2, 48], [3, 49]] });
  assert.equal([...h.layers][0].kind, "polyline");

  h.controls.type.value = "Polygon";
  h.draft.changeType();
  h.draft.start();
  h.click(2, 48);
  h.click(3, 48);
  h.click(3, 49);
  h.draft.confirm();
  assert.deepEqual(h.draft.draftGeometry(), { type: "Polygon",
    coordinates: [[[2, 48], [3, 48], [3, 49], [2, 48]]] });
  assert.equal([...h.layers][0].kind, "polygon");
  h.draft.dispose();
  assert.equal(h.handlers.size, 0);
  assert.equal(h.layers.size, 0);
  assert.equal(h.counts().begun, h.counts().finished);
});

// Niveau A : contrat synthétique approuvé, aucune mutation SQL. Le risque
// couvert est une capacité inconnue ou ancienne transformée en autorisation.
const contract = (overrides = {}) => ({ schemaVersion: 1,
  resource: "ouvrages-particuliers", target: "da:concept:ouvrage-particulier",
  status: "approved", contractVersion: "a".repeat(64), modelDigest: "b".repeat(64),
  allowedTypes: ["Point", "LineString", "Polygon"], required: true,
  create: true, edit: true, replace: true, convert: true, remove: false,
  validity: { preserveExisting: true, requiresConformingForValid: true }, ...overrides });

test("contrat absent, version inconnue ou décision non approuvée ne donnent aucune capacité", () => {
  const h = harness([], "ouvrages-particuliers");
  h.setOriginal(null);
  h.setMode("create");
  assert.equal(h.draft.canAcquire(), false);
  assert.throws(() => h.draft.validate(), /pas chargé/);
  for (const invalid of [contract({ schemaVersion: 2 }), contract({ status: "unavailable" }),
    contract({ contractVersion: "old" }), contract({ allowedTypes: ["MultiPolygon"] }),
    contract({ resource: "vegetation" })]) {
    assert.throws(() => h.draft.setContract(invalid), /contrat géométrique/);
  }
  assert.equal(h.draft.canAcquire(), false);
});

test("une capacité approuvée change les options et les modes sans modifier le contrôleur métier", () => {
  const h = harness([], "ouvrages-particuliers");
  h.draft.setContract(contract({ allowedTypes: ["Polygon"] }));
  assert.deepEqual(h.controls.type.children.map(option => option.value), ["Polygon"]);
  assert.match(h.controls.status.textContent, /Point existante hors contrat/);
  assert.equal(h.controls.contractStatus.textContent, "");
  assert.equal(h.draft.acceptGeometry({ type: "LineString", coordinates: [[2, 48], [3, 49]] }), false);
  h.controls.type.value = "Polygon";
  h.draft.start();
  for (const [lng, lat] of [[2, 48], [3, 48], [3, 49]]) h.click(lng, lat);
  h.draft.confirm();
  assert.equal(h.draft.validate().type, "Polygon");
  assert.throws(() => h.draft.setContract(contract()), /brouillon/);
  h.draft.reset();
  h.draft.setContract(contract({ allowedTypes: ["Point"], contractVersion: "c".repeat(64) }));
  assert.deepEqual(h.controls.type.children.map(option => option.value), ["Point"]);
  assert.equal(h.draft.contractVersion(), "c".repeat(64));
});

test("une géométrie historique incompatible se conserve uniquement sans validité métier", () => {
  const h = harness([], "ouvrages-particuliers");
  const historical = { type: "MultiPoint", coordinates: [[2, 48], [3, 49]] };
  h.setOriginal(historical);
  h.draft.setContract(contract());
  assert.equal(h.draft.validate({ valid: false }), historical);
  assert.throws(() => h.draft.validate({ valid: true }), /doit être de type/);
  assert.equal(h.draft.isChanged(), false);
  assert.equal(h.draft.acceptGeometry(historical), false);
  h.setMode("create");
  assert.throws(() => h.draft.validate({ valid: false }), /doit être de type/);
});

test("présence facultative, préservation NULL et compatibilité explicite restent distinctes", () => {
  const h = harness([], "ouvrages-particuliers");
  h.setOriginal(null);
  h.setMode("create");
  h.draft.setContract(contract({ status: "compatibility" }));
  assert.match(h.controls.contractStatus.textContent, /à qualifier/);
  assert.throws(() => h.draft.validate(), /obligatoire/);
  h.setMode("edit");
  assert.equal(h.draft.validate({ valid: false }), null);
  assert.equal(h.draft.isChanged(), false);
  h.draft.setContract(contract({ required: false }));
  h.setMode("create");
  assert.equal(h.draft.validate(), null);
  assert.equal(h.controls.requirement.required, false);
  h.draft.clearContract();
  assert.equal(h.draft.canAcquire(), false);
  assert.deepEqual(h.controls.type.children, []);
});

test("capacités de création et remplacement sont contrôlées séparément", () => {
  const h = harness([], "ouvrages-particuliers");
  h.setOriginal(null);
  h.setMode("create");
  h.draft.setContract(contract({ edit: false }));
  h.draft.start();
  h.click(2, 48);
  assert.equal(h.draft.validate().type, "Point");
  h.draft.reset();
  h.setOriginal(h.original);
  h.setMode("edit");
  assert.equal(h.draft.canAcquire(), false);
  h.draft.setContract(contract({ convert: false }));
  h.controls.type.value = "Polygon";
  assert.equal(h.draft.canAcquire(), false);
  assert.equal(h.draft.changeType(), false);
  assert.equal(h.controls.type.value, "Point");
  h.controls.type.value = "Point";
  assert.equal(h.draft.canAcquire(), true);
  assert.equal(h.draft.acceptGeometry({ type: "LineString", coordinates: [[2, 48], [3, 49]] }), false);
  assert.equal(h.draft.isChanged(), false);
  h.draft.setContract(contract({ replace: false }));
  assert.equal(h.draft.canAcquire(), false);
});

test("Point EMPTY reste conservable et signalé sans devenir valide", () => {
  const h = harness([], "ouvrages-particuliers");
  const empty = { type: "Point", coordinates: [] };
  h.setOriginal(empty);
  h.draft.setContract(contract());
  assert.match(h.controls.status.textContent, /vide, non dessinable/);
  assert.equal(h.draft.validate({ valid: false }), empty);
  assert.throws(() => h.draft.validate({ valid: true }), /est vide/);
  assert.equal(h.draft.isChanged(), false);
});

test("annulation restaure exactement la géométrie initiale et retire les handlers", () => {
  const h = harness();
  h.controls.type.value = "LineString";
  h.draft.changeType();
  h.draft.start();
  h.click(2, 48);
  assert.equal(h.draft.draftGeometry(), null);
  h.panel.options.cancel();
  assert.equal(h.handlers.size, 0);
  assert.equal(h.layers.size, 0);
  assert.equal(h.draft.isChanged(), false);
  assert.equal(h.controls.status.textContent, "");
  assert.deepEqual(h.original, { type: "Point", coordinates: [2, 48] });
  assert.equal(h.counts().begun, h.counts().finished);
});

test("la commande commune annule le dessin, respecte le verrou et est détachée à la fermeture", () => {
  const h = harness();
  h.controls.type.value = "LineString"; h.draft.changeType();
  h.draft.start(); h.click(2, 48);
  assert.equal(h.controls.cancel.hidden, false);
  h.setEditable(false); h.controls.cancel.dispatchEvent(new Event("click"));
  assert.equal(h.draft.isDrawing(), true);
  h.setEditable(true); h.controls.cancel.dispatchEvent(new Event("click"));
  assert.equal(h.draft.isDrawing(), false);
  assert.equal(h.draft.isChanged(), false);
  assert.equal(h.layers.size, 0); assert.equal(h.handlers.size, 0);
  assert.equal(h.controls.cancel.hidden, true);
  h.draft.dispose();
  const count = h.messages.length;
  h.controls.cancel.dispatchEvent(new Event("click"));
  assert.equal(h.messages.length, count);
});

test("une forme refusée et un GPS incomplet ne remplacent pas le brouillon", () => {
  const h = harness(["Point", "LineString"]);
  h.draft.start();
  h.click(2, 48);
  h.controls.type.value = "Polygon";
  assert.equal(h.draft.changeType(), false);
  assert.equal(h.controls.type.value, "Point");
  assert.deepEqual(h.draft.draftGeometry(), h.original);
  assert.equal(h.draft.acceptGeometry({ type: "Polygon", coordinates: [] }), false);
  assert.equal(h.draft.acceptGeometry({ type: "LineString", coordinates: [[2, 48]] }), false);
  assert.deepEqual(h.draft.draftGeometry(), h.original);
  assert.equal(h.messages.at(-1).error, true);
});

test("un tracé GPS confirmé remplace le brouillon sans nouveau tracking", () => {
  const h = harness();
  h.draft.start();
  h.click(2, 48);
  assert.equal(h.draft.acceptGeometry({ type: "LineString", coordinates: [[2, 48], [3, 49]] }), true);
  assert.deepEqual(h.draft.draftGeometry(),
    { type: "LineString", coordinates: [[2, 48], [3, 49]] });
  assert.equal(h.handlers.size, 0);
  assert.equal(h.layers.size, 1);
  h.draft.reset();
  assert.equal(h.draft.isChanged(), false);
  assert.equal(h.layers.size, 0);
});

test("Lit : géométrie absente uniquement sur décision approuvée et valid=false", () => {
  const h = harness([], "troncons-lit");
  h.setMode("create"); h.setOriginal(null);
  h.draft.setContract(contract({ resource: "troncons-lit", allowedTypes: ["LineString", "Polygon"],
    requiredForValid: true, allowMissingWhenInvalid: true }));
  assert.equal(h.draft.validate({ valid: false }), null);
  assert.throws(() => h.draft.validate({ valid: true }), /obligatoire/);
  assert.deepEqual(h.controls.requirement, { required: true, allowMissingWhenInvalid: true });
  assert.throws(() => h.draft.setContract(contract({ resource: "troncons-lit", status: "compatibility",
    requiredForValid: true, allowMissingWhenInvalid: true })), /incompatible/);
  const previous = harness([], "ouvrages-particuliers");
  previous.setMode("create"); previous.setOriginal(null);
  previous.draft.setContract(contract());
  assert.throws(() => previous.draft.validate({ valid: false }), /obligatoire/);
});

// FORM-GEOM-1 : annuler une acquisition conserve le brouillon précédent et les attributs.
test("une deuxième acquisition annulée conserve le premier tracé non enregistré", () => {
 const h=harness();h.controls.type.value="LineString";h.draft.changeType();
 h.draft.start();h.click(2,48);h.click(3,49);h.draft.confirm();
 const before=h.draft.draftGeometry();h.draft.start();h.click(4,50);h.controls.cancel.dispatchEvent(new Event("click"));
 assert.deepEqual(h.draft.draftGeometry(),before);assert.equal(h.draft.isChanged(),true);
 assert.equal(h.draft.isDrawing(),false);assert.equal(h.handlers.size,0);
 assert.equal(h.controls.restore.hidden,true);
});

// Niveau A : séquence Leaflet click + dblclick simulé tactile ; aucune mutation.
test("un deuxième tap tactile reste un sommet et Terminer demeure l'unique fin", () => {
 const events=new Map(),points=[];let ended=0,binding;
 let enabled=true;
 const map={on:(type,handler)=>events.set(type,handler),off:type=>events.delete(type),
   doubleClickZoom:{enabled:()=>enabled,disable:()=>{enabled=false},enable:()=>{enabled=true}}};
 binding=GeometryDraft.beginAcquisition({map,getType:()=>"LineString",getVertices:()=>points,
   onVertex:event=>points.push(event.latlng),finish:()=>{ended++;binding.stop()}});
 events.get("click")({latlng:{lat:48,lng:2},originalEvent:{pointerType:"touch",detail:1}});
 events.get("dblclick")({latlng:{lat:49,lng:3},originalEvent:{pointerType:"touch",detail:2}});
 assert.equal(points.length,2);assert.equal(ended,0);assert.equal(enabled,false);
 binding.finish();assert.equal(ended,1);assert.equal(enabled,true);assert.equal(events.size,0);
});
test("double-clic bureau ne double pas son sommet et restaure doubleClickZoom déjà désactivé", () => {
 const events=new Map(),points=[];let ended=0,binding,enabled=false;
 const map={on:(type,handler)=>events.set(type,handler),off:type=>events.delete(type),
   doubleClickZoom:{enabled:()=>enabled,disable:()=>{enabled=false},enable:()=>{enabled=true}}};
 binding=GeometryDraft.beginAcquisition({map,getType:()=>"LineString",getVertices:()=>points,
   onVertex:event=>points.push(event.latlng),finish:()=>{ended++;binding.stop()}});
 events.get("click")({latlng:{lat:48,lng:2},originalEvent:{detail:1}});
 events.get("click")({latlng:{lat:49,lng:3},originalEvent:{detail:1}});
 events.get("click")({latlng:{lat:49,lng:3},originalEvent:{detail:2}});
 events.get("dblclick")({latlng:{lat:49,lng:3}});
 assert.equal(points.length,2);assert.equal(ended,1);assert.equal(enabled,false);assert.equal(events.size,0);
});

// A: a complete Point must reach serializers in the same notification as a
// confirmed line. No backend writes or shared mutable fixtures (~1 s).
test("la notification du premier Point expose le remplacement confirmé",()=>{
 const observed=[];const h=harness(['Point'],null,draft=>observed.push({changed:draft.isChanged(),geometry:draft.draftGeometry()}));
 h.draft.start();h.click(3,49);
 assert.deepEqual(observed,[{changed:true,geometry:{type:'Point',coordinates:[3,49]}}]);
 assert.equal(h.draft.isDrawing(),false);h.draft.dispose();assert.equal(h.handlers.size,0);
});
