"""Deterministic relation proofs; declared facts and observed links stay separate.

These functions neither repair source references nor project target occurrences.
In particular, a reciprocal pair is one demonstrated source link, whereas an
asymmetric assertion remains an unresolved assertion with its original evidence.
"""

from __future__ import annotations

from collections import defaultdict
from hashlib import sha256

from .common import canonical_bytes


PROFILES = ["historical-mapping", "occurrence-projection", "full"]


def _diagnostic(code, fact, message, *, element=None):
    return {"invariant": code.split(".", 1)[0], "code": code,
            "severity": "ERROR", "element_id": element or fact["id"],
            "provenance": sorted(fact.get("provenance", []),
                                 key=lambda p: (p["source"], p["locator"])),
            "message": message, "profiles": list(PROFILES)}


def _ordered(diagnostics):
    unique = {canonical_bytes(d): d for d in diagnostics}
    return sorted(unique.values(), key=lambda d: (d["code"], d["element_id"],
                                                  canonical_bytes(d["provenance"]), d["message"]))


def _source_assignable(actual, expected, facts):
    """Follow only Ecore's declared super-types, never class-name similarities."""
    pending, visited = [actual], set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        row = facts.get(current, {})
        if row.get("kind") != "class":
            continue
        if current == expected:
            return True
        pending.extend(row.get("data", {}).get("super_types", []))
    return False


def _target_assignable(actual, expected, targets):
    seen = set()
    while actual in targets and actual not in seen:
        if actual == expected:
            return True
        seen.add(actual)
        row = targets[actual]
        if row.get("kind") not in {"type", "subtype"}:
            return False
        actual = row.get("parent")
    return False


def _bounds_contained(source_data, target_bounds):
    if not isinstance(target_bounds, dict):
        return None
    low, high = target_bounds.get("min"), target_bounds.get("max")
    if low is None or high is None:
        return None
    source_low, source_high = source_data.get("lower"), source_data.get("upper")
    if type(low) is not int or low < 0 or not (high == "*" or type(high) is int and high >= low):
        return False
    if (type(source_low) is not int or type(source_high) is not int or source_low < 0
            or source_high < -1 or source_high != -1 and source_high < source_low):
        return False
    return source_low >= low and (high == "*" or source_high != -1 and source_high <= high)


def _declared_reference_diagnostics(fact, facts):
    data, result = fact.get("data", {}), []
    if facts.get(fact.get("owner"), {}).get("kind") != "class":
        result.append(_diagnostic("INV-REF.SOURCE_RELATION_OWNER", fact,
                                  "Reference owner is not a declared historical class."))
    if facts.get(data.get("type"), {}).get("kind") != "class":
        result.append(_diagnostic("INV-REF.SOURCE_RELATION_TYPE", fact,
                                  "Reference destination is not a declared historical class."))
    if _bounds_contained(data, {"min": 0, "max": "*"}) is not True:
        result.append(_diagnostic("INV-CARD.SOURCE_RELATION_BOUNDS", fact,
                                  "Historical reference bounds are invalid."))
    opposite_id = data.get("opposite")
    if not opposite_id:
        return result
    opposite = facts.get(opposite_id)
    if opposite is None or opposite.get("kind") != "reference":
        result.append(_diagnostic("INV-REF.SOURCE_OPPOSITE_MISSING", fact,
                                  f"Declared opposite {opposite_id} is missing or not a reference."))
        return result
    if opposite.get("data", {}).get("opposite") != fact["id"]:
        result.append(_diagnostic("INV-REF.SOURCE_OPPOSITE_ASYMMETRIC", fact,
                                  f"Declared opposite {opposite_id} does not point back to this reference."))
    if (not _source_assignable(data.get("type"), opposite.get("owner"), facts)
            or not _source_assignable(fact.get("owner"), opposite.get("data", {}).get("type"), facts)):
        result.append(_diagnostic("INV-REF.SOURCE_OPPOSITE_TYPE", fact,
                                  f"Declared opposite {opposite_id} has incompatible endpoint types."))
    if data.get("containment") and opposite.get("data", {}).get("containment"):
        result.append(_diagnostic("INV-REF.SOURCE_OPPOSITE_DOUBLE_CONTAINMENT", fact,
                                  "Both directions cannot declare containment of one another."))
    return result


