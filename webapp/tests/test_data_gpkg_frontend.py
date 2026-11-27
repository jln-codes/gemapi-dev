import json
from pathlib import Path
import shutil
import subprocess
import unittest


FRONTEND_DIRECTORY = Path(__file__).resolve().parents[1] / "frontend"


class DataGeoPackageFrontendTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = (FRONTEND_DIRECTORY / "data.html").read_text(encoding="utf-8")
        cls.data_script = (FRONTEND_DIRECTORY / "js" / "data-view.js").read_text(
            encoding="utf-8"
        )

    def test_export_controls_and_module_are_scoped_to_data(self):
        for identifier in (
            "data-view-export-csv", "data-view-export-gpkg",
            "data-view-export-bar",
        ):
            self.assertIn(f'id="{identifier}"', self.page)
        self.assertIn('src="/static/js/data-gpkg-export.js"', self.page)
        self.assertIn("onDatasetChange: setViewDataset", self.data_script)
        self.assertNotIn("leaflet", self.page.casefold())
        self.assertNotIn("map.js", self.page)

    def test_views_button_visibility_tracks_requested_geographic_classes(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible")
        module_path = FRONTEND_DIRECTORY / "js" / "data-gpkg-export.js"
        program = f'''const Export = require({json.dumps(str(module_path))});
const button = {{ disabled: false, hidden: false, listeners: {{}},
  addEventListener(name, callback) {{ this.listeners[name] = callback; }},
  closest() {{ return {{ hidden: false }}; }} }};
const status = {{ textContent: "" }};
const controller = Export.createController({{
  button, status, hideWhenUnavailable: true,
  fetchImpl: async () => {{ throw new Error("Ne doit pas être appelé"); }},
  documentTarget: {{}}, urlApi: {{}},
}});
const states = [button.hidden];
controller.setDataset({{ root: "digues", fields: [{{table: "digues", field: "libelle"}}], entityClasses: ["digues"] }});
states.push(button.hidden);
controller.setDataset({{ root: "desordres", fields: [{{table: "desordres", field: "designation"}}], entityClasses: ["desordres"] }});
states.push(button.hidden);
process.stdout.write(JSON.stringify({{ states, enabled: !button.disabled }}));'''
        result = json.loads(subprocess.check_output([node, "-e", program], text=True))
        self.assertEqual(result, {"states": [True, True, False], "enabled": True})

    def test_export_posts_the_view_definition_not_the_visible_page(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible")
        module_path = FRONTEND_DIRECTORY / "js" / "data-gpkg-export.js"
        program = f'''const Export = require({json.dumps(str(module_path))});
const button = {{ disabled: false, listeners: {{}},
  addEventListener(name, callback) {{ this.listeners[name] = callback; }},
  closest() {{ return {{ hidden: false }}; }} }};
const status = {{ textContent: "" }};
const link = {{ clickCount: 0, click() {{ this.clickCount += 1; }}, remove() {{}} }};
const requests = [];
const controller = Export.createController({{
  button, status,
  fetchImpl: async (url, options) => {{ requests.push({{url, options}}); return {{
    ok: true, headers: {{ get: () => "attachment; filename=vue.gpkg" }},
    blob: async () => ({{ size: 4 }}),
  }}; }},
  documentTarget: {{ createElement: () => link, body: {{ append() {{}} }} }},
  urlApi: {{ createObjectURL: () => "blob:test", revokeObjectURL() {{}} }},
}});
const dataset = {{
  root: "desordres",
  fields: [{{ table: "desordres", field: "designation" }}],
  hiddenTronconIds: ["t2"],
  entityClasses: ["desordres"],
  rows: [{{ values: ["Cette page ne doit pas partir"] }}],
}};
controller.setDataset(dataset);
(async () => {{ await controller.exportDataset(); process.stdout.write(JSON.stringify({{
  url: requests[0].url,
  method: requests[0].options.method,
  body: JSON.parse(requests[0].options.body),
  download: link.download,
  clicks: link.clickCount,
  status: status.textContent,
}})); }})();'''
        result = json.loads(subprocess.check_output([node, "-e", program], text=True))
        self.assertEqual(result["url"], "/api/data/export-gpkg")
        self.assertEqual(result["method"], "POST")
        self.assertEqual(result["body"]["root"], "desordres")
        self.assertEqual(result["body"]["fields"], [
            {"table": "desordres", "field": "designation"},
        ])
        self.assertEqual(result["body"]["hidden_troncon_ids"], ["t2"])
        self.assertEqual(result["body"]["entity_classes"], ["desordres"])
        self.assertNotIn("rows", result["body"])
        self.assertEqual(result["download"], "vue.gpkg")
        self.assertEqual(result["clicks"], 1)
        self.assertEqual(result["status"], "GeoPackage téléchargé.")

    def test_csv_export_uses_the_same_complete_view_definition(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible")
        module_path = FRONTEND_DIRECTORY / "js" / "data-gpkg-export.js"
        program = f'''const Export = require({json.dumps(str(module_path))});
const button = {{ disabled: false, listeners: {{}}, addEventListener(name, callback) {{ this.listeners[name] = callback; }} }};
const status = {{ textContent: "" }};
const link = {{ click() {{}}, remove() {{}} }};
const requests = [];
const controller = Export.createCsvController({{
  button, status,
  fetchImpl: async (url, options) => {{ requests.push({{url, options}}); return {{ ok: true, headers: {{ get: () => "attachment; filename=vue.csv" }}, blob: async () => ({{}}) }}; }},
  documentTarget: {{ createElement: () => link, body: {{ append() {{}} }} }},
  urlApi: {{ createObjectURL: () => "blob:test", revokeObjectURL() {{}} }},
}});
controller.setDataset({{ root: "photos", fields: [{{table: "photos", field: "date"}}], hiddenTronconIds: ["t1"], entityClasses: ["photos"] }});
(async () => {{ await controller.exportDataset(); process.stdout.write(JSON.stringify({{url: requests[0].url, body: JSON.parse(requests[0].options.body), status: status.textContent}})); }})();'''
        result = json.loads(subprocess.check_output([node, "-e", program], text=True))
        self.assertEqual(result["url"], "/api/data/export-csv")
        self.assertEqual(result["body"], {
            "root": "photos",
            "fields": [{"table": "photos", "field": "date"}],
            "hidden_troncon_ids": ["t1"],
        })
        self.assertEqual(result["status"], "CSV téléchargé.")


if __name__ == "__main__":
    unittest.main()
