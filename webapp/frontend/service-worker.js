const buildId = new URL(self.location.href).searchParams.get("v") || "dev";
importScripts("/static/js/offline-basemap-store.js");
let localBasemapStore;
const SHELL_CACHE = `gemapi-shell-${buildId}`;
const NETWORK_TIMEOUT_MS = 10000;

const SHELL_URLS = [
  "/carte",
  "/base",
  "/branding/apple-touch-icon.png",
  "/branding/cartouche.png",
  "/branding/favicon-16x16.png",
  "/branding/favicon-32x32.png",
  "/branding/favicon.ico",
  "/branding/icons/favicon-48x48.png",
  "/branding/icons/icon-192.png",
  "/branding/icons/icon-512.png",
  "/branding/icons/icon-maskable-192.png",
  "/branding/icons/icon-maskable-512.png",
  "/branding/logo-seul.png",
  "/branding/site.webmanifest",
  "/static/css/app.css",
  "/static/css/entity-forms.css",
  "/static/css/modal.css",
  "/static/css/map-disorder-legend.css",
  "/static/css/business-session.css",
  "/static/js/business-context.js",
  "/static/js/ai-panel.js",
  "/static/js/app-header.js",
  "/static/js/technical-shell.js",
  "/static/js/data-view-default.js",
  "/static/js/troncon-form.js",
  "/static/js/troncon-reperage.js",
  "/static/js/data-entity-detail.js",
  "/static/js/base-detail-registry.js",
  "/static/js/base-detail-repository.js",
  "/static/js/base-detail-view.js",
  "/static/js/base-detail-editor.js",
  "/static/js/base-detail-controller.js",
  "/static/js/entity-form-view.js",
  "/static/js/data-gpkg-export.js",
  "/static/js/data-view-builder.js",
  "/static/js/data-view.js",
  "/static/js/disorder-reports.js",
  "/static/js/disorder-urgency-style.js",
  "/static/js/entity-editor-shell.js",
  "/static/js/entity-form-session.js",
  "/static/js/entity-geometry-draft.js",
  "/static/js/entity-navigation.js",
  "/static/js/heritage-panel.js",
  "/static/js/heritage-selection-sync.js",
  "/static/js/help-panel.js",
  "/static/js/inter-page-bus.js",
  "/static/js/interface-coordination.js",
  "/static/js/map.js",
  "/static/js/map-form-hosts.js",
  "/static/js/map-heritage-editor.js",
  "/static/js/leaflet-layer-order.js",
  "/static/js/lidar-map.js",
  "/static/js/lidar-canvas.js",
  "/static/js/lidar-prototype-core.js",
  "/static/js/mobilier-editor.js",
  "/static/js/mobilier-form.js",
  "/static/js/ouvrage-particulier-form.js",
  "/static/js/cheminement-form.js",
  "/static/js/vegetation-form.js",
  "/static/js/amenagement-hydraulique-form.js",
  "/static/js/zone-stockage-form.js",
  "/static/js/prestation-form.js",
  "/static/js/reseau-technique-editor.js",
  "/static/js/ouvrage-particulier-editor.js",
  "/static/js/lit-form.js",
  "/static/js/lit-characteristics.js",
  "/static/js/lit-editor.js",
  "/static/js/cheminement-editor.js",
  "/static/js/vegetation-editor.js",
  "/static/js/amenagement-hydraulique-editor.js",
  "/static/js/zone-stockage-editor.js",
  "/static/js/prestation-editor.js",
  "/static/js/map-feature-store.js",
  "/static/js/map-layer-controls.js",
  "/static/js/map-print.js",
  "/static/js/basemap.js",
  "/static/js/basemap-selector.js",
  "/static/js/offline-basemap-store.js",
  "/static/js/offline-basemap-download.js",
  "/static/js/offline-basemap-ui.js",
  "/static/js/map-resource-invalidators.js",
  "/static/js/observation-parent-map-invalidation.js",
  "/static/js/pwa.js",
  "/static/js/pwa-geolocation.js",
  "/static/js/pwa-map-position.js",
  "/static/js/pwa-settings.js",
  "/static/js/pwa-geometry.js",
  "/static/js/pwa-geometry-drafts.js",
  "/static/js/pwa-geometry-tracking.js",
  "/static/js/pwa-position-candidate.js",
  "/static/js/reference-data.js",
  "/static/js/relation-selector.js",
  "/static/js/disorder-related-objects.js",
  "/static/js/resource-invalidation.js",
  "/static/js/resource-events.js",
  "/static/js/resource-concurrency.js",
  "/static/js/resource-mutation.js",
  "/static/js/resource-server-transport.js",
  "/static/js/local-projection.js",
  "/static/js/offline-consultation.js",
  "/static/js/offline-operations.js",
  "/static/js/observation-create.js",
  "/static/js/observation-form.js",
  "/static/js/observation-detail.js",
  "/static/js/observation-parent-list.js",
  "/static/js/photo-form.js",
  "/static/js/photo-editor.js",
  "/static/js/photo-file.js",
  "/static/js/photo-offline.js",
  "/static/js/photo-localisation.js",
  "/static/js/data-resource-invalidators.js",
  "/static/js/sql-tool.js",
  "/static/js/top-navigation.js",
  "/static/vendor/html2canvas/html2canvas.min.js",
  "/static/vendor/jspdf/jspdf.umd.min.js",
  "/static/vendor/leaflet/images/layers-2x.png",
  "/static/vendor/leaflet/images/layers.png",
  "/static/vendor/leaflet/images/marker-icon-2x.png",
  "/static/vendor/leaflet/images/marker-icon.png",
  "/static/vendor/leaflet/images/marker-shadow.png",
  "/static/vendor/leaflet/leaflet.css",
  "/static/vendor/leaflet/leaflet.js",
  "/static/vendor/leaflet-editable/Leaflet.Editable.js",
  "/static/vendor/marked/marked.umd.js",
  "/static/vendor/proj4/proj4.js",
  "/static/vendor/geotiff/geotiff.js",
];
const SHELL_PATHS = new Set(SHELL_URLS);
const SHELL_DOCUMENTS = new Map([
  ["/", "/carte"],
  ["/carte", "/carte"],
  ["/base", "/base"],
]);

