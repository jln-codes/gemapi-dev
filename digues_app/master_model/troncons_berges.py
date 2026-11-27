"""B3 sealed physical slices: one source UUID, one identity; no SIRS business tag.

Provenance lives in technique. The mixed MOD-1M seal is retained as historical
proof for its unchanged Lit portion, never as authority for Berge deduplication.
"""
from copy import deepcopy
from importlib.resources import files
from uuid import UUID

from .common import canonical_bytes, digest, strict_load
from .decision_successions import resolve_decision_assertions

DECISION = "decision:TRONCONS-UNIFICATION-C2"
BERGE_BINDING_ID = "contract:mod1p/berge-physical-binding"
POSITION_BINDING_ID = "contract:mod1p/position-traces-binding"
KIND = "typed_troncon_physical_slice"
LEGACY_ID = "contract:mod1m/core-berge-lit-projection"
LEGACY_DIGEST = "6ce0c9bd96a5229991b916ee7fcdcabeb0fb4ff59daff246875307d813163dfe"
ROOT = "public.troncons"
TRACES = "public.traces_positions_troncons"
SOURCE_NAMES = {
    "berge/Berge": "amenagementHydrauliqueId author borneIds commentaire dateMaj date_debut date_fin designation digueId geometry gestions lastUpdateAuthor libelle photos systemeRepDefautId typeRiveId typeTronconId typologieTronconId valid".split(),
    "berge/TraitBerge": "author bergeId commentaire date_debut date_fin designation geometry lastUpdateAuthor valid".split(),
    "berge/PiedBerge": "author borneDebutId borneFinId borne_debut_aval borne_debut_distance borne_fin_aval borne_fin_distance commentaire crsName dateMaj date_debut date_fin designation editedGeoCoordinate epaisseur fonctionId geometry geometryMode largeurSabot lastUpdateAuthor latitudeMax latitudeMin linearId longitudeMax longitudeMin materiauId natureId numCouche photos positionDebut positionFin prDebut prFin riveId sourceId systemeRepId valid".split(),
    "berge/SommetBerge": "author borneDebutId borneFinId borne_debut_aval borne_debut_distance borne_fin_aval borne_fin_distance commentaire crsName dateMaj date_debut date_fin designation editedGeoCoordinate fonctionId geometry geometryMode lastUpdateAuthor latitudeMax latitudeMin linearId longitudeMax longitudeMin materiauId natureId natureSolId numCouche photos positionDebut positionFin prDebut prFin presenceRipisylve riveId systemeRepId valid".split(),
    "core/PiedDigue": "author borneDebutId borneFinId borne_debut_aval borne_debut_distance borne_fin_aval borne_fin_distance commentaire coteId crsName dateMaj date_debut date_fin designation editedGeoCoordinate fonctionId geometry geometryMode lastUpdateAuthor latitudeMax latitudeMin linearId longitudeMax longitudeMin materiauId natureId photos positionDebut positionFin prDebut prFin sourceId systemeRepId valid".split(),
}
COMMON = {key: key for key in ("designation", "commentaire", "date_debut", "date_fin", "geometry", "valid")}
BERGE_COLUMNS = {
    **{key: (ROOT, column) for key, column in COMMON.items()},
    "digueId": (ROOT, "digue_id"),
    "amenagementHydrauliqueId": ("external:source-inventory", "document"),
    **{key: (ROOT, column) for key, column in {
        "author": "created_by", "lastUpdateAuthor": "edited_by", "libelle": "libelle",
        "typeRiveId": "type_rive_id",
        "typeTronconId": "type_troncon_id", "typologieTronconId": "typologie_troncon_id",
        "systemeRepDefautId": "systeme_reperage_defaut_id",
    }.items()},
}
POSITION_COLUMNS = {
    **COMMON, "author": "created_by", "lastUpdateAuthor": "edited_by",
    "sourceId": "source_id", "fonctionId": "fonction_id", "natureId": "nature_id",
    "materiauId": "materiau_id", "riveId": "rive_id", "coteId": "cote_id",
    "natureSolId": "nature_sol_id", "numCouche": "num_couche", "largeurSabot": "largeur_sabot",
    "epaisseur": "epaisseur", "presenceRipisylve": "presence_ripisylve",
    "positionDebut": "position_debut", "positionFin": "position_fin",
    "borneDebutId": "borne_debut_id", "borneFinId": "borne_fin_id",
    "borne_debut_distance": "borne_debut_distance", "borne_fin_distance": "borne_fin_distance",
    "borne_debut_aval": "borne_debut_aval", "borne_fin_aval": "borne_fin_aval",
    "prDebut": "pr_debut", "prFin": "pr_fin",
}

