"""Actions de rapports PDF Désordre : module partagé et branchements UI.

Les scénarios JavaScript s'exécutent sous Node avec un DOM et un transport
factices : aucun réseau, aucun serveur. Les contrôles Python vérifient les
points d'intégration (pages, service worker, câblage Carte/Base).
"""

import json
from pathlib import Path
import shutil
import subprocess
import unittest


FRONTEND_DIRECTORY = Path(__file__).resolve().parents[1] / "frontend"
MODULE = FRONTEND_DIRECTORY / "js" / "disorder-reports.js"

HARNESS = r'''
const Reports = require(__MODULE__);
function element(tag, text) {
  return {
    tagName: tag, textContent: text || "", hidden: false, disabled: false,
    dataset: {}, children: [], listeners: {}, attributes: {},
    classList: {
      classes: new Set(),
      toggle(name, on) { on ? this.classes.add(name) : this.classes.delete(name); },
      contains(name) { return this.classes.has(name); },
    },
    setAttribute(name, value) { this.attributes[name] = String(value); },
    removeAttribute(name) { delete this.attributes[name]; },
    addEventListener(name, callback) { this.listeners[name] = callback; },
    append(...items) { this.children.push(...items); },
    remove() { this.removed = true; },
  };
}
function statusElement(messages) {
  let value = "";
  return {
    classList: {
      classes: new Set(),
      toggle(name, on) { on ? this.classes.add(name) : this.classes.delete(name); },
      contains(name) { return this.classes.has(name); },
    },
    get textContent() { return value; },
    set textContent(next) { value = next; messages.push(next); },
  };
}
function response(options) {
  const settings = options || {};
  const headers = settings.headers || {};
  return {
    ok: settings.ok !== false,
    status: settings.status || 200,
    headers: { get: (name) => headers[String(name).toLowerCase()] ?? headers[name] ?? null },
    json: async () => {
      if (settings.json === undefined) throw new Error("réponse sans JSON");
      return settings.json;
    },
    blob: async () => ({ size: 8 }),
  };
}
function downloadDocument() {
  const downloads = [];
  const documentTarget = {
    createElement(tag) {
      const link = element(tag);
      link.click = () => { downloads.push(link.download); };
      return link;
    },
    body: { append() {} },
  };
  return { documentTarget, downloads, urlApi: { createObjectURL: () => "blob:test", revokeObjectURL() {} } };
}
(async () => {
__SCENARIO__
})().catch((error) => { console.error(error); process.exit(1); });
'''


def run_scenario(scenario: str):
    node = shutil.which("node")
    if node is None:
        raise unittest.SkipTest("Node.js indisponible")
    program = HARNESS.replace("__MODULE__", json.dumps(str(MODULE))).replace(
        "__SCENARIO__", scenario
    )
    return json.loads(subprocess.check_output([node, "-e", program], text=True))


