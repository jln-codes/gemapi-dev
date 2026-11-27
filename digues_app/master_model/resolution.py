"""Individual, deterministic dispositions of declared historical facts.

No name is matched to a target name, and no SQL name participates. A source
value kept for later qualification is not a migrated target value. Operators
are enabled by versioned registry records; their applications retain both the
declaration and the class context that made them applicable.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy

from .geometry_contracts import MORPHOLOGY_TARGETS, geometry_obligations, preservation_geometry_supported


APPROVED = frozenset({"DECIDE", "CONSEQUENCE_NECESSAIRE"})
EVIDENCE_KINDS = frozenset({"observation", "contract_mapping", "inheritance",
                           "datatype", "external_type", "operation", "parameter"})
_SEMANTIC_SIGNATURE = ("type", "lower", "upper", "containment", "opposite", "ordered", "unique",
                       "default_value", "annotations")
_SIGNATURE = (*_SEMANTIC_SIGNATURE, "declared_in", "raw_type")


def _provenance(*items):
    return [{"source": source, "locator": locator} for source, locator in sorted({
        (p["source"], p["locator"]) for item in items
        for p in item.get("provenance", [])})]


def resolve_mappings(registry: dict, historical: dict) -> list[dict]:
    """Resolve every fact once; retain every conflict and outstanding obligation.

    Specific rules supply candidates first. A generic candidate never erases a
    different approved destination. ``supersedes`` is intentionally fail-closed:
    the current decision language contains no assertion proving a replacement.
    """
    facts = {f["id"]: f for f in historical.get("facts", [])}
    targets = {t["id"]: t for t in registry.get("targets", [])}
    decisions = {d["id"]: d for d in registry.get("decisions", [])}
    from .decision_successions import resolve_decision_assertions
    active_assertions = resolve_decision_assertions(registry)["assertions"]
    contracts = {c["id"]: c for c in registry.get("contracts", [])}
    candidates = defaultdict(list)
    rejected = defaultdict(list)
    generic = sorted(registry.get("mapping_rules", []), key=lambda r: r["id"])
    by_operator = defaultdict(list)
    for rule in generic:
        by_operator[rule["operator"]].append(rule)
    known_rules = {r["id"]: r for r in generic}
    known_rules.update({r["id"]: r for r in registry.get("rules", [])})
    known_rules.update({r["id"]: r for r in registry.get("correspondences", [])})
    known_rules.update(contracts)

    def approved(item):
        return (item.get("status") in APPROVED and all(
            decisions.get(identifier, {}).get("status") in APPROVED
            for identifier in item.get("decision_refs", [])))

    def ancestors(identifier, index, field):
        seen, pending = set(), [identifier]
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            node = index.get(current, {})
            parents = (node.get("data", {}).get(field, []) if field == "super_types" else
                       [node.get(field)] if node.get("kind") in {"type", "subtype"} else [])
            pending.extend(p for p in parents if p and p not in seen)
        return seen

    source_ancestors = {f["id"]: ancestors(f["id"], facts, "super_types")
                        for f in facts.values() if f["kind"] == "class"}
    target_ancestors = {t: ancestors(t, targets, "parent") for t in targets}
    declarations_by_name = defaultdict(list)
    for fact in facts.values():
        if fact["kind"] in {"attribute", "reference"}:
            declarations_by_name[fact["name"]].append(fact)

    def context_dependencies(fact):
        """Dependencies of a resolution, including currently unmet premises."""
        dependencies = set()
        owner = fact.get("owner")
        if owner in facts:
            dependencies.add("mapping:" + owner)
        if fact["kind"] == "application":
            declaration = fact["data"].get("declaration")
            if declaration in facts:
                dependencies.add("mapping:" + declaration)
            dependencies.update(rule["id"] for rule in by_operator["application"])
        if fact["kind"] in {"reference", "application"}:
            destination = fact["data"].get("type")
            if facts.get(destination, {}).get("kind") == "class":
                dependencies.add("mapping:" + destination)
        return dependencies

    def make(fact, rule, *, outcome, qualification="PARTIAL", destinations=(),
             obligations=(), dependencies=(), classification="PROVABLE_MAPPING", reason="",
             evidence=()):
        items = [fact, rule, *evidence]
        return {"source": fact["id"], "outcome": outcome, "qualification": qualification,
                "rules": sorted({item["id"] for item in items[1:] if item.get("id") in known_rules}),
                "targets": sorted(set(destinations)), "reason": reason,
                "provenance": _provenance(*items),
                "depends_on": sorted({fact["id"], rule["id"], *dependencies,
                                      *context_dependencies(fact),
                                      *rule.get("decision_refs", []), *rule.get("depends_on", [])}),
                "obligations": sorted(set(obligations)), "classification": classification}

    def reject(fact, rule, code, *, destinations=(), dependencies=(), evidence=()):
        rejected[fact["id"]].append(make(fact, rule, outcome="UNRESOLVED",
            qualification="UNRESOLVED", destinations=destinations, obligations=[code],
            dependencies=dependencies, classification="ENGINE_GAP",
            reason="An explicit candidate failed an applicability guard.", evidence=evidence))

    def add(fact, rule, **kwargs):
        if not approved(rule):
            reject(fact, rule, "RULE_NOT_APPROVED")
            return
        if rule.get("version") != "1":
            reject(fact, rule, "RULE_VERSION_UNSUPPORTED")
            return
        if rule.get("supersedes"):
            reject(fact, rule, "RULE_SUPERSESSION_UNPROVEN", dependencies=rule["supersedes"])
            return
        result = make(fact, rule, **kwargs)
        if any(t not in targets for t in result["targets"]):
            reject(fact, rule, "TARGET_MISSING", destinations=result["targets"])
            return
        if any(targets[t].get("status") not in APPROVED for t in result["targets"]):
            reject(fact, rule, "TARGET_NOT_APPROVED", destinations=result["targets"])
            return
        for decision in decisions.values():
            if not approved(decision):
                continue
            for assertion in active_assertions.get(decision["id"], []):
                if (assertion["predicate"] == "forbid_automatic_output"
                        and assertion["subject"] == fact["id"]
                        and set(result["targets"]) & set(assertion["value"])):
                    result["outcome"], result["qualification"] = "CONFLICT", "CONFLICT"
                    result["obligations"] = sorted(set(result["obligations"]) | {"APPROVED_DECISION_CONFLICT"})
                    result["depends_on"] = sorted(set(result["depends_on"]) | {decision["id"]})
                    result["provenance"] = _provenance(result, decision)
        candidates[fact["id"]].append(result)

    def combined(identifier):
        possible = candidates.get(identifier, [])
        failures = rejected.get(identifier, [])
        if not possible:
            return None
        signatures = {(tuple(c["targets"]), "absent" if c["outcome"] in {
            "deferred", "removed_container"} else "target") for c in possible}
        conflict = len(signatures) > 1 or any(c["outcome"] == "CONFLICT" for c in possible)
        # A failed applicability check is never silently hidden by another rule.
        if failures:
            conflict = True
        order = {"projected": 0, "logical": 1, "derived": 2, "preserved": 3,
                 "deferred": 4, "removed_container": 4, "auxiliary": 5,
                 "UNRESOLVED": 6, "CONFLICT": 7}
        result = deepcopy(min(possible, key=lambda c: (order[c["outcome"]], c["rules"])))
        for key in ("rules", "targets", "depends_on", "obligations"):
            result[key] = sorted({v for c in possible + failures for v in c[key]})
        result["provenance"] = _provenance(*possible, *failures)
        if conflict:
            result.update(outcome="CONFLICT", qualification="CONFLICT", classification="ENGINE_GAP",
                          reason="Applicable candidates disagree or an explicit guard failed.")
            result["obligations"] = sorted(set(result["obligations"]) | {"RULE_CONFLICT"})
        return result

    def business_destinations(identifier):
        item = combined(identifier)
        if item and item["outcome"] in {"projected", "logical", "derived"}:
            return set(item["targets"])
        return set()

    def physical_obligations(destinations):
        # A declaration alone is not an occurrence projection or a SQL binding.
        # Presence/status of a binding record is not physical certification.
        return ["PHYSICAL_BINDING_UNQUALIFIED", "OCCURRENCE_PROJECTION_UNIMPLEMENTED"]

    # Existing class projection rules retain their deliberately partial scope.
    for rule in sorted(registry.get("rules", []), key=lambda r: r["id"]):
        for source in sorted(rule.get("sources", [])):
            if source in facts:
                destinations = [o["target"] for o in rule.get("outputs", [])]
                if (rule.get("identity_policy") != "preserve_occurrences_no_merge"
                        or rule.get("field_policy") != "preserve_all_pending_target_attributes"
                        or not preservation_geometry_supported(rule, targets)
                        or any(o.get("operation") != "preserve_occurrence" for o in rule.get("outputs", []))):
                    reject(facts[source], rule, "LOGICAL_RULE_POLICY_UNQUALIFIED", destinations=destinations)
                    continue
                add(facts[source], rule, outcome="projected", destinations=destinations,
                    obligations=["ATTRIBUTE_BINDINGS_UNQUALIFIED", *physical_obligations(destinations)],
                    dependencies=destinations,
                    reason="Existing logical projection; target fields and occurrence execution remain separate.")

    for operator in by_operator["disposition"]:
        for contract in sorted(contracts.values(), key=lambda c: c["id"]):
            if contract.get("kind") != "source_disposition":
                continue
            for source in contract.get("subjects", []):
                if source not in facts:
                    continue
                if not approved(contract):
                    reject(facts[source], operator, "DISPOSITION_NOT_APPROVED", dependencies=[contract["id"]])
                    continue
                outcome = contract["parameters"]["outcome"]
                add(facts[source], operator, outcome="removed_container" if outcome == "removed_container" else "deferred",
                    obligations=["EXPLICIT_CHILD_DISPOSITIONS_REQUIRED", "OCCURRENCE_DISPOSITION_UNIMPLEMENTED"],
                    dependencies=[contract["id"], *contract.get("decision_refs", [])], evidence=[contract],
                    reason="Exact approved source disposition; no propagation to children or incoming references.")

    for operator in by_operator["correspondence"]:
        for correspondence in sorted(registry.get("correspondences", []), key=lambda c: c["id"]):
            source = correspondence["source"]
            if source not in facts:
                continue
            fact = facts[source]
            if not approved(correspondence):
                reject(fact, operator, "CORRESPONDENCE_NOT_APPROVED", dependencies=[correspondence["id"]])
                continue
            destination = correspondence["target"]
            preservation = contracts.get(correspondence["preservation_contract"])
            from .occurrence_projection import validate_berge_correspondence
            berge_equivalence = validate_berge_correspondence(registry, correspondence, historical)
            if not berge_equivalence and (not preservation or not approved(preservation) or preservation.get("kind") != "preservation"
                    or destination not in preservation.get("subjects", [])
                    or preservation.get("parameters", {}).get("identity") != "preserve"
                    or preservation.get("parameters", {}).get("merge") != "forbidden"):
                reject(fact, operator, "PRESERVATION_SCOPE_UNQUALIFIED", destinations=[destination],
                       dependencies=[correspondence["id"], correspondence["preservation_contract"]],
                       evidence=[correspondence])
                continue
            obligations = physical_obligations([destination])
            if fact["kind"] == "class":
                obligations.append("ATTRIBUTE_BINDINGS_UNQUALIFIED")
            if fact["kind"] == "catalog_value" and targets.get(destination, {}).get("kind") != "catalog":
                obligations.append("DISCRIMINATOR_GUARD_UNIMPLEMENTED")
            if fact["kind"] == "reference":
                obligations.append("REFERENCE_OCCURRENCES_UNQUALIFIED")
            add(fact, operator, outcome="logical", destinations=[destination], obligations=obligations,
                dependencies=[correspondence["id"], destination, correspondence["preservation_contract"],
                              *correspondence.get("depends_on", []), *correspondence.get("decision_refs", [])],
                evidence=[correspondence], reason="Explicit correspondence proves a logical destination, not a migrated occurrence.")

    for rule in by_operator["class_family"]:
        source, destination = rule["parameters"]["source"], rule["parameters"]["target"]
        if source not in facts:
            continue
        if facts[source]["kind"] != "class":
            reject(facts[source], rule, "FAMILY_ENDPOINT_INCOMPATIBLE", destinations=[destination])
            continue
        for identifier, lineage in sorted(source_ancestors.items()):
            fact = facts[identifier]
            if source not in lineage or fact["data"].get("abstract", False):
                continue
            if (facts[source]["data"].get("abstract") is not True
                    or targets.get(destination, {}).get("kind") != "component"):
                reject(fact, rule, "FAMILY_ENDPOINT_INCOMPATIBLE", destinations=[destination],
                       dependencies=[source], evidence=[facts[source]])
                continue
            add(fact, rule, outcome="logical", destinations=[destination],
                obligations=["ATTRIBUTE_BINDINGS_UNQUALIFIED", "OCCURRENCE_PARENT_VALIDATION",
                             *physical_obligations([destination])],
                dependencies=[source, destination, *sorted(lineage)], evidence=[facts[source]],
                classification="FORMAL_PROPAGATION",
                reason="Concrete subclass of the explicitly approved historical family.")

    class_destinations = {identifier: business_destinations(identifier) for identifier in source_ancestors}

    def property_guard(fact, rule):
        p = rule["parameters"]
        declaration = facts.get(p["source"])
        if declaration is None or declaration["kind"] != "attribute":
            return "PROPERTY_SOURCE_MISMATCH"
        if any(declaration["data"].get(key) != p[param] for key, param in (
                ("type", "source_type"), ("lower", "lower"), ("upper", "upper"))):
            return "PROPERTY_SOURCE_MISMATCH"
        target = targets.get(p["target"], {})
        if target.get("kind") != "attribute" or target.get("parent") != p["target_owner"]:
            return "PROPERTY_OWNER_INCOMPATIBLE"
        primitive_types = {"sirs-v2:datatype:ecore/EString": "string",
                           "sirs-v2:datatype:ecore/EDate": "date",
                           "sirs-v2:datatype:ecore/EBoolean": "boolean"}
        if primitive_types.get(p["source_type"]) != target.get("value_type", {}).get("kind"):
            return "PROPERTY_TYPE_UNQUALIFIED"
        if (p["source_type"] == "sirs-v2:datatype:ecore/EDate"
                and not any(annotation.get("source") == "localDate"
                            for annotation in declaration["data"].get("annotations", []))):
            return "PROPERTY_TYPE_UNQUALIFIED"
        cardinality = target.get("cardinality") or {}
        lower, upper = cardinality.get("min"), cardinality.get("max")
        source_upper = float("inf") if p["upper"] == -1 else p["upper"]
        if ((lower is not None and (not isinstance(lower, int) or lower > p["lower"]))
                or (upper is not None and upper != "*" and (
                    not isinstance(upper, int) or source_upper > upper))):
            return "PROPERTY_CARDINALITY_INCOMPATIBLE"
        owner = fact.get("owner")
        if p["source_owner"] not in source_ancestors.get(owner, set()):
            return "PROPERTY_OWNER_INCOMPATIBLE"
        owner_targets = class_destinations.get(owner, set())
        if not owner_targets:
            return "APPLICATION_OWNER_UNQUALIFIED"
        if len(owner_targets) != 1 or any(p["target_owner"] not in target_ancestors.get(t, set()) for t in owner_targets):
            return "PROPERTY_OWNER_INCOMPATIBLE"
        return None

    def application_guard(fact):
        declaration = facts.get(fact["data"].get("declaration"))
        if declaration is None or declaration["kind"] not in {"attribute", "reference"}:
            return "APPLICATION_DECLARATION_MISMATCH"
        owner = fact.get("owner")
        lineage = source_ancestors.get(owner, set())
        if declaration["owner"] not in lineage:
            return "APPLICATION_DECLARATION_MISMATCH"
        if (fact["data"].get("property_kind") != declaration["kind"]
                or any(fact["data"].get(k) != declaration["data"].get(k) for k in _SIGNATURE)):
            return "APPLICATION_DECLARATION_MISMATCH"
        # Use the exact historical declaration. A local redeclaration with the
        # same source name is evidence of shadowing, never a target-name match.
        for other in declarations_by_name[declaration["name"]]:
            if (other["id"] != declaration["id"]
                    and other.get("owner") in lineage
                    and declaration["owner"] in source_ancestors.get(other.get("owner"), set())):
                return "APPLICATION_SHADOWED_DECLARATION"
            if (other["id"] != declaration["id"] and other.get("owner") in lineage
                    and declaration["owner"] not in source_ancestors.get(other.get("owner"), set())
                    and other.get("owner") not in source_ancestors.get(declaration["owner"], set())
                    and any(other["data"].get(key) != declaration["data"].get(key)
                            for key in _SEMANTIC_SIGNATURE)):
                return "APPLICATION_INHERITANCE_AMBIGUOUS"
        return None

    for rule in by_operator["property"]:
        p = rule["parameters"]
        declaration = facts.get(p["source"])
        if declaration is None:
            continue
        # A scoped property rule does not give every use of an abstract
        # declaration the same target attribute.
        matches = ([declaration] if declaration.get("owner") == p["source_owner"] else [])
        matches += [f for f in facts.values() if f["kind"] == "application"
                    and f["data"].get("declaration") == p["source"]
                    and p["source_owner"] in source_ancestors.get(f.get("owner"), set())]
        for fact in matches:
            error = (application_guard(fact) if fact["kind"] == "application" else None) or property_guard(fact, rule)
            deps = [p["source"], p["source_owner"], p["target_owner"], p["target"], p["source_type"]]
            if error:
                reject(fact, rule, error, destinations=[p["target"]], dependencies=deps, evidence=[declaration])
            else:
                target_cardinality = targets[p["target"]].get("cardinality") or {}
                obligations = physical_obligations([p["target"]])
                if target_cardinality.get("min") is None or target_cardinality.get("max") is None:
                    obligations.append("PROPERTY_CARDINALITY_UNQUALIFIED")
                add(fact, rule, outcome="derived" if fact["kind"] == "application" else "logical",
                    destinations=[p["target"]], obligations=obligations,
                    dependencies=deps, evidence=[declaration],
                    classification=("FORMAL_PROPAGATION" if fact["kind"] == "application" else
                                    "PROVABLE_MAPPING" if "PROPERTY_CARDINALITY_UNQUALIFIED" in obligations else
                                    "PHYSICAL_UNKNOWN"),
                    reason="Exact declared property, source class context, source type and target owner verified.")

    # The B2/FM-13 rules already decide a context-specific owner projection.
    # That decision does not map generic Objet.linearId for every subclass.
    for operator in by_operator["application"]:
        for rule in sorted(registry.get("rules", []), key=lambda r: r["id"]):
            policy = rule.get("reference_policy", {})
            declaration_id = policy.get("parent_source")
            if not declaration_id or declaration_id not in facts:
                continue
            for fact in sorted(facts.values(), key=lambda f: f["id"]):
                if (fact["kind"] != "application" or fact.get("owner") not in rule.get("sources", [])
                        or fact["data"].get("declaration") != declaration_id
                        or fact["data"].get("property_kind") != "reference"):
                    continue
                destinations = class_destinations.get(fact["owner"], set())
                dependency = [declaration_id, policy.get("parent_target"), rule["id"], fact["owner"],
                              "mapping:" + fact["owner"]]
                error = application_guard(fact)
                if not approved(rule) or rule.get("version") != "1":
                    error = "PARENT_RULE_UNQUALIFIED"
                if (len(destinations) != 1
                        or destinations != {o["target"] for o in rule.get("outputs", [])}):
                    error = "PARENT_RULE_UNQUALIFIED"
                for destination in destinations:
                    target = targets.get(destination, {})
                    if (destination in MORPHOLOGY_TARGETS and
                            target.get('parent') == 'da:concept:element-morphologique-lit'):
                        # The four types share their family's one optional
                        # relation; subtype records do not duplicate ownership.
                        dependency.append(target['parent'])
                        target = targets.get(target['parent'], {})
                    if (policy.get("parent_target") not in target.get("owners", [])
                            or target.get("owner_cardinality") != policy.get("valid_parent_cardinality")
                            or any(policy.get(field) != "diagnose_no_invention"
                                   for field in ("missing", "incompatible", "ambiguous"))):
                        error = "PARENT_RULE_UNQUALIFIED"
                if error:
                    reject(fact, operator, error, dependencies=[d for d in dependency if d],
                           evidence=[rule, facts[declaration_id]])
                    continue
                add(fact, operator, outcome="derived", destinations=destinations,
                    obligations=["OCCURRENCE_PARENT_VALIDATION", *physical_obligations(destinations)],
                    dependencies=dependency, evidence=[rule, facts[declaration_id]],
                    classification="FORMAL_PROPAGATION",
                    reason="Exact class-scoped approved parent policy; destination is its ownership role, not a new relation identity.")

    if by_operator["composition"] or any(f["kind"] == "reference" and candidates.get(f["id"]) for f in facts.values()):
        from .relation_checks import evaluate_reference

        for fact in sorted(facts.values(), key=lambda f: f["id"]):
            if fact["kind"] != "reference":
                continue
            for candidate in candidates.get(fact["id"], []):
                for target_id in candidate["targets"]:
                    if targets.get(target_id, {}).get("kind") != "relation":
                        continue
                    check = evaluate_reference(fact, facts, targets, class_destinations, target_id=target_id)
                    candidate["depends_on"] = sorted(set(candidate["depends_on"]) | set(check["depends_on"]))
                    candidate["obligations"] = sorted(set(candidate["obligations"]) | set(check["obligations"]))
                    if not check["applicable"]:
                        candidate.update(outcome="UNRESOLVED", qualification="UNRESOLVED", classification="ENGINE_GAP")
            if not fact["data"].get("containment"):
                continue
            for rule in by_operator["composition"]:
                check = evaluate_reference(fact, facts, targets, class_destinations)
                if check["applicable"]:
                    add(fact, rule, outcome="logical", destinations=check["targets"],
                        obligations=[*check["obligations"], *physical_obligations(check["targets"])],
                        dependencies=check["depends_on"], classification="FORMAL_PROPAGATION",
                        reason="Containment, mapped endpoints, allowed owner and cardinalities are compatible.")
                elif check["targets"]:
                    for code in check["obligations"]:
                        reject(fact, rule, code, destinations=check["targets"], dependencies=check["depends_on"])

    for rule in by_operator["application"]:
        for fact in sorted(facts.values(), key=lambda f: f["id"]):
            if fact["kind"] != "application":
                continue
            declaration = facts.get(fact["data"].get("declaration"))
            base = combined(declaration["id"]) if declaration else None
            if not base or base["outcome"] not in {"logical", "projected", "derived"}:
                continue
            error = application_guard(fact)
            if not error and not class_destinations.get(fact.get("owner")):
                error = "APPLICATION_OWNER_UNQUALIFIED"
            if error:
                reject(fact, rule, error, dependencies=[declaration["id"], "mapping:" + declaration["id"]])
                continue
            owner_targets = class_destinations[fact["owner"]]
            destination_targets = [targets[t] for t in base["targets"] if t in targets]
            if any(t["kind"] == "attribute" and not all(t.get("parent") in target_ancestors.get(o, set())
                   for o in owner_targets) for t in destination_targets):
                reject(fact, rule, "PROPERTY_OWNER_INCOMPATIBLE", destinations=base["targets"])
                continue
            # Recheck relation/composition endpoints in the subclass context.
            extra_obligations, extra_dependencies = [], []
            if fact["data"].get("property_kind") == "reference":
                from .relation_checks import evaluate_reference
                valid = True
                for target in destination_targets:
                    check = evaluate_reference(fact, facts, targets, class_destinations,
                        target_id=target["id"] if target["kind"] == "relation" else None)
                    extra_obligations += check["obligations"]
                    extra_dependencies += check["depends_on"]
                    valid = valid and check["applicable"] and set(check["targets"]) == set(base["targets"])
                if not valid:
                    reject(fact, rule, "APPLICATION_OWNER_INCOMPATIBLE", destinations=base["targets"],
                           dependencies=extra_dependencies, evidence=[declaration])
                    continue
            add(fact, rule, outcome="derived", destinations=base["targets"],
                obligations=[*base["obligations"], *extra_obligations],
                dependencies=[declaration["id"], "mapping:" + declaration["id"],
                              "mapping:" + fact["owner"], *source_ancestors.get(fact["owner"], set()),
                              *base["rules"], *base["targets"], *extra_dependencies],
                evidence=[declaration, base], classification="FORMAL_PROPAGATION",
                reason="Actual effective declaration and class context satisfy the qualified property rule.")

    for operator in ("identity", "geometry"):
        for rule in by_operator[operator]:
            contract = contracts.get(rule["parameters"]["contract"])
            for fact in sorted(facts.values(), key=lambda f: f["id"]):
                if fact["kind"] != operator:
                    continue
                destination = class_destinations.get(fact.get("owner"), set())
                if not destination:
                    continue
                if (not contract or not approved(contract) or contract.get("kind") != "preservation"
                        or not destination <= set(contract.get("subjects", []))):
                    reject(fact, rule, "PRESERVATION_SCOPE_UNQUALIFIED", destinations=destination)
                    continue
                obligations = (["RUNTIME_IDENTITY_UNQUALIFIED"] if operator == "identity" else
                               geometry_obligations([targets[t] for t in sorted(destination)]))
                geometry_dependencies = (["geometry:" + t for t in sorted(destination)
                                          if targets[t].get("geometry") is not None]
                                         if operator == "geometry" else [])
                add(fact, rule, outcome="preserved", qualification="PRESERVED",
                    obligations=[*obligations, "SOURCE_PRESERVATION_NOT_MIGRATION"],
                    dependencies=[fact["owner"], "mapping:" + fact["owner"], contract["id"], *destination,
                                  *fact.get("depends_on", []), *geometry_dependencies], evidence=[contract],
                    classification="FORMAL_PROPAGATION",
                    reason="Source identity/geometry remains exact; no canonical value or geometry transform is asserted.")

    for rule in by_operator["catalog_membership"]:
        p = rule["parameters"]
        catalog, destination = facts.get(p["source"]), targets.get(p["target"])
        if catalog is None:
            continue
        if catalog["kind"] != "catalog" or not destination or destination.get("kind") != "catalog":
            reject(catalog, rule, "CATALOG_ENDPOINT_INCOMPATIBLE")
            continue
        add(catalog, rule, outcome="logical", destinations=[destination["id"]],
            obligations=["CATALOG_EQUIVALENCE_UNQUALIFIED", *physical_obligations([destination["id"]])],
            dependencies=[destination["id"]], reason="Explicit source catalogue domain, not label-based value equivalence.")
        for fact in facts.values():
            if fact.get("owner") != catalog["id"] or fact["kind"] not in {"catalog_value", "catalog_attribute", "catalog_relation"}:
                continue
            # A discriminator does not become a catalogue value correspondence.
            if candidates.get(fact["id"]):
                continue
            add(fact, rule, outcome="preserved", qualification="PRESERVED",
                obligations=["CATALOG_MEMBER_TARGET_UNQUALIFIED", "SOURCE_PRESERVATION_NOT_MIGRATION"],
                dependencies=[catalog["id"], destination["id"], "mapping:" + catalog["id"]], evidence=[catalog],
                classification="FORMAL_PROPAGATION",
                reason="Exact source member including missing/undefined values retained; no canonical value or code equivalence invented.")

    for rule in by_operator["evidence"]:
        requested = set(rule["parameters"].get("kinds", []))
        for fact in facts.values():
            if fact["kind"] not in requested:
                continue
            if fact["kind"] not in EVIDENCE_KINDS:
                reject(fact, rule, "EVIDENCE_KIND_NOT_AUXILIARY")
                continue
            obligations = []
            if fact["kind"] in {"operation", "parameter"}:
                obligations.append("SOURCE_BEHAVIOR_UNQUALIFIED")
            if fact["kind"] == "observation":
                obligations.append("MOCK_NOT_EXHAUSTIVE")
                from .relation_checks import observation_diagnostics
                obligations += [d["code"] for d in observation_diagnostics(fact)]
            add(fact, rule, outcome="auxiliary", qualification="AUXILIARY", obligations=obligations,
                dependencies=fact.get("depends_on", []), classification="AUXILIARY_FACT",
                reason="Extraction or declaration evidence retained individually; not a business occurrence to migrate.")

    results = []
    for identifier, fact in sorted(facts.items()):
        result = combined(identifier)
        if result is None:
            failures = rejected.get(identifier, [])
            dependencies = set(fact.get("depends_on", [])) | context_dependencies(fact)
            if fact.get("owner"):
                dependencies.add(fact["owner"])
            candidate_targets = class_destinations.get(fact.get("owner"), set())
            classification = "BUSINESS_UNKNOWN"
            obligations = {"CANONICAL_DESTINATION_UNQUALIFIED"}
            reason = "No approved unique canonical disposition exists for this fact."
            if fact["kind"] == "application":
                classification = "FORMAL_PROPAGATION"
                obligations = {"DECLARATION_MAPPING_UNQUALIFIED", "APPLICATION_CONTEXT_UNQUALIFIED"}
                reason = "Derived application awaits a qualified declaration and compatible owner context."
            elif candidate_targets:
                classification = "BUSINESS_UNKNOWN"
                obligations = {"TARGET_FIELD_OR_ROLE_UNQUALIFIED"}
                reason = "Owner destination is known; this field or role has no approved unique correspondence."
            if failures:
                classification = "ENGINE_GAP"
                reason = "Explicit candidate is blocked by an applicability guard."
            result = {"source": identifier, "outcome": "UNRESOLVED", "qualification": "UNRESOLVED",
                "rules": sorted({r for f in failures for r in f["rules"]}),
                "targets": sorted({t for f in failures for t in f["targets"]}), "reason": reason,
                "provenance": _provenance(fact, *failures),
                "depends_on": sorted(dependencies | {d for f in failures for d in f["depends_on"]}),
                "obligations": sorted(obligations | {o for f in failures for o in f["obligations"]}),
                "classification": classification}
        results.append(result)
    return results


def mapping_summary(mappings: list[dict], historical: dict) -> dict:
    """Exact partitions and multivalued dependency counts, without LLM labels.

    ``classification``/``kind``/``model``/``owner`` are partitions. Obligation,
    candidate-target and dependency counts are incidences and are not additive.
    """
    facts = {f["id"]: f for f in historical.get("facts", [])}
    mapping_index = {m["source"]: m for m in mappings}

    def owner_candidates(mapping):
        owner = facts.get(mapping["source"], {}).get("owner")
        parent = mapping_index.get(owner, {})
        if parent.get("outcome") in {"projected", "logical", "derived"}:
            return parent.get("targets", [])
        return []

    def counts(values):
        return dict(sorted(Counter(values).items()))

    def group(items):
        return {
            "total": len(items),
            "kind": counts(facts[m["source"]]["kind"] for m in items if m["source"] in facts),
            "model": counts(facts[m["source"]]["model"] for m in items if m["source"] in facts),
            "owner": counts(facts[m["source"]].get("owner") or "<none>" for m in items if m["source"] in facts),
            "outcome": counts(m["outcome"] for m in items),
            "qualification": counts(m["qualification"] for m in items),
            "classification": counts(m["classification"] for m in items),
            "obligation": counts(code for m in items for code in m["obligations"]),
            "candidate_target": counts(target for m in items for target in m["targets"]),
            "owner_candidate_target": counts(target for m in items for target in owner_candidates(m)),
            "dependency": counts(dependency for m in items for dependency in m["depends_on"]),
            "unresolved_dependency": counts(dependency for m in items for dependency in m["depends_on"]
                if mapping_index.get(dependency.removeprefix("mapping:"), {}).get("outcome") in {"UNRESOLVED", "CONFLICT"}),
            "without_target": sum(not m["targets"] for m in items),
            "without_target_or_known_owner": sum(not m["targets"] and not owner_candidates(m) for m in items),
            "without_obligation": sum(not m["obligations"] for m in items),
        }
    return {"all": group(mappings),
            "unresolved": group([m for m in mappings if m["outcome"] == "UNRESOLVED"]),
            "conflicts": group([m for m in mappings if m["outcome"] == "CONFLICT"])}