def validate_source_relations(historical):
    """Validate declarations and report, not repair, aggregated mock anomalies."""
    facts = {f["id"]: f for f in historical["facts"]}
    diagnostics = []
    for fact in sorted(facts.values(), key=lambda f: f["id"]):
        if fact["kind"] == "reference":
            diagnostics.extend(_declared_reference_diagnostics(fact, facts))
        diagnostics.extend(observation_diagnostics(fact))
    return _ordered(diagnostics)


def observation_diagnostics(fact):
    """Individual proof obligations retained even when the fact is auxiliary."""
    if fact.get("kind") != "observation":
        return []
    diagnostics, data = [], fact["data"]
    for field, key, code, message in (
        ("opposite_observe", "non", "INV-REF.MOCK_OPPOSITE_ASYMMETRIC", "asymmetric opposite assertions"),
        ("resolution_identite", "NON_RESOLUE_EXACTEMENT", "INV-REF.MOCK_TARGET_UNRESOLVED", "unresolved exact reference identities"),
        ("resolution_identite", "BORNE_ECORE_NON_RESPECTEE", "INV-CARD.MOCK_SOURCE_BOUNDS", "historical lower-bound violations"),
        ("type_cible_compatible", "non", "INV-REF.MOCK_TARGET_TYPE", "incompatible reference types"),
    ):
        count = data.get(field, {}).get(key, 0)
        if count:
            diagnostics.append(_diagnostic(code, fact,
                f"Observed mock aggregate contains {count} {message}; no target cardinality or repair is inferred."))
    return _ordered(diagnostics)


def evaluate_reference(fact, facts_by_id, targets_by_id, class_destinations, target_id=None):
    """Prove an explicit relation or a containment against known class mappings.

    A class destination is supplied only by an independently qualified rule.
    Unknown endpoints, ambiguous endpoint roles and stricter target bounds block
    this generic proof. Approved-specific transformations belong to other rules.
    """
    result = {"applicable": False, "targets": [], "obligations": [],
              "depends_on": [fact["id"]], "diagnostics": []}
    declaration = fact
    if fact.get("kind") == "application" and fact.get("data", {}).get("property_kind") == "reference":
        declaration = facts_by_id.get(fact["data"].get("declaration"), {})
        if (declaration.get("kind") != "reference"
                or not _source_assignable(fact.get("owner"), declaration.get("owner"), facts_by_id)
                or any(fact["data"].get(field) != declaration["data"].get(field)
                       for field in ("type", "lower", "upper", "containment", "opposite", "ordered", "unique"))):
            result["obligations"] = ["REFERENCE_APPLICATION_INCOMPATIBLE"]
            return result
    elif fact.get("kind") != "reference":
        result["obligations"] = ["REFERENCE_DECLARATION_REQUIRED"]
        return result
    data = fact["data"]
    result["diagnostics"] = _declared_reference_diagnostics(declaration, facts_by_id)
    dependencies = {fact["id"], declaration["id"], fact.get("owner"), data.get("type"), data.get("opposite")}
    owners = set(class_destinations.get(fact.get("owner"), ()))
    children = set(class_destinations.get(data.get("type"), ()))
    obligations = {"OCCURRENCE_REFERENCE_VALIDATION"}
    if result["diagnostics"]:
        obligations.add("SOURCE_RELATION_DECLARATION_INVALID")
    if len(owners) != 1 or len(children) != 1:
        obligations.add("RELATION_ENDPOINT_UNQUALIFIED" if not owners or not children
                        else "RELATION_ENDPOINT_AMBIGUOUS")
    else:
        owner, child = next(iter(owners)), next(iter(children))
        dependencies.update((owner, child))
        if owner not in targets_by_id or child not in targets_by_id:
            obligations.add("RELATION_ENDPOINT_UNQUALIFIED")
        elif any(targets_by_id[t].get("status") not in {"DECIDE", "CONSEQUENCE_NECESSAIRE"}
                 for t in (owner, child)):
            obligations.add("RELATION_ENDPOINT_UNAPPROVED")
        elif target_id is None:
            component = targets_by_id[child]
            if not data.get("containment") or component.get("kind") != "component":
                obligations.add("COMPONENT_CONTAINMENT_NOT_ESTABLISHED")
            elif owner not in component.get("owners", []):
                obligations.add("COMPONENT_OWNER_INCOMPATIBLE")
            elif component.get("owner_cardinality") != {"min": 1, "max": 1}:
                obligations.add("COMPONENT_OWNER_CARDINALITY_UNQUALIFIED")
            else:
                compatible = _bounds_contained(data, component.get("cardinality"))
                if compatible is not True:
                    obligations.add("COMPONENT_CARDINALITY_UNQUALIFIED" if compatible is None
                                    else "COMPONENT_CARDINALITY_INCOMPATIBLE")
                else:
                    result["targets"] = [child]
                    obligations.add("OCCURRENCE_PARENT_VALIDATION")
        else:
            relation = targets_by_id.get(target_id, {})
            dependencies.add(target_id)
            endpoints = relation.get("endpoints", [])
            pairs = []
            if (relation.get("kind") == "relation"
                    and relation.get("status") in {"DECIDE", "CONSEQUENCE_NECESSAIRE"}):
                for left in endpoints:
                    for right in endpoints:
                        if left is right:
                            continue
                        if (_target_assignable(owner, left.get("target"), targets_by_id)
                                and _target_assignable(child, right.get("target"), targets_by_id)):
                            pairs.append((left, right))
            if len(pairs) != 1:
                obligations.add("RELATION_ROLE_AMBIGUOUS" if pairs else "RELATION_ENDPOINT_INCOMPATIBLE")
            else:
                left, right = pairs[0]
                compatible = _bounds_contained(data, right.get("cardinality"))
                opposite = facts_by_id.get(data.get("opposite"))
                if opposite is not None:
                    reverse = _bounds_contained(opposite["data"], left.get("cardinality"))
                    if reverse is not True:
                        compatible = False if reverse is False else None
                if compatible is not True:
                    obligations.add("RELATION_CARDINALITY_UNQUALIFIED" if compatible is None
                                    else "RELATION_CARDINALITY_INCOMPATIBLE")
                else:
                    result["targets"] = [target_id]
                    if opposite is not None:
                        obligations.add("RECIPROCAL_OCCURRENCE_VALIDATION")
    blocking = obligations - {"OCCURRENCE_REFERENCE_VALIDATION", "OCCURRENCE_PARENT_VALIDATION",
                              "RECIPROCAL_OCCURRENCE_VALIDATION"}
    result["applicable"] = bool(result["targets"]) and not blocking
    result["depends_on"] = sorted(d for d in dependencies if d is not None)
    result["obligations"] = sorted(obligations)
    result["diagnostics"] = _ordered(result["diagnostics"])
    return result


