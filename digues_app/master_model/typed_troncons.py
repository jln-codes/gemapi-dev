"""Approved MOD-1P classification and explicitly blocked historical projections.

This module inventories and checks a transition. It does not select a SQL
representation or activate an occurrence producer. Pending destinations remain
pending even when their preservation witness is complete.
"""

from collections import Counter, defaultdict
from copy import deepcopy
from importlib.resources import files

from .common import canonical_bytes, digest, strict_load
from .decision_successions import resolve_decision_assertions


CONTRACT_ID = "contract:mod1p/troncons-types"
CLASS = "sirs-v2:class:"
SOURCE_CLASSES = frozenset(CLASS + name for name in (
    "core/TronconDigue", "core/Crete", "core/PiedDigue", "core/TalusDigue",
    "core/Fondation", "core/TalusRisberme", "core/SommetRisberme",
    "core/LargeurFrancBord", "berge/Berge", "berge/TraitBerge",
    "berge/PiedBerge", "berge/TalusBerge", "berge/SommetBerge",
    "dependance/TraitAmenagementHydraulique",
    "dependance/StructureAmenagementHydraulique",
))
EXCLUDED_CLASSES = frozenset(CLASS + name for name in (
    "lit/Lit", "lit/TronconLit", "lit/IleBancLit", "core/Epi",
    "berge/EpiBerge", "core/OuvrageRevanche", "berge/OuvrageRevancheBerge",
))
PRESERVATION = {
    "identity": "source_class_uuid_document_path",
    "merging": "forbidden_distinct_source_uuids",
    "geometry": "exact_geometry_and_srid",
    "invalid_geometry": "retain_invalid_with_diagnostic",
    "references": "preserve_typed_semantics_no_spatial_inference",
    "catalogs": "preserve_codes_and_domains",
}
SUBTYPES = {
    "da:type:troncon/berge": "Berge",
    "da:type:troncon/talus": "Talus",
    "da:type:troncon/talus-risberme": "Talus de risberme",
    "da:type:troncon/risberme": "Risberme",
    "da:type:troncon/crete": "Crête",
    "da:type:troncon/franc-bord": "Franc-bord",
    "da:type:troncon/indetermine": "Indéterminé",
}
B4_GEOMETRY_TYPES = frozenset(("da:type:troncon/risberme", "da:type:troncon/franc-bord"))
B4_PHYSICAL_SOURCES = frozenset((CLASS + "core/SommetRisberme", CLASS + "core/LargeurFrancBord"))


def _geometry_status(target):
    return "DECIDE" if target in B4_GEOMETRY_TYPES else "A_APPROUVER"


SIDE = {
    "values": [{"code": "TERRE", "label": "Terre"},
               {"code": "EAU", "label": "Rivière–Mer–Étang"},
               {"code": "INDETERMINE", "label": "Indéterminé"}],
    "mapping_status": "A_APPROUVER",
    "preserve_rive_separately": True,
    "forbid_position_inference": True,
}
POSITIONS = {
    "values": [{"code": "PIED", "label": "Pied"},
               {"code": "SOMMET", "label": "Sommet"},
               {"code": "PARTIE_COURANTE", "label": "Partie courante"},
               {"code": "INDETERMINEE", "label": "Indéterminée"}],
    "scope": "localized_object_qualification",
    "occurrence_attribute": "A_APPROUVER",
    "applicability": [
        {"target": target, "PIED": pied, "SOMMET": sommet,
         "PARTIE_COURANTE": "A_APPROUVER", "INDETERMINEE": "A_APPROUVER"}
        for target, pied, sommet in (
            ("da:type:troncon/berge", "ALLOWED", "ALLOWED"),
            ("da:type:troncon/talus", "ALLOWED", "ALLOWED"),
            ("da:type:troncon/talus-risberme", "A_APPROUVER", "A_APPROUVER"),
            ("da:type:troncon/risberme", "FORBIDDEN", "ALLOWED"),
            ("da:type:troncon/crete", "FORBIDDEN", "FORBIDDEN"),
            ("da:type:troncon/franc-bord", "FORBIDDEN", "FORBIDDEN"),
            ("da:type:troncon/indetermine", "A_APPROUVER", "A_APPROUVER"),
        )
    ],
}
GATE = {"sources": sorted(SOURCE_CLASSES), "policy": "preserve_unprojected_until_qualified"}
SOURCE_POLICY = {
    CLASS + source: {"disposition": disposition, "proposed_target": target, "position": position}
    for source, disposition, target, position in (
        ("core/TronconDigue", "typed_candidate", "da:type:troncon/indetermine", None),
        ("core/Crete", "typed_candidate", "da:type:troncon/crete", None),
        ("core/PiedDigue", "identity_trace", None, "PIED"),
        ("core/TalusDigue", "typed_candidate", "da:type:troncon/talus", None),
        ("core/Fondation", "deferred", None, None),
        ("core/TalusRisberme", "typed_candidate", "da:type:troncon/talus-risberme", None),
        ("core/SommetRisberme", "typed_candidate", "da:type:troncon/risberme", None),
        ("core/LargeurFrancBord", "typed_candidate", "da:type:troncon/franc-bord", None),
        ("berge/Berge", "typed_candidate", "da:type:troncon/berge", None),
        ("berge/TraitBerge", "typed_candidate", "da:type:troncon/berge", None),
        ("berge/PiedBerge", "identity_trace", None, "PIED"),
        ("berge/TalusBerge", "typed_candidate", "da:type:troncon/talus", None),
        ("berge/SommetBerge", "identity_trace", None, "SOMMET"),
        ("dependance/TraitAmenagementHydraulique", "typed_candidate", "da:type:troncon/indetermine", None),
        ("dependance/StructureAmenagementHydraulique", "deferred", None, None),
    )
}
RECORD_FIELDS = frozenset({"source_class", "uuid", "document_path", "attributes",
                           "references", "geometry", "srid"})

