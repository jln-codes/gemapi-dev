"""MOD-1P-B6 : projection physique des TronconDigue dans la racine commune."""

from importlib.resources import files

from .common import digest, strict_load
from .decision_successions import resolve_decision_assertions
from .typed_troncons import SOURCE_CLASSES, suspended_projection_sources

DECISION = "decision:TRONCONS-UNIFICATION-C2"
BINDING_ID = "contract:mod1p/core-troncon-physical-binding"
SOURCE = "sirs-v2:class:core/TronconDigue"
FIELDS = {
    "_id": ("public.troncons", "id"),
    "digueId": ("public.troncons", "digue_id"),
    "amenagementHydrauliqueId": ("external:source-inventory", "document"),
    "geometry": ("public.troncons", "geometry"),
    "designation": ("public.troncons", "designation"),
    "commentaire": ("public.troncons", "commentaire"),
    "date_debut": ("public.troncons", "date_debut"),
    "date_fin": ("public.troncons", "date_fin"),
    "valid": ("public.troncons", "valid"),
    "libelle": ("public.troncons", "libelle"),
    "typeRiveId": ("public.troncons", "type_rive_id"),
    "typeTronconId": ("public.troncons", "type_troncon_id"),
    "typologieTronconId": ("public.troncons", "typologie_troncon_id"),
    "systemeRepDefautId": ("public.troncons", "systeme_reperage_defaut_id"),
}
PARAMETERS = {
    "qualification": "PARTIAL",
    "source": SOURCE,
    "target": "da:type:troncon/indetermine",
    "identity": "exact_source_uuid_no_merge",
    "subtype": "INDETERMINE_explicit_decision",
    "geometry": "source_LineString_business_srid_no_reinterpretation",
    "digue": "explicit_digueId_only",
    "amenagement": "historical_zone_assertion_only_no_regulatory_parent",
    "inverse_amenagement_links": "independent_association_no_parent_inference",
    "storage": "public.troncons_single_canonical_row",
    "reperage": "optional_shared_systems_no_invented_axis",
    "provenance": "immutable_external_source_inventory_and_authenticated_native_report",
    "business_origin_markers": "forbidden",
    "fields": [{"source": key, "table": table, "column": column}
               for key, (table, column) in sorted(FIELDS.items())],
    "certification": "partial_no_global_claim",
}


def validate_b6_binding(registry, contract, historical=None):
    errors = []

    def fail(code):
        errors.append({"code": code, "element_id": BINDING_ID,
                       "message": "Projection TronconDigue incompatible avec MOD-1P-B6.",
                       "blocking": True})

    matches = [item for item in registry.get("contracts", []) if item.get("id") == BINDING_ID]
    if (len(matches) != 1 or matches[0] != contract or not isinstance(contract, dict)
            or contract.get("kind") != "typed_troncon_physical_slice"
            or contract.get("version") != "2" or contract.get("status") != "DECIDE"
            or DECISION not in contract.get("decision_refs", [])):
        fail("INV-VER.B6_BINDING_SHAPE")
        return errors
    if contract.get("parameters") != PARAMETERS:
        fail("INV-MAP.B6_BINDING_PARAMETERS")
    resolved = resolve_decision_assertions(registry)
    decision = next((item for item in registry.get("decisions", []) if item.get("id") == DECISION), {})
    assertions = resolved["assertions"].get(DECISION, [])
    if (resolved["diagnostics"] or decision.get("status") != "DECIDE"
            or {"subject": BINDING_ID, "predicate": "contract_parameters_digest",
                "value": digest(PARAMETERS)} not in assertions):
        fail("INV-VER.B6_BINDING_SEAL")
    try:
        if suspended_projection_sources(registry) != SOURCE_CLASSES:
            fail("INV-VER.B6_BINDING_GATE")
    except ValueError:
        fail("INV-VER.B6_BINDING_GATE")
    if historical is not None:
        applications = {item["name"] for item in historical.get("facts", [])
                        if item.get("kind") == "application" and item.get("owner") == SOURCE}
        if not set(FIELDS).difference({"_id"}) <= applications:
            fail("INV-COV.B6_BINDING_FIELDS")
    return errors


def load_b6_binding():
    registry = strict_load(files(__package__).joinpath("registry.json"))
    contract = next((item for item in registry["contracts"] if item["id"] == BINDING_ID), None)
    errors = validate_b6_binding(registry, contract)
    if errors:
        raise ValueError("INV-VER.B6_BINDING_REJECTED: " + ",".join(item["code"] for item in errors))
    return PARAMETERS
