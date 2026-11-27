"""Recovered decisions: scoped obligations and bounded executable witnesses.

Witnesses do not certify their production by an importer or an application.
Those integration gaps remain named limitations in the one registry.
"""
from __future__ import annotations

from uuid import UUID, uuid5

from .common import canonical_bytes
from .contracts import validate_contract_event
from .relation_checks import normalize_reference_occurrences, _source_assignable


CONTROLS = {
    "source_relations", "uuid_preservation", "synthetic_identity", "reference_promotion",
    "historical_prestation", "explicit_links", "observation_subject", "autonomous_import",
    "invalid_contract_gate", "author_import", "conditional_undefined", "native_prestation",
    "geometry_native", "ordered_values", "catalog_uniqueness", "scoped_override",
    "reference_cardinalities", "source_storage_absent", "sql_business_endpoints",
    "acquired_geometry", "disorder_multiplicity", "decision_assertions", "contract_event",
}


def validate_obligation_event(registry, identifier, event, *, historical=None):
    """Check an explicit witness. Unknown, inactive and incomplete inputs fail."""
    row = next((r for r in registry.get("obligations", []) if r["id"] == identifier), None)
    errors = []

    def fail(code, message):
        errors.append({"code": "INV-OBL." + code, "element_id": identifier,
                       "message": message, "provenance": (row or {}).get("provenance", [])})

    if row is None or row["status"] not in {"APPLICABLE", "PARTIALLY_SUPERSEDED"}:
        fail("INACTIVE", "This candidate is absent, replaced, proposed or undetermined.")
        return errors
    if not isinstance(event, dict) or "control" not in event or event["control"] not in row["controls"]:
        fail("EVENT_SHAPE", "A witness must name a control declared by this obligation.")
        return errors
    control, policy = event["control"], row["parameters"]
    boolean_fields = {
        "autonomous_import": ("cwd_dependent", "fallback"),
        "invalid_contract_gate": ("valid", "prepared", "written", "business_connected", "reserved"),
        "conditional_undefined": ("present", "admissible"),
        "geometry_native": ("accepted",),
        "scoped_override": ("condition_matches", "applied"),
    }
    if any(type(event.get(field)) is not bool for field in boolean_fields.get(control, ())):
        fail("EVENT_SHAPE", "Control flags require explicit booleans, without truth-value coercion.")
        return errors
    try:
        if control == "uuid_preservation":
            sources, outputs = event["sources"], event["outputs"]
            before, after = [UUID(s).int for s in sources], [UUID(s).int for s in outputs]
            if len(set(before)) != len(before) or sorted(before) != sorted(after):
                fail("UUID_CHANGED_OR_MERGED", "Preserve every historical UUID's 128 bits without merging occurrences.")
        elif control == "synthetic_identity":
            namespace = UUID(event["namespace"])
            expected = [str(uuid5(namespace, key)) for key in event["source_keys"]]
            if len(set(event["source_keys"])) != len(expected) or expected != event["outputs"]:
                fail("SYNTHETIC_IDENTITY_UNSTABLE", "Declared namespace and distinct source keys must reproduce the UUIDs.")
        elif control == "source_relations":
            if historical is None:
                raise ValueError("Historical declarations required")
            result = normalize_reference_occurrences(historical, event["occurrences"])
            if canonical_bytes(result) != canonical_bytes(event["expected"]):
                fail("RELATION_WITNESS_MISMATCH", "One logical link must retain the separate declarations, positions and unresolved states.")
            errors.extend(result["diagnostics"])
        elif control == "reference_promotion":
            if historical is None:
                raise ValueError("Pinned reference declarations are required")
            facts = {f["id"]: f for f in historical["facts"]}
            declarations = event["declarations"]
            projected = {r["id"]: r["class"] for r in event["projected"]}
            if len(projected) != len(event["projected"]):
                fail("AMBIGUOUS_IDENTITY", "Projected target identities must be unique.")
            if len({d["id"] for d in declarations}) != len(declarations):
                fail("AMBIGUOUS_DECLARATION", "Each source declaration has its own identity.")
            eligible = set()
            for d in declarations:
                expected_type = facts.get(d["reference"], {}).get("data", {}).get("type")
                if (expected_type is not None and d["resolution"] == "RESOLVED" and
                        _source_assignable(projected.get(d["target"]), expected_type, facts)
                        and (d["reference"] in policy["single_sided_references"] or
                             d["reference"] in policy["concordant_references"] and d["concordance"] == "BOTH")):
                    eligible.add(d["id"])
            if set(event["published"]) != eligible or len(event["published"]) != len(set(event["published"])):
                fail("UNPROVEN_REFERENCE_PROMOTION", "Publication requires exact projected identity/type and the field's explicit concordance policy.")
            if set(event["diagnosed"]) != {d["id"] for d in declarations} - eligible:
                fail("UNRESOLVED_REFERENCE_UNDIAGNOSED", "Every non-promoted declaration requires a diagnostic.")
        elif control == "historical_prestation":
            if event["source_values"] != event["preserved_values"] or event["inferred_status"] is not None:
                fail("HISTORICAL_PRESTATION_RECLASSIFIED", "Dates, cost and realisationInterne are preserved without inferring execution status.")
        elif control == "explicit_links":
            if sorted(map(canonical_bytes, event["declared"])) != sorted(map(canonical_bytes, event["published"])):
                fail("INFERRED_OR_LOST_LINK", "Publish only explicit qualified links, without transitive or geometric inference.")
        elif control == "observation_subject":
            if event["before"] != event["after"]:
                fail("OBSERVATION_REPARENTED", "A Prestation link cannot change the Observation's subject.")
        elif control == "autonomous_import":
            if set(event["contracts"]) != {"c1", "c2", "c3"} or event["private_inputs"] or event["cwd_dependent"] or event["fallback"]:
                fail("IMPORT_NOT_AUTONOMOUS", "The distributed importer uses embedded contracts without private inputs, cwd dependence or fallback.")
        elif control == "invalid_contract_gate":
            if not event["valid"] and (event["prepared"] or event["written"] or event["business_connected"] or event["reserved"]):
                fail("INVALID_CONTRACT_USED", "Invalid contracts stop preparation, writes and the concerned business connection/reservation.")
        elif control == "author_import":
            mapping = {UUID(k): UUID(v) for k, v in event["mapping"].items()}
            if len(mapping) != len(event["mapping"]):
                raise ValueError("Duplicate source UUID in author mapping")
            contacts = {UUID(v) for v in event["contacts"]}
            for field in ("author", "lastUpdateAuthor"):
                source = event["source"][field]
                try:
                    source_id = UUID(source) if source else None
                except (ValueError, TypeError, AttributeError):
                    source_id = None
                expected = mapping.get(source_id)
                expected = expected if expected in contacts else None
                actual = event["output"][field]
                if (UUID(actual) if actual is not None else None) != expected:
                    fail("AUTHOR_NOT_EXPLICIT", "Author attribution requires an explicit user UUID to existing Contact mapping in this database.")
                if source is not None and expected is None and field not in event["diagnosed"]:
                    fail("AUTHOR_UNDIAGNOSED", "An unresolved historical author must remain NULL with a diagnostic.")
        elif control == "conditional_undefined":
            if event["operation"] not in {"create", "import", "patch"}:
                raise ValueError("Undefined policy is scoped to create/import/patch")
            source = event["source_value"]
            # Omitted PATCH differs from a supplied null. A broken reference is
            # never eligible; the caller must provide the qualified exact domain.
            if event["operation"] == "patch" and not event["present"]:
                expected = event["previous"]
            elif source is None and event["admissible"] and event["undefined_id"] in event["catalog_ids"]:
                expected = event["undefined_id"]
            else:
                expected = source
            if event["output"] != expected or event["source_preserved"] != source:
                fail("UNDEFINED_OUTSIDE_CONTRACT", "Only an admissible absent/null value uses the exact domain reference; PATCH omission preserves the prior value.")
        elif control == "native_prestation":
            from datetime import date
            if event["origin"] != "native" or not isinstance(event["date_fin"], str):
                raise ValueError("Native realization requires an explicit end date")
            date.fromisoformat(event["date_fin"])
        elif control == "geometry_native":
            shape = event["shape"]
            allowed = (shape is None and not policy["geometry_required"]) or shape in policy["geometry_shapes"]
            if event["accepted"] != allowed:
                fail("NATIVE_GEOMETRY_CONTRACT", "Native acceptance must follow only the acquired shape and presence contract.")
        elif control == "ordered_values":
            if event["source"] != event["output"]:
                fail("BUSINESS_ORDER_LOST", "Preserve values and their business order independently of CouchDB document positions.")
        elif control == "catalog_uniqueness":
            records = event["values"]
            keys = [(r["domain"], r["id"]) for r in records]
            if len(keys) != len(set(keys)) or event["first_initialization"] != event["second_initialization"]:
                fail("CATALOG_COLLISION", "Catalogue identities must be unique and initialization repeatable.")
        elif control == "scoped_override":
            expected = event["dataset"] == event["qualified_dataset"] and event["condition_matches"]
            if event["applied"] != expected:
                fail("OVERRIDE_OUTSIDE_SCOPE", "A corpus exception applies only within its explicit dataset and condition.")
        elif control == "disorder_multiplicity":
            parents = event["parents_by_disorder"]
            maximum = policy["maximum"]
            if maximum != "*" and any(len(set(v)) > maximum for v in parents.values()):
                fail("DISORDER_MULTIPLICITY", "Principal AH is at most one; this rule does not invent a minimum.")
            if event["declared_links"] != event["preserved_links"]:
                fail("DISORDER_LINK_LOSS", "Preserve every explicit N:N link.")
        elif control == "contract_event":
            if event["contract"] not in row["depends_on"]:
                raise ValueError("Contract outside this obligation")
            errors.extend(validate_contract_event(registry, event["contract"], event["witness"]))
        else:
            fail("MODEL_CONTROL_ONLY", "This control is evaluated by validate_model, not by a runtime witness.")
    except (KeyError, ValueError, TypeError, AttributeError) as error:
        fail("EVENT_SHAPE", "Missing or invalid evidence: " + str(error))
    return errors


