"""Independent, deliberately open inventory of patrimonial structural members.

The declarations below are reconciled from the cited decisions, not copied from
``registry.targets``. Source EClasses are inventoried separately: observing one
never approves a target concept or the identity of a structured value.
"""

from __future__ import annotations

from copy import deepcopy

from .inventory_common import candidate


SOURCE_PATHS = {
    "evidence:mod-1c0": "docs/maintenance/mod-1c0-frontiere-metier-identifiants.md",
    "evidence:mod-1c0b": "docs/maintenance/mod-1c0b-arbitrages-frontiere-metier.md",
    "evidence:consolidation": "docs/maintenance/mod-1c0c-consolidation-decisions.md",
    "evidence:db-1": "docs/maintenance/audit-purge-modele-v2.md",
}

SCOPE = "scope:patrimoine"
PRESERVATION = [
    "Conserver les occurrences distinctes, identifiants, valeurs, références et géométries historiques.",
    "Une identité de modèle candidate n'approuve ni une clé canonique ni une projection d'occurrences.",
]


def _id(kind: str, code: str) -> str:
    return f"candidate:{kind}:patrimoine/{code}"


def _source(model: str, name: str) -> str:
    return f"sirs-v2:class:{model}/{name}"


def _proof(source: str, locator: str) -> dict:
    return {"source": source, "locator": locator}


# Code, definition, decision, source classes. These are semantic declarations,
# including the missing Île/Banc member: target presence is checked elsewhere.
CONCEPTS = (
    ("systeme-endiguement", "Système patrimonial auquel peuvent appartenir des digues et le périmètre exclusif d'un Plan de gestion.", "B3", ("core/SystemeEndiguement",)),
    ("digue", "Digue patrimoniale, distincte du Système d'endiguement et des Tronçons qui la décrivent.", "DB1-hierarchie-patrimoine", ("core/Digue",)),
    ("troncon-digue", "Tronçon de digue patrimonial support du repérage et des rattachements métier directs.", "DB1-hierarchie-patrimoine", ("core/TronconDigue",)),
    ("contact", "Personne contact métier, distincte du compte Utilisateur et reliée à des Organismes par des périodes.", "FM-06", ("core/Contact",)),
    ("organisme", "Organisation intervenant dans le patrimoine, distincte d'une Structure administrative de contrôle.", "FM-06", ("core/Organisme",)),
    ("desordre", "Désordre patrimonial unifiant les origines core, Dépendance et Lit sans fusion d'occurrences.", "DB1-desordres", ("core/Desordre", "dependance/DesordreDependance", "lit/DesordreLit")),
    ("prestation", "Réalisation autonome de prestation ; ne se confond ni avec une Prescription ni avec GlobalPrestation.", "DB1-prestations", ("core/Prestation", "dependance/PrestationAmenagementHydraulique")),
    ("amenagement-hydraulique", "Aménagement hydraulique patrimonial portant des structures, traits et gestionnaires ; peut être le périmètre exclusif d'un Plan.", "DB1-amenagements", ("dependance/AmenagementHydraulique",)),
    ("vegetation", "Végétation patrimoniale typée Herbacées, Buissons/Arbustes ou Arbres ; ni Parcelle ni Désordre.", "FM-05", ("vegetation/ZoneVegetation",)),
    ("plan-gestion", "Plan portant exactement un périmètre Système d'endiguement ou Aménagement hydraulique et des Prescriptions réutilisables.", "B3", ("vegetation/PlanVegetation",)),
    ("prescription", "Définition identitaire réutilisable du traitement et de ses paramètres, clôturée puis recréée en cas de modification réelle.", "B3", ()),
    ("structure-protection", "Ouvrage de protection dont les types exclusifs sont Épi et Revanche, indépendamment de son origine historique.", "FM-01", ("core/Epi", "core/OuvrageRevanche", "berge/EpiBerge", "berge/OuvrageRevancheBerge")),
    ("ouvrage-hydraulique", "Ouvrage patrimonial hydraulique à sept types exclusifs, distinct d'un réseau technique.", "FM-02", ("core/Deversoir", "core/StationPompage", "core/OuvertureBatardable", "core/OuvrageHydrauliqueAssocie", "lit/SeuilLit", "lit/PlageDepotLit")),
    ("ouvrage-particulier", "Ouvrage particulier accueillant également AutreDependance sans imposer un type ou une destination inconnus.", "DB1-particuliers", ("core/OuvrageParticulier", "dependance/AutreDependance")),
    ("equipement-mesure", "Instrument de mesure patrimonial ; son identité est distincte du relevé ou de la série de mesures.", "FM-10", ("core/EchelleLimnimetrique",)),
    ("ouvrage-telecom-energie", "Ouvrage télécom ou énergie qualifié par un type métier et ses relations au réseau.", "DB1-telecom", ("core/OuvrageTelecomEnergie",)),
    ("ouvrage-voirie", "Ouvrage de voirie unifiant les origines core et Dépendance avec les types métier conservés.", "DB1-voirie", ("core/OuvrageVoirie",)),
    ("reseau-hydraulique", "Réseau hydraulique de nature OUVERT ou FERME conservant écoulement, implantation, utilisation et sécurité.", "DB1-reseaux", ("core/ReseauHydrauliqueCielOuvert", "core/ReseauHydrauliqueFerme")),
    ("cheminement", "Voie d'accès ou cheminement patrimonial autonome, distinct de l'ouvrage permettant un franchissement.", "FM-09", ("core/VoieAcces", "core/VoieDigue")),
    ("franchissement", "Ouvrage permettant de franchir un obstacle, distinct du cheminement même lorsque le stockage SQL est partagé.", "FM-09", ("core/OuvrageFranchissement",)),
    ("profil-travers", "Profil topographique en travers, distinct de ses Positions, Levés et points de mesure.", "FM-11A", ("core/ProfilTravers",)),
    ("profil-long", "Profil topographique en long avec points ordonnés et paramètres hydrauliques conservés.", "DB1-profils", ("core/ProfilLong",)),
    ("leve-profil", "Levé de profil avec date, origine, système de relevé et points ; distinct de la Position de profil.", "FM-11A", ("core/LeveProfilTravers",)),
    ("evenement-hydraulique", "Événement hydraulique portant ses caractéristiques et des relations aux relevés, instruments et objets.", "DB1-evenements", ("core/EvenementHydraulique",)),
    ("objet-mesure-hydraulique", "Relevé ou trace hydraulique de type Ligne d'eau, Montée des eaux ou Laisse de crue, distinct de l'instrument.", "FM-10", ("core/LigneEau", "core/MonteeEaux", "core/LaisseCrue")),
    ("lit", "Lit actif parent de Tronçons de lit, avec identité distincte et sans dépendance implicite à Berge.", "FM-13", ("lit/Lit",)),
    ("troncon-lit", "Tronçon de lit, ancien Segment hydrographique ; chaque occurrence cible valide possède exactement un Lit.", "FM-13", ("lit/TronconLit",)),
    ("berge", "Berge patrimoniale indépendante de Lit, propriétaire de ses composants Pied, Talus et Sommet.", "FM-11B", ("berge/Berge",)),
    ("ile-banc-lit", "Île ou banc du lit, objet patrimonial conservé avec ses dimensions, végétalisation et rattachements historiques.", "DB1-lit-residuel", ("lit/IleBancLit",)),
)

