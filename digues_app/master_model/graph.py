"""Explicit dependency graph. No name matching and no inferred business edges."""

from __future__ import annotations

from collections import defaultdict, deque


def build_dependencies(model: dict) -> list[dict]:
    edges: set[tuple[str, str, str]] = set()

    def add(dependent, dependency, role):
        if dependent and dependency:
            edges.add((dependent, dependency, role))

    def provenance(item):
        for evidence in item.get("provenance", []):
            add(item["id"], evidence["source"], "provenance")

    for fact in model["historical"]["facts"]:
        provenance(fact)
        add(fact["id"], fact.get("owner"), "historical-owner")
        for dependency in fact["depends_on"]:
            add(fact["id"], dependency, "historical-declaration")
    registry = model["registry"]
    geometric_targets = {t["id"] for t in registry["targets"] if t.get("geometry") is not None}
    for collection in ("scopes", "decisions", "targets", "rules", "bindings", "projections",
                       "contracts", "limitations", "correspondences", "mapping_rules", "obligations",
                       "inventory", "inventory_limitations"):
        for item in registry.get(collection, []):
            provenance(item)
            for field in ("depends_on", "decision_refs", "sources", "rules", "bindings", "subjects", "supersedes", "superseded_by"):
                values = item.get(field, [])
                for dependency in ([values] if isinstance(values, str) else values):
                    add(item["id"], dependency, field)
            for field in ("parent", "target", "scope", "preservation_contract"):
                add(item["id"], item.get(field), field)
            if collection == "inventory":
                add(item["id"], item.get("owner"), "candidate-owner")
                details = item["details"]
                add(item["id"], details.get("catalog"), "candidate-catalog")
                for endpoint in details.get("endpoints", []):
                    add(item["id"], endpoint.get("target"), "candidate-endpoint:" + endpoint["role"])
                for support in item["binding"]["supports"]:
                    add(item["id"], support["source"], "candidate-physical-source")
            if "value_type" in item:
                add(item["id"], item["value_type"].get("reference"), "attribute-value-type")
            if collection == "correspondences":
                add(item["id"], item["source"], "correspondence-source")
            if collection == "mapping_rules":
                for field in ("source", "target", "source_owner", "target_owner", "source_type", "contract"):
                    add(item["id"], item["parameters"].get(field), "mapping-parameter:" + field)
            for owner in item.get("owners", []):
                add(item["id"], owner, "owner")
            for output in item.get("outputs", []):
                add(item["id"], output["target"], "output:" + output["role"])
            for endpoint in item.get("endpoints", []):
                add(item["id"], endpoint.get("target"), "endpoint:" + endpoint["role"])
                for allowed_id in endpoint.get("allowed_targets", []):
                    add(item["id"], allowed_id, "endpoint:" + endpoint["role"])
            if collection == "decisions":
                for succession in item.get("supersessions", []):
                    add(item["id"], succession["decision"], "decision-supersession")
                # Source facts constrain these decisions. Target assertions
                # run in the other direction (targets cite their decisions),
                # so adding target-subject edges would create false cycles.
                for assertion in item.get("assertions", []):
                    predicate, value = assertion["predicate"], assertion["value"]
                    if predicate not in {"source_reference_type", "projection_parent",
                                         "direct_photo_creation", "forbid_automatic_output"}:
                        continue
                    add(item["id"], assertion["subject"], "approved-source-assertion")
                    if predicate == "source_reference_type":
                        add(item["id"], value, "asserted-source-type")
                    elif predicate == "projection_parent":
                        add(item["id"], value["reference"], "projection-source-parent")
                    elif predicate == "direct_photo_creation":
                        for field in ("direct_reference", "direct_owner", "date_source",
                                      "coordinate_source", "media_source"):
                            add(item["id"], value[field], "photo-source:" + field)
                    else:
                        for forbidden in value:
                            add(item["id"], forbidden, "forbidden-target-output")
            policy = item.get("reference_policy", {})
            for field in ("parent_source", "parent_target"):
                add(item["id"], policy.get(field), field)
            geometry = item.get("geometry")
            if geometry is not None:
                geometry_id = "geometry:" + item["id"]
                add(geometry_id, item["id"], "geometry-owner")
                add(geometry_id, geometry.get("contract"), "geometry-contract")
            if collection == "bindings":
                add(item["id"], item["source"], "physical-source")
            if collection == "contracts" and item.get("kind") == "core_berge_lit_projection":
                parameters = item["parameters"]
                consumed = {parameters["berge"]["target"],
                            *[s["target"] for s in parameters["structures"]],
                            *[a[key] for a in parameters["associations"]
                              for key in ("source_target", "destination_target")]}
                for target in sorted(consumed & geometric_targets):
                    add(item["id"], "geometry:" + target, "projection-geometry-contract")
    for mapping in model["mappings"]:
        node = "mapping:" + mapping["source"]
        add(node, mapping["source"], "mapped-source")
        for rule in mapping["rules"]:
            add(node, rule, "mapping-rule")
        for dependency in mapping.get("depends_on", []):
            add(node, dependency, "mapping-proof")
        for target in mapping.get("targets", []):
            add(node, target, "mapping-target")
        for evidence in mapping.get("provenance", []):
            add(node, evidence["source"], "mapping-provenance")
    return [{"dependent": a, "dependency": b, "role": role}
            for a, b, role in sorted(edges)]


