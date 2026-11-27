import json
from pathlib import Path
import shutil
import subprocess
import unittest


FRONTEND_DIRECTORY = Path(__file__).resolve().parents[1] / "frontend"


class ReferenceDataFrontendTest(unittest.TestCase):
    def test_shared_catalog_module_is_loaded_by_both_pages(self):
        for page_name in ("map.html", "data.html"):
            page = (FRONTEND_DIRECTORY / page_name).read_text(encoding="utf-8")
            self.assertIn("reference-data.js", page)
            self.assertNotRegex(page, r'name="[^"]+_source_id"')

    def test_catalog_loading_is_cached_and_nullable_select_uses_labels(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible")
        module_path = FRONTEND_DIRECTORY / "js" / "reference-data.js"
        program = f'''const References = require({json.dumps(str(module_path))});
const calls = [];
const fetchJson = async (url) => {{
  calls.push(url);
  return {{ references: [
    {{ id: "r2", libelle: "Rive droite" }},
    {{ id: "r1", designation: "Rive gauche" }},
    {{ id: "r3", libelle: null, designation: null, valid: true }},
  ] }};
}};
class Element {{
  constructor() {{ this.children = []; this.value = ""; this.textContent = ""; }}
  replaceChildren() {{ this.children = []; }}
  append(child) {{ this.children.push(child); }}
}}
const documentTarget = {{ createElement: () => new Element() }};
const select = new Element(); select.ownerDocument = documentTarget;
const unavailableSelect = new Element(); unavailableSelect.ownerDocument = documentTarget;
(async () => {{
  const first = await References.load("rives", fetchJson);
  const second = await References.load("rives", fetchJson);
  References.fillSelect(select, first, null, "Aucune rive");
  References.fillSelect(
    unavailableSelect, first, "r0", "Aucune rive", "Ancienne rive",
  );
  process.stdout.write(JSON.stringify({{
    calls, same: first === second, value: select.value,
    options: select.children.map((item) => [item.value, item.textContent]),
    unavailable: {{
      value: unavailableSelect.value,
      options: unavailableSelect.children.map((item) => [item.value, item.textContent]),
    }},
    endpoint: References.CATALOGS.rives[0],
  }}));
}})();'''
        result = json.loads(subprocess.check_output([node, "-e", program], text=True))
        self.assertEqual(result["calls"], ["/api/referentiels/rives"])
        self.assertTrue(result["same"])
        self.assertEqual(result["value"], "")
        self.assertEqual(result["options"], [
            ["", "Aucune rive"], ["r2", "Rive droite"], ["r1", "Rive gauche"],
            ["r3", "r3"],
        ])
        self.assertEqual(result["unavailable"]["value"], "r0")
        self.assertEqual(
            result["unavailable"]["options"][-1],
            ["r0", "Ancienne rive (actuelle)"],
        )
        self.assertNotIn("r0", [
            label for _value, label in result["unavailable"]["options"]
        ])

    def test_failed_catalog_load_is_retriable_and_keeps_string_valid_ids(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible")
        module_path = FRONTEND_DIRECTORY / "js" / "reference-data.js"
        program = f'''const References = require({json.dumps(str(module_path))});
let attempts = 0;
const fetchJson = async () => {{
  attempts += 1;
  if (attempts === 1) throw new Error("indisponible");
  return {{ references: [
    {{ id: 12, libelle: "Douze", valid: true }},
    {{ id: 13, libelle: "Treize", valid: false }},
  ] }};
}};
(async () => {{
  let firstError = null;
  try {{ await References.load("positions", fetchJson); }}
  catch (error) {{ firstError = error.message; }}
  const rows = await References.load("positions", fetchJson);
  process.stdout.write(JSON.stringify({{ attempts, firstError, rows }}));
}})();'''
        result = json.loads(subprocess.check_output([node, "-e", program], text=True))
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(result["firstError"], "indisponible")
        self.assertEqual(result["rows"], [{
            "id": "12", "libelle": "Douze", "valid": True,
        }])

    def test_disorder_options_append_non_blank_abrege_without_changing_order_or_value(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible")
        module_path = FRONTEND_DIRECTORY / "js" / "reference-data.js"
        program = f'''const References = require({json.dumps(str(module_path))});
class Element {{
  constructor() {{ this.children = []; this.value = ""; this.textContent = ""; }}
  replaceChildren() {{ this.children = []; }}
  append(child) {{ this.children.push(child); }}
}}
const documentTarget = {{ createElement: () => new Element() }};
const payloads = {{
  categoriesDesordre: {{ categories: [
    {{ id: "c1", libelle: "Affaissement", abrege: " AFF " }},
    {{ id: "c2", libelle: "Brèche", abrege: null }},
    {{ id: "c3", libelle: "Glissement", abrege: "" }},
    {{ id: "c4", libelle: "Tassement", abrege: "   " }},
  ] }},
  typesDesordre: {{ types: [
    {{ id: "t1", categorie_id: "c1", libelle: "Alpha", abrege: "ZZZ" }},
    {{ id: "t2", categorie_id: "c1", libelle: "Bêta", abrege: "AAA" }},
  ] }},
}};
(async () => {{
  const result = {{}};
  for (const name of Object.keys(payloads)) {{
    const rows = await References.load(name, async () => payloads[name]);
    const select = new Element(); select.ownerDocument = documentTarget;
    References.fillSelect(select, rows, rows[0].id);
    result[name] = {{
      selected: select.value,
      options: select.children.slice(1).map((item) => [item.value, item.textContent]),
    }};
  }}
  process.stdout.write(JSON.stringify(result));
}})();'''
        result = json.loads(subprocess.check_output([node, "-e", program], text=True))
        self.assertEqual(result["categoriesDesordre"], {
            "selected": "c1",
            "options": [
                ["c1", "Affaissement — AFF"],
                ["c2", "Brèche"],
                ["c3", "Glissement"],
                ["c4", "Tassement"],
            ],
        })
        self.assertEqual(result["typesDesordre"], {
            "selected": "t1",
            "options": [["t1", "Alpha — ZZZ"], ["t2", "Bêta — AAA"]],
        })

    def test_heritage_panel_no_longer_owns_reference_based_editing(self):
        heritage = (FRONTEND_DIRECTORY / "js" / "heritage-panel.js").read_text(
            encoding="utf-8"
        )
        detail = (FRONTEND_DIRECTORY / "js" / "data-entity-detail.js").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("heritage-properties", heritage)
        self.assertNotIn('method: "PUT"', heritage)
        self.assertIn('referenceData.load(catalog, fetchJson)', detail)
        self.assertIn('method: "PUT"', detail)

    def test_map_preserves_current_type_until_category_is_changed(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible")
        map_path = FRONTEND_DIRECTORY / "js" / "map.js"
        program = f'''const fs = require("node:fs");
const source = fs.readFileSync({json.dumps(str(map_path))}, "utf8");
const functions = source.slice(
  source.indexOf("function fillTypeSelect("),
  source.indexOf("function filteredDesordreTronconOptions("),
);
const desordreTypes = [
  {{ id: "t1", libelle: "Type géotechnique", libelle_affichage: "Type géotechnique — GEO", categorie_id: "c1" }},
  {{ id: "t2", libelle: "Type hydraulique", libelle_affichage: "Type hydraulique — HYD", categorie_id: "c2" }},
  {{ id: "t3", libelle: "Type sans catégorie", categorie_id: null }},
];
const desordreCategories = [
  {{ id: "c1", libelle: "Géotechnique" }},
  {{ id: "c2", libelle: "Hydraulique" }},
];
const ReferenceData = require({json.dumps(str(FRONTEND_DIRECTORY / "js" / "reference-data.js"))});
const document = {{ createElement: () => ({{ value: "", textContent: "" }}) }};
const desordreCreateTypeReference = select();
const desordreCreateCategorieReference = select();
function select() {{ return {{
  children: [], _value: "", listeners: {{}},
  ownerDocument: document,
  get value() {{ return this._value; }},
  set value(value) {{
    this._value = this.children.some((item) => String(item.value) === String(value))
      ? String(value) : "";
  }},
  replaceChildren() {{ this.children = []; this._value = ""; }},
  append(item) {{ this.children.push(item); }},
  addEventListener(name, callback) {{ this.listeners[name] = callback; }},
}}; }}
function businessLabel(item, fallback = "Sans libellé") {{ return item?.libelle || fallback; }}
eval(functions);
fillDesordreReferenceOptions("t1", "c2", "Type géotechnique");
const initial = {{
  value: desordreCreateTypeReference.value,
  labels: desordreCreateTypeReference.children.map((item) => item.textContent),
}};
desordreCreateCategorieReference.value = "c2";
desordreCreateCategorieReference.listeners.change();
const afterCategoryChange = desordreCreateTypeReference.value;
const afterCategoryChangeOptions = desordreCreateTypeReference.children.map(
  (item) => [item.value, item.textContent],
);
fillDesordreReferenceOptions();
desordreCreateTypeReference.value = "t1";
desordreCreateTypeReference.listeners.change();
const automaticCategory = desordreCreateCategorieReference.value;
desordreCreateTypeReference.value = "t3";
desordreCreateTypeReference.listeners.change();
const categoryAfterUncategorizedType = desordreCreateCategorieReference.value;
fillDesordreReferenceOptions();
desordreCreateTypeReference.value = "t3";
desordreCreateTypeReference.listeners.change();
process.stdout.write(JSON.stringify({{
  initial,
  afterCategoryChange,
  afterCategoryChangeOptions,
  automaticCategory,
  categoryAfterUncategorizedType,
  categoryFromUncategorizedType: desordreCreateCategorieReference.value,
}}));'''
        result = json.loads(subprocess.check_output([node, "-e", program], text=True))
        self.assertEqual(result["initial"]["value"], "t1")
        self.assertTrue(any(
            "hors catalogue/catégorie" in label for label in result["initial"]["labels"]
        ))
        self.assertTrue(any(
            "Type géotechnique — GEO" in label for label in result["initial"]["labels"]
        ))
        self.assertEqual(result["afterCategoryChange"], "")
        self.assertEqual(
            result["afterCategoryChangeOptions"],
            [["", "Sans type"], ["t2", "Type hydraulique — HYD"]],
        )
        self.assertEqual(result["automaticCategory"], "c1")
        self.assertEqual(result["categoryAfterUncategorizedType"], "c1")
        self.assertEqual(result["categoryFromUncategorizedType"], "")


if __name__ == "__main__":
    unittest.main()