TYPES = (
    ("structure-protection", "concept", "FM-01", (("epi", "Épi"), ("revanche", "Revanche"))),
    ("ouvrage-hydraulique", "concept", "FM-02", (("deversoir", "Déversoir"), ("station-pompage", "Station de pompage"), ("ouverture-batardable", "Ouverture batardable"), ("seuil-lit", "Seuil de lit"), ("plage-depot", "Plage de dépôt"), ("prise-eau", "Prise d'eau"), ("associe", "Ouvrage hydraulique associé"))),
    ("structure-digue", "component", "FM-03", (("crete", "Crête"), ("fondation", "Fondation"), ("pied-digue", "Pied de digue"), ("talus-digue", "Talus de digue"), ("sommet-risberme", "Sommet de risberme"), ("talus-risberme", "Talus de risberme"))),
    ("vegetation", "concept", "FM-05", (("herbacees", "Herbacées"), ("buissons-arbustes", "Buissons / Arbustes"), ("arbres", "Arbres"))),
    ("objet-mesure-hydraulique", "concept", "FM-10", (("ligne-eau", "Ligne d'eau"), ("montee-eaux", "Montée des eaux"), ("laisse-crue", "Laisse de crue"))),
)

# Code, definition, decision, owner kind/code, source model/class and locator.
COMPONENTS = (
    ("observation", "Constat métier identitaire portant exactement sur un sujet métier dont l'univers reste à qualifier.", "FM-04", None, None, "core", "Observation", "#//Observation"),
    ("photo", "Photo identitaire avec original média et repérage, possédant exactement une Observation cible.", "FM-04", "component", "observation", "core", "Photo", "#//AbstractObservation/photos"),
    ("detail-observation-ouvrage-reseau", "Détail spécialisé d'une Observation ouvrage ou réseau, au plus un par Observation.", "FM-04", "component", "observation", "core", "AbstractObservationReseauEtOuvrage", "#//AbstractObservationReseauEtOuvrage"),
    ("position-profil", "Position identitaire dépendant d'un Profil en travers, distincte de ses relations aux Levés.", "FM-11A", "concept", "profil-travers", "core", "PositionProfilTravers", "#//PositionProfilTravers/sirsdocument"),
    ("structure-digue", "Composant identitaire localisé possédé exactement par un Tronçon de digue, Tronçon de lit ou Berge.", "FM-03", None, None, "core", "ObjetStructure", "#//ObjetStructure"),
    ("berge/pied", "Pied de Berge identitaire avec sa géométrie et son histoire.", "FM-11B", "concept", "berge", "berge", "PiedBerge", "#//PiedBerge"),
    ("berge/talus", "Talus de Berge identitaire avec sa géométrie et son histoire.", "FM-11B", "concept", "berge", "berge", "TalusBerge", "#//TalusBerge"),
    ("berge/sommet", "Sommet de Berge identitaire avec sa géométrie et son histoire.", "FM-11B", "concept", "berge", "berge", "SommetBerge", "#//SommetBerge"),
    ("ouvrage-hydraulique/dispositif-fusible", "Dispositif fusible identitaire d'un Déversoir.", "FM-11C", "type", "ouvrage-hydraulique/deversoir", "core", "DispositifFusible", "#//Deversoir/fusible"),
    ("ouvrage-hydraulique/fosse-dissipation", "Fosse de dissipation identitaire d'un Déversoir.", "FM-11C", "type", "ouvrage-hydraulique/deversoir", "core", "FosseDissipation", "#//Deversoir/fosse"),
    ("ouvrage-hydraulique/seuil-deversoir", "Seuil composant identitaire d'un Déversoir, distinct de l'ouvrage de type Seuil de lit.", "FM-11C", "type", "ouvrage-hydraulique/deversoir", "core", "Seuil", "#//Deversoir/seuil"),
    ("ouvrage-hydraulique/pompe", "Pompe identitaire d'une Station de pompage.", "FM-11C", "type", "ouvrage-hydraulique/station-pompage", "core", "Pompe", "#//StationPompage/pompes"),
    ("seuil-lit/couche", "Couche identitaire dépendante d'un Seuil de lit.", "FM-11C", "type", "ouvrage-hydraulique/seuil-lit", "lit", "CoucheSeuilLit", "#//SeuilLit/couches"),
    ("seuil-lit/inspection", "Inspection identitaire dépendante d'un Seuil de lit.", "FM-11C", "type", "ouvrage-hydraulique/seuil-lit", "lit", "InspectionSeuilLit", "#//SeuilLit/inspections"),
    ("seuil-lit/plan", "Pièce technique identitaire de Seuil de lit avec désignation et fichier à extension libre.", "PlanSeuilLit", "type", "ouvrage-hydraulique/seuil-lit", "lit", "PlanSeuilLit", "#//SeuilLit/plans"),
    ("plage-depot/barrage", "Barrage de régulation identitaire dépendant d'une Plage de dépôt.", "FM-11C", "type", "ouvrage-hydraulique/plage-depot", "lit", "BarrageLit", "#//PlageDepotLit/barrages"),
    ("plage-depot/ouvrage-freineur", "Ouvrage freineur identitaire dépendant d'une Plage de dépôt.", "FM-11C", "type", "ouvrage-hydraulique/plage-depot", "lit", "OuvrageFreineurLit", "#//PlageDepotLit/ouvragesFreineurs"),
    ("structure-amenagement-hydraulique", "Structure d'Aménagement hydraulique conservant couche, épaisseur, matériau, fonction, nature et géométrie.", "DB1-amenagements", "concept", "amenagement-hydraulique", "dependance", "StructureAmenagementHydraulique", "#//StructureAmenagementHydraulique"),
    ("trait-amenagement-hydraulique", "Trait géométrique métier d'un Aménagement hydraulique conservant son parent obligatoire.", "DB1-amenagements", "concept", "amenagement-hydraulique", "dependance", "TraitAmenagementHydraulique", "#//TraitAmenagementHydraulique"),
    ("trait-berge", "Trait géométrique métier d'une Berge conservant parent, rôle et géométrie.", "DB1-amenagements", "concept", "berge", "berge", "TraitBerge", "#//TraitBerge"),
)

