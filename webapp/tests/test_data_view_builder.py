import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest


FRONTEND_DIRECTORY = Path(__file__).resolve().parents[1] / "frontend"


class DataViewBuilderFrontendTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = (FRONTEND_DIRECTORY / "data.html").read_text(encoding="utf-8")
        cls.script = (FRONTEND_DIRECTORY / "js" / "data-view-builder.js").read_text(
            encoding="utf-8"
        )
        cls.data_script = (FRONTEND_DIRECTORY / "js" / "data-view.js").read_text(
            encoding="utf-8"
        )
        cls.css = (FRONTEND_DIRECTORY / "css" / "app.css").read_text(
            encoding="utf-8"
        )

    def test_builder_structure_replaces_the_views_placeholder(self):
        for identifier in (
            "data-view-builder-workspace",
            "data-view-root",
            "data-view-table",
            "data-view-field",
            "data-view-add-field",
            "data-view-field-list",
            "data-view-resizer",
            "data-view-result-table",
        ):
            self.assertIn(f'id="{identifier}"', self.page)
        self.assertNotIn('id="data-placeholder"', self.page)
        self.assertIn("Ajoutez un champ pour construire la vue.", self.page)
        self.assertIn('role="separator"', self.page)
        self.assertIn('aria-orientation="horizontal"', self.page)
        self.assertNotIn("leaflet", self.page.casefold())
        self.assertNotIn("map.js", self.page)

    def test_builder_uses_shared_table_style_and_remains_beside_ai(self):
        self.assertEqual(self.page.count('class="data-grid-table"'), 1)
        self.assertIn(
            'class="data-view-result-scroll data-grid-scroll"', self.page
        )
        self.assertIn(".data-grid-table", self.css)
        self.assertNotIn(".data-view-result .data-grid-table", self.css)
        self.assertNotIn(".data-view-result > .data-grid-scroll", self.css)
        self.assertLess(
            self.page.index('id="data-view-builder-workspace"'),
            self.page.index('id="ai-panel"'),
        )
        self.assertIn(
            'main.data-page[data-layout="single-tool"].ai-open .data-view-builder-workspace',
            self.css,
        )

    def test_workspace_has_a_real_resizable_vertical_split(self):
        def rule(selector):
            match = re.search(rf"(?m)^{re.escape(selector)}\s*\{{([^}}]+)\}}", self.css)
            self.assertIsNotNone(match)
            return match.group(1)

        workspace_rule = rule(".data-view-builder-workspace")
        builder_rule = rule(".data-view-builder")
        resizer_rule = rule(".data-view-resizer")
        result_rule = rule(".data-view-result")

        self.assertIn("display: flex", workspace_rule)
        self.assertIn("flex-direction: column", workspace_rule)
        self.assertIn("height: 100%", workspace_rule)
        self.assertIn("flex: 0 0 var(--data-view-builder-height)", builder_rule)
        self.assertIn("min-height: 5.5rem", builder_rule)
        self.assertIn("flex: 0 0 0.5rem", resizer_rule)
        self.assertIn("cursor: row-resize", resizer_rule)
        self.assertIn("flex: 1 1 0", result_rule)
        self.assertIn("min-height: 7.5rem", result_rule)

        builder_position = self.page.index('class="data-view-builder"')
        resizer_position = self.page.index('id="data-view-resizer"')
        result_position = self.page.index('class="data-view-result"')
        self.assertLess(builder_position, resizer_position)
        self.assertLess(resizer_position, result_position)

    def test_catalog_selection_order_filter_and_resize_helpers(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible")
        module_path = FRONTEND_DIRECTORY / "js" / "data-view-builder.js"
        program = f'''const Builder = require({json.dumps(str(module_path))});
const catalog = Builder.normalizeCatalog({{
  default_root: "desordres",
  roots: [{{ id: "troncons", label: "Tronçons" }}, {{ id: "desordres", label: "Désordres" }}],
  tables: [
  {{ id: "desordres", label: "Désordres", fields: [
    {{ id: "designation", label: "Désignation", type: "text" }},
    {{ id: "type", label: "Type", type: "text" }}
  ] }},
  {{ id: "troncons", label: "Tronçons", fields: [
    {{ id: "designation", label: "Désignation", type: "text" }}
  ] }}
] }});
let selected = Builder.addSelectedField([], "desordres", "type");
selected = Builder.addSelectedField(selected, "troncons", "designation");
selected = Builder.addSelectedField(selected, "desordres", "designation");
const ordered = selected.map((field) => `${{field.table}}.${{field.field}}`);
const remainingDisorderFields = Builder.availableFields(catalog, "desordres", selected);
selected = Builder.removeSelectedField(selected, 1);
process.stdout.write(JSON.stringify({{
  roots: catalog.roots.map((root) => root.id),
  tableFields: catalog.tables.map((table) => [table.id, table.fields.map((field) => field.id)]),
  ordered,
  afterRemoval: selected.map((field) => `${{field.table}}.${{field.field}}`),
  remainingDisorderFields,
  low: Builder.clampBuilderHeight(10, 500),
  high: Builder.clampBuilderHeight(490, 500),
}}));'''
        result = json.loads(subprocess.check_output([node, "-e", program], text=True))

        self.assertEqual(
            result["roots"], ["troncons", "desordres"],
        )
        self.assertEqual(
            result["tableFields"],
            [["desordres", ["designation", "type"]], ["troncons", ["designation"]]],
        )
        self.assertEqual(
            result["ordered"],
            ["desordres.type", "troncons.designation", "desordres.designation"],
        )
        self.assertEqual(
            result["afterRemoval"], ["desordres.type", "desordres.designation"]
        )
        self.assertEqual(result["remainingDisorderFields"], [])
        self.assertEqual(result["low"], 88)
        self.assertEqual(result["high"], 372)

    def test_add_remove_query_and_heritage_wiring_are_explicit(self):
        self.assertIn('fetchJson("/api/data/view-catalog")', self.script)
        self.assertIn('fetchJson("/api/data/view-query"', self.script)
        self.assertIn(
            "hidden_troncon_ids: [...activeDefinition.hiddenTronconIds]",
            self.script,
        )
        self.assertIn("page_size: PAGE_SIZE", self.script)
        self.assertIn('querySelector(".data-view-result-scroll")', self.script)
        self.assertNotIn('id="data-view-previous"', self.page)
        self.assertNotIn('id="data-view-next"', self.page)
        self.assertNotIn("Précédente", self.page)
        self.assertNotIn("Suivante", self.page)
        self.assertIn("setHiddenTronconIds(nextHiddenTronconIds)", self.data_script)
        self.assertIn('viewBuilderController.setOpen(centralMode === "views")', self.data_script)

    def test_controller_adds_removes_and_refreshes_columns_in_order(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible")
        module_path = FRONTEND_DIRECTORY / "js" / "data-view-builder.js"
        program = f'''const Builder = require({json.dumps(str(module_path))});
class Element {{
  constructor(tag = "div") {{
    this.tag = tag; this.children = []; this.dataset = {{}}; this.hidden = false;
    this.listeners = {{}}; this.properties = {{}}; this.textContent = ""; this.value = "";
    this.style = {{ setProperty: (name, value) => {{ this.properties[name] = value; }} }};
  }}
  append(...children) {{
    this.children.push(...children);
    if (this.tag === "select" && !this.value && children[0]) this.value = children[0].value;
  }}
  replaceChildren(...children) {{ this.children = []; this.value = ""; this.append(...children); }}
  addEventListener(name, callback) {{ this.listeners[name] = callback; }}
  setAttribute(name, value) {{ this[name] = String(value); }}
  getBoundingClientRect() {{ return {{ height: 144 }}; }}
}}
const elements = {{
  "#data-view-builder-workspace": new Element(), ".data-view-builder": new Element(),
  "#data-view-root": new Element("select"),
  "#data-view-table": new Element("select"), "#data-view-field": new Element("select"),
  "#data-view-add-field": new Element("button"), "#data-view-field-list": new Element(),
  "#data-view-resizer": new Element(), "#data-view-result-table": new Element("table"),
  "#data-view-result-head": new Element("tr"), "#data-view-result-body": new Element("tbody"),
  "#data-view-result-empty": new Element(), "#data-view-result-status": new Element(),
  "#data-view-export-bar": new Element(),
  ".data-view-result-scroll": new Element(),
}};
elements["#data-view-builder-workspace"].clientHeight = 500;
const root = {{
  ownerDocument: {{ createElement: (tag) => new Element(tag) }},
  querySelector: (selector) => elements[selector] || null,
}};
const requests = [];
const fetchJson = async (url, options = {{}}) => {{
  if (url.endsWith("view-catalog")) return {{
    default_root: "desordres",
    roots: [{{ id: "troncons", label: "Tronçons" }}, {{ id: "desordres", label: "Désordres" }}],
    tables: [{{
    id: "desordres", label: "Désordres", fields: [
      {{ id: "designation", label: "Désignation", type: "text" }},
      {{ id: "type", label: "Type", type: "text" }}
    ]
  }}, {{
    id: "troncons", label: "Tronçons", fields: [
      {{ id: "libelle", label: "Libellé", type: "text" }}
    ]
  }}] }};
  const request = JSON.parse(options.body);
  const fields = request.fields;
  requests.push({{
    root: request.root,
    fields: fields.map((field) => `${{field.table}}.${{field.field}}`),
    hidden: request.hidden_troncon_ids,
    offset: request.offset,
    pageSize: request.page_size,
  }});
  return {{
    columns: fields.map((field) => ({{
      key: `${{field.table}}.${{field.field}}`, label: field.field, type: "text",
      source_class: field.table, source_field: field.field,
      navigable: field.field === "designation" || field.field === "libelle",
    }})),
    rows: [{{ entities: [
      {{ class: "desordres", id: "d1", attributes: {{ designation: "designation" }} }},
      {{ class: "troncons", id: "t1", attributes: {{ designation: "T1" }} }},
    ], values: fields.map((field) => field.field) }}],
    total: 1, offset: request.offset, page_size: request.page_size,
    has_previous: false, has_next: false,
  }};
}};
const controller = Builder.createController({{ rootElement: root, fetchJson }});
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
(async () => {{
  await controller.setOpen(true);
  const initialRoot = elements["#data-view-root"].value;
  elements["#data-view-resizer"].listeners.pointerdown({{
    clientY: 100, pointerId: 1, preventDefault() {{}},
  }});
  elements["#data-view-resizer"].listeners.pointermove({{ clientY: 156 }});
  elements["#data-view-resizer"].listeners.pointerup({{ pointerId: 1 }});
  elements["#data-view-add-field"].listeners.click();
  await tick();
  elements["#data-view-add-field"].listeners.click();
  await tick();
  const headersAfterAdd = elements["#data-view-result-head"].children.map((item) => item.textContent);
  elements["#data-view-field-list"].children[0].children[0].listeners.click();
  await tick();
  process.stdout.write(JSON.stringify({{
    requests,
    initialRoot,
    tableAfterRootChange: elements["#data-view-table"].value,
    headersAfterAdd,
    headersAfterRemove: elements["#data-view-result-head"].children.map((item) => item.textContent),
    valuesAfterRemove: elements["#data-view-result-body"].children[0].children.map((item) => item.textContent),
    resizedHeight: elements["#data-view-builder-workspace"].properties["--data-view-builder-height"],
    resizeAriaValue: elements["#data-view-resizer"]["aria-valuenow"],
  }}));
}})();'''
        result = json.loads(subprocess.check_output([node, "-e", program], text=True))

        self.assertEqual(result["requests"], [
                {"root": "desordres", "fields": ["desordres.designation"], "hidden": [], "offset": 0, "pageSize": 500},
                {"root": "desordres", "fields": ["desordres.designation", "desordres.type"], "hidden": [], "offset": 0, "pageSize": 500},
                {"root": "desordres", "fields": ["desordres.type"], "hidden": [], "offset": 0, "pageSize": 500},
        ])
        self.assertEqual(result["initialRoot"], "")
        self.assertEqual(result["tableAfterRootChange"], "desordres")
        self.assertEqual(result["headersAfterAdd"], ["designation", "type"])
        self.assertEqual(result["headersAfterRemove"], ["type"])
        self.assertEqual(result["valuesAfterRemove"], ["type"])
        self.assertEqual(result["resizedHeight"], "200px")
        self.assertEqual(result["resizeAriaValue"], "200")

    def test_scroll_loads_and_concatenates_once_then_view_change_resets(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible")
        module_path = FRONTEND_DIRECTORY / "js" / "data-view-builder.js"
        program = f'''const Builder = require({json.dumps(str(module_path))});
class Element {{
  constructor(tag = "div") {{
    this.tag = tag; this.children = []; this.dataset = {{}}; this.hidden = false;
    this.listeners = {{}}; this.textContent = ""; this.value = ""; this.scrollTop = 0;
    this.scrollHeight = 1000; this.clientHeight = 400;
    this.style = {{ setProperty() {{}} }};
  }}
  append(...children) {{ this.children.push(...children); if (this.tag === "select" && !this.value && children[0]) this.value = children[0].value; }}
  replaceChildren(...children) {{ this.children = []; this.value = ""; this.append(...children); }}
  addEventListener(name, callback) {{ this.listeners[name] = callback; }}
  setAttribute(name, value) {{ this[name] = String(value); }}
  getBoundingClientRect() {{ return {{ height: 144 }}; }}
}}
const elements = {{
  "#data-view-builder-workspace": new Element(), ".data-view-builder": new Element(),
  "#data-view-root": new Element("select"), "#data-view-table": new Element("select"),
  "#data-view-field": new Element("select"), "#data-view-add-field": new Element("button"),
  "#data-view-field-list": new Element(), "#data-view-resizer": new Element(),
  "#data-view-result-table": new Element("table"), "#data-view-result-head": new Element("tr"),
  "#data-view-result-body": new Element("tbody"), "#data-view-result-empty": new Element(),
  "#data-view-result-status": new Element(), "#data-view-export-bar": new Element(),
  ".data-view-result-scroll": new Element(),
}};
elements["#data-view-builder-workspace"].clientHeight = 500;
const root = {{ ownerDocument: {{ createElement: (tag) => new Element(tag) }}, querySelector: (selector) => elements[selector] || null }};
const requests = [];
let resolveSecond;
let deferSecond = true;
let updated = false;
const response = (request) => ({{
  root: "desordres", granularity: "desordres",
  columns: request.fields.map((field) => ({{key: `${{field.table}}.${{field.field}}`, label: field.field, source_class: field.table, source_field: field.field}})),
  rows: [{{ values: [updated ? (request.offset ? "second updated" : "first updated")
    : (request.offset ? "second" : "first")], entities: [] }}],
  total: 2, offset: request.offset, page_size: 500,
  has_next: request.offset === 0,
}});
const fetchJson = async (url, options = {{}}) => {{
  if (url.endsWith("view-catalog")) return {{
    default_root: "desordres", roots: [{{id: "desordres", label: "Désordres"}}],
    tables: [{{id: "desordres", label: "Désordres", fields: [
      {{id: "designation", label: "Désignation"}}, {{id: "type", label: "Type"}},
    ]}}],
  }};
  const request = JSON.parse(options.body); requests.push(request);
  if (request.offset === 1 && deferSecond) {{
    deferSecond = false;
    return new Promise((resolve) => {{ resolveSecond = () => resolve(response(request)); }});
  }}
  return response(request);
}};
const controller = Builder.createController({{rootElement: root, fetchJson}});
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
(async () => {{
  await controller.setOpen(true);
  elements["#data-view-add-field"].listeners.click(); await tick();
  const firstValues = elements["#data-view-result-body"].children.map((row) => row.children[0].textContent);
  const scroller = elements[".data-view-result-scroll"];
  scroller.scrollTop = 500;
  scroller.listeners.scroll(); scroller.listeners.scroll();
  await tick();
  const pendingCount = requests.filter((request) => request.offset === 1).length;
  resolveSecond(); await tick(); await tick();
  const accumulatedValues = elements["#data-view-result-body"].children.map((row) => row.children[0].textContent);
  scroller.listeners.scroll(); await tick();
  const countAfterLastPage = requests.length;
  updated = true;
  const invalidated = await controller.invalidate({{ resource: "desordres" }});
  const refreshedValues = elements["#data-view-result-body"].children.map((row) => row.children[0].textContent);
  const scrollAfterRefresh = scroller.scrollTop;
  const requestsAfterRefresh = requests.length;
  elements["#data-view-add-field"].listeners.click(); await tick();
  const resetValues = elements["#data-view-result-body"].children.map((row) => row.children[0].textContent);
  process.stdout.write(JSON.stringify({{
    firstValues, pendingCount, accumulatedValues, countAfterLastPage,
    invalidated, refreshedValues, scrollAfterRefresh, requestsAfterRefresh,
    finalRequestCount: requests.length, resetOffset: requests.at(-1).offset,
    resetValues, pageSizes: requests.map((request) => request.page_size),
    status: elements["#data-view-result-status"].textContent,
  }}));
}})();'''
        result = json.loads(subprocess.check_output([node, "-e", program], text=True))

        self.assertEqual(result["firstValues"], ["first"])
        self.assertEqual(result["pendingCount"], 1)
        self.assertEqual(result["accumulatedValues"], ["first", "second"])
        self.assertEqual(result["countAfterLastPage"], 2)
        self.assertTrue(result["invalidated"])
        self.assertEqual(result["refreshedValues"], ["first updated", "second updated"])
        self.assertEqual(result["scrollAfterRefresh"], 500)
        self.assertEqual(result["requestsAfterRefresh"], 4)
        self.assertEqual(result["finalRequestCount"], 5)
        self.assertEqual(result["resetOffset"], 0)
        self.assertEqual(result["resetValues"], ["first updated"])
        self.assertEqual(result["pageSizes"], [500, 500, 500, 500, 500])
        self.assertEqual(result["status"], "1 ligne(s) chargée(s) sur 2")


if __name__ == "__main__":
    unittest.main()
