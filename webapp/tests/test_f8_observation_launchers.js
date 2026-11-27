const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const ObservationForm = require("../frontend/js/observation-form.js");

function parentFromEditor(filename, settings = {}) {
  const source = fs.readFileSync(path.join(__dirname, "../frontend/js", filename), "utf8");
  const match = source.match(/observationCreate\.open\(\{([\s\S]*?)\},\s*\(\) => loadObservations\((?:properties|p)\.id\)\);/);
  assert.ok(match, filename);
  const properties = { id: "parent-uuid", designation: "Objet F8" };
  return new Function("properties", "settings", "const p = properties; return ({" + match[1] + "});")(
    properties, settings,
  );
}

test("every level A editor supplies the exact Observation parent and UUID", () => {
  const cases = [
    ["mobilier-editor.js", { resource: "mobilier" }, "mobilier"],
    ["mobilier-editor.js", {
      resource: "equipements-mesure", observationResource: "equipements_mesure",
    }, "equipements_mesure"],
    ["reseau-technique-editor.js", {}, "reseaux_techniques"],
    ["ouvrage-particulier-editor.js", {}, "ouvrages_particuliers"],
    ["cheminement-editor.js", {}, "cheminements"],
    ["vegetation-editor.js", {}, "vegetation"],
    ["amenagement-hydraulique-editor.js", {}, "amenagements_hydrauliques"],
    ["zone-stockage-editor.js", {}, "zones_stockage"],
    ["prestation-editor.js", {}, "prestations"],
  ];
  for (const [filename, settings, resource] of cases) {
    const parent = parentFromEditor(filename, settings);
    assert.equal(parent.resource, resource, filename);
    assert.equal(parent.id, "parent-uuid", filename);
    assert.equal(ObservationForm.parent({ parent }).resource, resource, filename);
  }
});