B2 = (
    ("largeur-lit", "LargeurLit"), ("pente-lit", "PenteLit"),
    ("domanialite-lit", "DomanialiteLit"),
    ("regime-ecoulement-lit", "RegimeEcoulementLit"),
    ("occupation-riveraine-lit", "OccupationRiveraineLit"),
    ("zone-atterrissement-lit", "ZoneAtterrissementLit"),
)

# Their existence and preservation are decided. The representation as an
# identitary component, or as a structured value, is deliberately NOT decided.
STRUCTURED_VALUES = (
    ("evenement/meteo", "Description météorologique historique d'un Événement hydraulique, distincte des données externes courantes.", "DB1-evenements", "concept", "evenement-hydraulique", "Meteo", "EvenementHydraulique/meteos", "public.evenements_hydrauliques__meteos"),
    ("montee-eaux/mesure", "Mesure datée d'une montée des eaux avec hauteur, débit, observateur et référence de hauteur.", "FM-10", "type", "objet-mesure-hydraulique/montee-eaux", "MesureMonteeEaux", "MonteeEaux/mesures", "public.mesures_montees_eaux"),
    ("ligne-eau/mesure-xyz", "Point XYZ d'une Ligne d'eau, avec coordonnées et ordre métier à conserver.", "FM-10", "type", "objet-mesure-hydraulique/ligne-eau", "MesureLigneEauXYZ", "LigneEau/mesuresXYZ", "public.mesures_lignes_eau"),
    ("ligne-eau/mesure-prz", "Point PR/Z d'une Ligne d'eau conservant le système de repérage et les valeurs d et z.", "FM-10", "type", "objet-mesure-hydraulique/ligne-eau", "MesureLigneEauPrZ", "LigneEau/mesuresDZ", "public.mesures_lignes_eau"),
    ("profil-long/point-xyz", "Point XYZ ordonné d'un Profil en long, non confondu avec le profil parent.", "DB1-profils", "concept", "profil-long", "XYZProfilLong", "ProfilLong/pointsLeveXYZ", "public.points_profils"),
    ("profil-long/point-prz", "Point PR/Z ordonné d'un Profil en long, conservant distances et altitudes.", "DB1-profils", "concept", "profil-long", "PrZProfilLong", "ProfilLong/pointsLeveDZ", "public.points_profils"),
    ("leve-profil/point-xyz", "Point XYZ ordonné d'un Levé de profil en travers.", "DB1-profils", "concept", "leve-profil", "XYZLeveProfilTravers", "LeveProfilTravers/pointsLeveXYZ", "public.points_profils"),
    ("leve-profil/point-dz", "Point DZ ordonné d'un Levé de profil en travers.", "DB1-profils", "concept", "leve-profil", "DZLeveProfilTravers", "LeveProfilTravers/pointsLeveDZ", "public.points_profils"),
    ("profil-long/parametre-hydraulique", "Paramètres hydrauliques d'un Profil en long avec PR début/fin et débit de premier débordement.", "DB1-profils", "concept", "profil-long", "ParametreHydrauliqueProfilLong", "ProfilLong/parametresHydrauliques", "public.parametres_hydrauliques_profils"),
    ("profil-travers/parametre-hydraulique", "Paramètres hydrauliques d'un Profil en travers avec débit/vitesse de pointe et cote d'eau.", "DB1-profils", "concept", "profil-travers", "ParametreHydrauliqueProfilTravers", "ProfilTravers/parametresHydrauliques", "public.parametres_hydrauliques_profils"),
)