CRETE_BINDING_ID = "contract:mod1p/crete-physical-binding"
CRETE_BINDING_DECISION = "decision:TRONCONS-UNIFICATION-C2"
CRETE_BINDING_KIND = "typed_troncon_physical_slice"
CRETE_SOURCE = CLASS + "core/Crete"
CRETE_APPLICATIONS = tuple("sirs-v2:application:core/Crete/" + name for name in (
    "author", "borneDebutId", "borneFinId", "borne_debut_aval", "borne_debut_distance",
    "borne_fin_aval", "borne_fin_distance", "commentaire", "crsName", "dateMaj",
    "date_debut", "date_fin", "designation", "editedGeoCoordinate", "epaisseur",
    "fonctionId", "geometry", "geometryMode", "largeurMoyenne", "lastUpdateAuthor",
    "latitudeMax", "latitudeMin", "linearId", "longitudeMax", "longitudeMin", "materiauId",
    "natureId", "numCouche", "photos", "positionDebut", "positionFin", "prDebut", "prFin",
    "sourceId", "systemeRepId", "valid",
))
_CRETE_ROOT = "public.troncons"
_CRETE_PROVENANCE = "external:source-inventory"
_CRETE_COLUMNS = {
    "designation": (_CRETE_ROOT, "designation", "preserve_text"),
    "commentaire": (_CRETE_ROOT, "commentaire", "preserve_text"),
    "date_debut": (_CRETE_ROOT, "date_debut", "typed_date_with_raw_original"),
    "date_fin": (_CRETE_ROOT, "date_fin", "typed_date_with_raw_original"),
    "geometry": (_CRETE_ROOT, "geometry", "preserve_exact_geometry_and_srid"),
    "valid": (_CRETE_ROOT, "valid", "preserve_or_false_on_historical_geometry_failure"),
    "epaisseur": (_CRETE_ROOT, "epaisseur", "preserve_typed_value_with_raw_original"),
    "largeurMoyenne": (_CRETE_ROOT, "largeur_moyenne", "preserve_typed_value_with_raw_original"),
    "numCouche": (_CRETE_ROOT, "num_couche", "preserve_typed_value_with_raw_original"),
    "fonctionId": (_CRETE_ROOT, "fonction_id", "canonical_catalog_code_only"),
    "natureId": (_CRETE_ROOT, "nature_id", "canonical_catalog_code_only"),
    "materiauId": (_CRETE_ROOT, "materiau_id", "canonical_catalog_code_only"),
    "sourceId": (_CRETE_ROOT, "source_id", "canonical_catalog_code_only"),
    "linearId": (_CRETE_ROOT, "troncon_support_id", "exact_existing_legacy_troncon_only"),
    "author": (_CRETE_ROOT, "created_by", "explicit_contact_mapping_only"),
    "lastUpdateAuthor": (_CRETE_ROOT, "edited_by", "explicit_contact_mapping_only"),
}
CRETE_BINDING_PARAMETERS = {
    "operator": "typed_troncons_crete_partial_v1", "source": CRETE_SOURCE,
    "target": "da:type:troncon/crete", "qualification": "PARTIAL",
    "legacy_gate": CONTRACT_ID, "writer": "BC4E_CRETE_ONLY",
    "tables": {"root": _CRETE_ROOT, "provenance": _CRETE_PROVENANCE},
    "discriminator": {"column": "type_physique_id", "value": "CRETE",
                      "catalog": "referentiel.types_troncon_unifie"},
    "identity": {"mode": "preserve_source_uuid", "canonical_row": "same_source_uuid", "merge": "forbidden"},
    "bindings": [
        {"application": application, "source_key": application.rsplit("/", 1)[-1],
         "table": _CRETE_COLUMNS.get(application.rsplit("/", 1)[-1], (_CRETE_PROVENANCE, "document", "preserve_raw_only"))[0],
         "column": _CRETE_COLUMNS.get(application.rsplit("/", 1)[-1], (_CRETE_PROVENANCE, "document", "preserve_raw_only"))[1],
         "policy": _CRETE_COLUMNS.get(application.rsplit("/", 1)[-1], (_CRETE_PROVENANCE, "document", "preserve_raw_only"))[2],
         "proof_path": [application.rsplit("/", 1)[-1]]}
        for application in CRETE_APPLICATIONS
    ],
    "source_preservation": {"applications": list(CRETE_APPLICATIONS), "mode": "raw_document_presence_values_references",
                            "column": "document", "source_class_column": "source_class", "source_class_value": "Crete",
                            "unknowns": "preserve", "geometry": "exact_geometry_and_srid"},
    "geometry": {"target_status": "A_APPROUVER", "historical_contract": "da:type:structure-digue/crete",
                 "historical_diagnostic_only": True, "invalid_historical": "retain_invalid_with_diagnostic",
                 "conversion": "forbidden", "unknown_srid": "preserve_unknown_diagnose_no_inference",
                 "pending_diagnostic": "NEW_GEOMETRY_CONTRACT_PENDING"},
    "relations": {"digue": "explicit_exact_reference_only", "digue_column": "digue_id",
                  "unresolved": "retain_reference_with_diagnostic", "side": "null_without_qualified_mapping",
                  "side_column": "cote_physique_id", "side_catalog": "referentiel.cotes_troncon_unifie",
                  "photo": "preserve_direct_reference_no_observation_creation"},
    "shared_fields": {"location": "root_only", "columns": ["designation", "commentaire", "date_debut", "date_fin", "geometry", "valid"]},
    "scope": {"native_crud": False, "other_sources": False, "certification": False},
}

TALUS_BINDING_ID = "contract:mod1p/talus-physical-binding"
TALUS_BINDING_DECISION = "decision:TRONCONS-UNIFICATION-C2"
TALUS_SOURCE_NAMES = ("TalusDigue", "TalusRisberme")
TALUS_APPLICATION_NAMES = (
    "author", "borneDebutId", "borneFinId", "borne_debut_aval", "borne_debut_distance",
    "borne_fin_aval", "borne_fin_distance", "commentaire", "coteId", "crsName",
    "dateMaj", "date_debut", "date_fin", "designation", "editedGeoCoordinate",
    "epaisseurSommet", "fonctionBasId", "fonctionHautId", "geometry", "geometryMode",
    "lastUpdateAuthor", "latitudeMax", "latitudeMin", "linearId", "longitudeMax",
    "longitudeMin", "longueurRampantBas", "longueurRampantHaut", "materiauBasId",
    "materiauHautId", "natureBasId", "natureHautId", "numCouche", "penteInterieure",
    "photos", "positionDebut", "positionFin", "prDebut", "prFin", "sourceId",
    "systemeRepId", "valid",
)
_TALUS_COLUMNS = {
    **{key: value for key, value in _CRETE_COLUMNS.items() if key in (
        "designation", "commentaire", "date_debut", "date_fin", "geometry", "valid",
        "sourceId", "linearId", "author", "lastUpdateAuthor", "numCouche")},
    "epaisseurSommet": (_CRETE_ROOT, "epaisseur_sommet", "preserve_typed_value_with_raw_original"),
    "penteInterieure": (_CRETE_ROOT, "pente_interieure", "preserve_typed_value_with_raw_original"),
    "longueurRampantHaut": (_CRETE_ROOT, "longueur_rampant_haut", "preserve_typed_value_with_raw_original"),
    "longueurRampantBas": (_CRETE_ROOT, "longueur_rampant_bas", "preserve_typed_value_with_raw_original"),
    "fonctionHautId": (_CRETE_ROOT, "fonction_haut_id", "canonical_catalog_code_only"),
    "fonctionBasId": (_CRETE_ROOT, "fonction_bas_id", "canonical_catalog_code_only"),
    "natureHautId": (_CRETE_ROOT, "nature_haut_id", "canonical_catalog_code_only"),
    "natureBasId": (_CRETE_ROOT, "nature_bas_id", "canonical_catalog_code_only"),
    "materiauHautId": (_CRETE_ROOT, "materiau_haut_id", "canonical_catalog_code_only"),
    "materiauBasId": (_CRETE_ROOT, "materiau_bas_id", "canonical_catalog_code_only"),
    "coteId": (_CRETE_ROOT, "cote_physique_id", "approved_common_side_only_historical_code_in_extension_and_raw_original"),
}
TALUS_BINDING_PARAMETERS = {
    "operator": "typed_troncons_talus_partial_v1",
    "sources": [
        {
            "source": CLASS + "core/" + name,
            "target": "da:type:troncon/" + ("talus" if name == "TalusDigue" else "talus-risberme"),
            "source_class": name,
            "root_type": "TALUS" if name == "TalusDigue" else "TALUS_RISBERME",
            "extension_type": "TALUS" if name == "TalusDigue" else "TALUS_RISBERME",
            "applications": ["sirs-v2:application:core/" + name + "/" + key for key in TALUS_APPLICATION_NAMES],
            "bindings": [
                {"application": "sirs-v2:application:core/" + name + "/" + key,
                 "source_key": key,
                 "table": _TALUS_COLUMNS.get(key, (_CRETE_PROVENANCE, "document", "preserve_raw_only"))[0],
                 "column": _TALUS_COLUMNS.get(key, (_CRETE_PROVENANCE, "document", "preserve_raw_only"))[1],
                 "policy": _TALUS_COLUMNS.get(key, (_CRETE_PROVENANCE, "document", "preserve_raw_only"))[2],
                 "proof_path": [key]}
                for key in TALUS_APPLICATION_NAMES
            ],
        }
        for name in TALUS_SOURCE_NAMES
    ],
    "qualification": "PARTIAL",
    "legacy_gate": CONTRACT_ID,
    "writer": "BC4E_TALUS_ONLY",
    "tables": {"root": _CRETE_ROOT, "provenance": _CRETE_PROVENANCE},
    "identity": {"mode": "preserve_source_uuid", "canonical_row": "same_source_uuid", "merge": "forbidden"},
    "source_preservation": {"mode": "raw_document_presence_values_references", "column": "document",
                            "source_class_column": "source_class", "unknowns": "preserve",
                            "geometry": "exact_geometry_and_srid"},
    "side_mapping": {"RefCote:1": "EAU", "RefCote:2": "TERRE", "RefCote:4": "EAU",
                     "RefCote:99": "INDETERMINE"},
    "side_mapping_evidence": "private/sirs-v2/referentiels/catalogues-2.55/RefCote.json",
    "geometry": {"target_status": "A_APPROUVER", "historical_contract": "da:type:structure-digue/talus",
                 "historical_diagnostic_only": True, "invalid_historical": "retain_invalid_with_diagnostic",
                 "conversion": "forbidden", "unknown_srid": "preserve_unknown_diagnose_no_inference",
                 "pending_diagnostic": "NEW_GEOMETRY_CONTRACT_PENDING"},
    "relations": {"digue": "explicit_exact_reference_only", "digue_column": "digue_id",
                  "unresolved": "retain_reference_with_diagnostic",
                  "historical_side_column": "public.troncons.cote_id",
                  "historical_side_semantics": "resolved_RefCote_only_not_common_side_authority",
                  "photo": "preserve_direct_reference_no_observation_creation"},
    "shared_fields": {"location": "root_only", "columns": ["designation", "commentaire",
                      "date_debut", "date_fin", "geometry", "valid"]},
    "scope": {"native_crud": False, "other_sources": False, "certification": False},
}