function versionedShellUrl(pathname) {
  const url = new URL(pathname, self.location.origin);
  url.searchParams.set("v", buildId);
  return `${url.pathname}${url.search}`;
}

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    try {
      const cache = await caches.open(SHELL_CACHE);
      await cache.addAll(SHELL_URLS.map(versionedShellUrl));
    } catch (error) {
      await caches.delete(SHELL_CACHE);
      throw error;
    }
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    self.clients.claim().then(() => caches.keys()).then((cacheNames) => Promise.all(
      cacheNames
        .filter((cacheName) => (
          cacheName.startsWith("gemapi-shell-") && cacheName !== SHELL_CACHE
        ))
        .map((cacheName) => caches.delete(cacheName)),
    )),
  );
});

self.addEventListener("message", (event) => {
  if (event.data?.type === "SKIP_WAITING") {
    self.skipWaiting();
  }
});

async function cachedShellDocument(request, pathname) {
  const cache = await caches.open(SHELL_CACHE);
  const cached = await cache.match(versionedShellUrl(SHELL_DOCUMENTS.get(pathname)));
  if (cached) return cached;
  return timedNetworkFetch(request);
}

async function cachedShellResource(request, pathname) {
  const cache = await caches.open(SHELL_CACHE);
  const cached = await cache.match(versionedShellUrl(pathname));
  if (cached) return cached;
  return timedNetworkFetch(request);
}

async function timedNetworkFetch(request) {
  const controller = typeof AbortController === "function" ? new AbortController() : null;
  let timeoutId;
  try {
    return await Promise.race([
      fetch(request, controller ? { signal: controller.signal } : undefined),
      new Promise((_, reject) => {
        timeoutId = setTimeout(() => {
          controller?.abort();
          reject(new Error("Network request timed out"));
        }, NETWORK_TIMEOUT_MS);
      }),
    ]);
  } finally {
    clearTimeout(timeoutId);
  }
}

async function networkFirstNavigation(request) {
  try {
    const response = await timedNetworkFetch(request);
    if (response.status < 500 || response.status > 599) return response;
  } catch (_error) {
    // The cached document is also the fallback for network failures.
  }
  const cache = await caches.open(SHELL_CACHE);
  const cached = await cache.match(versionedShellUrl("/carte"));
  if (cached) return cached;
  return timedNetworkFetch(new URL("/carte", self.location.origin).href);
}

async function requestBasemapContext(clientId) {
  if (!clientId) return null;
  const client = await self.clients.get(clientId);
  if (!client) return null;
  return new Promise((resolve) => {
    const channel = new MessageChannel();
    const timer = setTimeout(() => { channel.port1.close(); resolve(null); }, 3000);
    channel.port1.onmessage = (event) => {
      clearTimeout(timer);
      channel.port1.close();
      const key = event.data?.contextKey;
      resolve(typeof key === "string" && key.length <= 180 ? key : null);
    };
    client.postMessage({ type: "DIGUES_BASEMAP_CONTEXT" }, [channel.port2]);
  });
}

async function localBasemapResponse(event, url) {
  const tile = self.OfflineBasemapStore.parseLocalPath(url.pathname);
  if (!tile || url.search) return new Response("Chemin de tuile invalide.", { status: 400 });
  const contextKey = await requestBasemapContext(event.clientId);
  if (!contextKey) return new Response("Contexte cartographique absent.", { status: 403 });
  try {
    localBasemapStore ||= self.OfflineBasemapStore.createStore({ origin: self.location.origin });
    const response = await localBasemapStore.resolve(tile.packageId, contextKey, tile.key);
    if (await requestBasemapContext(event.clientId) !== contextKey) {
      return new Response("Contexte cartographique expiré.", { status: 403 });
    }
    return response || new Response("Tuile locale absente.", { status: 404 });
  } catch (_) {
    return new Response("Paquet local indisponible.", { status: 404 });
  }
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);

  if (request.method !== "GET") {
    return;
  }
  if (url.pathname.startsWith("/api/")) {
    return;
  }
  if (url.origin !== self.location.origin) {
    return;
  }
  if (url.pathname.startsWith("/__offline-basemaps/")) {
    event.respondWith(localBasemapResponse(event, url));
    return;
  }

  if (request.mode === "navigate") {
    event.respondWith(
      SHELL_DOCUMENTS.has(url.pathname)
        ? cachedShellDocument(request, url.pathname)
        : networkFirstNavigation(request),
    );
    return;
  }
  if (SHELL_PATHS.has(url.pathname)) {
    event.respondWith(cachedShellResource(request, url.pathname));
  }
});
