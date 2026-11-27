// Objets entièrement fictifs et déterministes, utilisés seulement par le serveur
// local de prévisualisation. Le petit patrimoine historique reste partagé avec les
// tests de synchronisation de sélection Carte/Base.
const heritage = {
  systemes: [{ id: "s1", libelle: "SE 1", valid: true, digues: [{
    id: "d1", libelle: "Digue 1", valid: true,
    troncons: [{ id: "t1", libelle: "T1", valid: true }, { id: "t2", libelle: "T2", valid: true }],
  }] }],
};
const line = { type: "LineString", coordinates: [[-1.56, 47.21], [-1.55, 47.217]] };
const point = { type: "Point", coordinates: [-1.552, 47.214] };
const polygon = { type: "Polygon", coordinates: [[[-1.56, 47.21], [-1.55, 47.21], [-1.55, 47.22], [-1.56, 47.21]]] };
const troncons = { type: "FeatureCollection", features: [
  { type: "Feature", id: "t1", properties: { id: "t1", version: 1, libelle: "T1", designation: "Tronçon T1", digue_id: "d1", digue_libelle: "Digue 1", valid: true, troncon_ids: [] }, geometry: line },
  { type: "Feature", id: "t2", properties: { id: "t2", version: 1, libelle: "T2", designation: "Tronçon T2", digue_id: "d1", digue_libelle: "Digue 1", valid: true, troncon_ids: [] }, geometry: { type: "LineString", coordinates: [[-1.55, 47.217], [-1.538, 47.224]] } },
] };
const fakeId = number => `99990000-0000-4000-8000-${String(number).padStart(12, "0")}`;
// Dedicated preview examples also satisfy UUID API/navigation contracts. The
// older shared t1/t2 objects remain unchanged for unrelated synthetic suites.
const bornageTroncons = troncons.features.map((feature, index) => ({
  ...feature, id: fakeId(1200 + index), properties: { ...feature.properties,
    id: fakeId(1200 + index), libelle: `Tronçon repéré ${index + 1}`,
    designation: `Tronçon fictif avec bornage ${index + 1}` },
}));
const reperage = Object.fromEntries([...troncons.features, ...bornageTroncons].map((feature, index) => [feature.properties.id, {
  troncon_id: feature.properties.id, troncon_libelle: feature.properties.libelle,
  systeme_reperage_id: fakeId(1000 + index), systeme_reperage_libelle: `Repérage fictif ${index + 1}`,
  bornes: ["Début", "Fin"].map((label, endpoint) => ({
    id: fakeId(1100 + index * 10 + endpoint), libelle: `Borne ${label.toLowerCase()} ${feature.properties.libelle}`,
    libelle_affichage: `Borne ${label.toLowerCase()} ${feature.properties.libelle}`,
    role_spatial: "BORNE", valid: true, valeur_pr: endpoint * 1000,
  })),
}]));
const previewHeritage = { systemes: [{ ...heritage.systemes[0], digues: [
  heritage.systemes[0].digues[0], { id: "d2", libelle: "Digue 2", valid: true, troncons: [] },
] }] };
const feature = (number, designation, geometry, extra = {}) => ({ type: "Feature", id: fakeId(number), geometry,
  properties: { id: fakeId(number), version: 1, designation, libelle: designation, commentaire: "Exemple fictif", valid: true,
    date_debut: "2024-01-01", date_fin: null, ...extra } });
const descriptor = (number, segmentId, values) => ({ id: fakeId(number), segment_id: segmentId,
  designation: `Occurrence ${number}`, commentaire: null, valid: true, date_debut: "2024-01-01",
  date_fin: null, pr_debut: "0", pr_fin: null, ...values });