def _contract(registry):
    return next((row for row in registry.get("contracts", [])
                 if row.get("id") == CONTRACT_ID), None)


def suspended_projection_sources(registry):
    """Return the approved gate only when its active successor seal matches.

    An old v1 contract predates suspension. A malformed v2 declaration fails
    closed; it must never permit the caller to fall back to historical writers.
    """
    contracts = [row for row in registry.get("contracts", []) if row.get("id") == CONTRACT_ID]
    decisions = {row["id"]: row for row in registry.get("decisions", [])}
    if not contracts and "decision:MOD1P-A3" not in decisions:
        return frozenset()
    if len(contracts) != 1:
        raise ValueError("INV-VER.TRONCON_PROJECTION_GATE_REJECTED")
    contract = contracts[0]
    parameters = contract.get("parameters", {})
    if (contract.get("version") == "1" and parameters.get("operator") == "typed_troncons_transition_v1"
            and "decision:MOD1P-A3" not in decisions):
        return frozenset()
    succession = resolve_decision_assertions(registry)
    decision = decisions.get("decision:MOD1P-A3", {})
    seals = [a for name in ("decision:MOD1P-A3", "decision:MOD1P-B3", "decision:MOD1P-B4", "decision:MOD1P-B5", "decision:MOD1P-B6")
             for a in succession["assertions"].get(name, [])
             if a["subject"] == CONTRACT_ID and a["predicate"] == "contract_parameters_digest"]
    if (contract.get("version") != "2" or contract.get("status") != "DECIDE"
            or contract.get("kind") != "typed_troncons_transition"
            or parameters.get("operator") != "typed_troncons_transition_v2"
            or parameters.get("activation") != "BLOCKED_PENDING_DECISIONS"
            or parameters.get("classification_status") != "DECIDE"
            or parameters.get("historical_projection_gate") != GATE
            or decision.get("status") != "DECIDE"
            or "decision:MOD1P-A3" not in contract.get("decision_refs", [])
            or succession["diagnostics"] or len(seals) != 1
            or seals[0]["value"] != digest(parameters)):
        raise ValueError("INV-VER.TRONCON_PROJECTION_GATE_REJECTED")
    return SOURCE_CLASSES


def validate_crete_binding(registry, contract, historical=None):
    """Validate the scoped B1 authority, independently of private source files.

    The historical universe is optional at runtime. Compilation supplies it to
    verify that the 36 declared applications are the complete effective class.
    Successful validation authorizes this partial writer only, not certification
    or native CRUD and not any other suspended historical source.
    """
    errors = []
    def fail(code, message, element=None):
        errors.append({"code": code, "element_id": element or CRETE_BINDING_ID,
                       "message": message, "blocking": True})
    contracts = [row for row in registry.get("contracts", []) if row.get("id") == CRETE_BINDING_ID]
    if (not isinstance(contract, dict) or len(contracts) != 1 or contracts[0] != contract
            or contract.get("id") != CRETE_BINDING_ID or contract.get("kind") != CRETE_BINDING_KIND
            or contract.get("version") != "2" or contract.get("status") != "DECIDE"):
        fail("INV-VER.CRETE_BINDING_SHAPE", "Only the single approved version 2 Crête physical slice is supported.")
        return errors
    parameters = contract.get("parameters")
    if parameters != CRETE_BINDING_PARAMETERS:
        fail("INV-MAP.CRETE_BINDING_PARAMETERS", "The exact approved Crête scope, bindings and preservation policies are required.")
    decisions = {row["id"]: row for row in registry.get("decisions", [])}
    decision = decisions.get(CRETE_BINDING_DECISION, {})
    succession = resolve_decision_assertions(registry)
    assertions = succession["assertions"].get(CRETE_BINDING_DECISION, [])
    seals = [row for row in assertions if row["subject"] == CRETE_BINDING_ID
             and row["predicate"] == "contract_parameters_digest"]
    if (decision.get("status") != "DECIDE" or CRETE_BINDING_DECISION not in contract.get("decision_refs", [])
            or succession["diagnostics"] or len(seals) != 1 or seals[0]["value"] != digest(parameters)
            or {"subject": CRETE_BINDING_ID, "predicate": "contract", "value": CRETE_BINDING_KIND} not in assertions):
        fail("INV-VER.CRETE_BINDING_SEAL", "The partial physical slice requires its active approved B1 decision and exact parameter seal.")
    try:
        if suspended_projection_sources(registry) != SOURCE_CLASSES:
            raise ValueError("The A3 historical suspension gate is required.")
    except ValueError:
        fail("INV-VER.CRETE_BINDING_LEGACY_GATE", "The B1 writer never reactivates the fifteen suspended legacy projections.")
    targets = {row["id"]: row for row in registry.get("targets", [])}
    target = targets.get("da:type:troncon/crete", {})
    if (target.get("state") != "active" or target.get("status") != "DECIDE"
            or target.get("key_status") != "APPROUVE" or target.get("parent") != "da:concept:troncon"):
        fail("INV-MAP.CRETE_BINDING_TARGET", "The scoped writer requires the approved canonical Crête type.")
    if historical is not None:
        facts = {row["id"]: row for row in historical.get("facts", [])}
        applications = {row["id"]: row for row in facts.values()
                        if row["kind"] == "application" and row.get("owner") == CRETE_SOURCE}
        if set(applications) != set(CRETE_APPLICATIONS):
            fail("INV-COV.CRETE_BINDING_APPLICATIONS", "All and only the 36 effective Crête source applications require a preservation destination.")
        for application, fact in applications.items():
            declaration = facts.get(fact.get("data", {}).get("declaration"), {})
            if (fact.get("name") != application.rsplit("/", 1)[-1]
                    or declaration.get("kind") != fact.get("data", {}).get("property_kind")
                    or declaration.get("data", {}).get("type") != fact.get("data", {}).get("type")):
                fail("INV-REF.CRETE_BINDING_DECLARATION", "A preserved application must retain its exact historical declaration and type.", application)
    return sorted(errors, key=canonical_bytes)


