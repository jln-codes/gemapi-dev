"""B5: sealed root-only projection of the explicitly qualified hydraulic line."""

from copy import deepcopy
from importlib.resources import files
from uuid import UUID

from .common import canonical_bytes, digest, strict_load
from .decision_successions import resolve_decision_assertions
from .typed_troncons import SOURCE_CLASSES, suspended_projection_sources

DECISION = "decision:TRONCONS-UNIFICATION-C2"
BINDING_ID = "contract:mod1p/ah-trait-physical-binding"
KIND = "typed_troncon_physical_slice"
SOURCE = "sirs-v2:class:dependance/TraitAmenagementHydraulique"
TARGET = "da:type:troncon/indetermine"
COLUMNS = {
    "author": "created_by", "lastUpdateAuthor": "edited_by",
    "commentaire": "commentaire", "date_debut": "date_debut", "date_fin": "date_fin",
    "designation": "designation", "geometry": "geometry", "valid": "valid",
}
FIELDS = tuple(sorted((*COLUMNS, "dateMaj", "amenagementHydrauliqueId")))
SOURCE_BINDING = {
    "source": SOURCE, "source_class": "TraitAmenagementHydraulique", "target": TARGET,
    "root_type": "INDETERMINE",
    "applications": ["sirs-v2:application:dependance/TraitAmenagementHydraulique/" + key for key in FIELDS],
    "bindings": [{
        "application": "sirs-v2:application:dependance/TraitAmenagementHydraulique/" + key,
        "source_key": key, "proof_path": [key],
        "table": "public.troncons" if key in COLUMNS else "external:source-inventory",
        "column": COLUMNS.get(key, "document"),
    } for key in FIELDS],
}
PARAMETERS = {
    "qualification": "PARTIAL", "identity": "one_source_uuid_one_target_uuid",
    "merge": "forbidden", "business_origin_markers": "forbidden",
    "source_preservation": "immutable_external_source_inventory_and_authenticated_native_report",
    "subtype": "explicit_approved_INDETERMINE_not_mapping_failure",
    "geometry": {"target_status": "A_APPROUVER", "conversion": "forbidden",
                 "srid": "preserve_exact_no_inference",
                 "invalid": "retain_invalid_with_diagnostic"},
    "unresolved_references": "preserve_raw_and_diagnose_no_invention",
    "authors": "explicit_contact_mapping_only",
    "parent": "historical_zone_assertion_only_never_infer_regulatory_AH_or_digue",
    "side": "null_without_qualified_mapping", "position": "no_automatic_troncon_position_or_trace",
    "scope": {"native_crud": False, "certification": False,
              "StructureAmenagementHydraulique": "deferred"},
    "tables": {"root": "public.troncons", "provenance": "external:source-inventory"},
    "sources": [SOURCE_BINDING],
}


def validate_b5_binding(registry, contract, historical=None):
    errors = []

    def fail(code, element=BINDING_ID):
        errors.append({"code": code, "element_id": element,
                       "message": "B5 binding differs from its approved root-only scope.", "blocking": True})

    matches = [row for row in registry.get("contracts", []) if row.get("id") == BINDING_ID]
    if (len(matches) != 1 or matches[0] != contract or not isinstance(contract, dict)
            or contract.get("kind") != KIND or contract.get("version") != "2"
            or contract.get("status") != "DECIDE" or DECISION not in contract.get("decision_refs", [])):
        fail("INV-VER.B5_BINDING_SHAPE")
        return errors
    if contract.get("parameters") != PARAMETERS:
        fail("INV-MAP.B5_BINDING_PARAMETERS")
    resolution = resolve_decision_assertions(registry)
    decision = next((row for row in registry.get("decisions", []) if row.get("id") == DECISION), {})
    assertions = resolution["assertions"].get(DECISION, [])
    if (decision.get("status") != "DECIDE" or resolution["diagnostics"] or
            [row for row in assertions if row.get("subject") == BINDING_ID and
             row.get("predicate") == "contract_parameters_digest"] != [{
                 "subject": BINDING_ID, "predicate": "contract_parameters_digest", "value": digest(PARAMETERS)}]
            or {"subject": BINDING_ID, "predicate": "contract", "value": KIND} not in assertions):
        fail("INV-VER.B5_BINDING_SEAL")
    try:
        if suspended_projection_sources(registry) != SOURCE_CLASSES:
            fail("INV-VER.B5_BINDING_HISTORICAL_GATE")
    except ValueError:
        fail("INV-VER.B5_BINDING_HISTORICAL_GATE")
    target = next((row for row in registry.get("targets", []) if row.get("id") == TARGET), {})
    if (target.get("status") != "DECIDE" or target.get("state") != "active"
            or target.get("key_status") != "APPROUVE" or target.get("parent") != "da:concept:troncon"
            or (target.get("geometry") or {}).get("status", "A_APPROUVER") != "A_APPROUVER"):
        fail("INV-MAP.B5_BINDING_TARGET", TARGET)
    if historical is not None:
        applications = {row["id"]: row for row in historical.get("facts", [])
                        if row.get("kind") == "application" and row.get("owner") == SOURCE}
        if set(applications) != set(SOURCE_BINDING["applications"]):
            fail("INV-COV.B5_BINDING_APPLICATIONS", SOURCE)
        for binding in SOURCE_BINDING["bindings"]:
            if applications.get(binding["application"], {}).get("name") != binding["source_key"]:
                fail("INV-MAP.B5_BINDING_PROPERTY", binding["application"])
    return sorted(errors, key=canonical_bytes)


def load_b5_binding():
    registry = strict_load(files(__package__).joinpath("registry.json"))
    contract = next((row for row in registry["contracts"] if row["id"] == BINDING_ID), None)
    errors = validate_b5_binding(registry, contract)
    if errors:
        raise ValueError("INV-VER.B5_BINDING_REJECTED: " + ",".join(row["code"] for row in errors))
    return deepcopy(SOURCE_BINDING)


def validate_b5_binding_event(registry, event):
    contract = next((row for row in registry["contracts"] if row["id"] == BINDING_ID), None)
    errors = validate_b5_binding(registry, contract)
    if errors:
        return errors

    def fail(code):
        errors.append({"code": code, "element_id": BINDING_ID,
                       "message": "B5 occurrence identity/document witness is incomplete.", "blocking": True})

    fields = {"source_class", "source_id", "root_id", "target_type", "source_document",
              "stored_document", "claimed_complete"}
    if not isinstance(event, dict) or set(event) != fields:
        fail("INV-VER.B5_EVENT_SHAPE")
        return errors
    if event["source_class"] != "TraitAmenagementHydraulique" or event["target_type"] != "INDETERMINE":
        fail("INV-MAP.B5_EVENT_SCOPE")
    try:
        ids = [UUID(event[key]) for key in ("source_id", "root_id")]
        raw = event["source_document"].get("_id")
        if len(set(ids)) != 1 or (raw is not None and UUID(raw) != ids[0]):
            raise ValueError("identity")
    except (ValueError, TypeError, KeyError, AttributeError):
        fail("INV-ID.B5_EVENT_IDENTITY")
    if (not isinstance(event["source_document"], dict) or not isinstance(event["stored_document"], dict)
            or canonical_bytes(event["source_document"]) != canonical_bytes(event["stored_document"])):
        fail("INV-MAP.B5_EVENT_DOCUMENT_CHANGED")
    if event["claimed_complete"] is not False:
        fail("INV-COV.B5_EVENT_CERTIFICATION")
    return sorted(errors, key=canonical_bytes)
