const assert = require("node:assert/strict");
const test = require("node:test");
const CheminementEditor = require("../frontend/js/cheminement-editor.js");

test("seul le type canonique CAC autorise Polygon", () => {
  assert.deepEqual(CheminementEditor.allowedGeometryTypes("CAC"), ["LineString", "Polygon"]);
  for (const typeId of ["VAC", "CHE", "AUT", "", null]) {
    assert.deepEqual(CheminementEditor.allowedGeometryTypes(typeId), ["LineString"]);
  }
});

test("le dessin polygonal ferme le contour sans convertir la géométrie", () => {
  const geometry = CheminementEditor.geometryFromVertices("Polygon", [
    { lng: 2, lat: 48 }, { lng: 3, lat: 48 }, { lng: 3, lat: 49 },
  ]);
  assert.deepEqual(geometry, {
    type: "Polygon",
    coordinates: [[[2, 48], [3, 48], [3, 49], [2, 48]]],
  });
});