def load_crete_binding():
    """Read the packaged authority and fail before DML if its scope is stale.

    No compiler, private Ecore, model.json, DSN or mutable cache is consulted.
    A detached parameter copy prevents a caller from changing the authority.
    """
    registry = strict_load(files(__package__).joinpath("registry.json"))
    contract = next((row for row in registry.get("contracts", []) if row.get("id") == CRETE_BINDING_ID), None)
    errors = validate_crete_binding(registry, contract)
    if errors:
        raise ValueError("INV-VER.CRETE_BINDING_REJECTED: " + ",".join(row["code"] for row in errors))
    return deepcopy(contract["parameters"])


def validate_talus_binding(registry, contract, historical=None):
    """Seal both Core Talus sources without qualifying their new geometry contract."""
    errors = []
    def fail(code, message, element=TALUS_BINDING_ID):
        errors.append({"code": code, "element_id": element, "message": message, "blocking": True})
    matches = [row for row in registry.get("contracts", []) if row.get("id") == TALUS_BINDING_ID]
    if (len(matches) != 1 or matches[0] != contract or not isinstance(contract, dict)
            or contract.get("kind") != CRETE_BINDING_KIND or contract.get("version") != "2"
            or contract.get("status") != "DECIDE"):
        fail("INV-VER.TALUS_BINDING_SHAPE", "One approved B2 Talus physical slice is required.")
        return errors
    parameters = contract.get("parameters")
    if parameters != TALUS_BINDING_PARAMETERS:
        fail("INV-MAP.TALUS_BINDING_PARAMETERS", "Both exact 42-property bindings and the qualified side map are required.")
    decisions = {row["id"]: row for row in registry.get("decisions", [])}
    decision = decisions.get(TALUS_BINDING_DECISION, {})
    succession = resolve_decision_assertions(registry)
    assertions = succession["assertions"].get(TALUS_BINDING_DECISION, [])
    seals = [row for name in (TALUS_BINDING_DECISION, "decision:MOD1P-B3")
             for row in succession["assertions"].get(name, []) if row["subject"] == TALUS_BINDING_ID
             and row["predicate"] == "contract_parameters_digest"]
    if (decision.get("status") != "DECIDE" or TALUS_BINDING_DECISION not in contract.get("decision_refs", [])
            or succession["diagnostics"] or len(seals) != 1 or seals[0]["value"] != digest(parameters)
            or {"subject": TALUS_BINDING_ID, "predicate": "contract", "value": CRETE_BINDING_KIND} not in assertions):
        fail("INV-VER.TALUS_BINDING_SEAL", "The B2 partial writer requires an exact approved decision seal.")
    try:
        if suspended_projection_sources(registry) != SOURCE_CLASSES:
            raise ValueError("A3 gate missing")
    except ValueError:
        fail("INV-VER.TALUS_BINDING_LEGACY_GATE", "The historical fifteen-source gate remains active.")
    targets = {row["id"]: row for row in registry.get("targets", [])}
    for source in TALUS_BINDING_PARAMETERS["sources"]:
        target = targets.get(source["target"], {})
        if (target.get("state") != "active" or target.get("status") != "DECIDE"
                or target.get("key_status") != "APPROUVE" or target.get("parent") != "da:concept:troncon"):
            fail("INV-MAP.TALUS_BINDING_TARGET", "The approved canonical Talus target is required.", source["target"])
    if historical is not None:
        facts = {row["id"]: row for row in historical.get("facts", [])}
        for source in TALUS_BINDING_PARAMETERS["sources"]:
            applications = {row["id"]: row for row in facts.values()
                            if row["kind"] == "application" and row.get("owner") == source["source"]}
            if set(applications) != set(source["applications"]):
                fail("INV-COV.TALUS_BINDING_APPLICATIONS", "Every effective historical Talus property needs an exact destination.", source["source"])
            for application, fact in applications.items():
                declaration = facts.get(fact.get("data", {}).get("declaration"), {})
                if (fact.get("name") != application.rsplit("/", 1)[-1]
                        or declaration.get("kind") != fact.get("data", {}).get("property_kind")
                        or declaration.get("data", {}).get("type") != fact.get("data", {}).get("type")):
                    fail("INV-REF.TALUS_BINDING_DECLARATION", "Historical declaration and type must be retained.", application)
    return sorted(errors, key=canonical_bytes)


def load_talus_binding():
    """Return the sealed B2 runtime authority without consulting mutable evidence."""
    registry = strict_load(files(__package__).joinpath("registry.json"))
    contract = next((row for row in registry.get("contracts", []) if row.get("id") == TALUS_BINDING_ID), None)
    errors = validate_talus_binding(registry, contract)
    if errors:
        raise ValueError("INV-VER.TALUS_BINDING_REJECTED: " + ",".join(row["code"] for row in errors))
    return deepcopy(contract["parameters"])


def validate_talus_binding_event(registry, contract_id, event):
    """Check exact identities and raw preservation for one of two B2 sources."""
    from uuid import UUID
    contract = next((row for row in registry.get("contracts", []) if row.get("id") == contract_id), None)
    errors = validate_talus_binding(registry, contract)
    if errors:
        return errors
    def fail(code, message):
        errors.append({"code": code, "element_id": contract_id, "message": message, "blocking": True})
    fields = {"source_class", "source_id", "source_document", "root_id", "extension_id",
              "stored_document", "target_type", "diagnostics", "claimed_complete"}
    if (not isinstance(event, dict) or set(event) != fields
            or not isinstance(event.get("source_document"), dict)
            or not isinstance(event.get("stored_document"), dict)
            or not isinstance(event.get("diagnostics"), list)
            or type(event.get("claimed_complete")) is not bool
            or any(not isinstance(event.get(key), str) for key in
                   ("source_class", "source_id", "root_id", "extension_id", "target_type"))):
        fail("INV-VER.TALUS_BINDING_EVENT_SHAPE", "The exact scoped identity/document witness is required.")
        return errors
    sources = {row["source"]: row for row in TALUS_BINDING_PARAMETERS["sources"]}
    expected = sources.get(event["source_class"])
    if expected is None or event["target_type"] != expected["root_type"]:
        fail("INV-MAP.TALUS_BINDING_EVENT_SCOPE", "Only the matching historical Talus and target type are admitted.")
    try:
        identities = [UUID(event[key]) for key in ("source_id", "root_id", "extension_id")]
        if len(set(identities)) != 1:
            raise ValueError("identity merged")
        if "_id" in event["source_document"] and UUID(event["source_document"]["_id"]) != identities[0]:
            raise ValueError("source identity changed")
    except (ValueError, TypeError, AttributeError):
        fail("INV-ID.TALUS_BINDING_IDENTITY", "Source, root and extension must preserve one historical UUID.")
    if canonical_bytes(event["source_document"]) != canonical_bytes(event["stored_document"]):
        fail("INV-MAP.TALUS_BINDING_DOCUMENT_CHANGED", "All original properties, references and geometry must remain exact.")
    codes = {row.get("code") for row in event["diagnostics"] if isinstance(row, dict) and isinstance(row.get("code"), str)}
    if "NEW_GEOMETRY_CONTRACT_PENDING" not in codes:
        fail("INV-COV.TALUS_BINDING_GEOMETRY_DIAGNOSTIC", "The new target geometry contract remains pending.")
    raw_side = event["source_document"].get("coteId")
    if isinstance(raw_side, str) and raw_side and raw_side not in TALUS_BINDING_PARAMETERS["side_mapping"]:
        if not any(isinstance(row, dict) and row.get("code") == "HISTORICAL_SIDE_QUALIFICATION"
                   and row.get("source_id") == raw_side and row.get("status") == "UNQUALIFIED"
                   and row.get("target_id") is None for row in event["diagnostics"]):
            fail("INV-COV.TALUS_BINDING_SIDE_DIAGNOSTIC", "An unmapped historical side must retain its code and an explicit unresolved diagnostic.")
    if event["claimed_complete"]:
        fail("INV-COV.TALUS_BINDING_CERTIFICATION", "The physical slice cannot claim complete migration.")
    return sorted(errors, key=canonical_bytes)


