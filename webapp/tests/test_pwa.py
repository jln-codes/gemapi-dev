import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from digues_webapp.app import (
    APP_VERSION,
    BUILD_ID,
    FRONTEND_DIRECTORY,
    app,
    calculate_frontend_build_id,
)


def route_endpoint(path: str):
    return next(route.endpoint for route in app.routes if route.path == path)


class PwaBackendTest(unittest.TestCase):
    def test_dev_prototype_does_not_open_protected_distribution(self):
        with patch.dict("os.environ", {
            "DIGUES_ENVIRONMENT": "test",
            "LIDAR_PROTOTYPE_ENABLED": "1",
            "LIDAR_INTERNAL_TOKEN": "synthetic-token-for-qualification",
        }), TestClient(app) as client:
            self.assertEqual(client.get("/dev/lidar").status_code, 200)
            self.assertEqual(client.get("/api/lidar/policy").status_code, 403)
            self.assertEqual(client.post("/api/lidar/blocks/v1/read").status_code, 403)

    def test_manifest_is_served_with_manifest_media_type(self):
        response = route_endpoint("/branding/site.webmanifest")()
        self.assertEqual(response.media_type, "application/manifest+json")
        self.assertEqual(response.path, FRONTEND_DIRECTORY / "branding" / "site.webmanifest")

    def test_service_worker_is_served_from_root_without_durable_http_cache(self):
        response = route_endpoint("/service-worker.js")()
        self.assertEqual(response.media_type, "application/javascript")
        self.assertEqual(response.headers["cache-control"], "no-cache")
        self.assertEqual(response.path, FRONTEND_DIRECTORY / "service-worker.js")

    def test_versioned_worker_refuses_new_bytes_under_an_old_cache_identity(self):
        endpoint = route_endpoint("/service-worker.js")
        self.assertEqual(endpoint(v=BUILD_ID).path, FRONTEND_DIRECTORY / "service-worker.js")
        for stale in ("previous-build", ""):
            with self.subTest(version=stale), self.assertRaises(HTTPException) as failure:
                endpoint(v=stale)
            self.assertEqual(failure.exception.status_code, 409)
            self.assertEqual(failure.exception.headers["Cache-Control"], "no-cache")

    def test_config_exposes_build_and_app_versions_without_cache(self):
        with patch.dict("os.environ", {
            "BASEMAP_TILEJSON_URL": "https://example.test/internal.json",
            "DATABASE_URL": "postgresql://example.invalid/fixture",
        }):
            response = route_endpoint("/api/config")()
        self.assertEqual(json.loads(response.body), {"build_id": BUILD_ID, "app_version": APP_VERSION})
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_base_summary_is_closed_before_identity(self):
        with TestClient(app) as client:
            response = client.get("/api/base-summary")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_frontend_build_id_is_deterministic_and_path_sensitive(self):
        with (
            tempfile.TemporaryDirectory() as first,
            tempfile.TemporaryDirectory() as second,
        ):
            first_root = Path(first)
            second_root = Path(second)
            (first_root / "nested").mkdir()
            (second_root / "nested").mkdir()
            (first_root / "z.js").write_bytes(b"same content")
            (first_root / "nested" / "a.css").write_bytes(b"other content")
            (second_root / "nested" / "a.css").write_bytes(b"other content")
            (second_root / "z.js").write_bytes(b"same content")

            first_id = calculate_frontend_build_id(first_root)
            self.assertEqual(first_id, calculate_frontend_build_id(first_root))
            self.assertEqual(first_id, calculate_frontend_build_id(second_root))
            self.assertRegex(first_id, r"^[0-9a-f]{12}$")

            (second_root / "z.js").rename(second_root / "renamed.js")
            self.assertNotEqual(first_id, calculate_frontend_build_id(second_root))
            (second_root / "renamed.js").rename(second_root / "z.js")
            (second_root / "z.js").write_bytes(b"changed content")
            self.assertNotEqual(first_id, calculate_frontend_build_id(second_root))


class PwaFrontendContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = (FRONTEND_DIRECTORY / "map.html").read_text(encoding="utf-8")
        cls.service_worker = (FRONTEND_DIRECTORY / "service-worker.js").read_text(
            encoding="utf-8"
        )
        cls.registration = (FRONTEND_DIRECTORY / "js" / "pwa.js").read_text(
            encoding="utf-8"
        )

    def test_leaflet_dependencies_are_local_and_keep_the_existing_versions(self):
        self.assertNotIn("unpkg.com/leaflet", self.page)
        self.assertIn(
            'href="/static/vendor/leaflet/leaflet.css"', self.page
        )
        self.assertIn(
            'src="/static/vendor/leaflet/leaflet.js"', self.page
        )
        self.assertIn(
            'src="/static/vendor/leaflet-editable/Leaflet.Editable.js"',
            self.page,
        )

        leaflet_script = (
            FRONTEND_DIRECTORY / "vendor" / "leaflet" / "leaflet.js"
        ).read_text(encoding="utf-8")
        editable_package = json.loads(
            (
                FRONTEND_DIRECTORY
                / "vendor"
                / "leaflet-editable"
                / "package.json"
            ).read_text(encoding="utf-8")
        )
        self.assertIn("Leaflet 1.9.4", leaflet_script)
        self.assertEqual(editable_package["version"], "1.2.0")

    def test_map_declares_standard_and_apple_mobile_web_app_capabilities(self):
        self.assertEqual(
            self.page.count('<meta name="mobile-web-app-capable" content="yes">'),
            1,
        )
        self.assertEqual(
            self.page.count('<meta name="apple-mobile-web-app-capable" content="yes">'),
            1,
        )
        data_page = (FRONTEND_DIRECTORY / "data.html").read_text(encoding="utf-8")
        self.assertNotIn('name="mobile-web-app-capable"', data_page)

    def test_all_leaflet_runtime_files_exist_and_are_precached(self):
        leaflet_shell_files = (
            "/static/vendor/leaflet/leaflet.css",
            "/static/vendor/leaflet/leaflet.js",
            "/static/vendor/leaflet-editable/Leaflet.Editable.js",
            "/static/vendor/leaflet/images/layers.png",
            "/static/vendor/leaflet/images/layers-2x.png",
            "/static/vendor/leaflet/images/marker-icon.png",
            "/static/vendor/leaflet/images/marker-icon-2x.png",
            "/static/vendor/leaflet/images/marker-shadow.png",
        )
        for url in leaflet_shell_files:
            with self.subTest(url=url):
                self.assertTrue((FRONTEND_DIRECTORY / url.removeprefix("/static/")).is_file())
                self.assertIn(f'"{url}"', self.service_worker)

    def test_map_and_data_local_shell_assets_are_all_precached(self):
        for page_name, route in (("map.html", "/carte"), ("data.html", "/base")):
            page = (FRONTEND_DIRECTORY / page_name).read_text(encoding="utf-8")
            shell_urls = set(re.findall(r'(?:src|href)="(/[^"]+)"', page))
            local_assets = {
                url for url in shell_urls
                if url.startswith("/static/") or url.startswith("/branding/")
            }
            with self.subTest(page=page_name):
                self.assertIn(f'"{route}"', self.service_worker)
                for url in local_assets:
                    self.assertIn(f'"{url}"', self.service_worker)

    def test_shared_business_forms_are_loaded_in_both_hosts_and_precached(self):
        for filename in ("ouvrage-particulier-form.js", "cheminement-form.js",
                         "vegetation-form.js", "amenagement-hydraulique-form.js",
                         "prestation-form.js"):
            with self.subTest(filename=filename):
                url = f"/static/js/{filename}"
                self.assertTrue((FRONTEND_DIRECTORY / "js" / filename).is_file())
                for page_name in ("map.html", "data.html"):
                    page = (FRONTEND_DIRECTORY / page_name).read_text(encoding="utf-8")
                    self.assertIn(f'data-business-src="{url}"', page)
                self.assertIn(f'"{url}"', self.service_worker)

    def test_map_geolocation_service_is_loaded_and_precached_only_for_map(self):
        script = '/static/js/pwa-geolocation.js'
        self.assertIn(f'src="{script}"', self.page)
        self.assertIn(f'"{script}"', self.service_worker)
        data_page = (FRONTEND_DIRECTORY / "data.html").read_text(encoding="utf-8")
        self.assertNotIn(script, data_page)

    def test_map_position_candidate_is_precached_without_extra_gps_provider(self):
        script = '/static/js/pwa-position-candidate.js'
        self.assertIn(f'src="{script}"', self.page)
        self.assertIn(f'"{script}"', self.service_worker)
        data_page = (FRONTEND_DIRECTORY / "data.html").read_text(encoding="utf-8")
        self.assertNotIn(script, data_page)
        candidate = (FRONTEND_DIRECTORY / "js" / "pwa-position-candidate.js").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("navigator.geolocation", candidate)
        self.assertNotIn("watchPosition(", candidate)

    def test_map_settings_module_and_panel_are_precached_without_gps_capture(self):
        script = '/static/js/pwa-settings.js'
        self.assertIn(f'src="{script}"', self.page)
        self.assertIn(f'"{script}"', self.service_worker)
        self.assertIn('id="pwa-settings-panel"', self.page)
        self.assertIn('id="pwa-settings-form"', self.page)
        data_page = (FRONTEND_DIRECTORY / "data.html").read_text(encoding="utf-8")
        self.assertNotIn(script, data_page)
        settings = (FRONTEND_DIRECTORY / "js" / "pwa-settings.js").read_text(
            encoding="utf-8"
        )
        for forbidden in ("getCurrentPosition(", "watchPosition(", "fetch("):
            self.assertNotIn(forbidden, settings)

    def test_service_worker_excludes_api_non_get_and_cross_origin_requests(self):
        self.assertIn('request.method !== "GET"', self.service_worker)
        self.assertIn('url.pathname.startsWith("/api/")', self.service_worker)
        self.assertIn('url.origin !== self.location.origin', self.service_worker)

    def test_shell_cache_is_versioned_and_cleanup_is_shell_only(self):
        self.assertIn('`gemapi-shell-${buildId}`', self.service_worker)
        self.assertIn('cacheName.startsWith("gemapi-shell-")', self.service_worker)
        self.assertIn('cacheName !== SHELL_CACHE', self.service_worker)
        self.assertNotIn('startsWith("digues-map-")', self.service_worker)
        self.assertNotIn('startsWith("digues-data-")', self.service_worker)
        self.assertLess(
            self.service_worker.index("self.clients.claim()"),
            self.service_worker.index("caches.delete(cacheName)"),
        )

    def test_registration_discovers_the_current_build_id(self):
        self.assertIn('fetch("/api/config", { cache: "no-store" })', self.registration)
        self.assertIn(
            '`/service-worker.js?v=${encodeURIComponent(config.build_id)}`',
            self.registration,
        )
        self.assertIn('type: "SKIP_WAITING"', self.registration)
        self.assertIn('"controllerchange"', self.registration)

    def test_active_worker_serves_one_atomic_generation_for_map_and_data(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible")
        program = r'''
const vm = require("node:vm");
const source = __SERVICE_WORKER_SOURCE__;
let originGeneration = "v1";
let online = true;
const stores = new Map();
const cacheKey = (request) => {
  const url = new URL(
  typeof request === "string" ? request : request.url,
  "https://digues.test",
  );
  return `${url.pathname}${url.search}`;
};
const originFetch = async (request) => {
  if (!online) throw new Error("offline");
  return { generation: originGeneration, path: cacheKey(request) };
};
const cachesApi = {
  async open(name) {
    if (!stores.has(name)) stores.set(name, new Map());
    const store = stores.get(name);
    return {
      async addAll(urls) {
        const fetched = await Promise.all(urls.map(async (url) => [
          cacheKey(url), await originFetch(url),
        ]));
        fetched.forEach(([url, response]) => store.set(url, response));
      },
      async match(request) { return store.get(cacheKey(request)); },
    };
  },
  async keys() { return [...stores.keys()]; },
  async delete(name) { return stores.delete(name); },
  async match(request) {
    const key = cacheKey(request);
    for (const store of stores.values()) {
      if (store.has(key)) return store.get(key);
    }
    return undefined;
  },
};
function createWorker(buildId) {
  const handlers = new Map();
  const state = { claims: 0, skipWaiting: 0 };
  const self = {
    location: { href: `https://digues.test/service-worker.js?v=${buildId}`, origin: "https://digues.test" },
    addEventListener(type, callback) { handlers.set(type, callback); },
    clients: { async claim() { state.claims += 1; } },
    skipWaiting() { state.skipWaiting += 1; },
  };
  vm.runInNewContext(source, {
    self, caches: cachesApi, fetch: originFetch, URL,
    importScripts() {}, // The synthetic route is covered separately; this test exercises shell generations.
    Response: { error: () => ({ generation: "error", path: "error" }) },
    AbortController, setTimeout, clearTimeout,
  });
  const lifecycle = async (type, extra = {}) => {
    let pending = Promise.resolve();
    handlers.get(type)({ ...extra, waitUntil(value) { pending = Promise.resolve(value); } });
    await pending;
  };
  return {
    state,
    install: () => lifecycle("install"),
    activate: () => lifecycle("activate"),
    message: (data) => lifecycle("message", { data }),
    async fetch(path, mode = "same-origin") {
      let response;
      handlers.get("fetch")({
        request: { method: "GET", mode, url: `https://digues.test${path}` },
        respondWith(value) { response = Promise.resolve(value); },
      });
      return response ? response : originFetch(path);
    },
  };
}
(async () => {
  const v1 = createWorker("v1");
  await v1.install(); await v1.activate();
  originGeneration = "v2";
  const v2 = createWorker("v2");
  await v2.install();
  const beforeAcceptance = await Promise.all([
    v1.fetch("/carte", "navigate"),
    v1.fetch("/static/css/app.css"),
    v1.fetch("/static/js/map.js"),
    v1.fetch("/base", "navigate"),
    v1.fetch("/static/js/data-view.js"),
  ]);
  online = false;
  const offline = await Promise.all([
    v1.fetch("/carte", "navigate"), v1.fetch("/static/js/map.js"),
  ]);
  online = true;
  stores.get("gemapi-shell-v1").delete("/static/js/map.js?v=v1");
  const missingOldAsset = await v1.fetch("/static/js/map.js");
  await v2.message({ type: "SKIP_WAITING" });
  await v2.activate();
  const afterAcceptance = await Promise.all([
    v2.fetch("/carte", "navigate"),
    v2.fetch("/static/css/app.css"),
    v2.fetch("/static/js/map.js"),
    v2.fetch("/base", "navigate"),
    v2.fetch("/static/js/data-view.js"),
  ]);
  process.stdout.write(JSON.stringify({
    beforeAcceptance: beforeAcceptance.map((response) => response.generation),
    offline: offline.map((response) => response.generation),
    missingOldAsset: missingOldAsset.generation,
    afterAcceptance: afterAcceptance.map((response) => response.generation),
    cacheNames: await cachesApi.keys(),
    v2CacheKeys: [...stores.get("gemapi-shell-v2").keys()],
    v2State: v2.state,
  }));
})();
'''.replace("__SERVICE_WORKER_SOURCE__", json.dumps(self.service_worker))
        result = json.loads(subprocess.check_output([node, "-e", program], text=True))

        self.assertEqual(result["beforeAcceptance"], ["v1"] * 5)
        self.assertEqual(result["offline"], ["v1", "v1"])
        self.assertEqual(result["missingOldAsset"], "v2")
        self.assertEqual(result["afterAcceptance"], ["v2"] * 5)
        self.assertEqual(result["cacheNames"], ["gemapi-shell-v2"])
        self.assertTrue(result["v2CacheKeys"])
        self.assertTrue(all(
            key.endswith("?v=v2") for key in result["v2CacheKeys"]
        ))
        self.assertEqual(result["v2State"], {"claims": 1, "skipWaiting": 1})

    def test_waiting_update_is_user_activated_and_reloads_only_once(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js indisponible")
        program = r'''
const vm = require("node:vm");
const source = __PWA_SOURCE__;
const listeners = {};
const messages = [];
let reloads = 0;
let registeredUrl = null;
const notice = { hidden: true };
const button = {
  disabled: false, listeners: {},
  addEventListener(type, callback) { this.listeners[type] = callback; },
};
const waiting = { postMessage(message) { messages.push(message); } };
const registration = {
  waiting, installing: null, listeners: {},
  addEventListener(type, callback) { this.listeners[type] = callback; },
};
const serviceWorker = {
  controller: {},
  async register(url) { registeredUrl = url; return registration; },
  addEventListener(type, callback) { listeners[type] = callback; },
};
const context = {
  navigator: { serviceWorker },
  document: { getElementById(id) { return id === "pwa-update-notice" ? notice : button; } },
  fetch: async () => ({ ok: true, json: async () => ({ build_id: "v2" }) }),
  window: { location: { reload() { reloads += 1; } } },
  console,
  setTimeout,
};
vm.runInNewContext(source, context);
setTimeout(() => {
  button.listeners.click();
  listeners.controllerchange();
  listeners.controllerchange();
  process.stdout.write(JSON.stringify({
    registeredUrl, noticeHidden: notice.hidden, buttonDisabled: button.disabled,
    messages, reloads,
  }));
}, 0);
'''.replace("__PWA_SOURCE__", json.dumps(self.registration))
        result = json.loads(subprocess.check_output([node, "-e", program], text=True))

        self.assertEqual(result["registeredUrl"], "/service-worker.js?v=v2")
        self.assertFalse(result["noticeHidden"])
        self.assertTrue(result["buttonDisabled"])
        self.assertEqual(result["messages"], [{"type": "SKIP_WAITING"}])
        self.assertEqual(result["reloads"], 1)

    def test_standalone_header_exposes_only_map_actions(self):
        css = (FRONTEND_DIRECTORY / "css" / "app.css").read_text(encoding="utf-8")
        header = (FRONTEND_DIRECTORY / "js" / "app-header.js").read_text(
            encoding="utf-8"
        )
        standalone = css.split("@media (display-mode: standalone)", 1)[1]
        for selector in (
            ".map-page .app-brand",
            ".map-page .view-switcher",
            ".map-page .tools-menu",
            ".map-page #status",
        ):
            self.assertNotIn(selector, standalone)
        for visible_label in ("Créer", "Assistant IA"):
            self.assertIn(visible_label, header)
        self.assertIn('id="toggle-heritage"', header)
        self.assertIn('canAccessData = !standalone && Number(width) >= DATA_MIN_WIDTH', header)
        self.assertIn('data-capability="desktop-map-tools"', header)
        self.assertIn('data-capability="field-map-tools"', header)
        self.assertIn('windowTarget.location.replace(windowTarget.BusinessRuntime?.pageUrl("/carte") || "/carte")', header)
        self.assertNotIn('id="toggle-queries"', self.page)
        data_page = (FRONTEND_DIRECTORY / "data.html").read_text(encoding="utf-8")
        self.assertNotIn('rel="manifest"', data_page)
        self.assertIn('id="pwa-update-notice"', data_page)
        self.assertIn('src="/static/js/pwa.js"', data_page)


if __name__ == "__main__":
    unittest.main()
