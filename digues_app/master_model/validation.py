"""Closed, deterministic validation of the master model (MOD-1B invariants).

This validator never supplies a business default. An internally coherent draft
can still have blocked, separate certification profiles. SQL/occurrence proofs are
deliberately not inferred from an existing table or a successful extraction.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import re
import unicodedata

from jsonschema import Draft202012Validator

from . import ENGINE_VERSION, SCHEMA_VERSION
from .common import canonical_bytes, digest, engine_digest, file_digest, strict_load
from .geometry_contracts import (geometry_errors, MORPHOLOGY_TARGETS,
                                 preservation_geometry_supported,
                                 TECHNICAL_GEOMETRY_TARGETS)


PROFILES = ("target-structure", "historical-mapping", "physical-binding",
            "occurrence-projection", "application-invariants", "full")
PACKAGE = Path(__file__).parent
APPROVED = {"DECIDE", "CONSEQUENCE_NECESSAIRE"}
TARGET_KINDS = {"concept", "type", "subtype", "component", "attribute",
                "relation", "catalog", "catalog_value", "geometry"}
CONTRACT_KINDS = {"identity", "scope_partition", "preservation", "partial_import",
                  "prescription_revision", "execution", "temporal_relation",
                  "cross_scope_relation", "file_attachment", "source_disposition",
                  "geometry_import", "target_exclusion", "core_berge_lit_projection",
                  "typed_troncons_transition", "typed_troncon_physical_slice"}
FORMAL_COLLECTIONS = ("contracts", "limitations", "correspondences", "mapping_rules")


def _bounds(value):
    if not isinstance(value, dict) or set(value) != {"min", "max"}:
        return False
    low, high = value["min"], value["max"]
    return (type(low) is int and low >= 0 and
            (high == "*" or type(high) is int and high >= low))


def validate_model(model, expected_history=None, source_paths=None):
    """Return diagnostics and exact profile states; never mutate the inputs.

    ``expected_history`` must be independently extracted from pinned inputs,
    not reconstructed from the model's own counts. ``source_paths`` contains
    local paths keyed by manifest source id; paths never enter diagnostics.
    """
    diagnostics = []
    index, source_index = {}, {}

    def emit(invariant, code, element, message, profiles=PROFILES, provenance=(), severity="ERROR"):
        if not provenance:
            provenance = index.get(str(element), {}).get("provenance", ())
        if not provenance and str(element) in source_index:
            source = source_index[str(element)]
            provenance = [{"source": source["id"], "locator": source.get("path", "/")}]
        affected = (set(profiles) | {"full"}) if severity == "ERROR" else set()
        diagnostics.append({
            "invariant": invariant, "code": f"{invariant}.{code}",
            "severity": severity, "element_id": str(element),
            "provenance": sorted(provenance, key=lambda p: (p["source"], p["locator"])),
            "message": message,
            "profiles": [p for p in PROFILES if p in affected],
        })

    def finish():
        ordered = sorted(diagnostics, key=lambda d: (
            d["invariant"], d["code"], d["element_id"],
            canonical_bytes(d["provenance"]), d["message"]))
        counts = {}
        if isinstance(model, dict):
            historical = model.get("historical", {})
            registry = model.get("registry", {})
            facts = historical.get("facts", []) if isinstance(historical, dict) else []
            targets = registry.get("targets", []) if isinstance(registry, dict) else []
            counts = {
                "historical": dict(sorted(Counter(f.get("kind", "invalid") for f in facts
                                                    if isinstance(f, dict)).items())),
                "targets": dict(sorted(Counter(t.get("kind", "invalid") for t in targets
                                                 if isinstance(t, dict)).items())),
                "targets_by_scope": dict(sorted(Counter(t.get("scope", "invalid") for t in targets
                                                          if isinstance(t, dict)).items())),
                "formalization": {
                    "decisions": len(registry.get("decisions", [])),
                    "approved_decisions": sum(d.get("status") in APPROVED for d in registry.get("decisions", [])),
                    "executable_assertions": sum(len(d.get("assertions", [])) for d in registry.get("decisions", []) if d.get("status") in APPROVED),
                    "partially_formalized_decisions": sum(bool(d.get("limitation_refs")) for d in registry.get("decisions", []) if d.get("status") in APPROVED),
                    "contracts": len(registry.get("contracts", [])),
                    "limitations": len(registry.get("limitations", [])),
                    "correspondences": len(registry.get("correspondences", [])),
                    "mapping_rules": len(registry.get("mapping_rules", [])),
                    "obligations": dict(sorted(Counter(o["status"] for o in registry.get("obligations", [])).items())),
                },
                "mapping_outcomes": dict(sorted(Counter(m.get("outcome", "invalid")
                    for m in model.get("mappings", []) if isinstance(m, dict)).items())),
            }
        scope_certifications = {}
        if isinstance(model, dict) and isinstance(model.get("registry"), dict):
            registry = model["registry"]
            target_scopes = {t["id"]: t["scope"]
                             for collection in ("targets", "inventory", "inventory_limitations")
                             for t in registry.get(collection, [])
                             if isinstance(t, dict) and "id" in t and "scope" in t}
            scope_ids = {s["id"] for s in registry.get("scopes", []) if isinstance(s, dict) and "id" in s}
            for scope_id in sorted(scope_ids):
                relevant = [d for d in ordered if "target-structure" in d["profiles"] and
                            (d["element_id"] not in target_scopes or target_scopes[d["element_id"]] == scope_id) and
                            (d["element_id"] not in scope_ids or d["element_id"] == scope_id)]
                scope_certifications[scope_id] = {"target-structure": {
                    "status": "BLOCKED" if relevant else "CERTIFIED",
                    "input_digest": model.get("input_digest"), "blocking_count": len(relevant)}}
        return {
            "diagnostics": ordered,
            "certifications": {profile: {
                "status": "BLOCKED" if any(profile in d["profiles"] for d in ordered) else "CERTIFIED",
                "input_digest": model.get("input_digest") if isinstance(model, dict) else None,
                "blocking_count": sum(profile in d["profiles"] for d in ordered),
            } for profile in PROFILES},
            "scope_certifications": scope_certifications,
            "counts": counts,
        }

    required = {"schema_version", "engine_version", "registry_revision", "manifest", "registry",
                "historical", "mappings", "input_digest", "model_digest",
                "engine_sha256", "schema_sha256"}
    if (not isinstance(model, dict) or not required <= set(model) or
            not (set(model) - required) <= {"dependencies"}):
        emit("INV-VER", "MODEL_SHAPE", "model", "Model fields differ from the closed engine contract.")
        return finish()
    if not all(isinstance(model[k], dict) for k in ("registry", "manifest", "historical")):
        emit("INV-VER", "MODEL_SHAPE", "model", "Registry, manifest and history must be objects.")
        return finish()
    registry = model["registry"]
    schema_path = PACKAGE / "registry.schema.json"
    schema = strict_load(schema_path)
    for error in sorted(Draft202012Validator(schema).iter_errors(registry),
                        key=lambda e: (str(list(e.absolute_path)), e.message)):
        emit("INV-VER", "SCHEMA", "registry/" + "/".join(map(str, error.absolute_path)),
             error.message)
    if any(d["code"] == "INV-VER.SCHEMA" for d in diagnostics):
        return finish()
    history = model["historical"]
    if (set(history) != {"facts", "counts", "comparisons", "limitations"} or
            not isinstance(history.get("counts"), dict) or
            not isinstance(history.get("comparisons"), list) or
            not isinstance(history.get("limitations"), list)):
        emit("INV-VER", "HISTORY_SHAPE", "historical", "Historical universe has unsupported fields or metadata types.")
        return finish()
    facts = history.get("facts")
    if not isinstance(facts, list) or not all(isinstance(f, dict) and
            set(f) == {"id", "kind", "model", "name", "owner", "data", "depends_on", "provenance"}
            and all(isinstance(f[k], str) for k in ("id", "kind", "model", "name"))
            and (f["owner"] is None or isinstance(f["owner"], str)) and isinstance(f["data"], dict)
            and isinstance(f["depends_on"], list) and all(isinstance(d, str) for d in f["depends_on"])
            and isinstance(f["provenance"], list) and all(isinstance(p, dict) and
                set(p) == {"source", "locator"} and all(isinstance(v, str) for v in p.values())
                for p in f["provenance"])
            for f in facts):
        emit("INV-VER", "HISTORY_SHAPE", "historical", "Historical facts violate the extraction contract.")
        return finish()
    mapping_schema = Draft202012Validator({"$ref": "#/$defs/mapping_result", "$defs": schema["$defs"]})
    if not isinstance(model["mappings"], list) or not all(
            mapping_schema.is_valid(m) for m in model["mappings"]):
        emit("INV-MAP", "MAPPING_SHAPE", "mappings", "Mappings violate the closed engine contract.")
        return finish()
    recorded_dependencies = model.get("dependencies")
    if "dependencies" in model and (not isinstance(recorded_dependencies, list) or not all(isinstance(e, dict) and
            set(e) == {"dependent", "dependency", "role"} and all(isinstance(v, str) for v in e.values())
            for e in recorded_dependencies)):
        emit("INV-REF", "DEPENDENCY_SHAPE", "dependencies", "Dependency edges must be explicit and typed.")
        return finish()

    # INV-ID / INV-REP: all definition identities are global; source and target
    # namespaces remain separate. Semantic collection order is not JSON object order.
    collections = {"facts": facts, **{k: registry.get(k, []) for k in
                   ("scopes", "decisions", "targets", "rules", "bindings", "projections", "obligations",
                    "inventory", "inventory_limitations", *FORMAL_COLLECTIONS)}}
    index = {}
    for label, rows in collections.items():
        ids = [r["id"] for r in rows]
        if ids != sorted(ids):
            emit("INV-REP", "UNSORTED_COLLECTION", label, "Definitions must be ordered by stable id.")
        for row in rows:
            identifier = row["id"]
            if identifier in index:
                emit("INV-ID", "DUPLICATE", identifier, "Definition identity occurs more than once.",
                     provenance=row.get("provenance", []))
            else:
                index[identifier] = row
            if unicodedata.normalize("NFC", identifier) != identifier:
                emit("INV-ID", "NON_NORMALIZED", identifier, "Identity is not Unicode NFC.")
    fact_index = {f["id"]: f for f in facts}
    targets = {t["id"]: t for t in registry["targets"]}
    decisions = {d["id"]: d for d in registry["decisions"]}
    rules = {r["id"]: r for r in registry["rules"]}
    scopes = {s["id"]: s for s in registry["scopes"]}
    contracts = {c["id"]: c for c in registry.get("contracts", [])}
    limitations = {l["id"]: l for l in registry.get("limitations", [])}
    correspondences = {c["id"]: c for c in registry.get("correspondences", [])}
    mapping_rules = {r["id"]: r for r in registry.get("mapping_rules", [])}
    sources = model["manifest"].get("sources", [])
    if not isinstance(sources, list) or not all(isinstance(s, dict) and
            {"id", "sha256", "kind", "version"} <= set(s) and
            all(isinstance(s[k], str) for k in ("id", "sha256", "kind", "version")) for s in sources):
        emit("INV-VER", "MANIFEST_SHAPE", "manifest", "Every pinned source needs id, kind, version and hash.")
        return finish()
    source_index = {s["id"]: s for s in sources}
    if len(source_index) != len(sources):
        emit("INV-ID", "SOURCE_DUPLICATE", "manifest", "Manifest source ids must be unique.")
    registry_provenance = model["manifest"].get("registry_provenance")
    if registry_provenance is not None:
        if registry_provenance != "instruction:mod-1c" or registry_provenance in source_index:
            emit("INV-VER", "REGISTRY_PROVENANCE", "manifest", "The registry provenance handle must be distinct from pinned external inputs.")
        else:
            # Registry bytes are editable authority, already covered by the
            # input digest. Pinning them as an external source would force a
            # second-file update after every atomic geometry decision.
            source_index[registry_provenance] = {
                "id": registry_provenance, "kind": "registry_authority",
                "version": registry["revision"], "sha256": digest(registry),
                "path": "digues_app/master_model/registry.json",
            }
    for authority in model["manifest"].get("additional_registry_provenance", []):
        if authority not in {"instruction:mod-1l", "instruction:mod-1m", "instruction:mod-1p-a", "instruction:mod-1p-a3", "instruction:mod-1p-b2", "instruction:mod-1p-b3", "instruction:mod-1p-b4", "instruction:mod-1p-b5", "instruction:mod-1p-b6", "instruction:mod-1o-l01", "instruction:mod-1o-l02", "instruction:mod-1o-l02-rectificatif", "instruction:mod-1o-l02-b", "instruction:mod-ah-impl-1"} or authority in source_index:
            emit("INV-VER", "REGISTRY_PROVENANCE", "manifest", "Unknown or duplicate manual authority handle.")
        else:
            source_index[authority] = {"id": authority, "kind": "registry_authority",
                "version": registry["revision"], "sha256": digest(registry),
                "path": "digues_app/master_model/registry.json"}

    def ref(identifier, owner, allowed=None, profiles=PROFILES):
        if identifier is None:
            return
        if not isinstance(identifier, str):
            emit("INV-REF", "TYPE", owner, "Reference must be a typed identifier, not a coerced value.", profiles)
            return
        if identifier.startswith("geometry:"):
            target = targets.get(identifier.removeprefix("geometry:"))
            if target is not None and target.get("geometry") is not None and (allowed is None or "geometry" in allowed):
                return
        destination = index.get(identifier, source_index.get(identifier))
        if destination is None:
            emit("INV-REF", "MISSING", owner, f"Missing reference: {identifier}.", profiles)
        elif allowed is not None and destination.get("kind") not in allowed:
            emit("INV-REF", "TYPE", owner, f"Reference {identifier} has incompatible kind.", profiles)

    def evidence(row, profiles=PROFILES):
        for item in row.get("provenance", []):
            if item.get("source") not in source_index:
                emit("INV-REF", "PROVENANCE_SOURCE", row["id"],
                     "Evidence source is absent from the pinned manifest.", profiles)
        for decision in row.get("decision_refs", []):
            if decision not in decisions:
                emit("INV-REF", "DECISION_MISSING", row["id"], f"Unknown decision {decision}.", profiles)
            elif row.get("status") in APPROVED and decisions[decision]["status"] not in APPROVED:
                emit("INV-REF", "DECISION_UNAPPROVED", row["id"],
                     "An approved claim depends on an unapproved decision.", profiles)

    for fact in facts:
        evidence(fact)
        if fact["id"].split(":")[1:2] != [fact["kind"]]:
            emit("INV-ID", "SOURCE_KIND", fact["id"], "Source identity kind disagrees with its fact kind.")
        ref(fact["owner"], fact["id"])
        for dependency in fact["depends_on"]:
            ref(dependency, fact["id"])
        data = fact["data"]
        if fact["kind"] in {"attribute", "reference", "application"}:
            lower, upper = data.get("lower"), data.get("upper")
            if (type(lower) is not int or type(upper) is not int or lower < 0 or
                    upper < -1 or upper != -1 and upper < lower):
                emit("INV-CARD", "SOURCE_BOUNDS", fact["id"], "Invalid historical cardinality.")
            kind = data.get("property_kind", fact["kind"])
            allowed = {"class"} if kind == "reference" else {"datatype", "enum"}
            ref(data.get("type"), fact["id"], allowed)
            if data.get("opposite"):
                ref(data["opposite"], fact["id"], {"reference"})
        if fact["kind"] == "inheritance":
            for dependency in fact["depends_on"]:
                ref(dependency, fact["id"], {"class"})
        if fact["kind"] == "catalog_attribute":
            ref(data.get("type"), fact["id"], {"datatype"})
            ref(fact["owner"], fact["id"], {"catalog"})
        if fact["kind"] == "catalog_relation":
            for field in ("source", "target"):
                ref(data.get(field), fact["id"], {"catalog"})
            for field in ("property", "target_property"):
                ref(data.get(field), fact["id"], {"catalog_attribute"})

    for row in registry["decisions"]:
        evidence(row)
        if row["status"] not in APPROVED:
            emit("INV-COV", "DECISION_PENDING", row["id"],
                 "The recorded question is not an approved business decision.", ("target-structure",), row["provenance"])
        elif not row.get("assertions") and not row.get("limitation_refs"):
            emit("INV-COV", "DECISION_NOT_FORMALIZED", row["id"],
                 "Decision provenance is recorded, but its statement has no executable formal assertions in this lot.",
                 ("target-structure",), row["provenance"])
        for dependency in row["depends_on"]:
            ref(dependency, row["id"])
        for limitation in row.get("limitation_refs", []):
            if limitation not in limitations:
                emit("INV-REF", "LIMITATION_MISSING", row["id"],
                     f"Unknown formal limitation {limitation}.", ("target-structure",))
            elif row["id"] not in limitations[limitation]["decision_refs"]:
                emit("INV-REF", "LIMITATION_DECISION", row["id"],
                     "The limitation must identify the decision whose execution it limits.", ("target-structure",))
    from .decision_successions import resolve_decision_assertions
    succession = resolve_decision_assertions(registry)
    for diagnostic in succession["diagnostics"]:
        invariant, code = diagnostic["code"].split(".", 1)
        emit(invariant, code, diagnostic["element_id"], diagnostic["message"],
             ("target-structure", "historical-mapping"))
    assertions = defaultdict(list)
    source_predicates = {"source_reference_type", "projection_parent",
                         "direct_photo_creation", "forbid_automatic_output"}
    for decision in decisions.values():
        for assertion in succession["assertions"].get(decision["id"], []):
            predicate = assertion["predicate"]
            allowed = (None if predicate == "correspondence" else
                       CONTRACT_KINDS if predicate in {"contract", "contract_parameters_digest"} else
                       {"attribute", "catalog_value"} if predicate == "value_type" else
                       {"class", "application"} if predicate in source_predicates else TARGET_KINDS)
            ref(assertion["subject"], decision["id"],
                allowed,
                ("historical-mapping",) if predicate in source_predicates else ("target-structure",))
            if decision["status"] in APPROVED:
                assertions[(assertion["subject"], predicate)].append(
                    (decision["id"], assertion["value"]))
    for (subject, predicate), values in sorted(assertions.items()):
        profiles = (("historical-mapping",) if predicate in source_predicates else ("target-structure",))
        conflict_groups = defaultdict(set)
        for _, value in values:
            key = value["direct_reference"] if predicate == "direct_photo_creation" else "single"
            conflict_groups[key].add(canonical_bytes(value))
        if predicate not in {"constraint", "distinct", "correspondence"} and any(len(v) > 1 for v in conflict_groups.values()):
            emit("INV-MAP", "DECISION_CONTRADICTION", subject,
                 "Approved assertions disagree on " + predicate + ": " + ", ".join(d for d, _ in values), profiles)
        target = targets.get(subject)
        fact = fact_index.get(subject)
        for decision_id, value in values:
            if predicate in {"kind", "owner", "owner_cardinality", "cardinality", "endpoints", "constraint",
                             "parent", "owners", "scope", "state", "children", "distinct", "value_type"}:
                if target is None:
                    continue
                if predicate == "owner":
                    agrees = value in target["owners"]
                elif predicate == "constraint":
                    agrees = value in target["constraints"]
                elif predicate == "children":
                    for child in value:
                        ref(child, subject, TARGET_KINDS, profiles)
                    agrees = set(value) == {t["id"] for t in targets.values() if t["parent"] == subject and t["kind"] in {"type", "subtype"} and t["state"] == "active"}
                elif predicate == "distinct":
                    for other in value:
                        ref(other, subject, TARGET_KINDS, profiles)
                    agrees = subject not in value and len(set(value)) == len(value)
                elif predicate == "owners":
                    agrees = set(value) == set(target["owners"])
                else:
                    agrees = target.get(predicate) == value
                if not agrees:
                    emit("INV-MAP", "DECISION_NOT_APPLIED", subject,
                         f"Target {predicate} contradicts approved assertion {decision_id}.", profiles)
            elif predicate == "contract":
                contract = contracts.get(subject)
                if contract is None or contract["kind"] != value or contract["status"] not in APPROVED or decision_id not in contract["decision_refs"]:
                    emit("INV-MAP", "CONTRACT_NOT_APPLIED", subject,
                         "Approved assertion must name an approved executable contract of the exact kind and provenance.", profiles)
            elif predicate == "contract_parameters_digest":
                contract = contracts.get(subject)
                if (contract is None or contract["status"] not in APPROVED or
                        decision_id not in contract["decision_refs"] or
                        digest(contract["parameters"]) != value):
                    emit("INV-MAP", "CONTRACT_PARAMETERS_STALE", subject,
                         "The exact approved contract parameters have changed without a decision succession.", profiles)
            elif predicate == "correspondence":
                correspondence = correspondences.get(subject)
                if correspondence is None or correspondence["target"] != value or correspondence["status"] not in APPROVED or decision_id not in correspondence["decision_refs"]:
                    emit("INV-MAP", "CORRESPONDENCE_NOT_APPLIED", subject,
                         "Approved correspondence assertion lacks its exact target or decision provenance.", ("historical-mapping",))
            elif predicate == "source_reference_type":
                ref(value, subject, {"class"}, profiles)
                if fact and (fact["kind"] != "application" or
                             fact["data"].get("property_kind") != "reference" or
                             fact["data"].get("type") != value):
                    emit("INV-MAP", "SOURCE_REFERENCE_CONTRADICTION", subject,
                         "Historical reference type contradicts the approved fact assertion.", profiles)
            elif predicate == "projection_parent":
                ref(value["reference"], subject, {"reference"}, profiles)
                ref(value["target"], subject, {"concept"}, profiles)
                if fact and not any(f["kind"] == "application" and f["owner"] == subject and
                                    f["data"].get("declaration") == value["reference"] and
                                    f["data"].get("property_kind") == "reference" for f in facts):
                    emit("INV-MAP", "PARENT_SOURCE_NOT_DECLARED", subject,
                         "Required parent reference is not applicable to this source class.", profiles)
                for rule in rules.values():
                    if subject in rule["sources"] and (rule["reference_policy"]["parent_source"] != value["reference"] or
                            rule["reference_policy"]["parent_target"] != value["target"]):
                        emit("INV-MAP", "PARENT_PROJECTION_CONTRADICTION", rule["id"],
                             "Projection replaces the approved direct Tronçon parent reference.", profiles)
            elif predicate == "direct_photo_creation":
                if value["observation_target"] is None:
                    emit("INV-MAP", "PHOTO_OBSERVATION_TARGET_UNRESOLVED", subject,
                         "Standard Observation is approved in principle, but its canonical target binding and grouping remain unqualified.", profiles)
                else:
                    ref(value["observation_target"], subject, {"component"}, profiles)
                if value["coordinate_source"] is not None:
                    emit("INV-GEO", "PHOTO_COORDINATE_CONTRACT_PENDING", subject,
                         "Source coordinates may be carried only after target compatibility is qualified.",
                         ("historical-mapping", "occurrence-projection"))
                direct = fact_index.get(value["direct_reference"])
                ref(value["direct_reference"], subject, {"reference"}, profiles)
                ref(value["direct_owner"], subject, {"class"}, profiles)
                if direct and (direct["data"].get("type") != subject or
                               direct["owner"] != value["direct_owner"] or
                               not direct["data"].get("containment")):
                    emit("INV-MAP", "DIRECT_PHOTO_SOURCE_CONTRADICTION", subject,
                         "Declared direct containment does not point to this historical Photo class.", profiles)
                expected_types = {
                    "date_source": {"sirs-v2:datatype:ecore/EDate"},
                    "coordinate_source": {"sirs-v2:datatype:jts/Geometry", "sirs-v2:datatype:jts/Point"},
                    "media_source": {"sirs-v2:datatype:ecore/EString"},
                }
                for field in ("date_source", "coordinate_source", "media_source"):
                    handle = value[field]
                    if handle is None:
                        continue
                    ref(handle, subject, {"application"}, profiles)
                    source_field = fact_index.get(handle)
                    if source_field and (source_field["owner"] != subject or
                                         source_field["data"].get("property_kind") != "attribute"):
                        emit("INV-MAP", "PHOTO_FIELD_NOT_APPLICABLE", subject,
                             f"Declared {field} is not an attribute of the historical Photo class.", profiles)
                    elif source_field and source_field["data"].get("type") not in expected_types[field]:
                        emit("INV-MAP", "PHOTO_FIELD_TYPE", subject,
                             f"Declared {field} has an incompatible historical type.", profiles)
                grouping = decisions.get("decision:B4-photo-grouping")
                if grouping is None or grouping["status"] != "A_APPROUVER":
                    emit("INV-MAP", "PHOTO_GROUPING_STATUS", subject,
                         "The unapproved grouping question must remain explicit.", profiles)
                for rule in rules.values():
                    if subject in rule["sources"] and any(o["operation"] == "create_component" for o in rule["outputs"]):
                        emit("INV-MAP", "PHOTO_GROUPING_RULE_PREMATURE", rule["id"],
                             "Creating Observations from direct Photos requires a qualified grouping rule.", profiles)
            elif predicate == "forbid_automatic_output":
                for forbidden in value:
                    ref(forbidden, subject, {"concept"}, profiles)
                for rule in rules.values():
                    if subject in rule["sources"] and any(o["target"] in value for o in rule["outputs"]):
                        emit("INV-MAP", "TREATMENT_AUTOMATIC_OUTPUT", rule["id"],
                             "Historical treatment cannot automatically become a new Prescription or realized Prestation without an approved contract.", profiles)
    for scope in registry["scopes"]:
        evidence(scope, ("target-structure",))
        if scope["boundary_status"] != "SEALED" or scope["status"] not in APPROVED:
            emit("INV-COV", "TARGET_UNIVERSE_OPEN", scope["id"],
                 "No independently approved complete target universe is declared.", ("target-structure",), scope["provenance"])
        declared = {t["id"] for t in targets.values() if t["scope"] == scope["id"]}
        approved = set(scope["approved_members"])
        independent = (expected_history or {}).get("target_universes", {}).get(scope["id"])
        if (scope["universe_source"] is None or not independent or not independent.get("provenance") or
                scope["universe_source"] not in {p["source"] for p in independent.get("provenance", [])} or
                any(source_index.get(p["source"], {}).get("kind") != "target_universe"
                    for p in independent["provenance"])):
            emit("INV-COV", "TARGET_UNIVERSE_UNVERIFIED", scope["id"],
                 "Scope members lack an independently extracted and pinned approval authority.", ("target-structure",))
        elif approved != set(independent["members"]):
            emit("INV-COV", "TARGET_AUTHORITY_MISMATCH", scope["id"],
                 "Self-declared scope members differ from the independent approval authority.", ("target-structure",), independent["provenance"])
        if scope["boundary_status"] == "SEALED" and declared != approved:
            emit("INV-COV", "TARGET_UNIVERSE_MISMATCH", scope["id"],
                 "Declared target definitions differ from the approved scope members.", ("target-structure",))
        for member in approved:
            ref(member, scope["id"], TARGET_KINDS, ("target-structure",))

    composition = defaultdict(set)
    for target in targets.values():
        tid = target["id"]
        evidence(target, ("target-structure",))
        if not tid.startswith(f"da:{target['kind']}:"):
            emit("INV-ID", "TARGET_KIND", tid, "Target identity kind is inconsistent.", ("target-structure",))
        if target["key_status"] != "APPROUVE":
            emit("INV-ID", "KEY_UNAPPROVED", tid, "Canonical key remains a candidate.", ("target-structure",))
        if target["status"] not in APPROVED:
            emit("INV-COV", "TARGET_PENDING", tid, "Target definition is not approved.", ("target-structure",))
        if target["origin"] == "UNKNOWN":
            emit("INV-COV", "TARGET_ORIGIN_UNKNOWN", tid, "Target import/native capability is unknown.", ("target-structure",))
        if target["scope"] not in scopes:
            emit("INV-REF", "SCOPE", tid, "Target scope does not exist.", ("target-structure",))
        if target["kind"] == "type":
            ref(target["parent"], tid, {"concept", "component"}, ("target-structure",))
            if target["parent"] is None:
                emit("INV-REF", "PARENT_REQUIRED", tid, "A type requires one concept or component family parent.", ("target-structure",))
        elif target["kind"] == "subtype":
            ref(target["parent"], tid, {"type"}, ("target-structure",))
            if target["parent"] is None:
                emit("INV-REF", "PARENT_REQUIRED", tid, "A subtype requires one type parent.", ("target-structure",))
        elif target["kind"] == "attribute":
            ref(target["parent"], tid, {"concept", "type", "subtype", "component", "relation", "catalog"}, ("target-structure",))
        else:
            ref(target["parent"], tid, {"concept", "type", "component"}, ("target-structure",))
        for owner in target["owners"]:
            owner_kinds = ({"concept", "type", "subtype", "component", "relation", "catalog"}
                           if target["kind"] == "attribute" else {"concept", "type", "component"})
            ref(owner, tid, owner_kinds, ("target-structure",))
            composition[tid].add(owner)
        if target["kind"] == "relation":
            roles = []
            for endpoint in target.get("endpoints", []):
                roles.append(endpoint["role"])
                ref(endpoint["target"], tid, {"concept", "type", "subtype", "component"}, ("target-structure",))
                for alternative in endpoint.get("allowed_targets", []):
                    ref(alternative, tid, {"concept", "type", "subtype", "component"}, ("target-structure",))
                if endpoint["target"] is None and not endpoint.get("allowed_targets"):
                    emit("INV-REF", "ENDPOINT_TARGET_UNQUALIFIED", tid,
                         f"Relation role {endpoint['role']} has no qualified endpoint universe.", ("target-structure",))
                if endpoint["target"] is not None and endpoint.get("allowed_targets"):
                    emit("INV-REF", "ENDPOINT_TARGET_CONFLICT", tid,
                         "A relation role cannot specify both a fixed endpoint and alternative endpoints.", ("target-structure",))
                if endpoint["cardinality"] is None or endpoint["cardinality"].get("min") is None:
                    emit("INV-CARD", "ENDPOINT_UNQUALIFIED", tid,
                         f"Relation role {endpoint['role']} retains an unapproved cardinality bound.", ("target-structure",))
                elif not _bounds(endpoint["cardinality"]):
                    emit("INV-CARD", "ENDPOINT_BOUNDS", tid, "Invalid endpoint cardinality.", ("target-structure",))
                if endpoint.get("occurrence_cardinality") is not None and not _bounds(endpoint["occurrence_cardinality"]):
                    emit("INV-CARD", "ENDPOINT_OCCURRENCE_BOUNDS", tid, "Invalid association occurrence cardinality.", ("target-structure",))
            if len(roles) != len(set(roles)):
                emit("INV-REF", "RELATION_ROLES", tid, "Relation roles must be distinct.", ("target-structure",))
        if "value_type" in target:
            value_type = target["value_type"]
            ref(value_type["reference"], tid, TARGET_KINDS, ("target-structure",))
        for key in ("cardinality", "owner_cardinality"):
            if target[key] is not None and not _bounds(target[key]):
                emit("INV-CARD", "TARGET_BOUNDS", tid, f"Invalid {key}.", ("target-structure",))
        optional_structure = (
            tid == "da:component:structure-digue"
            and target["owners"] == ["da:concept:digue"]
            and target["owner_cardinality"] == {"min": 0, "max": 1}
            and ("decision:MOD1M-core", {"min": 0, "max": 1})
            in assertions.get((tid, "owner_cardinality"), [])
        )
        optional_trace = (
            tid == "da:component:troncon/trace-position"
            and target["owners"] == ["da:concept:troncon"]
            and target["owner_cardinality"] == {"min": 0, "max": 1}
            and ("decision:MOD1P-B3", {"min": 0, "max": 1})
            in assertions.get((tid, "owner_cardinality"), [])
        )
        if target["kind"] == "component" and target["state"] == "active" and not (optional_structure or optional_trace) and (not target["owners"] or target["owner_cardinality"] != {"min": 1, "max": 1}):
            emit("INV-CARD", "COMPONENT_OWNER", tid,
                 "A valid identity-dependent component requires exactly one owner.", ("target-structure",))
        for code, message in geometry_errors(target["geometry"]):
            emit("INV-GEO", code, tid, message, ("target-structure", "historical-mapping", "physical-binding", "occurrence-projection"))
        if (target["geometry"] or {}).get("policy") == "preserve_in_technical_support" and tid not in TECHNICAL_GEOMETRY_TARGETS:
            emit("INV-GEO", "TECHNICAL_POLICY_SCOPE", tid, "La conservation technique seule est approuvée uniquement pour les cinq descriptifs L02.")
        if target["geometry"] is not None and target["geometry"].get("status") not in APPROVED:
            emit("INV-GEO", "TARGET_CONTRACT_PENDING", tid,
                 "Geometry expectations and validity are not qualified.", ("physical-binding", "occurrence-projection"))

    from .geometry import GeometryExchangeError, owner_universe
    try:
        geometry_owners = owner_universe(model)
    except GeometryExchangeError:
        # Duplicate/absent identities already have their invariant diagnostics.
        geometry_owners = {}
    for owner, entry in geometry_owners.items():
        geometry = (entry["target"] or {}).get("geometry")
        if entry["target"] is not None and geometry is None:
            emit("INV-GEO", "OWNER_CONTRACT_MISSING", owner,
                 "A registered destination of historical geometry has no target contract.", ("physical-binding", "occurrence-projection"))
        if "QUALIFICATION_AMBIGUE" in entry["issues"]:
            emit("INV-GEO", "OWNER_QUALIFICATION_AMBIGUOUS", owner,
                 "Historical geometry has several candidate destinations; qualify ownership explicitly.", ("target-structure", "historical-mapping"))
        if (geometry is not None and geometry.get("target_shapes") == ["AUCUNE"] and entry["historical"]
                and not (owner in TECHNICAL_GEOMETRY_TARGETS and geometry.get("policy") == "preserve_in_technical_support")):
            emit("INV-GEO", "HISTORICAL_LOSS", owner,
                 "A historically spatial owner cannot be declared geometry-free.", ("historical-mapping", "occurrence-projection"))

    # Formal contracts do not grant occurrence certification: they express
    # executable obligations and keep missing production integration explicit.
    for collection in FORMAL_COLLECTIONS:
        for row in registry.get(collection, []):
            affected = tuple(row.get("profiles", ("historical-mapping",)
                if collection == "mapping_rules" else ("target-structure",)))
            evidence(row, affected)
            for dependency in row["depends_on"]:
                ref(dependency, row["id"], profiles=affected)
            for subject in row.get("subjects", []):
                ref(subject, row["id"], profiles=affected)
            if collection == "limitations":
                successor = decisions.get(row.get("superseded_by"), {})
                if row["status"] == "SUPERSEDED":
                    if (successor.get("status") not in APPROVED or
                            row["id"] not in successor.get("depends_on", []) or
                            not successor.get("supersessions") or succession["diagnostics"]):
                        emit("INV-COV", "LIMITATION_SUCCESSION_INVALID", row["id"],
                             "A retired limitation requires an approved, valid decision succession with an explicit dependency.", affected)
                else:
                    emit("INV-COV", "LIMITATION_UNRESOLVED", row["id"], row["reason"], affected)
            elif row["status"] not in APPROVED:
                emit("INV-COV", "FORMAL_OBLIGATION_PENDING", row["id"],
                     "Formal obligation is not approved.", affected)
    for rule in mapping_rules.values():
        parameters = rule["parameters"]
        expected_source_kind = {"class_family": "class", "catalog_membership": "catalog",
                                "property": "attribute"}.get(rule["operator"])
        for field in ("source", "source_owner", "source_type"):
            if field in parameters and parameters[field] not in fact_index:
                emit("INV-REF", "MAPPING_RULE_SOURCE", rule["id"],
                     f"Mapping parameter {field} is outside the historical universe.", ("historical-mapping",))
            elif field in parameters:
                allowed = ({expected_source_kind} if field == "source" and expected_source_kind else
                           {"class"} if field == "source_owner" else {"datatype", "enum", "external_type"})
                ref(parameters[field], rule["id"], allowed, ("historical-mapping",))
        for field in ("target", "target_owner"):
            if field in parameters:
                allowed = ({"attribute"} if rule["operator"] == "property" and field == "target" else
                           {"catalog"} if rule["operator"] == "catalog_membership" else
                           TARGET_KINDS - {"attribute", "catalog_value", "geometry"})
                ref(parameters[field], rule["id"], allowed, ("historical-mapping",))
        if "contract" in parameters and parameters["contract"] not in contracts:
            emit("INV-REF", "MAPPING_RULE_CONTRACT", rule["id"],
                 "Mapping preservation contract does not exist.", ("historical-mapping",))
        for replaced in rule["supersedes"]:
            ref(replaced, rule["id"], profiles=("historical-mapping",))
        if rule["supersedes"]:
            emit("INV-MAP", "SUPERSESSION_UNPROVEN", rule["id"],
                 "No executable approved replacement assertion justifies this precedence.", ("historical-mapping",))
    for contract in contracts.values():
        cid, kind, parameters = contract["id"], contract["kind"], contract["parameters"]
        if kind not in CONTRACT_KINDS:
            emit("INV-VER", "CONTRACT_UNSUPPORTED", cid, "Contract has no executable evaluator.")
        if kind == "typed_troncons_transition":
            from .typed_troncons import validate_transition_contract
            for diagnostic in validate_transition_contract(registry, contract, model["historical"]):
                invariant, code = diagnostic["code"].split(".", 1)
                emit(invariant, code, diagnostic["element_id"], diagnostic["message"],
                     ("target-structure", "historical-mapping", "physical-binding", "occurrence-projection"))
        if kind == "typed_troncon_physical_slice":
            from .typed_troncons import TALUS_BINDING_ID, validate_crete_binding, validate_talus_binding
            from .troncons_berges import PARAMETERS, validate_binding
            from .troncons_b4 import BINDING_ID, validate_b4_binding
            from .troncons_ah import BINDING_ID as B5_BINDING_ID, validate_b5_binding
            from .troncons_core import BINDING_ID as B6_BINDING_ID, validate_b6_binding
            evaluator = (validate_b6_binding if cid == B6_BINDING_ID else
                         validate_b5_binding if cid == B5_BINDING_ID else
                         validate_b4_binding if cid == BINDING_ID else
                         validate_binding if cid in PARAMETERS else
                         validate_talus_binding if cid == TALUS_BINDING_ID else validate_crete_binding)
            for diagnostic in evaluator(registry, contract, model["historical"]):
                invariant, code = diagnostic["code"].split(".", 1)
                emit(invariant, code, diagnostic["element_id"], diagnostic["message"],
                     ("target-structure", "historical-mapping", "physical-binding", "occurrence-projection"))
        if kind == "core_berge_lit_projection":
            from .occurrence_projection import validate_projection_contract
            for diagnostic in validate_projection_contract(registry, contract, model["historical"]):
                invariant, code = diagnostic["code"].split(".", 1)
                emit(invariant, code, diagnostic["element_id"], diagnostic["message"],
                     ("target-structure", "historical-mapping", "occurrence-projection"))
        if kind == "scope_partition":
            if set(parameters["scopes"]) != set(scopes):
                emit("INV-COV", "SCOPE_PARTITION", cid,
                     "Canonical scope inventory differs from the approved distinct scopes.", ("target-structure",))
            cross_relations = {s for c in contracts.values() if c["kind"] == "cross_scope_relation" for s in c["subjects"]}
            for target in targets.values():
                if target["kind"] != "relation":
                    continue
                endpoint_scopes = {targets[t]["scope"] for e in target.get("endpoints", [])
                                   for t in ([e["target"]] if e["target"] else e.get("allowed_targets", [])) if t in targets}
                if len(endpoint_scopes) > 1 and target["id"] not in cross_relations:
                    emit("INV-REF", "CROSS_SCOPE_UNDECLARED", target["id"],
                         "A relation crossing canonical scopes needs an explicit transversal contract.", ("target-structure",))
        elif kind == "target_exclusion":
            forbidden_ids = set(parameters["target_ids"])
            forbidden_labels = {unicodedata.normalize("NFC", v).casefold() for v in parameters["labels"]}
            for target in targets.values():
                if target["state"] == "active" and (target["id"] in forbidden_ids or unicodedata.normalize("NFC", target["label"]).casefold() in forbidden_labels):
                    emit("INV-COV", "TARGET_EXCLUDED", target["id"],
                         f"Target contradicts explicit exclusion {cid}.", ("target-structure",))
        elif kind == "source_disposition":
            for subject in contract["subjects"]:
                if subject not in fact_index:
                    emit("INV-REF", "SOURCE_DISPOSITION_SUBJECT", cid,
                         "A historical disposition requires an identified source fact.", ("historical-mapping",))
                for rule in rules.values():
                    if subject in rule["sources"] and rule["outputs"]:
                        emit("INV-MAP", "SOURCE_DISPOSITION_CONFLICT", rule["id"],
                             "Deferred or removed source has no approved automatic target; children require separate explicit rules.", ("historical-mapping",))
                for correspondence in correspondences.values():
                    if correspondence["source"] == subject:
                        emit("INV-MAP", "SOURCE_DISPOSITION_CONFLICT", correspondence["id"],
                             "A deferred or removed source cannot acquire a target through a partial correspondence.", ("historical-mapping",))
        elif kind in {"temporal_relation", "cross_scope_relation"}:
            for subject in contract["subjects"]:
                ref(subject, cid, {"relation"}, ("target-structure",))
                if kind == "cross_scope_relation" and subject in targets and targets[subject].get("context") != "business_database":
                    emit("INV-REF", "BUSINESS_DATABASE_CONTEXT", subject,
                         "Cross-scope relation requires an explicit business-database context.", ("target-structure",))
        elif kind == "preservation":
            for rule in rules.values():
                if set(contract["subjects"]) & (set(rule["sources"]) | {o["target"] for o in rule["outputs"]}):
                    if rule["identity_policy"] != "preserve_occurrences_no_merge":
                        emit("INV-MAP", "PRESERVATION_CONFLICT", rule["id"],
                             "Projection contradicts the approved per-occurrence preservation contract.", ("historical-mapping",))
    correspondence_sources = defaultdict(list)
    for correspondence in correspondences.values():
        cid = correspondence["id"]
        correspondence_sources[correspondence["source"]].append(correspondence)
        if correspondence["source"] not in fact_index:
            emit("INV-REF", "CORRESPONDENCE_SOURCE", cid,
                 "Correspondence source must be a historical fact.", ("historical-mapping",))
        ref(correspondence["target"], cid, TARGET_KINDS, ("historical-mapping",))
        for rule in rules.values():
            if (correspondence["source"] in rule["sources"]
                    and correspondence["target"] not in {o["target"] for o in rule["outputs"]}):
                emit("INV-MAP", "CORRESPONDENCE_RULE_CONFLICT", rule["id"],
                     f"Projection does not implement the recorded destination of {cid}.", ("historical-mapping",))
        preservation = contracts.get(correspondence["preservation_contract"])
        from .occurrence_projection import validate_berge_correspondence
        berge_equivalence = validate_berge_correspondence(registry, correspondence, model["historical"])
        if not berge_equivalence and (preservation is None or preservation["kind"] != "preservation" or preservation["status"] not in APPROVED):
            emit("INV-REF", "CORRESPONDENCE_PRESERVATION", cid,
                 "Correspondence requires an approved executable preservation contract.", ("historical-mapping",))
        elif not berge_equivalence and not {correspondence["source"], correspondence["target"]} & set(preservation["subjects"]):
            emit("INV-REF", "CORRESPONDENCE_PRESERVATION_SCOPE", cid,
                 "Preservation contract must cover this correspondence source or target.", ("historical-mapping",))
        emit("INV-MAP", "CORRESPONDENCE_PROJECTION_PENDING", cid,
             "Business correspondence is identified; occurrence projection remains unimplemented and mapping is not certified.", ("historical-mapping", "occurrence-projection"))
    for source, entries in sorted(correspondence_sources.items()):
        if len({entry["target"] for entry in entries}) > 1:
            emit("INV-MAP", "CORRESPONDENCE_CONFLICT", source,
                 "Several destinations lack an explicit approved conditional or parallel output rule.", ("historical-mapping",))

    visited, active = set(), set()
    def visit(node):
        if node in active:
            emit("INV-REF", "COMPOSITION_CYCLE", node, "Composition ownership contains a cycle.", ("target-structure",))
            return
        if node in visited:
            return
        active.add(node)
        for parent in sorted(composition[node]):
            visit(parent)
        active.remove(node)
        visited.add(node)
    for node in sorted(list(composition)):
        visit(node)

    # Exact coverage is checked against a fresh extraction, not self-declared counts.
    if expected_history is None:
        emit("INV-COV", "SOURCE_UNIVERSE_UNVERIFIED", "historical",
             "Independent extraction of the pinned source universe is required.", ("historical-mapping",))
    else:
        expected = {f["id"]: f for f in expected_history["facts"]}
        for missing in sorted(expected.keys() - fact_index.keys()):
            emit("INV-COV", "SOURCE_MISSING", missing, "Historical source fact omitted from the model.",
                 ("historical-mapping",), expected[missing]["provenance"])
        for extra in sorted(fact_index.keys() - expected.keys()):
            emit("INV-COV", "SOURCE_UNEXPECTED", extra, "Source fact is not in the independently extracted universe.", ("historical-mapping",))
        for key in sorted(expected.keys() & fact_index.keys()):
            if expected[key] != fact_index[key]:
                emit("INV-COV", "SOURCE_CHANGED", key, "Historical metadata differs from the pinned source extraction.",
                     ("historical-mapping",), expected[key]["provenance"])
        for field in ("counts", "comparisons", "limitations"):
            if history.get(field) != expected_history.get(field):
                emit("INV-COV", "EXTRACTION_METADATA_CHANGED", "historical/" + field,
                     "Extraction summary differs from independent source extraction.", ("historical-mapping",))
    for comparison in history.get("comparisons", []):
        if comparison.get("status") != "MATCH":
            emit("INV-COV", "INVENTORY_MISMATCH", comparison["id"],
                 "Source inventory comparison did not match exactly.", ("historical-mapping",), comparison.get("provenance", []))
    for limitation in history.get("limitations", []):
        contextual = limitation["code"] in {"C3.FIVE_MODELS_ONLY", "C2.NOT_TARGET_AUTHORITY"}
        affected = (("occurrence-projection",) if limitation["code"] in
                    {"MOCK.NOT_EXHAUSTIVE", "IDENTITY.RUNTIME_NOT_ECORE"} else ("historical-mapping",))
        emit("INV-COV", "EXTRACTION_LIMITATION", limitation.get("source", "historical"),
             f"{limitation['code']}: {limitation['detail']}", affected,
             severity="INFO" if contextual else "ERROR")

    mapping_groups = defaultdict(list)
    for mapping in model["mappings"]:
        mapping_groups[mapping["source"]].append(mapping)
    if [m["source"] for m in model["mappings"]] != sorted(m["source"] for m in model["mappings"]):
        emit("INV-REP", "MAPPINGS_ORDER", "mappings", "Mappings must be ordered by source id.")
    from .resolution import resolve_mappings
    from .relation_checks import validate_source_relations
    diagnostics.extend(validate_source_relations(history))
    # Never trust a stored result, even if its SHA was recalculated. Source
    # equality is checked independently above; every rule application is replayed.
    expected_mappings = {m["source"]: m for m in resolve_mappings(registry, history)}
    mapping_nodes = {"mapping:" + source for source in fact_index}
    available_rules = {**rules, **mapping_rules, **correspondences, **contracts}
    for source in sorted(fact_index):
        entries = mapping_groups.get(source, [])
        if not entries:
            emit("INV-MAP", "MISSING", source, "Every historical fact needs an explicit outcome.", ("historical-mapping",))
        elif len(entries) != 1:
            emit("INV-MAP", "CONFLICT", source, "Multiple outcomes classify the same historical fact.", ("historical-mapping",))
        else:
            entry = entries[0]
            outcome = entry["outcome"]
            if entry != expected_mappings[source]:
                emit("INV-MAP", "DERIVATION_MISMATCH", source,
                     "Stored result differs from individual replay of explicit rules and applicability guards.",
                     ("historical-mapping", "occurrence-projection"))
            if entry["qualification"] == "PARTIAL":
                emit("INV-MAP", "PARTIAL", source, "Mapping qualifies only an explicit part of the source contract.", ("historical-mapping",))
            if (outcome == "UNRESOLVED") != (entry["qualification"] == "UNRESOLVED"):
                emit("INV-MAP", "QUALIFICATION_CONFLICT", source, "Outcome and qualification disagree.", ("historical-mapping",))
            if outcome == "UNRESOLVED":
                emit("INV-MAP", "UNRESOLVED", source, entry["reason"], ("historical-mapping",), fact_index[source]["provenance"])
            elif outcome == "CONFLICT":
                emit("INV-MAP", "RULE_CONFLICT", source,
                     "Incompatible candidates: " + ", ".join(entry["rules"]) + "; " + ", ".join(entry["obligations"]),
                     ("historical-mapping",), entry["provenance"])
            elif not entry["rules"]:
                emit("INV-MAP", "UNJUSTIFIED", source, "A qualified outcome requires an explicit rule.", ("historical-mapping",))
            if outcome == "preserved":
                emit("INV-MAP", "SOURCE_PRESERVED_ONLY", source,
                     "Exact source preservation does not qualify a target migration: " + ", ".join(entry["obligations"]),
                     ("historical-mapping", "occurrence-projection"), entry["provenance"])
            if outcome == "auxiliary" and "SOURCE_BEHAVIOR_UNQUALIFIED" in entry["obligations"]:
                emit("INV-MAP", "SOURCE_BEHAVIOR_UNQUALIFIED", source,
                     "Declared operation or parameter is retained as evidence; application behavior is not yet qualified.",
                     ("historical-mapping",), entry["provenance"])
            for target in entry["targets"]:
                ref(target, source, TARGET_KINDS, ("historical-mapping",))
            for dependency in entry["depends_on"]:
                if dependency not in mapping_nodes:
                    ref(dependency, source, profiles=("historical-mapping",))
            for proof in entry["provenance"]:
                if proof["source"] not in source_index:
                    emit("INV-REF", "PROVENANCE_SOURCE", source,
                         "Mapping evidence source is absent from the pinned manifest.", ("historical-mapping",))
            for rid in entry["rules"]:
                if rid not in available_rules:
                    emit("INV-REF", "RULE_MISSING", source, f"Unknown rule {rid}.", ("historical-mapping",))
                elif (rid in rules and source not in rules[rid]["sources"]
                      and rid not in expected_mappings[source]["rules"]):
                    emit("INV-MAP", "RULE_SOURCE", source, "Mapping rule does not declare this source.", ("historical-mapping",))
            if outcome in {"deferred", "removed_container"}:
                emit("INV-MAP", "DISPOSITION_PROJECTION_PENDING", source,
                     "Approved disposition does not implement occurrence handling or qualify child dispositions.", ("occurrence-projection",))
    for extra in sorted(mapping_groups.keys() - fact_index.keys()):
        emit("INV-MAP", "UNKNOWN_SOURCE", extra, "Mapping source is outside the historical universe.", ("historical-mapping",))
    used_sources = defaultdict(list)
    imported_targets = set()
    reference_applications = defaultdict(set)
    for fact in facts:
        if fact["kind"] == "reference":
            reference_applications[fact["id"]].add(fact["owner"])
        elif fact["kind"] == "application" and fact["data"].get("property_kind") == "reference":
            reference_applications[fact["data"].get("declaration")].add(fact["owner"])
    for rule in rules.values():
        evidence(rule, ("historical-mapping",))
        if rule["status"] not in APPROVED:
            emit("INV-MAP", "RULE_UNAPPROVED", rule["id"], "Rule is not based on an approved decision.", ("historical-mapping",))
        for source in rule["sources"]:
            ref(source, rule["id"], profiles=("historical-mapping",))
            used_sources[source].append(rule["id"])
        for output in rule["outputs"]:
            ref(output["target"], rule["id"], TARGET_KINDS, ("historical-mapping",))
            imported_targets.add(output["target"])
            if ("decision:MOD-1O-L02-B" in rule.get("decision_refs", [])
                    and output["target"] in MORPHOLOGY_TARGETS
                    and targets.get(output["target"], {}).get("parent") ==
                    "da:concept:element-morphologique-lit"):
                imported_targets.add("da:concept:element-morphologique-lit")
            if "NO_GEOMETRIC_ASSOCIATION_INFERENCE" in targets.get(output["target"], {}).get("constraints", []):
                emit("INV-MAP", "RELATION_SOURCE_PROOF_REQUIRED", rule["id"],
                     "A relation projection needs an explicit non-geometric source-link contract; this rule version cannot certify one.",
                     ("historical-mapping",))
            if targets.get(output["target"], {}).get("origin") == "native_only":
                emit("INV-MAP", "NATIVE_ONLY_IMPORT", output["target"],
                     "An import output contradicts the target's native-only origin.", ("historical-mapping",))
            if output["operation"] != "preserve_occurrence":
                emit("INV-MAP", "OPERATION_UNSUPPORTED", rule["id"],
                     "This engine version only qualifies the explicit preserve-occurrence operation.", ("historical-mapping",))
        if len({o["role"] for o in rule["outputs"]}) != len(rule["outputs"]):
            emit("INV-MAP", "OUTPUT_ROLE_DUPLICATE", rule["id"], "Parallel output roles must be distinct.", ("historical-mapping",))
        if rule["field_policy"] == "preserve_all_pending_target_attributes":
            emit("INV-MAP", "FIELD_BINDINGS_PENDING", rule["id"],
                 "Preservation is decided, but target attribute mappings remain unqualified.", ("historical-mapping",))
        else:
            emit("INV-MAP", "FIELD_QUALIFIER_UNSUPPORTED", rule["id"],
                 "This rule version cannot certify field mappings from a qualification flag alone.", ("historical-mapping",))
        if rule["geometry_policy"] == "preserve_if_present_pending_contract":
            emit("INV-GEO", "RULE_CONTRACT_PENDING", rule["id"],
                 "Preservation is explicit; shape, CRS and validity contracts remain unqualified.", ("historical-mapping", "occurrence-projection"))
        elif rule["geometry_policy"] == "preserve_qualified":
            if not preservation_geometry_supported(rule, targets):
                emit("INV-GEO", "CONTRACT_PROOF_MISSING", rule["id"],
                     "A qualified geometry policy requires the approved morphology contract and no transformation.",
                     ("historical-mapping", "occurrence-projection"))
        elif rule["geometry_policy"] == "approved_transformation":
            emit("INV-GEO", "CONTRACT_PROOF_MISSING", rule["id"],
                 "A geometry transformation has no approved implementation in this projection.",
                 ("historical-mapping", "occurrence-projection"))
        elif rule["geometry_policy"] == "preserve_in_technical_support":
            if any(o['target'] not in TECHNICAL_GEOMETRY_TARGETS or
                   (targets.get(o['target'], {}).get('geometry') or {}).get('policy') != 'preserve_in_technical_support'
                   for o in rule['outputs']):
                emit("INV-GEO", "TECHNICAL_POLICY_SCOPE", rule["id"], "La conservation technique est limitée aux cinq descriptifs L02.")
        elif rule["geometry_policy"] != "absent_by_source_contract":
            emit("INV-GEO", "LOSS_UNAPPROVED", rule["id"],
                 "Unsupported geometry transformation or silent loss is forbidden.", ("historical-mapping", "occurrence-projection"))
        if rule["geometry_policy"] == "absent_by_source_contract":
            spatial_owners = {f["owner"] for f in facts if f["kind"] == "geometry"}
            if any(s in spatial_owners or fact_index.get(s, {}).get("kind") == "geometry" for s in rule["sources"]):
                emit("INV-GEO", "SOURCE_GEOMETRY_PRESENT", rule["id"],
                     "A no-geometry rule contradicts a historical geometry declaration.", ("historical-mapping",))
        parent = rule["reference_policy"]
        ref(parent["parent_source"], rule["id"], {"reference"}, ("historical-mapping",))
        ref(parent["parent_target"], rule["id"], {"concept", "type", "component"}, ("historical-mapping",))
        if (parent["parent_source"] is None) != (parent["parent_target"] is None):
            emit("INV-REF", "PARENT_POLICY_INCOMPLETE", rule["id"],
                 "A parent projection requires both explicit source role and target owner.", ("historical-mapping",))
        if parent["parent_source"] is not None:
            for source in rule["sources"]:
                if fact_index.get(source, {}).get("kind") == "class" and source not in reference_applications[parent["parent_source"]]:
                    emit("INV-REF", "PARENT_SOURCE_NOT_APPLICABLE", rule["id"],
                         f"The parent role is not a declared or inherited reference of {source}.", ("historical-mapping",))
        for output in rule["outputs"]:
            target = targets.get(output["target"], {})
            if target.get("owners") and (parent["parent_target"] not in target["owners"] or
                    parent["valid_parent_cardinality"] != target["owner_cardinality"]):
                emit("INV-CARD", "OWNER_POLICY_CONFLICT", rule["id"],
                     "Projection parent policy disagrees with the output's declared owners/cardinality.", ("historical-mapping",))
        morphology_family = targets.get("da:concept:element-morphologique-lit", {})
        optional_morphology_parent = (
            "decision:MOD-1O-L02-B" in rule.get("decision_refs", [])
            and bool(rule["outputs"])
            and all(output["target"] in MORPHOLOGY_TARGETS
                    and targets.get(output["target"], {}).get("parent") == morphology_family.get("id")
                    for output in rule["outputs"])
            and parent["parent_target"] == "da:concept:troncon-lit"
            and parent["valid_parent_cardinality"] == {"min": 0, "max": 1}
            and morphology_family.get("owners") == ["da:concept:troncon-lit"]
            and morphology_family.get("owner_cardinality") == {"min": 0, "max": 1}
            and all(parent.get(field) == "diagnose_no_invention"
                    for field in ("missing", "incompatible", "ambiguous"))
        )
        if (parent["parent_target"] is not None
                and parent["valid_parent_cardinality"] != {"min": 1, "max": 1}
                and not optional_morphology_parent):
            emit("INV-CARD", "RULE_PARENT", rule["id"], "The declared unique-parent projection must require 1..1.", ("historical-mapping",))
    for source, overlaps in sorted(used_sources.items()):
        if len(overlaps) > 1:
            emit("INV-MAP", "OVERLAPPING_RULES", source,
                 "First-lot unconditional exclusive rules overlap: " + ", ".join(sorted(overlaps)), ("historical-mapping",))
    for target in targets.values():
        if target["state"] == "active" and target["origin"] in {"import_only", "both"} and target["id"] not in imported_targets:
            emit("INV-MAP", "TARGET_IMPORT_PATH_MISSING", target["id"],
                 "An import-capable target has no explicit historical projection path.", ("historical-mapping",))
    for fact in facts:
        if fact["kind"] != "geometry":
            continue
        entries = mapping_groups.get(fact["id"], [])
        if len(entries) == 1 and entries[0]["outcome"] in {"auxiliary", "deferred", "removed_container"}:
            emit("INV-GEO", "SILENT_DROP", fact["id"],
                 "A historical geometry cannot be ignored without an approved loss rule.", ("historical-mapping",), fact["provenance"])

    # Explicit dependency edges: never infer impact from labels or physical names.
    nodes = set(index) | set(source_index)
    nodes.update("mapping:" + m["source"] for m in model["mappings"])
    nodes.update("geometry:" + t["id"] for t in targets.values() if t["geometry"] is not None)
    from .graph import build_dependencies
    dependencies = build_dependencies(model)
    for edge in dependencies:
        for endpoint in (edge["dependent"], edge["dependency"]):
            if endpoint not in nodes:
                emit("INV-REF", "GRAPH_ENDPOINT", edge["dependent"], f"Dependency graph endpoint is undefined: {endpoint}.")
    if recorded_dependencies is not None:
        edge_keys = [(e["dependent"], e["dependency"], e["role"]) for e in recorded_dependencies]
        if edge_keys != sorted(set(edge_keys)):
            emit("INV-REP", "GRAPH_ORDER", "dependencies", "Dependency edges must be distinct and canonically ordered.")
        if recorded_dependencies != dependencies:
            emit("INV-COV", "DEPENDENCIES_MISMATCH", "dependencies",
                 "Recorded impact edges differ from the formally declared dependencies.")

    from .obligations import validate_obligations
    validate_obligations(model, emit, ref, evidence)

    # Physical location, partial binding and full certification are distinct.
    bound_targets = set()
    for binding in registry["bindings"]:
        bound_targets.add(binding["target"])
        ref(binding["target"], binding["id"], TARGET_KINDS, ("physical-binding",))
        evidence(binding, ("physical-binding",))
        sql_source = source_index.get(binding["source"])
        if sql_source is None or sql_source.get("kind") != "ddl":
            emit("INV-SQL", "SOURCE", binding["id"], "Binding must name a pinned DDL authority.", ("physical-binding",))
        for dependency in binding["depends_on"]:
            if dependency not in source_index:
                ref(dependency, binding["id"], profiles=("physical-binding",))
    from .sql_evidence import qualify_bindings
    physical = qualify_bindings(registry, model["manifest"], source_paths or {})
    for diagnostic in physical["diagnostics"]:
        emit("INV-SQL", diagnostic["code"], diagnostic["element_id"], diagnostic["message"],
             ("physical-binding",), diagnostic["provenance"])
    for projection in registry["projections"]:
        evidence(projection, ("occurrence-projection",))
        if projection["status"] not in APPROVED:
            emit("INV-COV", "PROJECTION_PENDING", projection["id"],
                 "Occurrence projection contract remains unapproved.", ("occurrence-projection",), projection["provenance"])
        for rid in projection["rules"]:
            if rid not in rules:
                emit("INV-REF", "PROJECTION_RULE", projection["id"], "Projection references an absent rule.", ("occurrence-projection",))
        for bid in projection["bindings"]:
            if not any(b["id"] == bid for b in registry["bindings"]):
                emit("INV-REF", "PROJECTION_BINDING", projection["id"], "Projection references an absent binding.", ("occurrence-projection",))
    for target in targets.values():
        if target["state"] == "active" and target["id"] not in bound_targets:
            emit("INV-SQL", "BINDING_MISSING", target["id"],
                 "No physical binding is declared for this active target.", ("physical-binding",), target["provenance"])
    emit("INV-COV", "OCCURRENCE_PROOF_MISSING", "occurrence-projection",
         "No complete independently qualified occurrence corpus and projection certificate is supplied.", ("occurrence-projection",),
         [{"source": "evidence:mod-1b", "locator": "§12 occurrence-projection"}] if "evidence:mod-1b" in source_index else ())
    emit("INV-COV", "APPLICATION_PROOF_MISSING", "application-invariants",
         "Pure model witnesses do not qualify application transactions, authorization or offline synchronization.",
         ("application-invariants",))

    # Version and byte identity protect against stale same-name/same-version inputs.
    if model["schema_version"] != SCHEMA_VERSION or model["engine_version"] != ENGINE_VERSION:
        emit("INV-VER", "ENGINE_VERSION", "model", "Unsupported model schema or engine version.")
    if model["registry_revision"] != registry["revision"]:
        emit("INV-VER", "REGISTRY_REVISION", "registry", "Registry revision differs from the model envelope.")
    for source in sources:
        path = (source_paths or {}).get(source["id"])
        if path is None or not path.is_file():
            emit("INV-VER", "SOURCE_UNAVAILABLE", source["id"], "Pinned source bytes were not supplied to validation.")
        elif file_digest(path) != source["sha256"]:
            emit("INV-VER", "SOURCE_HASH", source["id"], "Source bytes differ from the pinned SHA-256.")
    if model["engine_sha256"] != engine_digest():
        emit("INV-VER", "ENGINE_HASH", "engine", "Engine bytes changed without rebuilding the model.")
    if model["schema_sha256"] != file_digest(schema_path):
        emit("INV-VER", "SCHEMA_HASH", "registry-schema", "Registry schema bytes differ from the model input.")
    input_payload = {k: model[k] for k in ("engine_version", "engine_sha256", "manifest", "registry", "schema_sha256")}
    if model["input_digest"] != digest(input_payload):
        emit("INV-REP", "INPUT_DIGEST", "model", "Input digest is not the canonical digest of declared inputs.")
    if model["model_digest"] != digest({k: v for k, v in model.items() if k != "model_digest"}):
        emit("INV-REP", "MODEL_DIGEST", "model", "Model digest does not match canonical model bytes.")
    from .inventory import validate_inventory
    inventory = validate_inventory(model, expected_history, source_paths, emit)
    from .occurrence_projection import CONTRACT_ID
    qualification = None
    if CONTRACT_ID in contracts:
        from .projection_qualification import qualify_projections
        qualification = qualify_projections(model, physical_evidence=physical)
        already_reported = {(d["code"], d["element_id"]) for d in diagnostics}
        for diagnostic in qualification["diagnostics"]:
            if (diagnostic["code"], diagnostic["element_id"]) not in already_reported:
                invariant, code = diagnostic["code"].split(".", 1)
                emit(invariant, code, diagnostic["element_id"], diagnostic["message"],
                     diagnostic["profiles"], diagnostic["provenance"])
    report = finish()
    report["physical_evidence"] = physical
    if qualification is not None:
        report["projection_qualification"] = qualification
    if inventory:
        report["target_inventory"] = inventory
    if any(c["kind"] == "typed_troncons_transition" for c in contracts.values()):
        from .typed_troncons import transition_inventory, transition_summary
        report["typed_troncons"] = transition_summary(registry, model["historical"])
        report["typed_troncons"]["inventory"] = transition_inventory(registry, model["historical"])
    return report