def validate_crete_binding_event(registry, contract_id, event):
    """Check an identity/document witness; PostgreSQL proves physical columns.

    Exact documents preserve all source properties, including missing/null
    distinctions, unknown extensions, original geometry/SRID and Photo links.
    This deliberately narrow witness never certifies the new geometry contract.
    """
    from uuid import UUID

    contract = next((row for row in registry.get("contracts", []) if row.get("id") == contract_id), None)
    errors = validate_crete_binding(registry, contract)
    if errors:
        return errors
    def fail(code, message):
        errors.append({"code": code, "element_id": contract_id, "message": message, "blocking": True})
    fields = {"source_class", "source_id", "source_document", "root_id", "extension_id",
              "stored_document", "target_type", "diagnostics", "claimed_complete"}
    if (not isinstance(event, dict) or set(event) != fields
            or not isinstance(event.get("source_document"), dict)
            or not isinstance(event.get("stored_document"), dict)
            or not isinstance(event.get("diagnostics"), list)
            or type(event.get("claimed_complete")) is not bool
            or any(not isinstance(event.get(key), str) for key in ("source_class", "source_id", "root_id", "extension_id", "target_type"))):
        fail("INV-VER.CRETE_BINDING_EVENT_SHAPE", "The exact scoped identity/document witness is required.")
        return errors
    if event["source_class"] != CRETE_SOURCE or event["target_type"] != "CRETE":
        fail("INV-MAP.CRETE_BINDING_EVENT_SCOPE", "The B1 writer handles only historical Crete into the CRETE type.")
    try:
        identities = [UUID(event[key]) for key in ("source_id", "root_id", "extension_id")]
        if len(set(identities)) != 1:
            raise ValueError("A source UUID cannot be replaced or merged.")
        if "_id" in event["source_document"] and UUID(event["source_document"]["_id"]) != identities[0]:
            raise ValueError("The raw document UUID must agree with the witness.")
    except (ValueError, TypeError, AttributeError):
        fail("INV-ID.CRETE_BINDING_IDENTITY", "The source, root and extension must retain the same valid historical UUID.")
    if canonical_bytes(event["source_document"]) != canonical_bytes(event["stored_document"]):
        fail("INV-MAP.CRETE_BINDING_DOCUMENT_CHANGED", "All original properties, references, geometry and unknown fields must remain exact.")
    codes = {row.get("code") for row in event["diagnostics"] if isinstance(row, dict) and isinstance(row.get("code"), str)}
    if "NEW_GEOMETRY_CONTRACT_PENDING" not in codes:
        fail("INV-COV.CRETE_BINDING_GEOMETRY_DIAGNOSTIC", "The new Crête geometry contract is still pending and requires an explicit diagnostic.")
    if event["claimed_complete"]:
        fail("INV-COV.CRETE_BINDING_CERTIFICATION", "The physical slice remains partial and cannot claim complete migration or geometry certification.")
    return sorted(errors, key=canonical_bytes)


def _assignable(facts, actual, expected):
    pending, seen = [actual], set()
    while pending:
        current = pending.pop()
        if current == expected:
            return True
        if current not in seen:
            seen.add(current)
            pending.extend(facts.get(current, {}).get("data", {}).get("super_types", []))
    return False


def transition_inventory(registry, historical):
    """Derive every effective property, incoming reference and catalog witness.

    The application identity is the logical preservation address. It is never
    a proposed SQL column. Exact declaration types, cardinalities and originals
    remain available independently of any future shared attribute definition.
    """
    contract = _contract(registry)
    if contract is None:
        raise ValueError("INV-REF.TRONCON_CONTRACT_MISSING")
    specs = {row["source"]: row for row in contract["parameters"]["sources"]}
    facts = {row["id"]: row for row in historical["facts"]}
    targets = {row["id"]: row for row in registry.get("targets", [])}
    geometries = defaultdict(list)
    for fact in facts.values():
        if fact["kind"] == "geometry":
            geometries[fact["data"].get("property")].append(fact)
    applications = []
    for fact in sorted(facts.values(), key=lambda row: row["id"]):
        if fact["kind"] != "application" or fact.get("owner") not in specs:
            continue
        data = fact["data"]
        applications.append({
            "id": fact["id"], "source_class": fact["owner"], "name": fact["name"],
            "kind": data["property_kind"], "declaration": data["declaration"],
            "declared_in": data["declared_in"], "inherited": data["inherited"],
            "type": data["type"], "cardinality": {"min": data["lower"], "max": data["upper"]},
            "ordered": data["ordered"], "unique": data["unique"],
            "containment": data.get("containment"), "opposite": data.get("opposite"),
            "default_value": deepcopy(data.get("default_value")),
            "geometry": [deepcopy(g) for g in sorted(geometries[fact["id"]], key=lambda row: row["id"])],
            "provenance": deepcopy(fact.get("provenance", [])),
            "disposition": {"mode": "preserve_by_application_key", "key": fact["id"],
                            "presence": "preserve_absence", "sql_destination": None,
                            "common_candidate": data["declaration"] in contract["parameters"]["property_policy"]["common_candidates"],
                            "destination_status": "A_APPROUVER"},
        })
    sources = []
    for source, spec in sorted(specs.items()):
        current = []
        for correspondence in registry.get("correspondences", []):
            if correspondence["source"] == source:
                target = targets.get(correspondence["target"], {})
                current.append({"correspondence": correspondence["id"], "target": correspondence["target"],
                                "status": correspondence["status"],
                                "geometry": deepcopy(target.get("geometry"))})
        fact = facts.get(source, {})
        application_ids = [a["id"] for a in applications if a["source_class"] == source]
        sources.append({
            **deepcopy(spec), "historical_class": deepcopy(fact),
            "historical_identity": [deepcopy(f) for f in sorted(facts.values(), key=lambda f: f["id"])
                                    if f["kind"] == "identity" and f.get("owner") == source],
            "application_ids": application_ids,
            "observed_evidence": [deepcopy(f) for f in sorted(facts.values(), key=lambda f: f["id"])
                                  if f["kind"] == "observation" and f.get("owner") in application_ids],
            "occurrence_coverage": "AGGREGATED_MOCK_ONLY_NOT_COMPLETE_POPULATION",
            "current_destinations": sorted(current, key=lambda row: row["correspondence"]),
            "future_geometry_status": _geometry_status(spec["proposed_target"]),
        })
    incoming = []
    for fact in sorted(facts.values(), key=lambda row: row["id"]):
        data = fact.get("data", {})
        if fact["kind"] != "application" or data.get("property_kind") != "reference":
            continue
        accepted = [source for source in sorted(specs) if _assignable(facts, source, data.get("type"))]
        if accepted:
            incoming.append({"application": fact["id"], "source_class": fact["owner"],
                             "declaration": data["declaration"], "declared_type": data["type"],
                             "candidate_destinations": accepted,
                             "cardinality": {"min": data["lower"], "max": data["upper"]},
                             "containment": data.get("containment"), "opposite": data.get("opposite"),
                             "disposition": "preserve_typed_semantics_no_spatial_inference"})
    reference_domains = {a["type"] for a in applications if a["kind"] == "reference"}
    catalogs = []
    for mapping in sorted(facts.values(), key=lambda row: row["id"]):
        if mapping["kind"] != "contract_mapping":
            continue
        catalog_ids = sorted(dep for dep in mapping.get("depends_on", [])
                             if facts.get(dep, {}).get("kind") == "catalog")
        domains = sorted(reference_domains.intersection(mapping.get("depends_on", [])))
        for catalog_id in catalog_ids:
            for domain in domains:
                catalogs.append({"reference_type": domain, "catalog": catalog_id,
                                 "evidence": mapping["id"],
                                 "value_ids": sorted(f["id"] for f in facts.values()
                                                     if f["kind"] == "catalog_value" and f.get("owner") == catalog_id),
                                 "disposition": "preserve_codes_and_domains"})
    return {"contract_id": CONTRACT_ID, "sources": sources, "applications": applications,
            "incoming_references": incoming, "catalogs": sorted(catalogs, key=canonical_bytes)}