def _source(source, *, positions=False):
    proof = "external:source-inventory" if positions else "external:source-inventory"
    bindings = []
    for key in SOURCE_NAMES[source]:
        if positions:
            column = POSITION_COLUMNS.get(key)
            if key == "linearId":
                column = "troncon_support_id" if source == "core/PiedDigue" else "troncon_position_id"
            elif key == "systemeRepId":
                column = "systeme_rep_id"
            table = TRACES if column else proof
        else:
            table, column = BERGE_COLUMNS.get(key, (proof, "document"))
        bindings.append({"application": "sirs-v2:application:" + source + "/" + key,
                         "source_key": key, "table": table, "column": column or "document",
                         "proof_path": [key]})
    return {"source": "sirs-v2:class:" + source,
            "target": "da:component:troncon/trace-position" if positions else "da:type:troncon/berge",
            "position": ("SOMMET" if source.endswith("SommetBerge") else "PIED") if positions else None,
            "bindings": bindings}

BASE_PARAMETERS = {
    "qualification": "PARTIAL", "identity": "one_source_uuid_one_target_uuid",
    "merge": "forbidden", "representative": "forbidden", "parent_field_inheritance": "forbidden",
    "business_origin_markers": "forbidden", "source_preservation": "immutable_external_source_inventory_and_authenticated_native_report",
    "geometry": {"conversion": "forbidden", "srid": "preserve_exact_no_inference",
                 "target_status": "A_APPROUVER", "invalid": "retain_invalid_with_diagnostic"},
    "unresolved_references": "preserve_raw_and_diagnose_no_invention",
    "catalogs": "exact_canonical_reference_only_no_label_inference",
    "authors": "explicit_contact_mapping_only",
    "scope": {"native_crud": False, "certification": False},
}
BERGE_BINDING_PARAMETERS = {
    **deepcopy(BASE_PARAMETERS), "operator": "typed_troncons_berges_partial_v1",
    "sources": [_source(source) for source in ("berge/Berge", "berge/TraitBerge")],
    "tables": {"root": ROOT, "provenance": "external:source-inventory"},
    "historical_berge_relation": "technical_document_only_no_business_association",
    "historical_contract": {"id": LEGACY_ID, "parameters_digest": LEGACY_DIGEST,
                            "berge_execution": "retired", "other_sources": "unchanged"},
    "shared_fields": "root_only", "side": "null_no_rive_or_position_conversion",
}
POSITION_BINDING_PARAMETERS = {
    **deepcopy(BASE_PARAMETERS), "operator": "typed_troncons_positions_partial_v1",
    "sources": [_source(source, positions=True) for source in
                ("berge/PiedBerge", "berge/SommetBerge", "core/PiedDigue")],
    "tables": {"trace": TRACES, "provenance": "external:source-inventory"},
    "parent": "exact_Berge_UUID_only_or_existing_Troncon_no_Talus_inference",
    "position": "source_meaning_only_never_calculated_endpoint",
    "unknown_position": "INDETERMINEE_available_no_implicit_conversion",
}
PARAMETERS = {BERGE_BINDING_ID: BERGE_BINDING_PARAMETERS, POSITION_BINDING_ID: POSITION_BINDING_PARAMETERS}


