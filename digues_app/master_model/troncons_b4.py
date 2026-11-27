"""Sealed partial B4 projection for the two physical Tronçon types.

The historical class is confined to the import contract and its technical
proof. It is never a business discriminator or a position on the Tronçon.
"""

from __future__ import annotations

from copy import deepcopy
from importlib.resources import files
from uuid import UUID

from .common import canonical_bytes, digest, strict_load
from .decision_successions import resolve_decision_assertions
from .typed_troncons import SOURCE_CLASSES, suspended_projection_sources


DECISION = "decision:TRONCONS-UNIFICATION-C2"
BINDING_ID = "contract:mod1p/b4-physical-binding"
KIND = "typed_troncon_physical_slice"
SHAPES = ["LINESTRING", "POLYGON"]
SOURCES = {
    "SommetRisberme": {
        "target": "da:type:troncon/risberme", "type": "RISBERME",
        "applications": (
            "author", "borneDebutId", "borneFinId", "borne_debut_aval", "borne_debut_distance",
            "borne_fin_aval", "borne_fin_distance", "commentaire", "coteId", "crsName",
            "dateMaj", "date_debut", "date_fin", "designation", "editedGeoCoordinate",
            "epaisseur", "fonctionId", "geometry", "geometryMode", "lastUpdateAuthor",
            "latitudeMax", "latitudeMin", "linearId", "longitudeMax", "longitudeMin",
            "materiauId", "natureId", "numCouche", "photos", "positionDebut",
            "positionFin", "prDebut", "prFin", "sourceId", "systemeRepId", "valid",
        ),
        "columns": {
            "numCouche": ("public.troncons", "num_couche"),
            "epaisseur": ("public.troncons", "epaisseur"),
            "coteId": ("public.troncons", "cote_id"),
            "fonctionId": ("public.troncons", "fonction_id"),
            "natureId": ("public.troncons", "nature_id"),
            "materiauId": ("public.troncons", "materiau_id"),
        },
    },
    "LargeurFrancBord": {
        "target": "da:type:troncon/franc-bord", "type": "FRANC_BORD",
        "applications": (
            "author", "borneDebutId", "borneFinId", "borne_debut_aval", "borne_debut_distance",
            "borne_fin_aval", "borne_fin_distance", "commentaire", "crsName", "dateMaj",
            "date_debut", "date_fin", "designation", "editedGeoCoordinate", "geometry",
            "geometryMode", "lastUpdateAuthor", "latitudeMax", "latitudeMin", "linearId",
            "longitudeMax", "longitudeMin", "photos", "positionDebut", "positionFin",
            "prDebut", "prFin", "sourceId", "systemeRepId", "typeLargeurFrancBord", "valid",
        ),
        "columns": {
            "typeLargeurFrancBord": ("public.troncons", "largeur_franc_bord_id"),
        },
    },
}
COMMON = {
    "designation": ("public.troncons", "designation"),
    "commentaire": ("public.troncons", "commentaire"),
    "date_debut": ("public.troncons", "date_debut"),
    "date_fin": ("public.troncons", "date_fin"),
    "geometry": ("public.troncons", "geometry"),
    "valid": ("public.troncons", "valid"),
    "linearId": ("public.troncons", "troncon_support_id"),
    "sourceId": ("public.troncons", "source_id"),
    "author": ("public.troncons", "created_by"),
    "lastUpdateAuthor": ("public.troncons", "edited_by"),
}


def _source_binding(name: str, settings: dict) -> dict:
    source = "sirs-v2:class:core/" + name
    columns = COMMON | settings["columns"]
    applications = ["sirs-v2:application:core/" + name + "/" + field
                    for field in settings["applications"]]
    return {
        "source": source, "source_class": name, "target": settings["target"],
        "root_type": settings["type"], "extension_type": settings["type"],
        "applications": applications,
        "bindings": [{
            "application": application,
            "source_key": field,
            "table": columns.get(field, ("external:source-inventory", "document"))[0],
            "column": columns.get(field, ("external:source-inventory", "document"))[1],
            "proof_path": [field],
        } for field, application in zip(settings["applications"], applications, strict=True)],
    }


PARAMETERS = {
    "qualification": "PARTIAL",
    "identity": "one_source_uuid_one_target_uuid",
    "merge": "forbidden",
    "business_origin_markers": "forbidden",
    "source_preservation": "immutable_external_source_inventory_and_authenticated_native_report",
    "geometry": {
        "target_status": "DECIDE", "allowed_shapes": SHAPES,
        "required_for_valid": True, "conversion": "forbidden",
        "srid": "preserve_exact_no_inference",
        "invalid": "retain_invalid_with_diagnostic",
        "missing_diagnostic": "TRONCON_GEOMETRY_MISSING",
        "shape_diagnostic": "TRONCON_GEOMETRY_SHAPE_INCOMPATIBLE",
        "invalid_diagnostic": "TRONCON_GEOMETRY_INVALID",
        "empty_diagnostic": "TRONCON_GEOMETRY_EMPTY",
    },
    "unresolved_references": "preserve_raw_and_diagnose_no_invention",
    "side": "null_without_qualified_mapping",
    "position": "no_automatic_troncon_position_or_trace",
    "catalogs": "exact_canonical_reference_only_no_label_inference",
    "authors": "explicit_contact_mapping_only",
    "parent": "explicit_exact_source_reference_only_no_spatial_inference",
    "width": "historical_numeric_null_without_actual_numeric_source",
    "scope": {"native_crud": False, "certification": False, "fondation": "deferred"},
    "tables": {
        "root": "public.troncons",
        "provenance": "external:source-inventory",
    },
    "sources": [_source_binding(name, settings) for name, settings in SOURCES.items()],
}