def validate_transition_contract(registry, contract, historical):
    """Reject altered authority and report all still blocking choices explicitly."""
    errors = []
    def fail(code, message, element=None):
        errors.append({"code": code, "element_id": element or CONTRACT_ID,
                       "message": message, "blocking": True})
    if (contract.get("id") != CONTRACT_ID or contract.get("kind") != "typed_troncons_transition"
            or contract.get("status") != "DECIDE"):
        fail("INV-VER.TRONCON_CONTRACT_SHAPE", "The approved transition contract identity and kind are required.")
        return errors
    p = contract.get("parameters", {})
    fields = {"operator", "activation", "classification_status", "family", "subtypes", "sources",
              "exclusions", "side", "positions", "historical_projection_gate",
              "property_policy", "preservation", "questions", "successions"}
    if not isinstance(p, dict) or set(p) != fields:
        fail("INV-VER.TRONCON_CONTRACT_SHAPE", "Transition parameters must match the closed contract.")
        return errors
    if (contract.get("version") != "2" or p["operator"] != "typed_troncons_transition_v2"
            or p["classification_status"] != "DECIDE" or p["activation"] != "BLOCKED_PENDING_DECISIONS"
            or p["family"] != "da:concept:troncon" or p["preservation"] != PRESERVATION
            or p["side"] != SIDE):
        fail("INV-MAP.TRONCON_POLICY_CHANGED", "No identity merge, geometry conversion, side inference or activation is approved.")
    if p["positions"] != POSITIONS:
        fail("INV-MAP.TRONCON_POSITION_POLICY", "The approved position matrix cannot infer unspecified applicability or an occurrence attribute.")
    try:
        if suspended_projection_sources(registry) != SOURCE_CLASSES:
            raise ValueError("The v2 gate is missing.")
    except ValueError:
        fail("INV-VER.TRONCON_PROJECTION_GATE_REJECTED", "All fifteen historical projections require the exact actively sealed A3 suspension gate.")
    policy = p["property_policy"]
    if (not isinstance(policy, dict) or set(policy) != {"mode", "common_candidates", "missing", "physical_destinations"}
            or policy.get("mode") != "preserve_by_application_key" or policy.get("missing") != "preserve_absence"
            or policy.get("physical_destinations") != "A_APPROUVER"):
        fail("INV-MAP.TRONCON_PROPERTY_POLICY", "Each effective property keeps its identity and absent/null distinction without a SQL destination.")
    facts = {row["id"]: row for row in historical["facts"]}
    targets = {row["id"]: row for row in registry.get("targets", [])}
    source_rows = p["sources"]
    source_ids = [row.get("source") for row in source_rows]
    if set(source_ids) != SOURCE_CLASSES or len(source_ids) != len(set(source_ids)):
        fail("INV-COV.TRONCON_SOURCE_UNIVERSE", "The fifteen individually identified Core, Berge and AH candidates must be present exactly once.")
    if set(p["exclusions"]) != EXCLUDED_CLASSES or len(p["exclusions"]) != len(set(p["exclusions"])):
        fail("INV-COV.TRONCON_EXCLUSIONS", "Lit, Tronçon de lit, Île/Banc, Épis and Revanches remain explicit exclusions.")
    for source in sorted(SOURCE_CLASSES | EXCLUDED_CLASSES):
        if facts.get(source, {}).get("kind") != "class":
            fail("INV-REF.TRONCON_SOURCE_MISSING", "Historical class evidence is missing.", source)
    subtype_ids = [row.get("target") for row in p["subtypes"]]
    if set(subtype_ids) != set(SUBTYPES) or len(subtype_ids) != len(SUBTYPES):
        fail("INV-COV.TRONCON_SUBTYPES", "Only the seven approved types are allowed; Digue is a parent and Pied/Sommet are positions.")
    for row in p["subtypes"]:
        if (set(row) != {"target", "label", "geometry_status"}
                or row.get("geometry_status") != _geometry_status(row.get("target"))):
            fail("INV-MAP.TRONCON_GEOMETRY_PREMATURE", "Each type geometry must match its approved status.", row.get("target"))
        if row.get("label") != SUBTYPES.get(row.get("target")):
            fail("INV-COV.TRONCON_SUBTYPES", "Type identity and business label must match the approved classification.", row.get("target"))
    for identifier in [p["family"], *subtype_ids]:
        target = targets.get(identifier, {})
        if (target.get("state") != "active" or target.get("key_status") != "APPROUVE"
                or target.get("status") != "DECIDE"
                or (identifier == p["family"] and target.get("kind") != "concept")
                or (identifier != p["family"] and (target.get("kind") != "type" or target.get("parent") != p["family"]))):
            fail("INV-MAP.TRONCON_CLASSIFICATION_NOT_APPLIED", "The common family and exactly seven canonical types are approved and active.", identifier)
    family = targets.get(p["family"], {})
    if (family.get("owners") != ["da:concept:amenagement-hydraulique", "da:concept:digue"]
            or family.get("owner_cardinality") is not None):
        fail("INV-MAP.TRONCON_PARENT_POLICY", "Digue and Aménagement hydraulique may each own a Tronçon; no owner cardinality is inferred.")
    for identifier in ("da:type:troncon/digue", "da:type:troncon/pied"):
        if targets.get(identifier, {}).get("state") != "ignored":
            fail("INV-MAP.TRONCON_RETIRED_CATEGORY", "An old Digue/Pied category cannot remain active or pending as a Tronçon type.", identifier)
    questions = p["questions"]
    qids = [row.get("id") for row in questions]
    if not qids or len(qids) != len(set(qids)):
        fail("INV-COV.TRONCON_QUESTIONS", "Open choices require unique explicit questions.")
    limitations = {row["id"]: row for row in registry.get("limitations", [])}
    for question in questions:
        if (set(question) != {"id", "question", "blocks", "status"} or question.get("status") != "A_APPROUVER"
                or not question.get("question") or not question.get("blocks")
                or limitations.get(question.get("id"), {}).get("status") != "A_APPROUVER"):
            fail("INV-COV.TRONCON_QUESTION_UNRECORDED", "Every open choice needs an unresolved limitation and affected identities.", question.get("id"))
        else:
            fail("INV-COV.TRONCON_CHOICE_PENDING", question["question"], question["id"])
    for row in source_rows:
        source = row.get("source")
        expected_source = SOURCE_POLICY.get(source, {})
        if (set(row) != {"source", "proposed_target", "status", "disposition", "position", "qualification_candidates", "question_refs"}
                or row.get("status") != "A_APPROUVER"
                or row.get("proposed_target") not in {None, *subtype_ids}
                or any(row.get(key) != value for key, value in expected_source.items())
                or (not row.get("question_refs") and source not in B4_PHYSICAL_SOURCES)
                or not set(row["question_refs"]) <= set(qids)):
            fail("INV-MAP.TRONCON_SOURCE_DESTINATION", "Generic historical projections stay pending; scoped physical bindings retain distinct identities.", source)
        for qualification in row.get("qualification_candidates", []):
            if (set(qualification) != {"axis", "value", "status"}
                    or qualification.get("status") != "A_APPROUVER"):
                fail("INV-MAP.TRONCON_QUALIFICATION_PREMATURE", "A proposed qualification is not an approved value mapping.", source)
        applications = [f for f in facts.values() if f["kind"] == "application" and f.get("owner") == source]
        if not applications:
            fail("INV-COV.TRONCON_APPLICATIONS_MISSING", "Candidate class lacks its effective historical properties.", source)
        for fact in applications:
            data = fact["data"]
            declaration = facts.get(data.get("declaration"), {})
            if (declaration.get("kind") != data.get("property_kind")
                    or declaration.get("data", {}).get("type") != data.get("type")):
                fail("INV-REF.TRONCON_DECLARATION_MISMATCH", "Effective property must retain its exact declaration and type.", fact["id"])
    for candidate in policy.get("common_candidates", []) if isinstance(policy, dict) else []:
        if facts.get(candidate, {}).get("kind") not in {"attribute", "reference"}:
            fail("INV-REF.TRONCON_COMMON_PROPERTY", "A common property candidate needs an exact historical declaration.", candidate)
    decisions = {row["id"]: row for row in registry.get("decisions", [])}
    succession = resolve_decision_assertions(registry)
    if not p["successions"]:
        fail("INV-DEC.TRONCON_SUCCESSION_MISSING", "A3 requires real exact successions of incompatible prior assertions.")
    resolved = {(row["decision"], canonical_bytes(row["assertion"]), row["successor"],
                 row["replacement"]["subject"], row["replacement"]["predicate"])
                for row in succession["superseded"]}
    for proposed in p["successions"]:
        previous = decisions.get(proposed.get("decision"), {})
        token = canonical_bytes(proposed.get("assertion"))
        active = {canonical_bytes(a) for a in succession["assertions"].get(previous.get("id"), [])}
        if (set(proposed) != {"decision", "assertion", "replacement", "status", "reason"}
                or proposed.get("status") not in {"DECIDE", "A_APPROUVER"} or not proposed.get("reason")
                or previous.get("status") not in {"DECIDE", "CONSEQUENCE_NECESSAIRE"}
                or not isinstance(proposed.get("replacement"), dict)
                or set(proposed["replacement"]) != {"subject", "predicate", "value"}):
            fail("INV-DEC.TRONCON_SUCCESSION_INVALID", "A succession must quote an exact prior assertion and its explicit replacement.", proposed.get("decision"))
            continue
        replacement = proposed["replacement"]
        if proposed["status"] == "A_APPROUVER":
            if token not in active:
                fail("INV-DEC.TRONCON_SUCCESSION_PROPOSAL", "An open succession proposal must quote a still active assertion.", proposed.get("decision"))
            else:
                fail("INV-COV.TRONCON_CHOICE_PENDING", "The proposed succession remains unapproved: " + proposed["reason"], proposed["decision"])
        elif ((previous["id"], token, "decision:MOD1P-A3", replacement["subject"], replacement["predicate"]) not in resolved
              or canonical_bytes(replacement) not in {
                  canonical_bytes(a) for a in succession["assertions"].get("decision:MOD1P-A3", [])}
              | {canonical_bytes(row["assertion"]) for row in succession["superseded"]
                 if row["decision"] == "decision:MOD1P-A3"}):
            fail("INV-DEC.TRONCON_SUCCESSION_NOT_APPLIED", "An approved A3 succession must be resolved and its exact replacement active.", proposed.get("decision"))
    return sorted(errors, key=canonical_bytes)