def validate_binding(registry, contract, historical=None):
    errors = []
    cid = contract.get("id") if isinstance(contract, dict) else None
    def fail(code, message, element=None):
        errors.append({"code": code, "element_id": element or cid or BERGE_BINDING_ID,
                       "message": message, "blocking": True})
    expected = PARAMETERS.get(cid)
    if expected is None or contract.get("kind") != KIND or contract.get("version") != "2" or contract.get("status") != "DECIDE":
        fail("INV-VER.B3_BINDING_SHAPE", "An approved B3 physical slice is required.")
        return errors
    if len([c for c in registry.get('contracts', []) if c.get('id') == cid]) != 1:
        fail("INV-VER.B3_BINDING_SHAPE", "The binding identity must be unique.")
    if contract.get("parameters") != expected:
        fail("INV-MAP.B3_BINDING_PARAMETERS", "Exact UUID preservation, field bindings and bounded scope are required.")
    succession = resolve_decision_assertions(registry)
    decision = next((d for d in registry.get("decisions", []) if d["id"] == DECISION), {})
    assertions = succession["assertions"].get(DECISION, [])
    seals = [a for a in assertions if a["subject"] == cid and a["predicate"] == "contract_parameters_digest"]
    if (succession["diagnostics"] or decision.get("status") != "DECIDE" or DECISION not in contract.get("decision_refs", [])
            or len(seals) != 1 or seals[0]["value"] != digest(contract.get("parameters"))
            or {"subject": cid, "predicate": "contract", "value": KIND} not in assertions):
        if cid != BERGE_BINDING_ID or not any(a == {"subject": cid, "predicate": "contract_parameters_digest", "value": digest(contract.get("parameters"))} for a in succession["assertions"].get("decision:MOD1P-B4", [])):
            fail("INV-VER.B3_BINDING_SEAL", "The active B3/B4 decision must seal this exact physical slice.")
    from .typed_troncons import SOURCE_CLASSES, suspended_projection_sources
    try:
        if suspended_projection_sources(registry) != SOURCE_CLASSES:
            raise ValueError("gate")
    except ValueError:
        fail("INV-VER.B3_BINDING_LEGACY_GATE", "B3 does not reactivate suspended legacy projectors.")
    forbidden = {"EXACT_PROJECTED_EQUIVALENCE_ONLY", "SINGLE_HISTORICAL_REPRESENTATIVE"}
    if any(a.get("value") in forbidden for aa in succession['assertions'].values() for a in aa
           if isinstance(a.get("value"), str) and a.get("predicate") == "constraint"):
        fail("INV-DEC.B3_DEDUPLICATION_ACTIVE", "Historical deduplication assertions must be succeeded explicitly.")
    if historical is not None:
        facts = {f['id']: f for f in historical['facts']}
        for source in expected['sources']:
            applications = {f['id']: f for f in facts.values() if f['kind'] == 'application' and f.get('owner') == source['source']}
            if set(applications) != {b['application'] for b in source['bindings']}:
                fail("INV-COV.B3_BINDING_APPLICATIONS", "Every effective historical property needs its exact destination.", source['source'])
            for application, fact in applications.items():
                declaration = facts.get(fact['data'].get('declaration'), {})
                if declaration.get('kind') != fact['data'].get('property_kind') or declaration.get('data', {}).get('type') != fact['data'].get('type'):
                    fail("INV-REF.B3_BINDING_DECLARATION", "Historical declaration and type must remain exact.", application)
    return sorted(errors, key=canonical_bytes)


def _load(cid):
    registry = strict_load(files(__package__).joinpath("registry.json"))
    contract = next((c for c in registry['contracts'] if c['id'] == cid), None)
    errors = validate_binding(registry, contract)
    if errors:
        raise ValueError("INV-VER.B3_BINDING_REJECTED: " + ','.join(e['code'] for e in errors))
    return deepcopy(contract['parameters'])


def load_berge_binding():
    return _load(BERGE_BINDING_ID)


def load_position_binding():
    return _load(POSITION_BINDING_ID)


def validate_binding_event(registry, contract_id, event):
    contract = next((c for c in registry['contracts'] if c['id'] == contract_id), None)
    errors = validate_binding(registry, contract)
    if errors:
        return errors
    def fail(code):
        errors.append({"code": code, "element_id": contract_id, "message": "B3 identity or source preservation witness rejected.", "blocking": True})
    required = {'source_class', 'source_id', 'target_id', 'source_document', 'stored_document', 'claimed_complete'}
    if not isinstance(event, dict) or set(event) != required:
        fail('INV-VER.B3_EVENT_SHAPE')
        return errors
    if event['source_class'] not in {s['source'] for s in contract['parameters']['sources']}:
        fail('INV-MAP.B3_EVENT_SCOPE')
    try:
        if UUID(event['source_id']) != UUID(event['target_id']):
            raise ValueError('merged')
        raw = event['source_document'].get('_id', event['source_document'].get('id'))
        if raw is not None and UUID(raw) != UUID(event['source_id']):
            raise ValueError('source changed')
    except (ValueError, TypeError, AttributeError):
        fail('INV-ID.B3_IDENTITY')
    if not isinstance(event['source_document'], dict) or canonical_bytes(event['source_document']) != canonical_bytes(event['stored_document']):
        fail('INV-MAP.B3_DOCUMENT_CHANGED')
    if event['claimed_complete'] is not False:
        fail('INV-COV.B3_CERTIFICATION')
    return sorted(errors, key=canonical_bytes)
