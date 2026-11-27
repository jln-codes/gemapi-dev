(function (root) {
  "use strict";

  const fallback = {
    url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    options: { maxZoom: 19, attribution: "&copy; OpenStreetMap contributors" },
  };
  const TILEJSON_TIMEOUT_MS = 8000;
  // Digues-app policy: one Leaflet overzoom level, never a server-side tile.
  const RASTER_OVERZOOM_LEVELS = 1;

  async function loadStrict(tilejsonUrl) {
    if (!tilejsonUrl || typeof tilejsonUrl !== "string") {
      throw new Error("URL TileJSON absente");
    }
    const controller = typeof AbortController === "function" ? new AbortController() : null;
    let timeoutId;
    try {
      const timeout = new Promise((_, reject) => {
        timeoutId = root.setTimeout(() => {
          controller?.abort();
          reject(new Error("Délai de chargement du TileJSON dépassé"));
        }, TILEJSON_TIMEOUT_MS);
      });
      const document = (async () => {
        const scoped = tilejsonUrl.startsWith('/api/bases/') && root.BusinessRuntime?.fetch;
        const response = await (scoped ? root.BusinessRuntime.fetch(tilejsonUrl,
          controller ? { signal: controller.signal } : {})
          : fetch(tilejsonUrl, controller ? { signal: controller.signal } : {}));
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const tilejson = await response.json();
        const url = tilejson?.tiles?.find(
          (tile) => typeof tile === "string" &&
            ["{z}", "{x}", "{y}"].every((part) => tile.includes(part)),
        );
        if (!url) throw new Error("tiles absent ou inutilisable");
        for (const zoom of ["minzoom", "maxzoom"]) {
          if (tilejson[zoom] !== undefined && !Number.isFinite(tilejson[zoom])) {
            throw new Error(`${zoom} invalide`);
          }
        }
        if (tilejson.minzoom > tilejson.maxzoom) {
          throw new Error("Plage de zoom invalide");
        }
        const documentUrl = response.url || new URL(tilejsonUrl, root.location.href).href;
        const resolvedUrl = new URL(url, documentUrl);
        if (!["http:", "https:"].includes(resolvedUrl.protocol)) {
          throw new Error("Protocole de tuiles invalide");
        }
        const tileUrl = resolvedUrl.href.replace(/%7B/gi, "{").replace(/%7D/gi, "}");
        const options = {
          attribution: tilejson.attribution || "",
          maxZoom: tilejson.maxzoom === undefined
            ? fallback.options.maxZoom
            : tilejson.maxzoom + RASTER_OVERZOOM_LEVELS,
        };
        if (tilejson.minzoom !== undefined) options.minZoom = tilejson.minzoom;
        if (tilejson.maxzoom !== undefined) options.maxNativeZoom = tilejson.maxzoom;
        if (tilejson.scheme === "tms") options.tms = true;
        return { url: tileUrl, options };
      })();
      return await Promise.race([document, timeout]);
    } finally {
      root.clearTimeout(timeoutId);
    }
  }

  async function load(tilejsonUrl) {
    if (!tilejsonUrl || typeof tilejsonUrl !== "string") return fallback;
    try {
      return await loadStrict(tilejsonUrl);
    } catch (error) {
      console.warn("Fond de carte TileJSON indisponible ; utilisation d’OpenStreetMap.", error);
      return fallback;
    }
  }

  root.Basemap = { fallback, load, loadStrict };
})(window);