def transition_summary(registry, historical):
    contract = _contract(registry)
    if contract is None:
        raise ValueError("INV-REF.TRONCON_CONTRACT_MISSING")
    inventory = transition_inventory(registry, historical)
    counts = Counter(row["kind"] for row in inventory["applications"])
    physical_slices = []
    for physical in registry.get("contracts", []):
        if physical.get("id") == CRETE_BINDING_ID:
            diagnostics = validate_crete_binding(registry, physical, historical)
            physical_slices.append({"contract_id": CRETE_BINDING_ID, "source": CRETE_SOURCE,
                                   "target": "da:type:troncon/crete", "qualification": "PARTIAL",
                                   "scoped_writer_authorized": not diagnostics, "certified": False,
                                   "native_crud": False, "legacy_projection_suspended": True,
                                   "diagnostics": diagnostics})
        elif physical.get("id") == TALUS_BINDING_ID:
            diagnostics = validate_talus_binding(registry, physical, historical)
            for source in TALUS_BINDING_PARAMETERS["sources"]:
                physical_slices.append({"contract_id": TALUS_BINDING_ID, "source": source["source"],
                                        "target": source["target"], "qualification": "PARTIAL",
                                        "scoped_writer_authorized": not diagnostics, "certified": False,
                                        "native_crud": False, "legacy_projection_suspended": True,
                                        "diagnostics": diagnostics})
    from .troncons_berges import PARAMETERS, validate_binding
    for physical in registry.get("contracts", []):
        if physical.get("id") in PARAMETERS:
            diagnostics = validate_binding(registry, physical, historical)
            for source in physical["parameters"]["sources"]:
                physical_slices.append({"contract_id": physical["id"], "source": source["source"],
                                        "target": source["target"], "qualification": "PARTIAL",
                                        "scoped_writer_authorized": not diagnostics, "certified": False,
                                        "native_crud": False, "legacy_projection_suspended": True,
                                        "diagnostics": diagnostics})
    from .troncons_b4 import BINDING_ID as B4_BINDING_ID, validate_b4_binding
    for physical in registry.get("contracts", []):
        if physical.get("id") == B4_BINDING_ID:
            diagnostics = validate_b4_binding(registry, physical, historical)
            for source in physical["parameters"]["sources"]:
                physical_slices.append({"contract_id": B4_BINDING_ID, "source": source["source"],
                                        "target": source["target"], "qualification": "PARTIAL",
                                        "scoped_writer_authorized": not diagnostics, "certified": False,
                                        "native_crud": False, "legacy_projection_suspended": True,
                                        "diagnostics": diagnostics})
    from .troncons_ah import BINDING_ID as B5_BINDING_ID, validate_b5_binding
    for physical in registry.get("contracts", []):
        if physical.get("id") == B5_BINDING_ID:
            diagnostics = validate_b5_binding(registry, physical, historical)
            source = physical["parameters"]["sources"][0]
            physical_slices.append({"contract_id": B5_BINDING_ID, "source": source["source"],
                                    "target": source["target"], "qualification": "PARTIAL",
                                    "scoped_writer_authorized": not diagnostics, "certified": False,
                                    "native_crud": False, "legacy_projection_suspended": True,
                                    "diagnostics": diagnostics})
    from .troncons_core import BINDING_ID as B6_BINDING_ID, validate_b6_binding
    for physical in registry.get("contracts", []):
        if physical.get("id") == B6_BINDING_ID:
            diagnostics = validate_b6_binding(registry, physical, historical)
            physical_slices.append({"contract_id": B6_BINDING_ID, "source": CLASS + "core/TronconDigue",
                                    "target": "da:type:troncon/indetermine", "qualification": "PARTIAL",
                                    "scoped_writer_authorized": not diagnostics, "certified": False,
                                    "native_crud": True, "legacy_projection_suspended": True,
                                    "diagnostics": diagnostics})
    return {"contract_id": CONTRACT_ID, "status": "BLOCKED_PENDING_DECISIONS",
            "classification_status": "DECIDE",
            "certified": False, "executable": False,
            "physical_slices": physical_slices,
            "counts": {"sources": len(inventory["sources"]), "applications": len(inventory["applications"]),
                       "attributes": counts["attribute"], "references": counts["reference"],
                       "inherited": sum(row["inherited"] for row in inventory["applications"]),
                       "geometries": sum(bool(row["geometry"]) for row in inventory["applications"]),
                       "incoming_references": len(inventory["incoming_references"]),
                       "catalogs": len(inventory["catalogs"])},
            "questions": deepcopy(contract["parameters"]["questions"]),
            "diagnostics": validate_transition_contract(registry, contract, historical)}


