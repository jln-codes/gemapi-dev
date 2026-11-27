"""Pure event checks for approved master-model obligations.

These functions evaluate supplied evidence; they neither mutate application data
nor certify that a production writer invokes them. Missing evidence fails closed.
Events are small, explicit witness records, not a second persistence model.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
import re

from jsonschema import Draft202012Validator

from .common import canonical_bytes, strict_load


APPROVED = {"DECIDE", "CONSEQUENCE_NECESSAIRE"}
EVENT_FIELDS = {
    "identity": {"before", "after", "reused_code", "equivalence_decision"},
    "scope_partition": {"inventories", "certifications", "cross_relations", "global_claim"},
    "preservation": {"sources", "outputs", "diagnosed_references"},
    "partial_import": {"source_ids", "imported_ids", "omitted_ids", "accepted_omissions", "claimed_complete", "deferred_outputs"},
    "prescription_revision": {"previous", "closed_previous", "replacement", "previous_links", "retained_links", "new_links", "manually_selected_plans", "context_scopes", "generated_recurrence"},
    "execution": {"prestations", "nature_values", "generated_events", "quantity_channels"},
    "temporal_relation": {"before", "after"},
    "cross_scope_relation": {"business_database", "contact_database", "relation_database", "inverse_cardinality"},
    "file_attachment": {"filename", "logical_media_key", "historical_path"},
    "source_disposition": {"source_ids", "diagnosed_ids", "outputs", "child_rules"},
    "geometry_import": {"source_geometry", "output_geometry", "expected", "valid", "diagnostics", "retained"},
    "target_exclusion": {"outputs"},
}


def _record_ids(rows):
    return isinstance(rows, list) and all(isinstance(r, dict) and isinstance(r.get("id"), str) and r["id"] for r in rows)


def _strings(values):
    return isinstance(values, list) and all(isinstance(v, str) for v in values)


def _date(value):
    if not isinstance(value, str):
        raise ValueError("A date witness must be an ISO date or datetime")
    return datetime.fromisoformat(value) if "T" in value else date.fromisoformat(value)


def validate_contract_event(registry, contract_id, event):
    """Return stable diagnostic records for an explicit prospective event.

    An empty list means only that the supplied event satisfies this contract.
    It does not approve a canonical key, a missing mapping, or a SQL projection.
    Unknown contracts, unsupported events and missing evidence are errors.
    """
    contracts = {c["id"]: c for c in registry.get("contracts", [])}
    contract = contracts.get(contract_id)
    errors = []

    def fail(code, message, element=None):
        errors.append({"code": code, "element_id": element or contract_id,
                       "provenance": sorted((contract or {}).get("provenance", []),
                                            key=lambda p: (p["source"], p["locator"])),
                       "message": message})

    def require(condition, message):
        if not condition:
            fail("INV-VER.CONTRACT_EVENT_SHAPE", message)
        return condition

    if contract is None:
        fail("INV-REF.CONTRACT_MISSING", "Unknown formal contract.")
        return errors
    schema = strict_load(Path(__file__).with_name("registry.schema.json"))
    contract_schema = {"$defs": schema["$defs"], "$ref": "#/$defs/formal_contract"}
    if not Draft202012Validator(contract_schema).is_valid(contract):
        fail("INV-VER.CONTRACT_SHAPE", "Contract definition violates the strict master-registry schema.")
        return errors
    if contract["status"] not in APPROVED:
        fail("INV-COV.CONTRACT_PENDING", "The contract is not approved.")
        return errors
    kind, parameters = contract["kind"], contract["parameters"]
    if kind == "typed_troncons_transition":
        from .typed_troncons import validate_transition_event
        return validate_transition_event(registry, contract_id, event)
    if kind == "typed_troncon_physical_slice":
        from .troncons_ah import BINDING_ID as B5_BINDING_ID, validate_b5_binding_event
        if contract_id == B5_BINDING_ID:
            return validate_b5_binding_event(registry, event)
        from .troncons_b4 import BINDING_ID, validate_b4_binding_event
        if contract_id == BINDING_ID:
            return validate_b4_binding_event(registry, event)
        from .troncons_berges import PARAMETERS, validate_binding_event
        if contract_id in PARAMETERS:
            return validate_binding_event(registry, contract_id, event)
        from .typed_troncons import TALUS_BINDING_ID, validate_crete_binding_event, validate_talus_binding_event
        return (validate_talus_binding_event if contract_id == TALUS_BINDING_ID else
                validate_crete_binding_event)(registry, contract_id, event)
    if kind == "core_berge_lit_projection":
        from .occurrence_projection import validate_projection_event
        return validate_projection_event(registry, contract_id, event)
    if not require(kind in EVENT_FIELDS and isinstance(event, dict) and set(event) == EVENT_FIELDS[kind],
                   "Event fields must match the closed contract witness exactly."):
        return errors

    if kind == "identity":
        before, after = event["before"], event["after"]
        if not require(all(isinstance(r, dict) and set(r) == {"id", "kind"} and
                           all(isinstance(v, str) for v in r.values()) for r in (before, after)) and
                       type(event["reused_code"]) is bool, "Identity requires typed before/after records and an explicit reuse flag."):
            return errors
        for value in (before, after):
            if (not Draft202012Validator(schema["$defs"]["target"]["properties"]["id"]).is_valid(value["id"])
                    or not value["id"].startswith("da:" + value["kind"] + ":")):
                fail("INV-ID.EVENT_KIND", "Canonical identity must carry its exact kind.", value["id"])
        if before != after:
            fail("INV-ID.IMMUTABLE", "An existing canonical identity and its kind cannot be changed.")
        if event["reused_code"]:
            decision = next((d for d in registry["decisions"] if d["id"] == event["equivalence_decision"]), None)
            # The current assertion vocabulary cannot prove semantic equivalence
            # for reused codes; approval text by itself is not such a proof.
            fail("INV-ID.REUSE_EQUIVALENCE_UNQUALIFIED", "Code reuse requires an explicitly qualified equivalence contract." if decision else
                 "Code reuse has no identified equivalence decision.")

    elif kind == "scope_partition":
        inventories, certifications = event["inventories"], event["certifications"]
        if not require(isinstance(inventories, dict) and all(_strings(v) for v in inventories.values()) and
                       isinstance(certifications, dict) and isinstance(event["cross_relations"], list) and
                       type(event["global_claim"]) is bool, "Scope evidence requires inventories, certifications and explicit relations."):
            return errors
        expected = set(parameters["scopes"])
        if set(inventories) != expected or set(certifications) != expected:
            fail("INV-COV.SCOPE_PARTITION", "Each canonical scope needs its independent inventory and qualification.")
        ids = [i for values in inventories.values() for i in values]
        if len(ids) != len(set(ids)):
            fail("INV-ID.SCOPE_COLLISION", "An element cannot occupy several canonical scope inventories.")
        declared = {s for c in contracts.values() if c["kind"] == "cross_scope_relation" and c["status"] in APPROVED for s in c["subjects"]}
        for relation in event["cross_relations"]:
            if (not isinstance(relation, dict) or set(relation) != {"id", "source_scope", "target_scope", "verified"}
                    or not all(isinstance(relation[k], str) for k in ("id", "source_scope", "target_scope"))
                    or type(relation["verified"]) is not bool):
                fail("INV-VER.CONTRACT_EVENT_SHAPE", "Transversal witness requires identity, two scopes and verification.")
                continue
            if relation["id"] not in declared or relation["source_scope"] not in expected or relation["target_scope"] not in expected:
                fail("INV-REF.CROSS_SCOPE_UNDECLARED", "Transversal relation lacks a declared contract.", relation["id"])
            if event["global_claim"] and relation["verified"] is not True:
                fail("INV-COV.CROSS_SCOPE_UNVERIFIED", "Global qualification requires verified transversal relations.", relation["id"])
        if event["global_claim"] and declared != {r["id"] for r in event["cross_relations"] if isinstance(r, dict) and isinstance(r.get("id"), str)}:
            fail("INV-COV.CROSS_SCOPE_UNVERIFIED", "Global qualification must include every declared transversal relation.")
        if event["global_claim"] and any(certifications.get(s) != "CERTIFIED" for s in expected):
            fail("INV-COV.GLOBAL_CERTIFICATION_PREMATURE", "Global qualification requires every canonical scope.")

    elif kind == "preservation":
        sources, outputs = event["sources"], event["outputs"]
        if not require(_record_ids(sources) and _record_ids(outputs) and _strings(event["diagnosed_references"]),
                       "Preservation requires identified source/output records and named diagnosed references."):
            return errors
        source_ids, output_ids = [s["id"] for s in sources], [o["id"] for o in outputs]
        if len(source_ids) != len(set(source_ids)) or len(output_ids) != len(set(output_ids)):
            fail("INV-ID.OCCURRENCE_DUPLICATE", "Occurrences must retain individual unique identities.")
        if set(source_ids) != set(output_ids):
            fail("INV-MAP.OCCURRENCE_LOSS_OR_MERGE", "Projection must preserve every individual source identity.")
        indexed = {o["id"]: o for o in outputs}
        for source in sources:
            output = indexed.get(source["id"])
            if output is None:
                continue
            for field, value in source.items():
                if field not in output or output[field] != value:
                    code = "INV-GEO.SILENT_DROP" if field == "geometry" else "INV-MAP.PRESERVATION_CONFLICT"
                    fail(code, f"Historical {field} was lost or changed without a transformation contract.", source["id"])
            if set(output) - set(source):
                fail("INV-MAP.INVENTED_SOURCE_DATA", "A preservation event cannot add facts absent from its historical witness.", source["id"])

    elif kind == "partial_import":
        fields = ("source_ids", "imported_ids", "omitted_ids", "accepted_omissions", "deferred_outputs")
        if not require(all(_strings(event[k]) for k in fields) and type(event["claimed_complete"]) is bool,
                       "Partial import requires explicit identified sets and completeness claim."):
            return errors
        source, imported, omitted, accepted = (set(event[k]) for k in fields[:4])
        if imported & omitted or imported | omitted != source:
            fail("INV-COV.PARTIAL_IMPORT_COVERAGE", "Every source occurrence needs exactly one explicit import or omission outcome.")
        if omitted != accepted:
            fail("INV-MAP.OMISSION_NOT_ACCEPTED", "A partial import requires explicit acceptance of the named omissions.")
        if event["claimed_complete"] and omitted:
            fail("INV-COV.PARTIAL_IMPORT_COMPLETE_CLAIM", "An import with omitted objects is not complete.")
        if event["deferred_outputs"]:
            fail("INV-MAP.DEFERRED_TARGET_CREATED", "Deferred objects have no implicit target placeholder.")

    elif kind == "prescription_revision":
        previous, closed, replacement = (event[k] for k in ("previous", "closed_previous", "replacement"))
        if not require(all(isinstance(r, dict) and {"id", "parameters", "date_fin"} <= set(r) and isinstance(r["id"], str)
                           and isinstance(r["parameters"], dict) for r in (previous, closed, replacement)) and
                       all(isinstance(event[k], list) for k in ("previous_links", "retained_links", "new_links", "context_scopes", "generated_recurrence")) and
                       _strings(event["manually_selected_plans"]),
                       "Prescription revision requires both identities, parameters, closure, preserved links and contextual scopes."):
            return errors
        changed = previous["parameters"] != replacement["parameters"]
        if changed and previous["id"] == replacement["id"]:
            fail("INV-ID.PRESCRIPTION_NEW_IDENTITY", "A real parameter change requires a new Prescription identity.")
        expected_old = {**previous, "date_fin": closed["date_fin"]}
        if closed != expected_old or changed and closed["date_fin"] is None:
            fail("INV-MAP.PRESCRIPTION_HISTORY_LOSS", "Keep the previous Prescription and close it by date_fin after a parameter change.")
        if closed["date_fin"] is not None:
            try:
                _date(closed["date_fin"])
            except (ValueError, TypeError):
                fail("INV-VER.CONTRACT_EVENT_SHAPE", "Prescription closure must be an actual ISO date witness.")
        if sorted(map(canonical_bytes, event["previous_links"])) != sorted(map(canonical_bytes, event["retained_links"])):
            fail("INV-MAP.PRESCRIPTION_LINK_HISTORY_LOSS", "Old Plan–Prescription associations must be retained.")
        new_plans = []
        for link in event["new_links"]:
            if not isinstance(link, dict) or set(link) != {"plan", "prescription", "mode"} or not all(isinstance(v, str) for v in link.values()):
                fail("INV-VER.CONTRACT_EVENT_SHAPE", "New association needs explicit Plan, Prescription and manual attachment mode.")
                continue
            new_plans.append(link["plan"])
            if link["mode"] != "manual" or link["prescription"] != replacement["id"]:
                fail("INV-MAP.PRESCRIPTION_AUTOMATIC_REASSOCIATION", "Attach only the new Prescription manually to selected Plans.")
        if set(new_plans) != set(event["manually_selected_plans"]):
            fail("INV-MAP.PRESCRIPTION_AUTOMATIC_REASSOCIATION", "New associations differ from the explicitly selected Plans.")
        for context in event["context_scopes"]:
            if (not isinstance(context, dict) or set(context) != {"plan", "prescription", "vegetation", "geographic_scope"}
                    or not _strings(context.get("vegetation")) or not all(isinstance(context[k], str) and context[k] for k in ("plan", "prescription"))):
                fail("INV-MAP.PRESCRIPTION_GLOBAL_SCOPE", "Vegetation and geography require an explicit Plan–Prescription context.")
        if event["generated_recurrence"]:
            fail("INV-MAP.PRESCRIPTION_AUTOMATIC_RECURRENCE", "Recurrence does not generate Prescription events or repeated Prescriptions.")

    elif kind == "execution":
        if not require(_record_ids(event["prestations"]) and _strings(event["nature_values"]) and
                       isinstance(event["generated_events"], list) and isinstance(event["quantity_channels"], dict),
                       "Execution evidence needs identified Prestations, a strict nature catalogue and quantity channels."):
            return errors
        for prestation in event["prestations"]:
            if set(prestation) != {"id", "nature", "prescriptions", "realized_quantity"} or not _strings(prestation.get("prescriptions")):
                fail("INV-VER.CONTRACT_EVENT_SHAPE", "Prestation witness requires nature, explicit optional references and realized quantity.")
                continue
            if prestation["nature"] not in event["nature_values"]:
                fail("INV-REF.PRESTATION_NATURE", "Prestation nature is outside the strict supplied reference catalogue.", prestation["id"])
            if prestation["prescriptions"]:
                fail("INV-CARD.CONTRIBUTIONS_UNQUALIFIED", "Prescription contributions, including a single one, require the still-unapproved allocation contract and nature compatibility.", prestation["id"])
        channels = event["quantity_channels"]
        if set(channels) != {"prescribed", "realized"} or channels["prescribed"] == channels["realized"]:
            fail("INV-MAP.QUANTITY_CONFLATION", "Prescribed and realized quantities must have distinct channels.")
        if event["generated_events"]:
            fail("INV-MAP.EXECUTION_AUTOMATIC_EVENTS", "Actual Prestations prove recurrence without generating artificial events.")

    elif kind == "temporal_relation":
        before, after = event["before"], event["after"]
        if not require(_record_ids(before) and _record_ids(after), "Temporal relations need individually identified occurrences."):
            return errors
        after_ids = [r["id"] for r in after]
        if len(after_ids) != len(set(after_ids)) or not {r["id"] for r in before} <= set(after_ids):
            fail("INV-ID.TEMPORAL_OCCURRENCE_LOSS", "Temporal association occurrences cannot be merged or deduplicated.")
        for row in after:
            if not {"date_debut", "date_fin"} <= set(row):
                fail("INV-VER.CONTRACT_EVENT_SHAPE", "Date witnesses must explicitly represent absent values as null.", row["id"])
                continue
            try:
                start = _date(row["date_debut"]) if row["date_debut"] is not None else None
                end = _date(row["date_fin"]) if row["date_fin"] is not None else None
                if start is not None and end is not None and end < start:
                    fail("INV-CARD.TEMPORAL_ORDER", "End date cannot precede start date.", row["id"])
            except (TypeError, ValueError):
                fail("INV-VER.CONTRACT_EVENT_SHAPE", "Temporal endpoints need comparable ISO date witnesses.", row["id"])
        # No overlap prohibition is inferred. It is either allowed or remains
        # a named limitation in the registry, independently of this order check.

    elif kind == "cross_scope_relation":
        contexts = [event[k] for k in ("business_database", "contact_database", "relation_database")]
        if not all(isinstance(v, str) and v for v in contexts) or len(set(contexts)) != 1:
            fail("INV-REF.BUSINESS_DATABASE_CONTEXT", "Utilisateur–Contact association and Contact must carry the same explicit business database.")
        if event["inverse_cardinality"] is not None:
            fail("INV-CARD.INVERSE_UNQUALIFIED", "No inverse Utilisateur–Contact bound has been approved.")

    elif kind == "file_attachment":
        if not require(all(isinstance(event[k], str) and event[k] for k in event), "Attachment requires filename, logical media key and historical path witness."):
            return errors
        if event["logical_media_key"] == event["historical_path"] or re.match(r"^(?:/|[A-Za-z]:[\\/]|file:)", event["logical_media_key"]):
            fail("INV-REF.HISTORICAL_PATH_AS_MEDIA_KEY", "A historical filesystem path is not a logical media identity.")
        # Extension is deliberately unrestricted, including no extension.

    elif kind == "source_disposition":
        if not require(_strings(event["source_ids"]) and _strings(event["diagnosed_ids"]) and
                       isinstance(event["outputs"], list) and isinstance(event["child_rules"], dict) and
                       all(isinstance(k, str) and isinstance(v, str) for k, v in event["child_rules"].items()),
                       "Source disposition requires named occurrences, diagnostics and explicit child rules."):
            return errors
        if set(event["source_ids"]) != set(event["diagnosed_ids"]):
            fail("INV-MAP.DISPOSITION_UNDIAGNOSED", "Every deferred or removed source occurrence needs a diagnostic.")
        if event["outputs"]:
            fail("INV-MAP.SOURCE_DISPOSITION_CONFLICT", "This historical disposition does not authorize a target for the container.")
        rules = {r["id"]: r for r in registry.get("rules", [])}
        for child, rid in event["child_rules"].items():
            rule = rules.get(rid)
            if rule is None or rule["status"] not in APPROVED or child not in rule["sources"]:
                fail("INV-REF.CHILD_RULE_UNQUALIFIED", "A child projection needs an identified approved rule.", child)

    elif kind == "geometry_import":
        if not require(type(event["expected"]) is bool and type(event["valid"]) is bool and
                       type(event["retained"]) is bool and _strings(event["diagnostics"]),
                       "Geometry event requires explicit expectations, validity, retention and diagnostics."):
            return errors
        missing = event["expected"] and event["source_geometry"] is None
        unexpected = not event["expected"] and event["source_geometry"] is not None
        if not event["retained"] or event["source_geometry"] != event["output_geometry"]:
            fail("INV-GEO.SILENT_DROP", "Retain source occurrence and available geometry; no invention or loss is authorized.")
        for anomaly, code in ((missing, parameters["missing_diagnostic"]), (unexpected, parameters["unexpected_diagnostic"])):
            if anomaly and (event["valid"] or code not in event["diagnostics"]):
                fail("INV-GEO.ANOMALY_UNDIAGNOSED", "Missing or unexpected geometry must remain invalid with its named diagnostic.")

    elif kind == "target_exclusion":
        if not require(_record_ids(event["outputs"]), "Target exclusion requires identified prospective outputs."):
            return errors
        labels = {v.casefold() for v in parameters["labels"]}
        for output in event["outputs"]:
            if output["id"] in parameters["target_ids"] or str(output.get("label", "")).casefold() in labels:
                fail("INV-COV.TARGET_EXCLUDED", "Output is explicitly excluded from the target universe.", output["id"])
    return sorted(errors, key=lambda e: (e["code"], e["element_id"], e["message"]))


def validate_relation_event(registry, relation_id, event):
    """Check a closed relation population, without inferring absent instances.

    ``populations`` names every participant considered on each role; ``links``
    holds individually identified association occurrences. Counts concern distinct
    partners, so a historical relationship can retain several temporal occurrences.
    Unknown bounds remain blocking even if a particular sample satisfies them.
    """
    targets = {t["id"]: t for t in registry.get("targets", [])}
    relation = targets.get(relation_id)
    errors = []

    def fail(code, message, element=None):
        errors.append({"code": code, "element_id": element or relation_id,
                       "provenance": (relation or {}).get("provenance", []), "message": message})

    if relation is None or relation["kind"] != "relation":
        fail("INV-REF.RELATION_MISSING", "Expected a declared canonical relation.")
        return errors
    schema = strict_load(Path(__file__).with_name("registry.schema.json"))
    target_schema = {"$defs": schema["$defs"], "$ref": "#/$defs/target"}
    if not Draft202012Validator(target_schema).is_valid(relation):
        fail("INV-VER.RELATION_SHAPE", "Relation definition violates the strict master-registry schema.")
        return errors
    if relation["status"] not in APPROVED:
        fail("INV-COV.RELATION_PENDING", "A pending relation cannot validate occurrence evidence.")
        return errors
    if not isinstance(event, dict) or set(event) != {"populations", "links", "business_database"} or not isinstance(event["populations"], dict) or not _record_ids(event["links"]):
        fail("INV-VER.RELATION_EVENT_SHAPE", "A relation witness needs explicit role populations, links and database context.")
        return errors
    endpoints = relation.get("endpoints", [])
    roles = {e["role"] for e in endpoints}
    if len(endpoints) != 2 or set(event["populations"]) != roles:
        fail("INV-VER.RELATION_EVENT_SHAPE", "This evaluator needs exactly the two declared endpoint populations.")
        return errors
    populations = {}
    for endpoint in endpoints:
        role = endpoint["role"]
        rows = event["populations"][role]
        if not _record_ids(rows) or any(set(row) != {"id", "type", "business_database"} or not isinstance(row["type"], str)
                                      or not (row["business_database"] is None or isinstance(row["business_database"], str)) for row in rows):
            fail("INV-VER.RELATION_EVENT_SHAPE", "Participants need identity, canonical type and database context.")
            return errors
        populations[role] = {r["id"]: r for r in rows}
        if len(populations[role]) != len(rows):
            fail("INV-ID.OCCURRENCE_DUPLICATE", "A role population contains duplicate identities.")
        admissible = set(endpoint.get("allowed_targets", [])) | ({endpoint["target"]} if endpoint["target"] else set())
        if not admissible:
            fail("INV-REF.ENDPOINT_TARGET_UNQUALIFIED", "The role's participant universe is not approved.")
        for row in rows:
            ancestors, current = set(), row["type"]
            while current in targets and current not in ancestors:
                ancestors.add(current)
                # Composition is ownership, never substitutable inheritance.
                if targets[current]["kind"] not in {"type", "subtype"}:
                    break
                current = targets[current].get("parent")
            if not ancestors & admissible:
                fail("INV-REF.PARTICIPANT_TYPE", "Participant type is incompatible with its declared role.", row["id"])
            if relation.get("context") == "business_database":
                scope = targets.get(row["type"], {}).get("scope")
                if not event["business_database"] or (scope == "scope:patrimoine" and row["business_database"] != event["business_database"]):
                    fail("INV-REF.BUSINESS_DATABASE_CONTEXT", "Patrimonial participant must belong to the explicit relation database.", row["id"])
    identifiers = [r["id"] for r in event["links"]]
    if len(set(identifiers)) != len(identifiers):
        fail("INV-ID.ASSOCIATION_DUPLICATE", "Association occurrences require unique identities.")
    links = []
    for link in event["links"]:
        if set(link) != {"id", "roles", "basis"} or not isinstance(link["basis"], str) or not isinstance(link["roles"], dict) or set(link["roles"]) != roles or not all(isinstance(v, str) for v in link["roles"].values()):
            fail("INV-VER.RELATION_EVENT_SHAPE", "Each link must identify exactly one participant per role and its non-inferred basis.", link["id"])
            continue
        if any(link["roles"][role] not in populations[role] for role in roles):
            fail("INV-REF.PARTICIPANT_MISSING", "Link references a participant absent from its explicit population.", link["id"])
            continue
        if "NO_GEOMETRIC_ASSOCIATION_INFERENCE" in relation["constraints"] and link["basis"] not in {"explicit_source", "manual"}:
            fail("INV-MAP.SPATIAL_RELATION_INFERENCE", "A Vegetation relation requires explicit source evidence or manual attachment.", link["id"])
        links.append(link)
    for endpoint in endpoints:
        role = endpoint["role"]
        inverse = next(r for r in roles if r != role)
        bounds = endpoint["cardinality"]
        if bounds is None or bounds["min"] is None:
            fail("INV-CARD.ENDPOINT_UNQUALIFIED", "An endpoint cardinality is not fully approved.")
            continue
        for owner in populations[inverse]:
            partners = {link["roles"][role] for link in links if link["roles"][inverse] == owner}
            if len(partners) < bounds["min"] or bounds["max"] != "*" and len(partners) > bounds["max"]:
                fail("INV-CARD.RELATION_BOUND", f"Role {role} violates its declared partner cardinality.", owner)
    return sorted(errors, key=lambda e: (e["code"], e["element_id"], e["message"]))
