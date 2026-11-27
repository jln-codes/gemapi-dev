"""Independent MOD-1L universe and its explicitly open canonical boundary.

Extractors do not read registry targets. They enumerate pinned source contracts
and explicit decision subjects. The registry records each member's disposition;
validation compares it with a fresh extraction, never with its own row count.
"""

from collections import Counter, defaultdict
from copy import deepcopy

from .common import canonical_bytes, digest


APPROVED = {"DECIDE", "CONSEQUENCE_NECESSAIRE"}
ENTITY_KINDS = {"concept", "type", "subtype", "component", "unknown",
                "configuration", "mechanism", "infrastructure"}


def modules():
    from . import (inventory_administration, inventory_patrimoine,
                   inventory_properties, inventory_contract_members)
    return inventory_patrimoine, inventory_properties, inventory_administration, inventory_contract_members


def source_requirements():
    result = {}
    for module in modules():
        for key, path in module.SOURCE_PATHS.items():
            if key in result and result[key] != path:
                raise ValueError(f"INV-UNIV.SOURCE_COLLISION: {key}")
            result[key] = path
    return result


def normalize_members(rows):
    rows = deepcopy(rows)
    for row in rows:
        for field in ("depends_on", "decision_refs", "preservation", "questions", "evidence_keys"):
            row[field] = sorted(row[field])
        row["provenance"].sort(key=lambda p: (p["source"], p["locator"]))
        row["binding"]["supports"].sort(key=canonical_bytes)
    return sorted(rows, key=lambda row: row["id"])


def extract_inventory(historical, paths):
    """Read-only, deterministic and independent of registry membership."""
    rows, limits = [], []
    for module in modules():
        rows.extend(module.inventory(historical, paths))
        limits.extend(module.limitations())
    for row in limits:
        row["owners"].sort()
        row["provenance"].sort(key=lambda p: (p["source"], p["locator"]))
    return normalize_members(rows), sorted(limits, key=lambda row: row["id"])


def inventory_summary(registry):
    rows = registry.get("inventory", [])
    scopes = {}
    for scope in ("scope:patrimoine", "scope:administration"):
        selected = [r for r in rows if r["scope"] == scope]
        scopes[scope] = {
            "members": len(selected),
            "kinds": dict(sorted(Counter(r["kind"] for r in selected).items())),
            "dispositions": dict(sorted(Counter(r["status"] for r in selected).items())),
            "boundary": "OPEN",
        }
    attributes = [r for r in rows if r["kind"] == "attribute"]
    represented = {r["target"] for r in rows if r["target"]}
    return {
        "scopes": scopes, "members": len(rows),
        "sha256": digest(rows),
        "catalog_domains": [r["id"] for r in rows if r["kind"] == "catalog"],
        "atomic_attributes": len(attributes),
        "attributes_by_owner": dict(sorted(Counter(r["owner"] or "UNKNOWN" for r in attributes).items())),
        "unqualified_attribute_owners": sorted({r["owner"] for r in attributes
                                               if r["owner"] and r["owner"].startswith("candidate:unknown:")}),
        "limitations": registry.get("inventory_limitations", []),
        "unmatched_candidates": [r["id"] for r in rows if r["target"] is None],
        "registered_targets_without_independent_member": sorted(t["id"] for t in registry["targets"]
                                                               if t["id"] not in represented),
        "existing_binding_links": [
            {"candidate": r["id"], "target": r["target"], "bindings": [
                {"id": b["id"], "physical_status": b["physical_status"]}
                for b in registry["bindings"] if b["target"] == r["target"]]}
            for r in rows if r["target"]],
        "canonical_boundary_approved": False,
    }