def validate_classification_event(registry, contract_id, event):
    """Validate a localized qualification without authorizing an import.

    ``position=None`` explicitly means that no position is qualified. A false
    validity flag cannot turn a forbidden or undecided qualification into a
    permitted mapping. Historic geometry retention is a separate contract.
    """
    errors = []
    def fail(code, message):
        errors.append({"code": code, "element_id": contract_id, "message": message, "blocking": True})
    contract = _contract(registry)
    if contract_id != CONTRACT_ID or contract is None:
        fail("INV-REF.TRONCON_CONTRACT_MISSING", "Unknown classification contract.")
        return errors
    try:
        if suspended_projection_sources(registry) != SOURCE_CLASSES:
            raise ValueError("The v2 contract is required.")
    except ValueError:
        fail("INV-VER.TRONCON_PROJECTION_GATE_REJECTED", "Classification requires the approved A3 contract and its unchanged projection gate.")
        return errors
    parameters = contract["parameters"]
    if parameters.get("positions") != POSITIONS or parameters.get("side") != SIDE:
        fail("INV-MAP.TRONCON_POLICY_CHANGED", "The approved side and position policies are required.")
        return errors
    if (not isinstance(event, dict) or set(event) != {"target", "side", "position", "valid", "physical_outputs"}
            or type(event.get("valid")) is not bool or not isinstance(event.get("physical_outputs"), list)
            or not isinstance(event.get("target"), str) or not isinstance(event.get("side"), str)
            or (event.get("position") is not None and not isinstance(event["position"], str))):
        fail("INV-VER.TRONCON_CLASSIFICATION_SHAPE", "A qualification requires an explicit type, side, optional position, validity and physical outputs.")
        return errors
    if event["physical_outputs"]:
        fail("INV-COV.TRONCON_ACTIVATION_BLOCKED", "Classification does not authorize physical output or source mapping.")
    if event["target"] not in SUBTYPES:
        fail("INV-MAP.TRONCON_CLASSIFICATION_TYPE", "Digue and Pied are not Tronçon types; only the seven approved types apply.")
    else:
        target = next((row for row in registry.get("targets", []) if row["id"] == event["target"]), {})
        if (target.get("state") != "active" or target.get("key_status") != "APPROUVE"
                or target.get("status") != "DECIDE" or target.get("parent") != "da:concept:troncon"):
            fail("INV-MAP.TRONCON_CLASSIFICATION_NOT_APPLIED", "The selected canonical type must be approved and active.")
    if event["side"] not in {row["code"] for row in SIDE["values"]}:
        fail("INV-MAP.TRONCON_CLASSIFICATION_SIDE", "Historical rive or position values cannot be inferred as a side.")
    positions = {row["code"] for row in POSITIONS["values"]}
    if event["position"] is not None and event["position"] not in positions:
        fail("INV-MAP.TRONCON_CLASSIFICATION_POSITION", "Unknown localized position.")
    if event["target"] in SUBTYPES and event["position"] in positions:
        row = next(row for row in POSITIONS["applicability"] if row["target"] == event["target"])
        applicability = row[event["position"]]
        if applicability == "FORBIDDEN":
            fail("INV-MAP.TRONCON_POSITION_FORBIDDEN", "This localized position is explicitly forbidden for this type, regardless of validity.")
        elif applicability == "A_APPROUVER":
            fail("INV-COV.TRONCON_POSITION_PENDING", "Position applicability has not been approved; valid=false cannot supply a missing mapping.")
    return sorted(errors, key=canonical_bytes)


def validate_transition_event(registry, contract_id, event):
    """Check exact preservation; this witness can never activate the transition.

    ``before`` and ``after`` contain records with exactly RECORD_FIELDS. Values
    use historical application/declaration keys; absence, null, ordering,
    geometry and SRID are compared without normalization. A Berge deduplication
    needs its existing dedicated proof and is deliberately not inferred here.
    """
    errors = []
    def fail(code, message, element=None):
        errors.append({"code": code, "element_id": element or contract_id,
                       "message": message, "blocking": True})
    contract = _contract(registry)
    if contract_id != CONTRACT_ID or contract is None:
        fail("INV-REF.TRONCON_CONTRACT_MISSING", "Unknown transition contract.")
        return errors
    if (contract.get("status") != "DECIDE" or contract.get("kind") != "typed_troncons_transition"
            or contract.get("parameters", {}).get("activation") != "BLOCKED_PENDING_DECISIONS"):
        fail("INV-VER.TRONCON_CONTRACT_SHAPE", "The recorded transition must still prohibit activation.")
        return errors
    try:
        if suspended_projection_sources(registry) != SOURCE_CLASSES:
            raise ValueError("The v2 gate is required.")
    except ValueError:
        fail("INV-VER.TRONCON_PROJECTION_GATE_REJECTED", "Preservation requires the exact actively sealed A3 gate.")
        return errors
    if not isinstance(event, dict) or set(event) != {"activate", "physical_outputs", "before", "after"}:
        fail("INV-VER.TRONCON_EVENT_SHAPE", "Preservation witness fields must be explicit and closed.")
        return errors
    if type(event["activate"]) is not bool or not isinstance(event["physical_outputs"], list):
        fail("INV-VER.TRONCON_EVENT_SHAPE", "Activation and physical outputs require their explicit types.")
        return errors
    if event["activate"] or event["physical_outputs"]:
        fail("INV-COV.TRONCON_ACTIVATION_BLOCKED", "Pending choices prohibit activation and physical outputs.")
    indexed = []
    for side in ("before", "after"):
        records = event[side]
        if not isinstance(records, list) or any(
            not isinstance(row, dict) or set(row) != RECORD_FIELDS
            or not all(isinstance(row[key], str) and row[key] for key in ("source_class", "uuid", "document_path"))
            or not isinstance(row["attributes"], dict) or not isinstance(row["references"], dict)
            for row in records
        ):
            fail("INV-VER.TRONCON_EVENT_SHAPE", "Each record requires its class, UUID, document path and exact values.")
            return sorted(errors, key=canonical_bytes)
        rows = {(row["source_class"], row["uuid"], row["document_path"]): row for row in records}
        for row in records:
            if row["source_class"] not in SOURCE_CLASSES:
                fail("INV-REF.TRONCON_EVENT_SOURCE", "The witness cannot absorb an excluded or unknown historical class.", row["source_class"])
        if len(rows) != len(records):
            fail("INV-ID.TRONCON_DUPLICATE_IDENTITY", "Distinct records need explicit source identities; duplicate identities cannot be collapsed.")
        indexed.append(rows)
    before, after = indexed
    if set(before) != set(after):
        fail("INV-ID.TRONCON_OCCURRENCE_LOSS", "No source identity may disappear, be replaced or be merged without its separate approved proof.")
    for identity in sorted(set(before).intersection(after)):
        for field in ("attributes", "references", "geometry", "srid"):
            if canonical_bytes(before[identity][field]) != canonical_bytes(after[identity][field]):
                fail("INV-MAP.TRONCON_PRESERVATION_CHANGED", "Historical " + field + " changed; retain its original value and presence.", "/".join(identity))
    return sorted(errors, key=canonical_bytes)