def impact(model: dict, identifier: str) -> dict:
    """Return direct/transitive dependents from explicitly declared edges.

    The graph is derived on demand from source facts and approved/manual
    declarations. No inferred business relationship is introduced.
    """
    dependencies = build_dependencies(model)
    reverse = defaultdict(list)
    nodes = {s["id"] for s in model["manifest"]["sources"]}
    if model["manifest"].get("registry_provenance"):
        nodes.add(model["manifest"]["registry_provenance"])
    nodes.update(model["manifest"].get("additional_registry_provenance", []))
    nodes.update(f["id"] for f in model["historical"]["facts"])
    for collection in ("scopes", "decisions", "targets", "rules", "bindings", "projections",
                       "contracts", "limitations", "correspondences", "mapping_rules", "obligations",
                       "inventory", "inventory_limitations"):
        nodes.update(i["id"] for i in model["registry"].get(collection, []))
    for edge in dependencies:
        nodes.update((edge["dependent"], edge["dependency"]))
        reverse[edge["dependency"]].append(edge["dependent"])
    if identifier not in nodes:
        raise ValueError(f"INV-REF.UNKNOWN_IMPACT_ID: {identifier}")
    distance = {identifier: 0}
    pending = deque([identifier])
    while pending:
        dependency = pending.popleft()
        for dependent in sorted(reverse[dependency]):
            if dependent not in distance:
                distance[dependent] = distance[dependency] + 1
                pending.append(dependent)
    affected = set(distance) - {identifier}
    return {
        "id": identifier,
        "input_digest": model["input_digest"],
        "model_digest": model["model_digest"],
        "direct": sorted(i for i in affected if distance[i] == 1),
        "transitive": sorted(i for i in affected if distance[i] > 1),
        "rules": sorted(i for i in affected if i.startswith(("da:rule:", "mapping-rule:"))),
        "bindings": sorted(i for i in affected if i.startswith("binding:")),
        "obligations": sorted(i for i in affected if i.startswith("obligation:")),
        "projections": sorted(i for i in affected if i.startswith("projection:")),
        "contracts": sorted(i for i in affected if i.startswith("contract:")),
        "limitations": sorted(i for i in affected if i.startswith("limitation:")),
        "correspondences": sorted(i for i in affected if i.startswith("correspondence:")),
        "edges": [e for e in dependencies
                  if e["dependent"] in affected and e["dependency"] in distance],
    }
