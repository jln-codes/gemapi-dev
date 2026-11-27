(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.EntityNavigation = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const ENTITY_CLASSES = Object.freeze([
    "systemes",
    "digues",
    "troncons",
    "troncons_lit",
    "elements_morphologiques_lit",
    "mobilier",
    "equipements_mesure",
    "reseaux_techniques",
    "ouvrages_particuliers",
    "cheminements",
    "vegetation",
    "amenagements_hydrauliques",
    "zones_stockage",
    "prestations",
  ]);
  const classes = new Set(ENTITY_CLASSES);
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

  function target(entityClass, id) {
    const normalizedClass = String(entityClass || "").trim();
    const normalizedId = String(id || "").trim().toLowerCase();
    if (!classes.has(normalizedClass)) throw new Error("Catégorie de navigation Carte inconnue.");
    if (!UUID.test(normalizedId)) throw new Error("Identifiant de navigation Carte invalide.");
    return Object.freeze({ entityClass: normalizedClass, id: normalizedId });
  }

  function fromSearch(search) {
    const parameters = new URLSearchParams(search || "");
    const entityClass = parameters.get("entity");
    const id = parameters.get("id");
    if (entityClass === null && id === null) return null;
    if (entityClass === null || id === null) throw new Error("Lien Carte incomplet.");
    return target(entityClass, id);
  }

  function mapUrl(entityClass, id, {
    runtime = globalThis.BusinessRuntime,
    origin = globalThis.location?.origin || "http://localhost",
  } = {}) {
    const selected = target(entityClass, id);
    const url = new URL(runtime?.pageUrl("/carte") || "/carte", origin);
    url.searchParams.set("entity", selected.entityClass);
    url.searchParams.set("id", selected.id);
    return `${url.pathname}${url.search}${url.hash}`;
  }

  function automaticUrl(href, { mode = "position", view, position = null, selected = null,
    origin = globalThis.location?.origin || "http://localhost" } = {}) {
    const url = new URL(href, origin);
    if (view !== "map" && view !== "data") throw new Error("Interface de navigation inconnue.");
    for (const parameter of ["lat", "lon", "zoom", "entity", "id"]) url.searchParams.delete(parameter);
    const validPosition = view === "map" && mode !== "minimal"
      && Number.isFinite(position?.lat) && position.lat >= -90 && position.lat <= 90
      && Number.isFinite(position?.lon) && position.lon >= -180 && position.lon <= 180
      && Number.isFinite(position?.zoom) && position.zoom >= 0;
    if (validPosition) {
      url.searchParams.set("lat", position.lat.toFixed(6));
      url.searchParams.set("lon", position.lon.toFixed(6));
      url.searchParams.set("zoom", String(position.zoom));
    }
    if (mode === "object" && selected) {
      try {
        const destination = target(selected.entityClass, selected.id);
        url.searchParams.set("entity", destination.entityClass);
        url.searchParams.set("id", destination.id);
      } catch (_) { /* Do not publish a link that the incoming parser cannot reopen. */ }
    }
    return `${url.pathname}${url.search}${url.hash}`;
  }

  return { ENTITY_CLASSES, automaticUrl, fromSearch, mapUrl, target };
});