def validate_obligations(model, emit, ref, evidence):
    registry = model["registry"]
    entries = {r["id"]: r for r in registry.get("obligations", [])}
    decisions = {d["id"]: d for d in registry["decisions"]}
    limits = {l["id"]: l for l in registry.get("limitations", [])}
    sources = {s["id"] for s in model["manifest"]["sources"]}
    for row in entries.values():
        profiles = tuple(row["profiles"])
        evidence(row, profiles)
        for dependency in row["depends_on"] + row["superseded_by"]:
            if dependency not in sources:
                ref(dependency, row["id"], profiles=profiles)
        active = row["status"] in {"APPLICABLE", "PARTIALLY_SUPERSEDED"}
        if row["audit_id"] != "MOD-1G/" + row["id"].split("/")[-1].upper():
            emit("INV-OBL", "AUDIT_IDENTITY_MISMATCH", row["id"], "Stable obligation and recovered audit identity disagree.", ("target-structure", "historical-mapping"))
            continue
        original = row["audit_id"].split("/")[-1][0]
        if original in "XPI" and row["status"] != {"X": "SUPERSEDED", "P": "PROPOSED", "I": "UNDETERMINED"}[original]:
            emit("INV-OBL", "UNAPPROVED_ACTIVATION", row["id"], "A recovered replaced/proposed/undetermined entry cannot be promoted by this integration.", ("target-structure", "historical-mapping"))
            continue
        if not active:
            if row["controls"] or row["limitation_refs"]:
                emit("INV-OBL", "INACTIVE_EXECUTION", row["id"], "An inactive candidate cannot carry active controls or certification blockers.", profiles)
            if row["status"] == "SUPERSEDED" and not row["superseded_by"]:
                emit("INV-OBL", "SUCCESSOR_MISSING", row["id"], "A replaced rule requires explicit successors.", profiles)
            continue
        if not row["controls"] and not row["limitation_refs"]:
            emit("INV-OBL", "UNFORMALIZED", row["id"], "An applicable obligation requires an executable control or formal limitation.", profiles)
        for limitation in row["limitation_refs"]:
            if limitation not in limits:
                emit("INV-OBL", "LIMITATION_MISSING", row["id"], "Unknown limitation " + limitation, profiles)
            elif not set(limits[limitation]["profiles"]) <= set(row["profiles"]):
                emit("INV-OBL", "PROFILE_LEAK", row["id"], "The limitation must not widen the obligation's certification scope.", profiles)
        for dep in row["depends_on"]:
            if dep in entries and entries[dep]["status"] in {"SUPERSEDED", "PROPOSED", "UNDETERMINED"}:
                emit("INV-OBL", "UNAPPROVED_DEPENDENCY", row["id"], "An applicable rule cannot depend on an inactive proposal.", profiles)
        if "decision_assertions" in row["controls"]:
            for did in row["decision_refs"]:
                decision = decisions.get(did, {})
                if decision.get("status") not in {"DECIDE", "CONSEQUENCE_NECESSAIRE"}:
                    emit("INV-OBL", "DECISION_UNAPPROVED", row["id"], "Recovered obligations do not approve a pending decision.", profiles)
                elif not decision.get("assertions"):
                    emit("INV-OBL", "DECISION_ASSERTIONS_MISSING", row["id"], f"{did} still has no executable assertion; its limitation remains required.", profiles)
        if "reference_cardinalities" in row["controls"]:
            facts = {f["id"]: f for f in model["historical"]["facts"]}
            fields = row["parameters"]["optional_reference_fields"]
            if len(fields) != 91 or any(facts.get(f, {}).get("data", {}).get("lower") != 0 or
                                       facts.get(f, {}).get("data", {}).get("upper") != 1 for f in fields):
                emit("INV-OBL", "REFERENCE_CARDINALITY", row["id"], "Preserve the 91 specifically documented optional reference fields (0..1).", profiles)
        if "geometry_native" in row["controls"]:
            targets = {t["id"]: t for t in registry["targets"]}
            for tid in row["depends_on"]:
                if tid not in targets:
                    continue
                geometry = targets[tid].get("geometry") or {}
                if geometry.get("status") not in {"DECIDE", "CONSEQUENCE_NECESSAIRE"}:
                    emit("INV-OBL", "ACQUIRED_GEOMETRY_NOT_INTEGRATED", row["id"], "The acquired native geometry rule is recorded; its candidate target contract remains unqualified.", profiles)
                elif (set(geometry.get("target_shapes", [])) != {s.upper() for s in row["parameters"]["geometry_shapes"]} or
                      geometry.get("required_for_valid") != row["parameters"]["geometry_required"]):
                    emit("INV-OBL", "ACQUIRED_GEOMETRY_CONFLICT", row["id"], "Target geometry contradicts the acquired native rule.", profiles)
        if "acquired_geometry" in row["controls"]:
            targets = {t["id"]: t for t in registry["targets"]}
            for tid in row["parameters"]["required_targets"]:
                geometry = targets.get(tid, {}).get("geometry") or {}
                if geometry.get("required_for_valid") is not True:
                    emit("INV-OBL", "ACQUIRED_GEOMETRY_NOT_INTEGRATED", row["id"], "Required native geometry remains unqualified for " + tid, profiles)