STRUCTURED_COLUMNS = {
    "evenement/meteo": ["id", "evenement_hydraulique_id", "date_debut", "date_fin", "vitesse_vent", "pression", "orientation_vent_id"],
    "montee-eaux/mesure": ["id", "objet_mesure_hydraulique_id", "date_mesure", "hauteur", "debit_max", "observateur_id", "reference_hauteur_id"],
    "ligne-eau/mesure-xyz": ["id", "objet_mesure_hydraulique_id", "type_point", "x", "y", "z"],
    "ligne-eau/mesure-prz": ["id", "objet_mesure_hydraulique_id", "type_point", "d", "z"],
    "profil-long/point-xyz": ["id", "profil_long_id", "type_point", "ordre_point", "x", "y", "z"],
    "profil-long/point-prz": ["id", "profil_long_id", "type_point", "ordre_point", "d", "z"],
    "leve-profil/point-xyz": ["id", "leve_profil_travers_id", "type_point", "ordre_point", "x", "y", "z"],
    "leve-profil/point-dz": ["id", "leve_profil_travers_id", "type_point", "ordre_point", "d", "z"],
    "profil-long/parametre-hydraulique": ["id", "profil_long_id", "type_parametre", "pr_debut", "pr_fin", "debit_premier_debordement", "evenement_hydraulique_id"],
    "profil-travers/parametre-hydraulique": ["id", "profil_travers_id", "type_parametre", "debit_pointe", "vitesse_pointe", "cote_eau", "evenement_hydraulique_id"],
}

OPEN_MEMBERS = (
    ("mobilier", "Mobilier patrimonial déjà exposé par l'application ; sa frontière avec Ouvrage particulier n'est pas approuvée.", ("concept", "type"), (), "§3.1 ligne 21", None),
    ("reseau-technique", "Réseau réel télécom/énergie, distinct de l'Ouvrage hydraulique associé.", ("concept", "type"), ("core/ReseauTelecomEnergie",), "§3.1 ligne 24", None),
    ("borne-reperage", "Borne servant au repérage linéaire ; autonomie et propriétaires cibles restent à qualifier.", ("concept", "component"), ("core/BorneDigue",), "§3.1 ligne 6", None),
    ("systeme-reperage", "Système de repérage linéaire avec associations ordonnées de bornes, distinct de ses associations.", ("concept", "component"), ("core/SystemeReperage",), "§3.1 ligne 7", None),
    ("organe-protection-collective", "Organe conservé d'un Aménagement hydraulique avec type, état, cote et géométrie ; genre canonique non approuvé.", ("concept", "component"), ("dependance/OrganeProtectionCollective",), "§2 Aménagements hydrauliques ; §4 parent AH obligatoire", "amenagement-hydraulique"),
    ("largeur-franc-bord", "Caractéristique ou valeur localisée rattachée historiquement directement au Tronçon de digue ; jamais septième type physique de structure.", ("attribute", "component"), ("core/LargeurFrancBord",), "§2 FM-03 ; §4 B4", "troncon-digue"),
)

DEFERRED_CLASSES = frozenset({
    "RapportEtude", "DocumentGrandeEchelle", "ArticleJournal", "Marche",
    "MaitreOeuvreMarche", "Convention", "AssociationConvention", "ConventionPJ",
    "PositionConvention", "ObligationReglementaire", "EtapeObligationReglementaire",
    "PlanificationObligationReglementaire", "AireStockageDependance", "MateriauDependance",
    "GardeObjet", "GestionObjet", "ProprieteObjet", "GlobalPrestation",
})


def _decision_proof(decision: str) -> dict:
    if decision == "MOD-1O-L02-B":
        return _proof("instruction:mod-1o-l02-b", "Morphologie L02-B : famille commune, géométrie autonome et rattachement facultatif explicite")
    if decision == "MOD-1O-L02-rectificatif":
        return _proof("instruction:mod-1o-l02-rectificatif", "Rectificatif L02 : cinq descriptifs et famille morphologique distincte")
    if decision.startswith("MOD1M-"):
        return _proof("instruction:mod-1m", "Décisions Core / Berge / Lit des 19 et 20 octobre 2026 ; " + decision)
    if decision.startswith("DB1-"):
        return _proof("evidence:db-1", f"§2, §4 et §5 ; {decision}")
    return _proof("evidence:consolidation", f"§1 approbations ; §2 et §4 {decision}")