def validate_inventory(model, expected_history, paths, emit):
    registry = model["registry"]
    if "inventory" not in registry:
        return {}
    rows = registry["inventory"]
    targets = {t["id"]: t for t in registry["targets"]}
    decisions = {d["id"]: d for d in registry["decisions"]}
    facts = {f["id"]: f for f in model["historical"]["facts"]}
    members = {r["id"]: r for r in rows}
    sources = {s["id"]: s for s in model["manifest"]["sources"]}
    sources["instruction:mod-1c"] = {}
    for authority in model["manifest"].get("additional_registry_provenance", []):
        sources[authority] = {}
    known = {**facts, **targets, **members, **decisions}
    known.update({o["id"]: o for o in registry.get("obligations", [])})
    known.update({s["id"]: s for s in registry["scopes"]})

    def issue(code, row, message, profiles=("target-structure",)):
        emit("INV-UNIV", code, row["id"], message, profiles, row.get("provenance", []))

    for identifier, count in Counter(r["id"] for r in rows).items():
        if count > 1:
            issue("DUPLICATE_MEMBER", members[identifier], "Candidate identity occurs more than once.")

    expected, expected_limits = [], []
    if expected_history is None or paths is None:
        issue("EXTRACTION_UNVERIFIED", {"id": "inventory"},
              "Independent pinned history and source bytes are required to verify the candidate universe.")
    else:
        try:
            expected, expected_limits = extract_inventory(expected_history, paths)
        except (ValueError, KeyError, OSError) as error:
            issue("EXTRACTION_UNAVAILABLE", {"id": "inventory"}, str(error))
    expected_index = {r["id"]: r for r in expected}
    for reference in expected:
        row = members.get(reference["id"])
        if row is None:
            issue("DECIDED_MEMBER_MISSING" if reference["status"] in APPROVED else "MISSING_MEMBER",
                  reference, "Independently evidenced member has no recorded disposition.")
            continue
        # Dispositions may evolve by an explicit registry decision. The observed
        # evidence cannot be edited to make an incompatible disposition pass.
        evidence_fields = ("kind", "scope", "definition", "owner", "origin", "provenance",
                           "depends_on", "preservation", "evidence_keys", "details", "binding")
        for field in evidence_fields:
            if row[field] != reference[field]:
                issue("EVIDENCE_DRIFT", row, f"{field} differs from independent source reconstruction.")
        if reference["status"] in APPROVED and row["status"] not in APPROVED:
            issue("DECIDED_MEMBER_DOWNGRADED", row, "An acquired decision cannot become an undecided candidate.")
        if reference["status"] not in APPROVED and row["status"] in APPROVED:
            issue("UNAPPROVED_PROMOTION", row, "An unrelated approved decision cannot approve this candidate.")
        if row["status"] != reference["status"]:
            issue("DISPOSITION_UNJUSTIFIED", row, "Disposition differs from the specifically evidenced decision or pending question.")
        if row["decision_refs"] != reference["decision_refs"]:
            issue("DECISION_PROVENANCE_CHANGED", row, "Decision evidence does not apply to this candidate as reconstructed.")
        if row["target"] != reference["target"]:
            issue("TARGET_CORRESPONDENCE_CHANGED", row, "Explicit source/decision correspondence was changed.")
    if expected:
        for row in rows:
            if row["id"] not in expected_index:
                issue("UNSOURCED_MEMBER", row, "Member is absent from independent source inventory.")
        actual_limits = {r["id"]: r for r in registry.get("inventory_limitations", [])}
        for limitation in expected_limits:
            if actual_limits.get(limitation["id"]) != limitation:
                issue("LIMITATION_MISSING_OR_CHANGED", limitation,
                      "An unresolved source/owner family must remain explicitly blocking.")

    evidence_owners = defaultdict(list)
    relation_declarations = defaultdict(list)
    concept_targets = defaultdict(list)
    from .sql_evidence import read_catalogs
    ddl, _ = read_catalogs(model["manifest"], paths or {})
    for row in rows:
        identifier, kind = row["id"], row["kind"]
        if not identifier.startswith(f"candidate:{kind}:"):
            issue("KIND_MISMATCH", row, "Typed candidate key disagrees with the recorded kind.")
        if row["key_status"] != "A_APPROUVER":
            issue("KEY_APPROVAL_UNJUSTIFIED", row, "Source enumeration never approves a canonical key.")
        if kind == "unknown":
            issue("AMBIGUOUS_KIND", row, "Existence is evidenced; canonical kind, identity or ownership remains unapproved.")
        if row["status"] in APPROVED:
            if not row["decision_refs"] or any(decisions.get(d, {}).get("status") not in APPROVED for d in row["decision_refs"]):
                issue("UNAPPROVED_PROMOTION", row, "An approved member requires applicable approved decisions.")
            if row["target"] is None and kind in {"concept", "type", "subtype", "component", "relation", "attribute", "catalog"}:
                issue("DECIDED_TARGET_MISSING", row, "Acquired target has not been represented in canonical target definitions.")
        elif row["status"] == "A_APPROUVER":
            issue("DECISION_PENDING", row, "Candidate remains distinct from an approved canonical member.")
        elif row["status"] == "DIFFERE":
            issue("DEFERRED_MEMBER", row, "Explicit deferral does not close the current target inventory.")
        if row["status"] in {"A_APPROUVER", "DIFFERE"} and not row["questions"]:
            issue("DISPOSITION_UNEXPLAINED", row, "Pending/deferred candidates require an explicit unresolved question.")
        if row["origin"] == "UNKNOWN":
            issue("ORIGIN_UNQUALIFIED", row, "Native/import capability has no complete supporting proof.")
        for proof in row["provenance"]:
            if proof["source"] not in sources:
                issue("UNKNOWN_PROVENANCE", row, f"Unpinned provenance source {proof['source']}.")
        for dependency in row["depends_on"] + row["decision_refs"]:
            if dependency not in known and dependency not in sources:
                issue("UNKNOWN_REFERENCE", row, f"Unknown dependency {dependency}.")
        owner = known.get(row["owner"])
        if kind == "attribute" and row["owner"] is None:
            issue("ATTRIBUTE_WITHOUT_OWNER", row, "Every atomic attribute must identify its evidenced owner.")
        if row["owner"] is not None:
            if owner is None:
                issue("UNKNOWN_OWNER", row, f"Unknown owner {row['owner']}.")
            elif owner.get("kind") not in ENTITY_KINDS | {"class", "relation", "catalog"}:
                issue("INCOMPATIBLE_OWNER", row, "Attribute/component owner has an incompatible kind.")
            elif owner.get("scope") is not None and owner["scope"] != row["scope"]:
                issue("INCOMPATIBLE_OWNER_SCOPE", row, "Owned member and owner belong to different target scopes.")
        target = targets.get(row["target"])
        if row["target"] is not None:
            if target is None:
                issue("TARGET_MISSING", row, f"Missing target definition {row['target']}.")
            elif kind != "unknown" and (target["kind"] != kind or target["scope"] != row["scope"]):
                issue("INCOMPATIBLE_TARGET", row, "Target kind or scope differs from the evidenced candidate.")
            if kind == "concept":
                concept_targets[row["target"]].append(identifier)
        for key in row["evidence_keys"]:
            evidence_owners[(kind, key)].append(identifier)
        details = row["details"]
        if kind == "attribute":
            required = {"name", "role", "value_type", "unit", "catalog", "cardinality", "storage"}
            if not required <= set(details):
                issue("ATTRIBUTE_CONTRACT_INCOMPLETE", row, "Atomic type, unit, catalogue, bounds and storage need explicit values or unknowns.")
            catalog = details.get("catalog")
            if catalog is not None and known.get(catalog, {}).get("kind") != "catalog":
                issue("CATALOG_DOMAIN_MISSING", row, f"Unknown catalogue domain {catalog}.")
        if kind == "relation":
            endpoints = details.get("endpoints", [])
            if len(endpoints) < 2:
                issue("RELATION_ENDPOINTS_MISSING", row, "A relation requires explicit ends, including unresolved ends.")
            for endpoint in endpoints:
                end = endpoint.get("target")
                alternatives = endpoint.get("allowed_targets", [])
                if end is None and not alternatives:
                    issue("ENDPOINT_UNQUALIFIED", row, f"Unqualified endpoint {endpoint.get('role')}.")
                for target_id in ([end] if end is not None else []) + alternatives:
                    if target_id not in known:
                        issue("UNKNOWN_REFERENCE", row, f"Unknown relation endpoint {target_id}.")
                    elif known[target_id].get("kind") not in ENTITY_KINDS | {"class"}:
                        issue("INCOMPATIBLE_ENDPOINT", row, f"Incompatible endpoint {target_id}.")
            for declaration in details.get("declarations", []):
                relation_declarations[declaration].append(identifier)
        for support in row["binding"]["supports"]:
            table = ddl.get(support["source"], {}).get(support["table"])
            if table is None or any(c not in table["columns"] for c in support["columns"]):
                issue("PHYSICAL_SUPPORT_UNVERIFIED", row, f"Exact SQL support is unavailable: {support['table']}.", ("physical-binding",))
        if row["binding"]["status"] == "BINDING_PARTIEL" and not row["binding"]["supports"]:
            issue("PHYSICAL_SUPPORT_MISSING", row, "Partial binding requires exact located support.", ("physical-binding",))
    for groups, code in ((evidence_owners, "DUPLICATE_EVIDENCE_MEMBER"),
                         (relation_declarations, "REDUNDANT_INVERSE_ASSOCIATION"),
                         (concept_targets, "DUPLICATE_CONCEPT")):
        for key, identifiers in groups.items():
            if code == "DUPLICATE_EVIDENCE_MEMBER" and len(identifiers) == 2:
                # One raw source EClass and its explicitly linked semantic
                # candidate are two observations, not duplicate concepts.
                if any(other in members[first]["details"].get("semantic_candidates", [])
                       for first, other in (identifiers, identifiers[::-1])):
                    continue
            if len(identifiers) > 1:
                issue(code, members[identifiers[0]], f"Repeated semantic evidence {key}: {', '.join(identifiers)}.")
    for limitation in registry.get("inventory_limitations", []):
        issue("EXHAUSTIVENESS_LIMITATION", limitation, limitation["reason"])
    represented = {r["target"] for r in rows if r["target"]}
    for identifier in sorted(set(targets) - represented):
        issue("TARGET_WITHOUT_INDEPENDENT_MEMBER", targets[identifier],
              "Registered target has no independently reconstructed inventory member.")
    return inventory_summary(registry)
