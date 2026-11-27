(function (root, factory) {
  const referenceData = factory();
  if (typeof module === "object" && module.exports) module.exports = referenceData;
  root.ReferenceData = referenceData;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const CATALOGS = Object.freeze({
    typesDesordre: ["/api/referentiels/types-desordre", "types"],
    categoriesDesordre: ["/api/referentiels/categories-desordre", "categories"],
    rives: ["/api/referentiels/rives", "references"],
    typesTroncon: ["/api/referentiels/types-troncon", "references"],
    typologiesTroncon: ["/api/referentiels/typologies-troncon", "references"],
    cotes: ["/api/referentiels/cotes", "references"],
    positions: ["/api/referentiels/positions", "references"],
    sources: ["/api/referentiels/sources", "references"],
    suitesApporter: ["/api/referentiels/suites-apporter", "references"],
    urgences: ["/api/referentiels/urgences", "references"],
    orientationsPhoto: ["/api/referentiels/orientations-photo", "references"],
    typesMobilier: ["/api/referentiels/types-mobilier", "references"],
    typesEquipementMesure: ["/api/referentiels/types-equipement-mesure", "references"],
    typesReseauTechnique: ["/api/referentiels/types-reseau-technique", "references"],
    typesCheminement: ["/api/referentiels/types-cheminement", "references"],
    materiauxCheminement: ["/api/referentiels/materiaux-cheminement", "references"],
    revetementsCheminement: ["/api/referentiels/revetements-cheminement", "references"],
    usagesCheminement: ["/api/referentiels/usages-cheminement", "references"],
    typesVoie: ["/api/referentiels/types-voie", "references"],
    securites: ["/api/referentiels/securites", "references"],
    orientationsOuvrage: ["/api/referentiels/orientations-ouvrage", "references"],
    typesAmenagementHydraulique: ["/api/referentiels/types-amenagement-hydraulique", "references"],
    fonctionnementsAmenagementHydraulique: ["/api/referentiels/fonctionnements-amenagement-hydraulique", "references"],
    typesPrestation: ["/api/referentiels/types-prestation", "references"],
    problematiquesVegetation: ["/api/referentiels/problematiques-vegetation", "references"],
    etatsSanitairesVegetation: ["/api/referentiels/etats-sanitaires-vegetation", "references"],
    classesHauteurVegetation: ["/api/referentiels/classes-hauteur-vegetation", "references"],
    classesDiametreVegetation: ["/api/referentiels/classes-diametre-vegetation", "references"],
    especesVegetation: ["/api/referentiels/especes-vegetation", "references"],
    domanialitesLit: ["/api/referentiels/domanialites-lit", "references"],
    regimesEcoulementLit: ["/api/referentiels/regimes-ecoulement-lit", "references"],
    occupationsRiverainesLit: ["/api/referentiels/occupations-riveraines-lit", "references"],
    typesZoneAtterrissementLit: ["/api/referentiels/types-zone-atterrissement-lit", "references"],
  });
  const cache = new Map();
  const DISORDER_CATALOGS = new Set(["typesDesordre", "categoriesDesordre"]);

  function optionLabel(row) {
    return row?.libelle_affichage || row?.libelle || "";
  }

  function disorderOptionLabel(row, label) {
    const abbreviation = typeof row?.abrege === "string" ? row.abrege.trim() : "";
    return abbreviation ? `${label} — ${abbreviation}` : label;
  }

  async function defaultFetchJson(url) {
    const response = await (globalThis.BusinessRuntime?.fetch || fetch)(url);
    let payload = null;
    try { payload = await response.json(); } catch (_error) { /* HTTP fallback. */ }
    if (!response.ok) throw new Error(payload?.detail || "Référentiel indisponible.");
    return payload;
  }

  async function load(name, fetchJson = defaultFetchJson) {
    if (!CATALOGS[name]) throw new Error(`Référentiel inconnu : ${name}.`);
    const key = globalThis.BusinessRuntime?.key(name) || name;
    if (!cache.has(key)) {
      const [url, property] = CATALOGS[name];
      cache.set(key, Promise.resolve(fetchJson(url)).then((payload) => {
        const rows = payload?.[property];
        if (!Array.isArray(rows)) throw new Error("Réponse de référentiel invalide.");
        return rows.filter((row) => row?.valid !== false).map((row) => {
          const libelle = String(row.libelle || row.designation || row.abrege || row.id);
          return {
            ...row,
            id: String(row.id),
            libelle,
            ...(DISORDER_CATALOGS.has(name)
              ? { libelle_affichage: disorderOptionLabel(row, libelle) }
              : {}),
          };
        });
      }).catch((error) => {
        cache.delete(key);
        throw error;
      }));
    }
    return cache.get(key);
  }

  function fillSelect(
    select,
    rows,
    selectedId = null,
    emptyLabel = "Aucune valeur",
    currentLabel = null,
  ) {
    select.replaceChildren();
    const empty = select.ownerDocument.createElement("option");
    empty.value = "";
    empty.textContent = emptyLabel;
    select.append(empty);
    rows.forEach((row) => {
      const option = select.ownerDocument.createElement("option");
      option.value = row.id;
      option.textContent = optionLabel(row);
      select.append(option);
    });
    const selectedValue = selectedId == null ? "" : String(selectedId);
    if (selectedValue && !rows.some((row) => row.id === selectedValue)) {
      const current = select.ownerDocument.createElement("option");
      current.value = selectedValue;
      current.textContent = `${currentLabel || "Valeur actuelle indisponible"} (actuelle)`;
      if (current.dataset) current.dataset.missing = "true";
      select.append(current);
    }
    select.value = selectedValue;
  }

  return { CATALOGS, fillSelect, load, optionLabel };
});
