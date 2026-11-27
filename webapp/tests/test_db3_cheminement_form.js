const assert = require("node:assert/strict");
const test = require("node:test");
const CheminementForm = require("../frontend/js/cheminement-form.js");

test("les champs métier DB-2 du Cheminement passent du formulaire au DTO", () => {
  const descriptor = CheminementForm.composition();
  const names = Object.values(descriptor.fields).flat().map(field => field.name);
  const elements = Object.fromEntries(names.map((name) => [name, { value: "", checked: true }]));
  elements.type_cheminement_id.value = "CAC";
  elements.statut_acces.value = "PRIVE";
  elements.materiau_id.value = "M-1";
  elements.position_haut_id.value = "P-1";
  elements.source_id.value = "S-1";
  const payload = CheminementForm.payload({ elements }, {}, { values: () => [] });
  assert.equal(payload.type_cheminement_id, "CAC");
  assert.equal(payload.statut_acces, "PRIVE");
  assert.equal(payload.materiau_id, "M-1");
  assert.equal(payload.position_haut_id, "P-1");
  assert.equal(payload.source_id, "S-1");
  assert.equal(payload.revetement_bas_id, null);
  assert.deepEqual(payload.troncon_ids, []);
});
