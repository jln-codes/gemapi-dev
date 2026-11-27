/* Adaptateurs de lecture Base : payloads existants, relations structurées, contexte capturé. */
(function (root, factory) {
  const common = typeof module === "object" && module.exports;
  const api = factory(common ? require("./base-detail-registry.js") : root.BaseDetailRegistry,
    common ? require("./resource-concurrency.js") : root.ResourceConcurrency);
  if (typeof module === "object" && module.exports) module.exports = api;
  root.BaseDetailRepository = api;
})(globalThis, function (Registry, Concurrency) {
  "use strict";
  const own = (object, key) => Object.prototype.hasOwnProperty.call(object, key);
  const readPath = (object, path) => path.split(".").reduce((value, part) => value?.[part], object);
  const text = value => value == null ? "" : String(value).trim();
  const label = row => text(row?.label || row?.designation || row?.libelle || row?.nom);
  const geometryNames = { Point: "Point", LineString: "Tracé linéaire", Polygon: "Surface", MultiPoint: "Ensemble de points",
    MultiLineString: "Ensemble de tracés", MultiPolygon: "Ensemble de surfaces", GeometryCollection: "Collection géométrique" };
  const physicalNames = { INDETERMINE: "Indéterminé", BERGE: "Berge", CRETE: "Crête", TALUS: "Talus", TALUS_RISBERME: "Talus de risberme", RISBERME: "Risberme", FRANC_BORD: "Franc-bord" };
  const enumNames = { ARBRES: "Arbres", HERBACEES: "Herbacées", BUISSONS_ARBUSTES: "Buissons et arbustes", ISOLE: "Isolé", LIGNE: "Ligne", SURFACE: "Surface", ARBRE_SUR_PIED: "Arbre sur pied", SOUCHE: "Souche" };
  function normalize(resource, payload) {
    const definition = Registry.get(resource);
    const raw = payload?.type === "Feature" ? payload.properties : payload;
    if (!raw || typeof raw !== "object" || !raw.id) throw new Error("Réponse détaillée invalide.");
    const values = { ...raw };
    if (own(payload, "geometry")) {
      const type = payload.geometry?.type;
      const historicalNames = { CIRCULARSTRING: "Courbe circulaire historique", COMPOUNDCURVE: "Courbe composée historique", CURVEPOLYGON: "Surface à contours courbes" };
      values.localisation = geometryNames[type] || (raw.geometry_type
        ? `${historicalNames[String(raw.geometry_type).toUpperCase()] || raw.geometry_type} — affichage cartographique indisponible` : "Géométrie non renseignée");
    }
    if (resource === "troncons") values.type_physique_libelle ||= physicalNames[raw.type_physique_id];
    if (resource === "elements_morphologiques_lit") values.type_morphologie_libelle ||=
      { ILE: "Île", BANC: "Banc", ATTERISSEMENT: "Atterrissement", INDETERMINE: "Indéterminé" }[raw.type_morphologie];
    return { resource, id: String(raw.id), definition, payload, values, relations: {}, catalogs: {}, errors: [],
      title: text(raw.designation || raw.libelle) || definition.label,
      subtitle: values.type_physique_libelle || values.type_libelle || "Consultation" };
  }
  function relationRows(detail, spec) {
    const value = readPath(detail.values, spec.source || spec.key);
    const ids = spec.idsSource ? readPath(detail.values, spec.idsSource) : undefined;
    if (value === undefined && ids === undefined) return { state: "notLoaded", items: [] };
    const rows = [...(value == null || value === "" ? [] : Array.isArray(value) ? value : [value]), ...(ids || [])];
    const seen = new Set();
    const items = rows.flatMap(row => {
      const object = typeof row === "object" ? row : { id: row };
      const id = text(object.id);
      const resource = object.resource || spec.resource;
      const key = `${resource}:${id}`;
      if (!id || seen.has(key)) return [];
      seen.add(key);
      const name = label(object) || (rows.length === 1 && spec.labelKey ? text(detail.values[spec.labelKey]) : "");
      return [{ resource, id, label: name && name !== id ? name : "Libellé non résolu", state: name && name !== id ? "value" : "unresolved",
        role: object.relation_role || spec.key, canonicalResource: object.canonical_resource || null,
        qualification: object.type_physique_libelle || physicalNames[object.type_physique_id] || object.type_physique_id || "" }];
    });
    return { state: items.length ? "value" : "empty", items };
  }
  function value(detail, spec) {
    if (spec.format === "relations") return detail.relations[spec.key] || relationRows(detail, spec);
    const key = spec.source || spec.key;
    const raw = readPath(detail.values, key);
    if (raw === undefined) return { state: "notLoaded", value: null };
    if (raw === null) return { state: "unset", value: null };
    if (raw === "" || Array.isArray(raw) && !raw.length) return { state: "empty", value: raw };
    if (spec.format === "reference") {
      const name = text(detail.values[spec.labelKey]) || label((detail.catalogs[spec.catalog] || []).find(row => String(row.id) === String(raw)));
      return name && name !== String(raw) ? { state: "value", value: name } : { state: "unresolved", value: raw };
    }
    if (spec.format === "labels") return { state: "value", value: (raw || []).map(id => label((detail.catalogs[spec.catalog] || []).find(row => String(row.id) === String(id))) || "Libellé non résolu").join(" · ") };
    return { state: "value", value: enumNames[raw] || raw };
  }
  async function fetchJson(url, options = {}) {
    const runtime = globalThis.BusinessRuntime, offline = globalThis.OfflineRuntime;
    await globalThis.ResourceEventTransport?.bootstrap;
    const originHeaders = options.method && options.method !== "GET"
      ? await globalThis.ResourceMutationController?.requestHeaders() : {};
    if (runtime !== globalThis.BusinessRuntime || runtime?.isActive?.() === false || offline !== globalThis.OfflineRuntime)
      throw new Error("Le contexte de la base a changé.");
    const response = await (offline?.fetch || runtime?.fetch || globalThis.fetch)(url, { ...options, headers: { ...originHeaders, ...options.headers } });
    let body = null;
    try { body = await response.json(); } catch (_error) { /* Keep HTTP failure explicit. */ }
    if (!response.ok) {
      throw Concurrency.httpError(response.status, body, "Chargement impossible.");
    }
    return body;
  }
  const choicePaths = {
    mobilier: "/api/troncons/options", equipements_mesure: "/api/troncons/options", reseaux_techniques: "/api/troncons/options",
    ouvrages_particuliers: "/api/troncons/options", cheminements: "/api/troncons/options", troncons: "/api/troncons/options",
    vegetation: "/api/vegetation/relations/options", amenagements_hydrauliques: "/api/amenagements-hydrauliques/relations/options",
    zones_stockage: "/api/zones-stockage/relations/options", prestations: "/api/prestations/relations/options",
    troncons_lit: "/api/troncons-lit/options", elements_morphologiques_lit: "/api/troncons-lit/options",
  };
  const choicesKey = { systemes: "systemes", troncons: "troncons", digues: "digues", amenagements_hydrauliques: "amenagements", zones_stockage: "zones", reseaux_hydrauliques: "reseaux", organismes: "organismes", contacts: "contacts", troncons_lit: "troncons_lit" };
  function create({ request = fetchJson, referenceData = globalThis.ReferenceData } = {}) {
    async function read(resource, id) {
      const runtime = globalThis.BusinessRuntime;
      const current = () => runtime === globalThis.BusinessRuntime && runtime?.isActive?.() !== false;
      const requests = new Map();
      const get = url => {
        if (!current()) return Promise.reject(new Error("Le contexte de la base a changé."));
        if (!requests.has(url)) requests.set(url, Promise.resolve(request(url)));
        return requests.get(url);
      };
      const definition = Registry.get(resource);
      const detail = normalize(resource, await get(`${definition.endpoint}/${encodeURIComponent(id)}`));
      if (detail.id !== String(id)) throw new Error("Identifiant de fiche incohérent.");
      if (!current()) throw new Error("Le contexte de la base a changé.");
      const fields = Registry.fields(resource, detail.values);
      let options = {};
      let disorderChoices = [];
      const enrichments = [];
      if (choicePaths[resource]) enrichments.push(get(choicePaths[resource]).then(result => { options = result; }));
      const disorderIds = detail.values.desordre_ids || detail.values.object_ids?.desordres || [];
      if (disorderIds.length) enrichments.push((async () => {
        const ids = [...new Set(disorderIds.map(String))];
        for (let offset = 0; offset < ids.length; offset += 100) {
          const parameters = new URLSearchParams();
          ids.slice(offset, offset + 100).forEach(id => parameters.append("ids", id));
          const result = await get(`/api/desordres/selection?${parameters}`);
          if (!Array.isArray(result.items)) throw new Error("Libellés des Désordres indisponibles.");
          disorderChoices.push(...result.items);
        }
      })());
      if (resource === "systemes") enrichments.push(get("/api/systemes-endiguement").then(result => {
        const system = result.systemes?.find(row => String(row.id) === detail.id);
        if (system) detail.values.digues = system.digues;
        else delete detail.values.digues; // The detail endpoint's constant [] is not a search result.
      }));
      if (resource === "desordres" && Array.isArray(detail.values.troncon_ids)) enrichments.push((async () => {
        const trunks = await Promise.all(detail.values.troncon_ids.map(identifier => get(`/api/troncons/${encodeURIComponent(identifier)}`)));
        const props = trunks.map(item => item.properties || item);
        detail.values.troncon_ids = props.map(row => ({ id: row.id, designation: row.designation, libelle: row.libelle }));
        detail.values.digues = props.filter(row => row.digue_id).map(row => ({ id: row.digue_id, libelle: row.digue_libelle }));
        detail.values.systemes = props.filter(row => row.systeme_endiguement_id).map(row => ({ id: row.systeme_endiguement_id, libelle: row.systeme_endiguement_libelle }));
      })());
      if (referenceData) for (const catalog of new Set(fields.filter(spec => spec.catalog && Registry.populated(detail.values[spec.key])
          && !detail.values[spec.labelKey]).map(spec => spec.catalog))) {
        enrichments.push(referenceData.load(catalog, get).then(rows => { detail.catalogs[catalog] = rows; }));
      }
      const results = await Promise.allSettled(enrichments);
      if (!current()) throw new Error("Le contexte de la base a changé.");
      results.forEach(result => { if (result.status === "rejected") detail.errors.push(result.reason.message); });
      detail.ownerLabels = Object.fromEntries([...(options.troncons || []).map(row => [String(row.id), label(row)]), [detail.id, detail.title]]);
      for (const spec of fields.filter(item => item.format === "relations")) {
        const relation = relationRows(detail, spec);
        const optionRows = options[choicesKey[spec.resource]] || options[spec.resource] || [];
        const candidates = [...optionRows, ...(spec.resource === "desordres" ? disorderChoices : [])];
        relation.items = relation.items.map(item => {
          if (item.state !== "unresolved") return item;
          const name = label(candidates.find(row => String(row.id) === item.id));
          return name ? { ...item, label: name, state: "value" } : item;
        });
        detail.relations[spec.key] = relation;
      }
      return detail;
    }
    return { read, request };
  }
  return { create, normalize, value, relationRows, fetchJson };
});