def validate_b4_binding(registry: dict, contract: dict | None, historical: dict | None = None) -> list[dict]:
    errors: list[dict] = []

    def fail(code: str, element: str = BINDING_ID):
        errors.append({"code": code, "element_id": element,
                       "message": "B4 physical binding differs from its exact approved scope.",
                       "blocking": True})

    matches = [row for row in registry.get("contracts", []) if row.get("id") == BINDING_ID]
    if len(matches) != 1 or matches[0] != contract or not isinstance(contract, dict) or any((
        contract.get("kind") != KIND,
        contract.get("version") != "2",
        contract.get("status") != "DECIDE",
        DECISION not in contract.get("decision_refs", []),
    )):
        fail("INV-VER.B4_BINDING_SHAPE")
        return errors
    if contract.get("parameters") != PARAMETERS:
        fail("INV-MAP.B4_BINDING_PARAMETERS")
    resolution = resolve_decision_assertions(registry)
    decision = next((row for row in registry.get("decisions", []) if row.get("id") == DECISION), {})
    assertions = resolution["assertions"].get(DECISION, [])
    if (decision.get("status") != "DECIDE" or resolution["diagnostics"] or
            [row for row in assertions if row.get("subject") == BINDING_ID and
             row.get("predicate") == "contract_parameters_digest"] != [{
                "subject": BINDING_ID, "predicate": "contract_parameters_digest", "value": digest(PARAMETERS)
             }] or
            {"subject": BINDING_ID, "predicate": "contract", "value": KIND} not in assertions):
        fail("INV-VER.B4_BINDING_SEAL")
    try:
        if suspended_projection_sources(registry) != SOURCE_CLASSES:
            fail("INV-VER.B4_BINDING_HISTORICAL_GATE")
    except ValueError:
        fail("INV-VER.B4_BINDING_HISTORICAL_GATE")
    targets = {row["id"]: row for row in registry.get("targets", [])}
    for source in PARAMETERS["sources"]:
        target = targets.get(source["target"], {})
        geometry = target.get("geometry") or {}
        if (target.get("status") != "DECIDE" or target.get("state") != "active" or
                target.get("key_status") != "APPROUVE" or target.get("parent") != "da:concept:troncon" or
                geometry.get("status") != "DECIDE" or geometry.get("target_shapes") != SHAPES or
                geometry.get("required_for_valid") is not True or geometry.get("policy") != "preserve_if_present"):
            fail("INV-MAP.B4_BINDING_TARGET", source["target"])
        if historical is not None:
            applications = {row["id"]: row for row in historical.get("facts", [])
                            if row.get("kind") == "application" and row.get("owner") == source["source"]}
            if set(applications) != set(source["applications"]):
                fail("INV-COV.B4_BINDING_APPLICATIONS", source["source"])
            for binding in source["bindings"]:
                fact = applications.get(binding["application"], {})
                if fact.get("name") != binding["source_key"] or binding["proof_path"] != [binding["source_key"]]:
                    fail("INV-MAP.B4_BINDING_PROPERTY", binding["application"])
    return sorted(errors, key=canonical_bytes)


def load_b4_binding(source_class: str) -> dict:
    """Return one exact source slice only after checking the packaged B4 seal."""
    if source_class not in SOURCES:
        raise ValueError("INV-VER.B4_UNQUALIFIED_SOURCE: " + source_class)
    registry = strict_load(files(__package__).joinpath("registry.json"))
    contract = next((row for row in registry.get("contracts", []) if row.get("id") == BINDING_ID), None)
    errors = validate_b4_binding(registry, contract)
    if errors:
        raise ValueError("INV-VER.B4_BINDING_REJECTED: " + ",".join(row["code"] for row in errors))
    return deepcopy(next(row for row in PARAMETERS["sources"] if row["source_class"] == source_class))


def validate_b4_binding_event(registry: dict, event: dict) -> list[dict]:
    """Scoped identity and complete source-document witness, not global certification."""
    contract = next((row for row in registry.get("contracts", []) if row.get("id") == BINDING_ID), None)
    errors = validate_b4_binding(registry, contract)
    if errors:
        return errors

    def fail(code: str):
        errors.append({"code": code, "element_id": BINDING_ID,
                       "message": "B4 occurrence identity/document witness is incomplete.", "blocking": True})

    fields = {"source_class", "source_id", "root_id", "extension_id", "target_type",
              "source_document", "stored_document", "claimed_complete"}
    if not isinstance(event, dict) or set(event) != fields:
        fail("INV-VER.B4_EVENT_SHAPE")
        return errors
    source = next((row for row in PARAMETERS["sources"] if row["source_class"] == event["source_class"]), None)
    if source is None or event["target_type"] != source["root_type"]:
        fail("INV-MAP.B4_EVENT_SCOPE")
    try:
        uuids = [UUID(event[key]) for key in ("source_id", "root_id", "extension_id")]
        raw = event["source_document"].get("_id")
        if len(set(uuids)) != 1 or (raw is not None and UUID(raw) != uuids[0]):
            raise ValueError("identity mismatch")
    except (ValueError, TypeError, KeyError, AttributeError):
        fail("INV-ID.B4_EVENT_IDENTITY")
    if (not isinstance(event["source_document"], dict) or
            not isinstance(event["stored_document"], dict) or
            canonical_bytes(event["source_document"]) != canonical_bytes(event["stored_document"])):
        fail("INV-MAP.B4_EVENT_DOCUMENT_CHANGED")
    if event["claimed_complete"] is not False:
        fail("INV-COV.B4_EVENT_CERTIFICATION")
    return sorted(errors, key=canonical_bytes)