def _apply_mod1m(rows, facts):
    """Apply the explicit succession to the independently enumerated members.

    Historical candidates remain visible; this is not a new registry or a
    lookup of registry.targets. The previous member description is retained as
    evidence of the exact scope replaced by the dated instruction.
    """
    indexed = {row["id"]: row for row in rows}

    def evolve(kind, code, decision, definition, **changes):
        row = indexed[_id(kind, code)]
        row["details"]["previous_contract"] = {
            key: deepcopy(row[key]) for key in ("definition", "owner", "status", "target")}
        row["definition"] = definition
        row["decision_refs"].append("decision:" + decision)
        row["depends_on"].append("decision:" + decision)
        row["provenance"].append(_decision_proof(decision))
        row.update(changes)
        return row

    evolve("concept", "lit", "MOD1M-lit",
           "Ancien concept cible Lit retiré ; informations historiques conservées avec issue explicite, sans conteneur cible fictif.",
           status="EXCLU", questions=[])
    troncon = evolve("concept", "troncon-lit", "MOD1M-lit",
                    "Tronçon de lit autonome ; association facultative N:N à Digue ; les six familles B2 restent ses composants identitaires.",
                    owner=None)
    troncon["depends_on"] = [value for value in troncon["depends_on"] if value != _id("concept", "lit")]
    berge = evolve("concept", "berge", "MOD1M-berge",
                   "Berge unique issue de Berge et TraitBerge ; équivalence intégrale exacte après projection des attributs, avant suffixage, et correspondance de chaque UUID source.")
    trait = _source("berge", "TraitBerge")
    if trait in facts:
        # The base constructor intentionally shares its source list with the
        # evidence keys. Assign separate sets here instead of appending twice.
        berge["evidence_keys"] = sorted({*berge["evidence_keys"], trait})
        berge["depends_on"].append(trait)
        berge["details"]["source_classes"] = sorted({*berge["details"]["source_classes"], trait})
    berge["preservation"].append("Seule la fusion intégralement équivalente Berge/TraitBerge est permise ; les UUID sources restent dans la correspondance déterministe.")
    evolve("component", "trait-berge", "MOD1M-berge",
           "Ancienne cible Trait de Berge retirée ; chaque source rejoint Berge avec correspondance d'identité explicite.",
           status="EXCLU", questions=[])
    structure = evolve("component", "structure-digue", "MOD1M-core",
                       "Structure descriptive Core associée directement à sa Digue éventuelle, résolue par le Tronçon historique ; conservée sans Digue et sans découpage géométrique.",
                       owner=_id("concept", "digue"))
    structure["details"]["owners"] = [_id("concept", "digue")]
    structure["details"]["owner_cardinality"] = "0..1"
    structure["depends_on"] = [value for value in structure["depends_on"] if value not in
                                 {_id("concept", code) for code in ("troncon-digue", "troncon-lit", "berge")}]
    structure["depends_on"].append(_id("concept", "digue"))
    for kind, code in (("type", "structure-digue/talus-digue"),
                       ("type", "structure-digue/talus-risberme"), ("component", "berge/talus")):
        row = evolve(kind, code, "MOD1M-core",
                     "Ancien type de Talus remplacé par le Talus commun ; occurrences distinctes et distinctions Nature/Côté conservées.",
                     status="EXCLU", questions=[])
        row["details"]["successor_target"] = "da:type:structure-digue/talus"

    proof = [_decision_proof("MOD1M-core")]
    talus_sources = [source for source in (_source("core", "TalusDigue"), _source("core", "TalusRisberme"),
                                         _source("berge", "TalusBerge")) if source in facts]
    rows.append(candidate(_id("type", "structure-digue/talus"), "type", SCOPE,
                          "Talus commun aux sources Core et Berge ; Nature et Côté indépendants, sans fusion des occurrences.",
                          proof, status="DECIDE", owner=_id("component", "structure-digue"),
                          target="da:type:structure-digue/talus", decision_refs=["decision:MOD1M-core"],
                          depends_on=["decision:MOD1M-core", _id("component", "structure-digue"), *talus_sources],
                          evidence_keys=talus_sources, preservation=list(PRESERVATION), questions=[],
                          details={"identity": "same_occurrence_as_parent", "source_classes": talus_sources,
                                   "missing_source_classes": [], "occurrence_merge": "forbidden"}))
    for code, label, values in (("nature", "Nature", ["Principal", "Risberme", "Indéfini"]),
                                ("cote", "Côté", ["Rivière-mer", "Terre", "Indéfini"])):
        rows.append(candidate(_id("attribute", "talus/" + code), "attribute", SCOPE,
                              f"{label} du Talus, indépendante de l'autre attribut, domaine fermé approuvé.",
                              proof, status="DECIDE", owner=_id("type", "structure-digue/talus"),
                              target="da:attribute:talus/" + code, decision_refs=["decision:MOD1M-core"],
                              depends_on=["decision:MOD1M-core", _id("type", "structure-digue/talus")],
                              preservation=list(PRESERVATION), questions=[],
                              details={"name": code, "role": "business", "value_type": "string", "unit": None,
                                       "catalog": None, "cardinality": "A_APPROUVER", "storage": "A_APPROUVER",
                                       "approved_values": values, "independent_of": "cote" if code == "nature" else "nature"}))
    rows.append(candidate(_id("relation", "digue-troncon-lit"), "relation", SCOPE,
                          "Association facultative N:N entre Digue et Tronçon de lit autonome, sans inférence spatiale.",
                          [_decision_proof("MOD1M-lit")], status="DECIDE", target="da:relation:digue-troncon-lit",
                          decision_refs=["decision:MOD1M-lit"],
                          depends_on=["decision:MOD1M-lit", _id("concept", "digue"), _id("concept", "troncon-lit")],
                          preservation=list(PRESERVATION), questions=[],
                          details={"endpoints": [{"role": role, "target": _id("concept", code),
                                                   "cardinality": {"min": 0, "max": "*"}}
                                                  for role, code in (("digue", "digue"), ("troncon_lit", "troncon-lit"))],
                                   "inference": "forbidden"}))


