"""Logical preservation proof for declared vertical rules, NOT a SQL migrator.

Input values use source property handles, not presumed canonical attributes.
Pending attribute bindings remain pending even when every byte is preserved.
No decision concerning the four open B4 relations is executed here.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy

from .common import canonical_bytes, digest
from .geometry_contracts import MORPHOLOGY_TARGETS, preservation_geometry_supported


def _project_preservation(model: dict, corpus: dict) -> dict:
    if (not isinstance(corpus, dict) or set(corpus) != {"dataset", "occurrences"}
            or not isinstance(corpus["dataset"], str) or not corpus["dataset"]):
        raise ValueError("INV-ID.INVALID_CORPUS_NAMESPACE")
    if not isinstance(corpus["occurrences"], list):
        raise ValueError("INV-REF.INVALID_OCCURRENCES")
    rules = defaultdict(list)
    for rule in model["registry"]["rules"]:
        for source in rule["sources"]:
            rules[source].append(rule)
    records = {}
    diagnostics = []
    outputs = {}
    unprojected = []
    decisions = {d["id"]: d for d in model["registry"]["decisions"]}
    targets = {target['id']: target for target in model['registry']['targets']}

    def diagnostic(code, record, rule=None):
        diagnostics.append({"code": code, "element_id": record["id"],
                            "source_class": record["class"], "rule_id": rule["id"] if rule else None,
                            "provenance": rule["provenance"] if rule else [],
                            "input_digest": model["input_digest"]})

    for record in corpus["occurrences"]:
        if (not isinstance(record, dict) or set(record) != {"id", "class", "values"}
                or not isinstance(record["id"], str) or not record["id"]
                or not isinstance(record["class"], str) or not isinstance(record["values"], dict)):
            raise ValueError("INV-REF.INVALID_OCCURRENCE")
        if record["id"] in records:
            raise ValueError("INV-ID.OCCURRENCE_COLLISION: " + record["id"])
        records[record["id"]] = record
    for identifier, record in sorted(records.items()):
        candidates = rules[record["class"]]
        if len(candidates) != 1:
            diagnostic("INV-MAP.NO_RULE" if not candidates else "INV-MAP.CONFLICT", record)
            unprojected.append(deepcopy(record))
            continue
        rule = candidates[0]
        if (rule["status"] not in {"DECIDE", "CONSEQUENCE_NECESSAIRE"}
                or any(decisions.get(d, {}).get("status") not in {"DECIDE", "CONSEQUENCE_NECESSAIRE"}
                       for d in rule["decision_refs"])):
            diagnostic("INV-MAP.UNAPPROVED_LOGICAL_RULE", record, rule)
            unprojected.append(deepcopy(record))
            continue
        if (rule["identity_policy"] != "preserve_occurrences_no_merge"
                or rule["field_policy"] != "preserve_all_pending_target_attributes"
                or not preservation_geometry_supported(rule, targets)
                or len(rule["outputs"]) != 1
                or rule["outputs"][0]["operation"] != "preserve_occurrence"):
            diagnostic("INV-MAP.UNSUPPORTED_LOGICAL_RULE", record, rule)
            unprojected.append(deepcopy(record))
            continue
        output = rule["outputs"][0]
        outputs[identifier] = {
            "logical_key": [corpus["dataset"], identifier, output["role"]],
            "target": output["target"], "rule": rule["id"],
            "source_class": record["class"],
            "source_values": deepcopy(record["values"]),
            "parent": None, "parent_resolved": True,
            "qualification": "LOGICAL_ONLY_TARGET_ATTRIBUTES_AND_SQL_UNQUALIFIED",
        }
    dependencies = {identifier: set() for identifier in outputs}
    rule_index = {r["id"]: r for r in model["registry"]["rules"]}
    for identifier, output in sorted(outputs.items()):
        record = records[identifier]
        rule = rule_index[output["rule"]]
        policy = rule["reference_policy"]
        if policy["parent_source"] is None:
            continue
        parent = record["values"].get(policy["parent_source"])
        candidates = parent if isinstance(parent, list) else ([] if parent is None or parent == "" else [parent])
        code = None
        if not candidates:
            if (output["target"] in MORPHOLOGY_TARGETS and
                    policy.get("valid_parent_cardinality") == {"min": 0, "max": 1} and
                    decisions.get("decision:MOD-1O-L02-B", {}).get("status") in
                    {"DECIDE", "CONSEQUENCE_NECESSAIRE"}):
                # L02-B: absence is allowed; only an explicit, singular and
                # compatible reference may establish the optional association.
                continue
            code = "PARENT_MISSING"
        elif len(candidates) != 1:
            code = "PARENT_AMBIGUOUS"
        elif not isinstance(candidates[0], str) or candidates[0] not in outputs:
            code = "PARENT_UNRESOLVED"
        elif outputs[candidates[0]]["target"] != policy["parent_target"]:
            code = "PARENT_INCOMPATIBLE"
        if code:
            output["parent_resolved"] = False
            diagnostic(code, record, rule)
        else:
            parent_id = candidates[0]
            output["parent"] = outputs[parent_id]["logical_key"]
            dependencies[identifier].add(parent_id)
    # Reproducible topological order, without inferring any undeclared edge.
    insertion_order = []
    pending = {key: set(value) for key, value in dependencies.items()}
    while pending:
        ready = sorted(key for key, value in pending.items() if not value)
        if not ready:
            raise ValueError("INV-REF.PROJECTION_CYCLE")
        for identifier in ready:
            insertion_order.append(identifier)
            for parent_id in dependencies[identifier]:
                if not outputs[parent_id]["parent_resolved"]:
                    outputs[identifier]["parent_resolved"] = False
                    diagnostic("PARENT_UNQUALIFIED", records[identifier], rule_index[outputs[identifier]["rule"]])
            del pending[identifier]
        for remaining in pending.values():
            remaining.difference_update(ready)
    result = {
        "status": "LOGICAL_PROOF_ONLY", "certified": False,
        "input_digest": model["input_digest"],
        "corpus_digest": digest({"dataset": corpus["dataset"], "occurrences": sorted(corpus["occurrences"], key=lambda r: r["id"])}),
        "outputs": [outputs[key] for key in sorted(outputs)],
        "unprojected": sorted(unprojected, key=lambda r: r["id"]),
        "insertion_order": [outputs[key]["logical_key"] for key in insertion_order],
        "diagnostics": sorted(diagnostics, key=canonical_bytes),
    }
    result["digest"] = digest(result)
    return result


def project_logical(model: dict, corpus: dict) -> dict:
    """Use the acquired MOD-1M contract within the existing logical engine."""
    from .occurrence_projection import CONTRACT_ID, project_core_berge_lit
    contracts = [c for c in model["registry"].get("contracts", []) if c["id"] == CONTRACT_ID]
    if not contracts:
        return _project_preservation(model, corpus)
    if (not isinstance(corpus, dict) or set(corpus) != {"dataset", "occurrences"}
            or not isinstance(corpus["dataset"], str) or not corpus["dataset"]
            or not isinstance(corpus["occurrences"], list)):
        raise ValueError("INV-ID.INVALID_CORPUS_NAMESPACE")
    seen = set()
    for record in corpus["occurrences"]:
        if (not isinstance(record, dict) or set(record) != {"id", "class", "values"}
                or not isinstance(record["id"], str) or not record["id"]
                or not isinstance(record["class"], str) or not isinstance(record["values"], dict)):
            raise ValueError("INV-REF.INVALID_OCCURRENCE")
        if record["id"] in seen:
            raise ValueError("INV-ID.OCCURRENCE_COLLISION: " + record["id"])
        seen.add(record["id"])
    if len(contracts) != 1:
        raise ValueError("INV-ID.PROJECTION_CONTRACT_COLLISION")
    return project_core_berge_lit(model, corpus, _project_preservation)