def normalize_reference_occurrences(historical, occurrences):
    """Check a bounded source corpus without inventing, merging or repairing it.

    Occurrences are ``{id, class, values}``; ``values`` is keyed by declared
    historical reference id (not a guessed property name). Values are occurrence
    ids, arrays of ids or null. The supplied corpus is the explicit resolution
    boundary: targets outside it are unresolved. Attributed relation occurrences
    keep their own ids, even when endpoint tuples are equal.
    """
    facts = {f["id"]: f for f in historical["facts"]}
    references = {k: f for k, f in facts.items() if f["kind"] == "reference"}
    grouped = defaultdict(list)
    for occurrence in occurrences:
        grouped[occurrence["id"]].append(occurrence)
    diagnostics, candidates, unresolved = [], {}, []
    applications_by_class = defaultdict(list)
    for fact in facts.values():
        if fact["kind"] == "application":
            applications_by_class[fact["owner"]].append(fact)
    effective_cache = {}
    index = {key: rows[0] for key, rows in grouped.items() if len(rows) == 1}
    for identifier, rows in sorted(grouped.items()):
        if len(rows) != 1:
            diagnostics.append(_diagnostic("INV-ID.OCCURRENCE_DUPLICATE",
                facts.get(rows[0].get("class"), {"id": identifier}),
                "Occurrence identity is not unique in the explicit source corpus.", element=identifier))
            unresolved.extend({"occurrence": identifier, "class": row.get("class"),
                               "values": row.get("values"), "reason": "DUPLICATE_IDENTITY"}
                              for row in rows)
    for occurrence in sorted(index.values(), key=lambda o: o["id"]):
        oid, cls = occurrence["id"], occurrence["class"]
        cls_fact = facts.get(cls, {"id": cls})
        if cls_fact.get("kind") != "class":
            diagnostics.append(_diagnostic("INV-REF.OCCURRENCE_CLASS", cls_fact,
                                           "Occurrence class is not a declared historical class.", element=oid))
            unresolved.append({"occurrence": oid, "class": cls, "values": occurrence["values"],
                               "reason": "CLASS_UNRESOLVED"})
            continue
        effective = effective_cache.get(cls)
        # Small adversarial corpora need not materialize applications. Resolve
        # declared inheritance only; a same-name specialization removes its base.
        if effective is None and cls in applications_by_class:
            effective = {f["data"]["declaration"] for f in applications_by_class[cls]
                         if f["data"].get("property_kind") == "reference"}
        elif effective is None:
            applicable = [f for f in references.values() if _source_assignable(cls, f["owner"], facts)]
            effective = {f["id"] for f in applicable if not any(
                g["id"] != f["id"] and g["name"] == f["name"]
                and g["owner"] != f["owner"] and _source_assignable(g["owner"], f["owner"], facts)
                for g in applicable)}
        effective_cache[cls] = effective
        for rid in sorted(set(occurrence["values"]) | effective):
            reference = references.get(rid)
            raw = occurrence["values"].get(rid)
            values = [] if raw is None else raw if isinstance(raw, list) else [raw]
            evidence = {"occurrence": oid, "reference": rid}
            if reference is None or rid not in effective:
                diagnostics.append(_diagnostic("INV-REF.OCCURRENCE_REFERENCE", cls_fact,
                    f"Reference {rid} is not effective for this source class.", element=oid))
                unresolved.append({**evidence, "values": raw, "reason": "REFERENCE_NOT_EFFECTIVE"})
                continue
            data = reference["data"]
            declaration_errors = _declared_reference_diagnostics(reference, facts)
            if declaration_errors:
                diagnostics.extend(declaration_errors)
                unresolved.append({**evidence, "values": raw, "reason": "INVALID_DECLARATION"})
                continue
            lower, upper = data.get("lower", 0), data.get("upper", 1)
            if len(values) < lower or upper != -1 and len(values) > upper:
                diagnostics.append(_diagnostic("INV-CARD.OCCURRENCE_REFERENCE_BOUNDS", reference,
                    f"Occurrence {oid} has {len(values)} values outside declared {lower}..{upper}.", element=oid))
                unresolved.append({**evidence, "values": raw, "reason": "CARDINALITY_VIOLATION"})
                continue
            if data.get("unique", True) and len({canonical_bytes(v) for v in values}) != len(values):
                diagnostics.append(_diagnostic("INV-CARD.OCCURRENCE_REFERENCE_DUPLICATE", reference,
                    f"Occurrence {oid} repeats a value of a unique reference.", element=oid))
                unresolved.append({**evidence, "values": raw, "reason": "DUPLICATE_REFERENCE"})
                continue
            for position, target_id in enumerate(values):
                edge = {**evidence, "target": target_id, "position": position}
                target = index.get(target_id) if isinstance(target_id, str) else None
                if target is None:
                    diagnostics.append(_diagnostic("INV-REF.OCCURRENCE_TARGET_MISSING", reference,
                        f"Occurrence {oid} references unresolved or ambiguous identity {target_id!r}.", element=oid))
                    unresolved.append({**edge, "reason": "TARGET_UNRESOLVED"})
                elif not _source_assignable(target["class"], data.get("type"), facts):
                    diagnostics.append(_diagnostic("INV-REF.OCCURRENCE_TARGET_TYPE", reference,
                        f"Occurrence {oid} references {target_id} with an incompatible source type.", element=oid))
                    unresolved.append({**edge, "reason": "TARGET_TYPE_INCOMPATIBLE"})
                else:
                    candidates[(oid, rid, target_id, position)] = edge
    # Ecore containment gives a child one container, independently of SQL.
    containers = defaultdict(set)
    for oid, rid, target_id, _ in candidates:
        if references[rid]["data"].get("containment"):
            containers[target_id].add((oid, rid))
    links, seen, invalid_containment = [], set(), set()
    for oid, rid, target_id, _ in candidates:
        reference = references[rid]
        if reference["data"].get("containment") and len(containers[target_id]) != 1:
            invalid_containment.add((oid, rid, target_id))
            if reference["data"].get("opposite"):
                invalid_containment.add((target_id, reference["data"]["opposite"], oid))
            diagnostics.append(_diagnostic("INV-CARD.OCCURRENCE_MULTIPLE_CONTAINERS", reference,
                f"Child {target_id} has multiple explicitly asserted containers.", element=target_id))
    multiplicities = defaultdict(int)
    for oid, rid, target_id, _ in candidates:
        multiplicities[(oid, rid, target_id)] += 1
    ambiguous_opposites = set()
    for (oid, rid, target_id), count in multiplicities.items():
        opposite = references[rid]["data"].get("opposite")
        if opposite and count > 1:
            ambiguous_opposites.update(((oid, rid, target_id), (target_id, opposite, oid)))
            diagnostics.append(_diagnostic("INV-REF.OCCURRENCE_OPPOSITE_MULTIPLICITY", references[rid],
                f"Repeated assertions between {oid} and {target_id} cannot be paired to an opposite without an explicit rule.", element=oid))
    candidate_triples = ({(a, b, c) for a, b, c, _ in candidates}
                         - invalid_containment - ambiguous_opposites)
    positions = {(a, b, c): position for a, b, c, position in candidates}
    for key, edge in sorted(candidates.items()):
        oid, rid, target_id, position = key
        reference = references[rid]
        opposite = reference["data"].get("opposite")
        if (oid, rid, target_id) in invalid_containment:
            unresolved.append({**edge, "reason": "MULTIPLE_CONTAINERS"})
            continue
        if (oid, rid, target_id) in ambiguous_opposites:
            unresolved.append({**edge, "reason": "OPPOSITE_MULTIPLICITY_UNQUALIFIED"})
            continue
        if opposite and (target_id, opposite, oid) not in candidate_triples:
            diagnostics.append(_diagnostic("INV-REF.OCCURRENCE_OPPOSITE_ASYMMETRIC", reference,
                f"Assertion {oid} -> {target_id} lacks its explicit opposite {opposite}; no repair was made.", element=oid))
            unresolved.append({**edge, "reason": "OPPOSITE_ASYMMETRIC"})
            continue
        ends = [(rid, oid), (opposite, target_id)] if opposite else [(rid, oid), (None, target_id)]
        if opposite:
            ends.sort()
        identity = (ends, None if opposite or reference["data"].get("unique", True) else position)
        identifier = "source-link:" + sha256(canonical_bytes(identity)).hexdigest()
        if identifier in seen:
            continue
        seen.add(identifier)
        evidence = [{"occurrence": oid, "reference": rid, "position": position}]
        if opposite and (target_id, opposite) != (oid, rid):
            evidence.append({"occurrence": target_id, "reference": opposite,
                             "position": positions[(target_id, opposite, oid)]})
        links.append({"id": identifier,
                      "left": {"occurrence": ends[0][1], "reference": ends[0][0]},
                      "right": {"occurrence": ends[1][1], "reference": ends[1][0]},
                      "evidence": sorted(evidence, key=lambda e: (e["reference"], e["occurrence"]))})
    # MOD-1G M02: retain each original declaration separately, including its
    # owner, exact target, list position and resolution state. This is a pure
    # transient witness, never a permanent source_v2 business archive.
    accepted = {(e["occurrence"], e["reference"], e["position"]):
                "RESOLVED_RECIPROCAL" if len(link["evidence"]) == 2 else "RESOLVED_SINGLE"
                for link in links for e in link["evidence"]}
    rejected = {(e["occurrence"], e.get("reference"), e.get("position")): e["reason"]
                for e in unresolved}
    declarations = []
    for occurrence in occurrences:
        for rid, raw in occurrence["values"].items():
            values = raw if isinstance(raw, list) else [raw]
            for position, value in enumerate(values):
                key = (occurrence["id"], rid, position)
                state = accepted.get(key) or rejected.get(key) or rejected.get(key[:2] + (None,))
                declarations.append({"occurrence": occurrence["id"], "class": occurrence["class"],
                    "reference": rid, "declaring_class": references.get(rid, {}).get("owner"),
                    "target": value, "position": position,
                    "state": state or ("ABSENT" if value is None else "UNRESOLVED")})
    return {"links": sorted(links, key=lambda x: x["id"]),
            "declarations": sorted(declarations, key=canonical_bytes),
            "unresolved": sorted(unresolved, key=canonical_bytes),
            "diagnostics": _ordered(diagnostics),
            "occurrence_ids": sorted(o["id"] for o in occurrences)}
