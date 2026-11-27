import json
from pathlib import Path
import shutil
import subprocess
import unittest


PROJECT_DIRECTORY = Path(__file__).resolve().parents[2]
MAP_SCRIPT = PROJECT_DIRECTORY / "webapp" / "frontend" / "js" / "map.js"


class HeritageFormFilterTest(unittest.TestCase):
    def test_disorder_troncon_choices_follow_existing_territorial_selection(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible")
        program = r'''
const fs = require("node:fs");
const RelationSelector = require("./webapp/frontend/js/relation-selector.js");
const source = fs.readFileSync("./webapp/frontend/js/map.js", "utf8");
const functions = source.slice(
  source.indexOf("function filteredDesordreTronconOptions"),
  source.indexOf("function selectedDesordreMode"),
);
const desordreTronconOptions = [
  { id: "t1", libelle: "T1", digue_id: "d1", digue_libelle: "Digue 1" },
  { id: "t2", libelle: "T2", digue_id: "d1", digue_libelle: "Digue 1" },
  { id: "t3", libelle: "T3", digue_id: "d2", digue_libelle: "Digue 2" },
  { id: "t4", libelle: "T4", digue_id: "d3", digue_libelle: "Digue 3" },
];
const hiddenTronconIds = new Set();
const document = { createElement() { return { value: "", textContent: "", selected: false }; } };
function businessLabel(item, fallback) { return item.libelle || fallback; }
function select() { return {
  children: [], multiple: true, size: 5, ownerDocument: document,
  replaceChildren() { this.children = []; },
  append(option) { this.children.push(option); },
}; }
let selectedHeritageObject = null;
const desordreCreateGeometryType = { value: "Point" };
const desordreCreateTroncons = select();
eval(functions);

function setVisible(...ids) {
  hiddenTronconIds.clear();
  desordreTronconOptions.forEach((item) => {
    if (!ids.includes(String(item.id))) hiddenTronconIds.add(String(item.id));
  });
}
function values(element) {
  return element.children.map((option) => String(option.value)).filter(Boolean);
}
function selectedValues(element) {
  return element.children.filter((option) => option.selected).map((option) => String(option.value));
}
function render(selectedIds = [], settings = {}) {
  const element = select();
  fillTronconSelect(element, selectedIds, settings);
  return element;
}

hiddenTronconIds.clear();
const noFilter = values(render());
setVisible("t1", "t2", "t3"); // système 1 actif
const system = values(render());
setVisible("t1", "t2"); // digue 1 active
const digue = values(render());
setVisible("t1", "t3"); // deux tronçons actifs, issus de digues différentes
const troncons = values(render());
setVisible(); // toutes les cases ont été explicitement désactivées
const explicitEmptySelection = values(render());

setVisible("t1", "t3");
selectedHeritageObject = { kind: "Tronçon", item: { id: "t1" } };
fillDesordreTronconOptions();
const creation = {
  values: values(desordreCreateTroncons),
  selected: selectedValues(desordreCreateTroncons),
};
const editInside = render(["t1"], {
  multiple: false,
  includeSelectedOutsideFilter: true,
});
const editOutside = render(["t2"], {
  multiple: false,
  includeSelectedOutsideFilter: true,
});
const outsideOption = editOutside.children.find((option) => option.value === "t2");

process.stdout.write(JSON.stringify({
  noFilter,
  system,
  digue,
  troncons,
  explicitEmptySelection,
  creation,
  editInside: {
    values: values(editInside),
    selected: selectedValues(editInside),
  },
  editOutside: {
    values: values(editOutside),
    selected: selectedValues(editOutside),
    label: outsideOption?.textContent,
  },
}));
'''
        result = json.loads(
            subprocess.check_output(
                [node, "-e", program], cwd=PROJECT_DIRECTORY, text=True
            )
        )
        self.assertEqual(result["noFilter"], ["t1", "t2", "t3", "t4"])
        self.assertEqual(result["system"], ["t1", "t2", "t3"])
        self.assertEqual(result["digue"], ["t1", "t2"])
        self.assertEqual(result["troncons"], ["t1", "t3"])
        self.assertEqual(result["explicitEmptySelection"], [])
        self.assertEqual(result["creation"]["values"], ["t1", "t3"])
        self.assertEqual(result["creation"]["selected"], ["t1"])
        self.assertEqual(result["editInside"]["values"], ["t1", "t3"])
        self.assertEqual(result["editInside"]["selected"], ["t1"])
        self.assertEqual(result["editOutside"]["values"], ["t1", "t2", "t3"])
        self.assertEqual(result["editOutside"]["selected"], ["t2"])
        self.assertIn("rattachement actuel hors sélection", result["editOutside"]["label"])

    def test_all_disorder_editors_preserve_persisted_outside_links(self):
        script = MAP_SCRIPT.read_text(encoding="utf-8")
        # Three disorder geometry renderers use the selector. The canonical
        # Tronçon editor has its own optional-parent contract, not a fourth
        # disorder relation control. Each renderer is checked below, and the
        # executable selector test above proves preservation outside the filter.
        polygon_renderer = script.split("function renderPolygonServerFeature", 1)[1]
        polygon_renderer = polygon_renderer.split("function stopGraphicEdit", 1)[0]
        point_renderer = script.split("function renderPointServerFeature", 1)[1]
        point_renderer = point_renderer.split("function lineReperageSummary", 1)[0]
        line_renderer = script.split("function renderLineServerFeature", 1)[1]
        line_renderer = line_renderer.split("function lineBornageDraftModified", 1)[0]
        for renderer in (polygon_renderer, point_renderer, line_renderer):
            self.assertIn("includeSelectedOutsideFilter: true", renderer)


if __name__ == "__main__":
    unittest.main()