class DisorderReportsModuleTest(unittest.TestCase):
    def test_endpoints_and_summary_are_pure(self):
        result = run_scenario(r'''
const endpoints = [
  Reports.catalogueEndpoint("Système d'endiguement", "se-1"),
  Reports.catalogueEndpoint("Digue", "dg-1"),
  Reports.catalogueEndpoint("Tronçon", "tr-1"),
  Reports.catalogueEndpoint("Autre", "x"),
  Reports.catalogueEndpoint("Digue", ""),
];
const summaries = [
  Reports.catalogueSummary({ entry_count: 12, digue_count: 2, troncon_count: 5 }),
  Reports.catalogueSummary({ entry_count: 1, digue_count: 1, troncon_count: 1 }),
  Reports.catalogueSummary({}),
];
process.stdout.write(JSON.stringify({ endpoints, summaries,
  pdfUrl: Reports.disorderPdfUrl("d/1") }));''')
        self.assertEqual(
            result["endpoints"],
            [
                {
                    "json": "/api/systemes-endiguement/se-1/desordres/catalogue",
                    "pdf": "/api/systemes-endiguement/se-1/desordres/catalogue/pdf",
                },
                {
                    "json": "/api/digues/dg-1/desordres/catalogue",
                    "pdf": "/api/digues/dg-1/desordres/catalogue/pdf",
                },
                {
                    "json": "/api/troncons/tr-1/desordres/catalogue",
                    "pdf": "/api/troncons/tr-1/desordres/catalogue/pdf",
                },
                None,
                None,
            ],
        )
        self.assertEqual(
            result["summaries"],
            [
                "12 désordres · 2 digues · 5 tronçons",
                "1 désordre · 1 digue · 1 tronçon",
                "0 désordre · 0 digue · 0 tronçon",
            ],
        )
        self.assertEqual(result["pdfUrl"], "/api/desordres/d%2F1/pdf")

    def test_download_name_prefers_the_utf8_filename(self):
        result = run_scenario(r'''
const encoded = Reports.downloadName({ headers: { get: () => "attachment; filename=\"desordre_X_Y.pdf\"; filename*=UTF-8''desordre_Tron%C3%A7on_%C3%89rosion.pdf" } });
const plain = Reports.downloadName({ headers: { get: () => "attachment; filename=digue.gpkg" } });
const fallback = Reports.downloadName({ headers: { get: () => "" } }, "catalogue-desordres.pdf");
process.stdout.write(JSON.stringify({ encoded, plain, fallback }));''')
        self.assertEqual(result["encoded"], "desordre_Tronçon_Érosion.pdf")
        self.assertEqual(result["plain"], "digue.gpkg")
        self.assertEqual(result["fallback"], "catalogue-desordres.pdf")

    def test_catalogue_runs_json_preflight_then_pdf(self):
        result = run_scenario(r'''
const button = element("button", "Outils");
const messages = [];
const status = statusElement(messages);
const { documentTarget, downloads, urlApi } = downloadDocument();
const requests = [];
const fetchImpl = async (url, options) => {
  requests.push({ url, accept: options?.headers?.Accept });
  if (url.endsWith("/catalogue")) {
    return response({ json: { entry_count: 12, digue_count: 2, troncon_count: 5 } });
  }
  return response({ headers: { "content-disposition": "attachment; filename=\"Desordres_SE Carbonade.pdf\"; filename*=UTF-8''Desordres_SE%20Carbonade.pdf" } });
};
const invoked = [];
const controller = Reports.createCatalogueController({
  button, status, fetchImpl,
  getScope: () => ({ kind: "Système d'endiguement", item: { id: "se-1", label: "SE Carbonade" } }),
  documentTarget, urlApi,
  onInvoke: () => invoked.push(true),
});
const promise = controller.run();
const during = { disabled: button.disabled, busy: button.attributes["aria-busy"], status: status.textContent };
await promise;
process.stdout.write(JSON.stringify({ requests, downloads, invoked, during,
  messages, disabled: button.disabled, busy: button.attributes["aria-busy"] ?? null,
  busyLabelRestored: button.textContent }));''')
        self.assertEqual(
            [request["url"] for request in result["requests"]],
            [
                "/api/systemes-endiguement/se-1/desordres/catalogue",
                "/api/systemes-endiguement/se-1/desordres/catalogue/pdf",
            ],
        )
        self.assertEqual(result["downloads"], ["Desordres_SE Carbonade.pdf"])
        self.assertEqual(result["invoked"], [True])
        self.assertTrue(result["during"]["disabled"])
        self.assertEqual(result["during"]["busy"], "true")
        self.assertTrue(
            any("12 désordres · 2 digues · 5 tronçons" in message for message in result["messages"])
        )
        self.assertIn("Catalogue téléchargé", result["messages"][-1])
        self.assertFalse(result["disabled"])
        self.assertIsNone(result["busy"])
        self.assertEqual(result["busyLabelRestored"], "Outils")

    def test_empty_catalogue_never_requests_the_pdf(self):
        result = run_scenario(r'''
const button = element("button", "Outils");
const messages = [];
const status = statusElement(messages);
const { documentTarget, downloads, urlApi } = downloadDocument();
const requests = [];
const controller = Reports.createCatalogueController({
  button, status, documentTarget, urlApi,
  getScope: () => ({ kind: "Digue", item: { id: "dg-1" } }),
  fetchImpl: async (url) => { requests.push(url); return response({ json: { entry_count: 0, digue_count: 0, troncon_count: 0 } }); },
});
await controller.run();
process.stdout.write(JSON.stringify({ requests, downloads, status: status.textContent }));''')
        self.assertEqual(
            result["requests"], ["/api/digues/dg-1/desordres/catalogue"]
        )
        self.assertEqual(result["downloads"], [])
        self.assertEqual(result["status"], "Aucun désordre dans ce périmètre.")

    def test_catalogue_without_selection_is_explained(self):
        result = run_scenario(r'''
const button = element("button", "Outils");
const messages = [];
const status = statusElement(messages);
let calls = 0;
const controller = Reports.createCatalogueController({
  button, status,
  getScope: () => null,
  fetchImpl: async () => { calls += 1; return response({}); },
});
await controller.run();
process.stdout.write(JSON.stringify({ calls, status: status.textContent,
  expected: Reports.NO_SELECTION_MESSAGE, error: status.classList.contains("error") }));''')
        self.assertEqual(result["calls"], 0)
        self.assertEqual(result["status"], result["expected"])
        self.assertTrue(result["error"])

    def test_offline_reports_require_the_server_without_any_call(self):
        result = run_scenario(r'''
const button = element("button", "Fiche PDF");
const messages = [];
const status = statusElement(messages);
let calls = 0;
const controller = Reports.createDisorderPdfController({
  button, status, getDisorderId: () => "d-1",
  isOffline: () => true,
  fetchImpl: async () => { calls += 1; return response({}); },
});
await controller.run();
process.stdout.write(JSON.stringify({ calls, status: status.textContent,
  disabled: button.disabled, busy: button.attributes["aria-busy"] ?? null }));''')
        self.assertEqual(result["calls"], 0)
        self.assertIn("nécessite le serveur", result["status"])
        self.assertFalse(result["disabled"])
        self.assertIsNone(result["busy"])

    def test_network_failure_is_explained_and_state_is_restored(self):
        result = run_scenario(r'''
const button = element("button", "Fiche PDF");
const messages = [];
const status = statusElement(messages);
const controller = Reports.createDisorderPdfController({
  button, status, getDisorderId: () => "d-1",
  fetchImpl: async () => { throw new TypeError("Failed to fetch"); },
});
await controller.run();
process.stdout.write(JSON.stringify({ status: status.textContent,
  disabled: button.disabled, busy: button.attributes["aria-busy"] ?? null }));''')
        self.assertIn("injoignable", result["status"])
        self.assertFalse(result["disabled"])
        self.assertIsNone(result["busy"])

    def test_http_error_detail_is_shown(self):
        result = run_scenario(r'''
const button = element("button", "Fiche PDF");
const messages = [];
const status = statusElement(messages);
const controller = Reports.createDisorderPdfController({
  button, status, getDisorderId: () => "d-1",
  fetchImpl: async () => response({ ok: false, status: 404, json: { detail: "Désordre introuvable." } }),
});
await controller.run();
process.stdout.write(JSON.stringify({ status: status.textContent,
  error: status.classList.contains("error") }));''')
        self.assertEqual(result["status"], "Désordre introuvable.")
        self.assertTrue(result["error"])

    def test_double_click_is_ignored_while_pending(self):
        result = run_scenario(r'''
const button = element("button", "Fiche PDF");
const messages = [];
const status = statusElement(messages);
const { documentTarget, downloads, urlApi } = downloadDocument();
let calls = 0;
let release;
const gate = new Promise((resolve) => { release = resolve; });
const controller = Reports.createDisorderPdfController({
  button, status, getDisorderId: () => "d-1", documentTarget, urlApi,
  fetchImpl: async () => { calls += 1; await gate; return response({ headers: { "content-disposition": "attachment; filename=fiche.pdf" } }); },
});
const first = controller.run();
const second = await controller.run();
release();
const firstResult = await first;
process.stdout.write(JSON.stringify({ calls, second, firstResult, downloads }));''')
        self.assertEqual(result["calls"], 1)
        self.assertFalse(result["second"])
        self.assertTrue(result["firstResult"])
        self.assertEqual(result["downloads"], ["fiche.pdf"])


class DisorderReportsUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.map_page = (FRONTEND_DIRECTORY / "map.html").read_text(encoding="utf-8")
        cls.data_page = (FRONTEND_DIRECTORY / "data.html").read_text(encoding="utf-8")
        cls.map_script = (FRONTEND_DIRECTORY / "js" / "map.js").read_text(encoding="utf-8")
        cls.data_view = (FRONTEND_DIRECTORY / "js" / "data-view.js").read_text(
            encoding="utf-8"
        )
        cls.detail_script = (
            FRONTEND_DIRECTORY / "js" / "data-entity-detail.js"
        ).read_text(encoding="utf-8")
        cls.service_worker = (
            FRONTEND_DIRECTORY / "service-worker.js"
        ).read_text(encoding="utf-8")
        cls.header_script = (
            FRONTEND_DIRECTORY / "js" / "app-header.js"
        ).read_text(encoding="utf-8")

    def test_module_is_loaded_on_both_pages_and_precached(self):
        script = '/static/js/disorder-reports.js'
        self.assertIn(f'src="{script}"', self.map_page)
        self.assertIn(f'src="{script}"', self.data_page)
        self.assertIn(f'"{script}"', self.service_worker)

    def test_catalogue_tool_is_available_on_map_and_data(self):
        self.assertIn('id="toggle-tools-menu"', self.header_script)
        self.assertIn('data-tool-action="disorder-catalogue"', self.header_script)
        self.assertIn("createCatalogueController", self.map_script)
        self.assertIn("createCatalogueController", self.data_view)
        self.assertIn("disorder-catalogue", self.data_view)
        self.assertIn('async onSelection(selection, { origin = "user" } = {})', self.data_view)
        self.assertIn("toolsMenuList", self.data_view)

    def test_disorder_pdf_is_contextual_in_carte_and_base(self):
        from webapp.tests.test_web_api import mounted_map_html
        self.assertEqual(mounted_map_html().count('id="desordre-pdf"'), 1)
        self.assertIn("createDisorderPdfController", self.map_script)
        self.assertIn('editorState.mode === "edit"', self.map_script)
        self.assertIn("Fiche PDF", self.detail_script)
        self.assertIn("createDisorderPdfController", self.detail_script)
        self.assertNotIn('data-tool-action="disorder-pdf"', self.data_page)


if __name__ == "__main__":
    unittest.main()