def inventory(historical: dict, paths: dict) -> list[dict]:
    """Enumerate semantic decisions and all EClasses without consulting targets."""
    del paths  # All provenance handles are checked against the pinned manifest.
    result = []
    facts = {f["id"]: f for f in historical["facts"]}
    classes = [f for f in historical["facts"] if f["kind"] == "class"]

    def semantic(kind, code, definition, decision, source_classes=(), owner=None, **kw):
        source_ids = [_source(*value.split("/", 1)) for value in source_classes]
        present = [value for value in source_ids if value in facts]
        # A source listed incorrectly must remain a visible gap, never an
        # invented historical handle or proof of origin.
        missing = sorted(set(source_ids) - set(present))
        details = dict(kw.pop("details", {}))
        details.update(source_classes=present, missing_source_classes=missing)
        questions = list(kw.pop("questions", []))
        if missing:
            questions.append("Correspondances historiques à qualifier : " + ", ".join(missing))
        item = candidate(
            _id(kind, code), kind, SCOPE, definition,
            [_decision_proof(decision)], owner=owner,
            status="DECIDE", decision_refs=[f"decision:{decision}"],
            target=f"da:{kind}:{code}",
            depends_on=[f"decision:{decision}", *present, *([owner] if owner else [])],
            preservation=list(PRESERVATION), questions=questions,
            evidence_keys=present, details=details, **kw,
        )
        result.append(item)
        return item

    for code, definition, decision, sources in CONCEPTS:
        owner = _id("concept", "lit") if code == "troncon-lit" else None
        item = semantic("concept", code, definition, decision, sources, owner)
        if code == "ile-banc-lit":
            item["decision_refs"].append("decision:MOD1L-ile-banc")
            item["depends_on"].append("decision:MOD1L-ile-banc")
            item["provenance"].append(_proof("evidence:mod-1c0", "§3.1 ligne 42"))
            item["binding"] = {
                "status": "ABSENT", "supports": [],
                "gaps": ["Ancienne destination remplacée par la famille morphologique commune MOD-1O-L02-B."],
            }
            item["questions"].append("Parent, cardinalités, origine native et contrat géométrique cible à qualifier.")
        if code == "prescription":
            item["details"]["historical_conversion"] = "Aucune conversion automatique des TraitementZoneVegetation/TraitementParcelleVegetation."

    for parent, parent_kind, decision, entries in TYPES:
        for code, label in entries:
            semantic("type", f"{parent}/{code}", f"{label} : type métier exclusif de {parent} selon {decision}.", decision,
                     owner=_id(parent_kind, parent), details={"identity": "same_occurrence_as_parent"})
    for code, label in (("arbre-sur-pied", "Arbre sur pied"), ("souche", "Souche")):
        semantic("subtype", f"vegetation/{code}", f"{label} : sous-type d'Arbres, distinct de disposition et invasif.", "FM-05",
                 owner=_id("type", "vegetation/arbres"), details={"identity": "same_occurrence_as_parent"})

    for code, definition, decision, owner_kind, owner_code, model, cls, locator in COMPONENTS:
        owner = _id(owner_kind, owner_code) if owner_kind else None
        item = semantic("component", code, definition, decision, (f"{model}/{cls}",), owner,
                        details={"identity": "distinct", "owner_cardinality": "1" if owner else "A_APPROUVER"})
        item["provenance"].append(_proof(f"ecore:{model}", locator))
        if code == "structure-digue":
            item["details"].update(owners=[_id("concept", p) for p in ("troncon-digue", "troncon-lit", "berge")],
                                   owner_cardinality="exactly_one_of_allowed_owners")
            item["depends_on"].extend(item["details"]["owners"])
            item["questions"].append("Bornes inverses par propriétaire et classement fonctionnel du franc-bord à qualifier ; le rattachement source direct de celui-ci n'est pas remplacé.")
        if code == "observation":
            item["questions"].append("Inventaire fermé des sujets métier autorisés et cardinalités inverses non approuvé.")
    for code, cls in B2:
        item = semantic("component", code,
                        f"{cls} : composant métier identitaire localisé du Tronçon de lit, sans aplatissement en scalaire.",
                        "B2", (f"lit/{cls}",), _id("concept", "troncon-lit"),
                        details={"identity": "distinct", "owner_cardinality": "1", "children_per_owner": "0..N"})
        item["preservation"].append("Conserver chaque occurrence, ses dates et repérages ; ni parent inventé, ni fusion, ni synthèse sans règle explicite.")

    for code, definition, decision, owner_kind, owner_code, cls, locator, table in STRUCTURED_VALUES:
        source = _source("core", cls)
        reference = f"sirs-v2:reference:core/{locator}"
        declared_reference = facts.get(reference)
        source_cardinality = "UNKNOWN"
        if declared_reference is not None:
            bounds = declared_reference["data"]
            source_cardinality = {"min": bounds["lower"],
                                  "max": "*" if bounds["upper"] == -1 else bounds["upper"]}
        owner = _id(owner_kind, owner_code)
        result.append(candidate(
            _id("unknown", code), "unknown", SCOPE, definition,
            [_decision_proof(decision), _proof("ecore:core", f"#//{locator}"),
             _proof("sql:business", f"CREATE TABLE {table}")],
            owner=owner, decision_refs=[f"decision:{decision}"],
            depends_on=[owner, source, reference, f"decision:{decision}"], evidence_keys=[source, reference],
            preservation=[*PRESERVATION, "Conserver valeurs, ordre métier démontré, propriétaire et références ; ne pas déduire une identité autonome de la PK SQL."],
            questions=["Genre composant ou valeur structurée, identité cible et bornes à qualifier."],
            binding={"status": "BINDING_PARTIEL", "supports": [{"source": "sql:business", "table": table,
                                                                     "columns": list(STRUCTURED_COLUMNS[code])}],
                     "gaps": ["Présence physique localisée uniquement ; identité, partition, types de parents et règles métier non certifiés."]},
            details={"existence_status": "DECIDE", "kind_options": ["component", "attribute"],
                     "identity": "A_APPROUVER", "source_class": source,
                     "source_reference": reference,
                     "source_owner": f"sirs-v2:class:core/{locator.split('/')[0]}",
                     "source_cardinality": source_cardinality, "target_cardinality": "A_APPROUVER"},
        ))

    for code, definition, options, sources, locator, owner_code in OPEN_MEMBERS:
        source_ids = [_source(*value.split("/", 1)) for value in sources]
        source = "evidence:db-1" if code == "organe-protection-collective" else (
            "evidence:consolidation" if code == "largeur-franc-bord" else "evidence:mod-1c0")
        decisions = ["decision:DB1-amenagements"] if code == "organe-protection-collective" else (
            ["decision:FM-03", "decision:B4-franc-bord-source"] if code == "largeur-franc-bord" else [])
        owner = _id("concept", owner_code) if owner_code else None
        result.append(candidate(
            _id("unknown", code), "unknown", SCOPE, definition,
            [_proof(source, locator), *[_proof(f"ecore:{s.split('/')[0]}", f"#//{s.split('/')[1]}") for s in sources]],
            owner=owner, decision_refs=decisions, evidence_keys=source_ids,
            depends_on=[*source_ids, *decisions, *([owner] if owner else [])],
            preservation=list(PRESERVATION),
            questions=["Genre, propriétaire, cardinalités, origine et clé canonique restent A_APPROUVER."],
            details={"existence_status": "DECIDE" if decisions else "DEMONSTREE",
                     "kind_options": list(options), "identity": "A_APPROUVER", "source_classes": source_ids},
        ))

    _apply_mod1m(result, facts)
    # Preserve prior candidate identities, then record the scoped L02 succession.
    for item in result:
        if item['id'] in {_id('component', 'zone-atterrissement-lit'), _id('concept', 'ile-banc-lit')}:
            item['status'] = 'DIFFERE'
            item['owner'] = None
            item['definition'] = 'Ancienne destination conceptuelle remplacée par la famille morphologique L02 ; source conservée.'
            item['questions'] = [
                'Quelles anciennes références ou relations, conservées en preuve technique, '
                'peuvent être qualifiées vers la famille morphologique commune sans recréer '
                'une seconde représentation active de cette identité historique ?'
            ]
            item['details']['semantic_candidates'] = [_id('concept', 'element-morphologique-lit')]
            item['details']['succession'] = 'decision:MOD-1O-L02-rectificatif'
            item['decision_refs'].append('decision:MOD-1O-L02-rectificatif')
            item['provenance'].append(_decision_proof('MOD-1O-L02-rectificatif'))
        elif item['id'] == _id('concept', 'troncon-lit'):
            item['definition'] = 'Tronçon de lit autonome, associé aux Digues N:N ; cinq descriptifs identitaires, éléments morphologiques distincts.'
            item['decision_refs'].append('decision:MOD-1O-L02-rectificatif')
            item['provenance'].append(_decision_proof('MOD-1O-L02-rectificatif'))
        elif item['id'] in {_id('component', code) for code, cls in B2 if cls != 'ZoneAtterrissementLit'}:
            item['details']['geometry_destination'] = 'technical_source_only'
            item['decision_refs'].append('decision:MOD-1O-L02-rectificatif')
            item['provenance'].append(_decision_proof('MOD-1O-L02-rectificatif'))
    morpho = semantic('concept', 'element-morphologique-lit',
        'Objet physique autonome du lit ; géométrie Point, LineString ou Polygon obligatoire pour valid=true et lien explicite facultatif vers un Tronçon de lit.',
        'MOD-1O-L02-B', ('lit/ZoneAtterrissementLit', 'lit/IleBancLit'),
        details={'identity': 'distinct', 'owner_cardinality': '0..1',
                 'children_per_owner': '0..N', 'geometry_required_for_valid': True,
                 'geometry_shapes': ['POINT', 'LINESTRING', 'POLYGON']})
    morpho['provenance'].append(_proof('sql:business', 'CREATE TABLE public.elements_morphologiques_lit'))
    morpho['binding'] = {
        'status': 'BINDING_PARTIEL',
        'supports': [{'source': 'sql:business', 'table': 'public.elements_morphologiques_lit',
                      'columns': ['id', 'type_morphologie', 'segment_id', 'designation', 'libelle',
                                  'commentaire', 'date_debut', 'date_fin', 'date_maj',
                                  'geometry', 'longueur', 'largeur', 'rattache_berge',
                                  'vegetalise', 'type_zone_id', 'valid', 'created_by', 'edited_by',
                                  'position_debut', 'position_fin', 'borne_debut_id', 'borne_fin_id',
                                  'borne_debut_distance', 'borne_fin_distance', 'borne_debut_aval',
                                  'borne_fin_aval', 'pr_debut', 'pr_fin', 'systeme_rep_id']}],
        'gaps': ['Opérations natives collaboratives hors périmètre de ce lot.'],
    }
    for code in ('ile', 'banc', 'atterrissement', 'indetermine'):
        semantic('type', 'element-morphologique-lit/'+code,
            'Sous-type morphologique distinct, sans inférence par libellé ou géométrie.',
            'MOD-1O-L02-B', ('lit/ZoneAtterrissementLit',) if code == 'atterrissement' else
            ('lit/IleBancLit',) if code == 'indetermine' else (),
            _id('concept', 'element-morphologique-lit'),
            details={'identity': 'same_occurrence_as_parent', 'physical_binding': 'public.elements_morphologiques_lit',
                     'classification': 'explicit_source_evidence_only' if code in ('ile', 'banc') else
                     'source_class_proves_atterrissement' if code == 'atterrissement' else 'unqualified_island_or_bank'})

    semantic_by_source = {}
    for item in result:
        details = item["details"]
        for source in [*details.get("source_classes", []), *([details["source_class"]] if "source_class" in details else [])]:
            semantic_by_source.setdefault(source, []).append(item["id"])

    for fact in sorted(classes, key=lambda f: f["id"]):
        deferred = fact["name"] in DEFERRED_CLASSES
        excluded_lit = fact["id"] == "sirs-v2:class:lit/Lit"
        excluded = excluded_lit or fact["id"] == "sirs-v2:class:vegetation/ParcelleVegetation"
        technical = fact["data"].get("interface", False)
        linked = sorted(semantic_by_source.get(fact["id"], []))
        scope = "scope:administration" if fact["name"] == "Utilisateur" else SCOPE
        provenance = list(fact["provenance"])
        if deferred:
            provenance.append(_proof("evidence:db-1", "§5 Domaines absents ; GlobalPrestation distinct"))
        if excluded_lit:
            provenance.append(_decision_proof("MOD1M-lit"))
        elif excluded:
            provenance.append(_proof("evidence:consolidation", "§2 FM-05 ; §3 Parcelle non cible"))
        result.append(candidate(
            f"candidate:unknown:historical/{fact['model']}/{fact['name']}", "unknown", scope,
            f"Déclaration historique {fact['model']}/{fact['name']} ; présence source démontrée, aucune promotion automatique en concept cible.",
            provenance, status="DIFFERE" if deferred else "EXCLU" if excluded else "A_APPROUVER",
            decision_refs=["decision:DB1-differes"] if deferred else ["decision:MOD1M-lit"] if excluded_lit else ["decision:FM-05"] if excluded else [],
            depends_on=[fact["id"], *linked], evidence_keys=[fact["id"]],
            preservation=["Préserver tous les faits historiques de cette classe, y compris hors CRUD, interfaces et classes abstraites.",
                          "Un domaine différé reste explicitement non projeté et visible ; aucune cible fictive." if deferred else PRESERVATION[1]],
            questions=["Domaine explicitement différé ; unité d'acceptation de l'omission et conditions de reprise de l'import à qualifier."] if deferred else (
                [] if excluded else ["Qualifier le rôle source, le genre et l'issue cible sans déduire un concept de l'EClass."]),
            details={"source_class": fact["id"], "abstract": fact["data"].get("abstract", False),
                     "interface": technical, "semantic_candidates": linked,
                     "existence_status": "SOURCE_DEMONSTREE", "scope_status": "A_APPROUVER" if fact["model"] == "carto" else "DOCUMENTE",
                     "identity": "A_APPROUVER",
                     "exclusion_scope": ("Lit sans cible active ; informations et relations historiques conservées avec issue explicite."
                                         if excluded_lit else "Cible Parcelle seulement ; faits et relations historiques conservés." if excluded else None)},
        ))
    return sorted(result, key=lambda item: item["id"])