const lit = feature(20, "Tronçon de lit fictif", line, {
  digue_ids: ["d1", "d2"], digues: [{ id: "d1", libelle: "Digue 1", valid: true }, { id: "d2", libelle: "Digue 2", valid: true }],
  largeurs_lit: [descriptor(21, fakeId(20), { largeur_moyenne: "12.50" }), descriptor(22, fakeId(20), { largeur_moyenne: "12.50" })],
  pentes_lit: [descriptor(23, fakeId(20), { pente_moyenne: "0.25" })],
  domanialites_lit: [descriptor(24, fakeId(20), { type_id: "RefDomanialiteLit:2" })],
  regimes_ecoulement_lit: [descriptor(25, fakeId(20), { regime_id: "RefRegimeLit:99" })],
  occupations_riveraines_lit: [descriptor(26, fakeId(20), { type_occupation_id: "RefOccupationLit:99", rive_id: "RefRive:1" })],
  elements_morphologiques_lit: [{ id: fakeId(30), type_morphologie: "ILE", designation: "Île fictive", valid: true }],
  geometry_diagnostics: [], geometry_srid: 3950,
});
const morphology = feature(30, "Île fictive", polygon, {
  type_morphologie: "ILE", segment_id: fakeId(20), segment_libelle: lit.properties.designation,
  longueur: "31.5", largeur: "8.2", rattache_berge: false, vegetalise: true, type_zone_id: null,
  geometry_diagnostics: [], geometry_srid: 3950,
});
const invalidMorphology = feature(31, "Banc historique non conforme", { type: "MultiPoint", coordinates: [[-1.554, 47.215], [-1.553, 47.216]] }, {
  type_morphologie: "INDETERMINE", segment_id: null, longueur: "0", largeur: null,
  rattache_berge: null, vegetalise: false, type_zone_id: null, valid: false,
  geometry_srid: 3950,
  geometry_diagnostics: ["FORME_HORS_CONTRAT"],
});
const records = {
  desordres: [feature(10, "Désordre fictif", point, { troncon_ids: ["t1"], type_id: "RefTypeDesordre:99", urgence_id: "RefUrgence:99", observation_count: 1,
    // Synthetic existing selection outside the active catalog. This tests the
    // real current-value fallback; it does not add a reference to the catalog.
    cote_id: "preview-inactive-cote", cote_libelle: "Côté historique fictif" })],
  mobilier: [feature(40, "Mobilier fictif", point, { troncon_id: "t1", desordre_ids: [] })],
  "equipements-mesure": [feature(41, "Équipement de mesure fictif", point, { troncon_id: "t1", desordre_ids: [] })],
  "reseaux-techniques": [feature(42, "Réseau technique fictif", line, { troncon_id: "t1", desordre_ids: [] })],
  "ouvrages-particuliers": [feature(43, "Ouvrage particulier fictif", point, { troncon_id: "t1", desordre_ids: [fakeId(10)], type_id: null })],
  cheminements: [feature(44, "Cheminement fictif", line, { troncon_ids: ["t1"], desordre_ids: [], type_cheminement_id: null })],
  vegetation: [feature(45, "Végétation fictive", polygon, { famille_vegetation: "ARBRES", disposition: "ISOLE",
    sous_type: "ARBRE_SUR_PIED", invasif: false, contact_eau: false, troncon_ids: ["t1", "t2"],
    amenagement_hydraulique_ids: [fakeId(46)], desordre_ids: [], problematique_ids: [] })],
  "amenagements-hydrauliques": [feature(46, "Aménagement hydraulique non dessiné", null, { troncon_ids: ["t2"], zone_ids: [fakeId(48)], desordre_ids: [], type_id: null })],
  "zones-stockage": [feature(48, "Zone de stockage fictive", polygon, {
    amenagement_hydraulique_id: fakeId(46), reseau_hydraulique_ids: [], collectivite_competente_id: null,
  })],
  prestations: [feature(47, "Prestation fictive", point, { troncon_ids: ["t1"], desordre_ids: [], intervenant_ids: [], object_ids: {}, type_prestation_id: null })],
  "troncons-lit": [lit], "elements-morphologiques-lit": [morphology, invalidMorphology],
};
const observation = { id: fakeId(50), version: 1, designation: "Observation fictive", date: "2024-06-01", valid: true,
  parent: { resource: "ouvrages_particuliers", id: fakeId(43), designation: "Ouvrage particulier fictif" },
  photo_count: 1, photos: [{ id: fakeId(51), observation_id: fakeId(50), version: 1,
    libelle: "Photo fictive sans fichier", content_available: false }] };
module.exports = { heritage, troncons, previewHeritage, records, observation, fakeId, reperage, bornageTroncons };