def limitations() -> list[dict]:
    return [
        {
            "id": "inventory-limit:patrimoine/semantic-boundary",
            "scope": SCOPE,
            "families": ["concept", "type", "subtype", "component"],
            "owners": [],
            "reason": "L'univers indépendant réconcilie décisions acquises, candidats nommés et les huit Ecore ; T(Patrimoine), les clés et l'exhaustivité native exigent une approbation explicite indépendante des tables et catalogues SQL.",
            "provenance": [_proof("evidence:consolidation", "§4 B1/B6 ; §6 Conditions de scellement")],
        },
        {
            "id": "inventory-limit:patrimoine/structured-values",
            "scope": SCOPE,
            "families": ["mesures-evenements", "points-profils-leves", "parametres-hydrauliques"],
            "owners": [_id("unknown", row[0]) for row in STRUCTURED_VALUES],
            "reason": "Les dix familles ont une conservation décidée et des supports physiques localisés ; leur genre composant identitaire ou valeur structurée, identité, bornes, unités et règles d'ordre cibles restent à qualifier.",
            "provenance": [_proof("evidence:db-1", "§2 Profils/Événements ; §4 valeurs métier à garder"), _proof("evidence:consolidation", "§2 FM-10 ; §4 B4")],
        },
        {
            "id": "inventory-limit:patrimoine/open-members",
            "scope": SCOPE,
            "families": [row[0] for row in OPEN_MEMBERS],
            "owners": [_id("unknown", row[0]) for row in OPEN_MEMBERS],
            "reason": "Existence connue des six candidats ; genre, clés, propriétaires et cardinalités ne sont pas approuvés par leur seule existence applicative. Franc-bord n'est pas un septième type physique ; son lien source reste directement Tronçon de digue.",
            "provenance": [_proof("evidence:mod-1c0", "§3.1 ; §3.3"), _proof("evidence:consolidation", "§2 FM-03 ; §4 B4")],
        },
    ]
