"""Read-only, reproducible facts from the eight historical Ecore and C1/C2/C3.

This module deliberately does not infer target concepts, relations or SQL bindings.
MOD-1R is a comparison/observation source, never the theoretical universe. Existing
Ecore and contract readers are reused; raw XML guards cover their lossy boundaries
(duplicate declarations, enums, interfaces, operations and external datatypes).
"""

from __future__ import annotations

from collections import Counter, defaultdict
import csv
from pathlib import Path
from xml.etree import ElementTree as ET

from digues_app.model_manifest import XSI_TYPE, parse_ecore, resolve_effective_fields
from digues_app.references.validation import validate_contracts
from digues_app.structure_contract import validate_structure_contract

from .common import canonical_bytes, file_digest, source_id, strict_load


MODELS = frozenset({"core", "lit", "berge", "vegetation", "dependance",
                    "aot-cot", "reglementary", "carto"})
C3_MODELS = MODELS - {"aot-cot", "reglementary", "carto"}


def _require(condition, code, detail):
    if not condition:
        raise ValueError(f"{code}: {detail}")


def _provenance(source, locator):
    return {"source": source, "locator": locator}


def _fact(kind, model, parts, *, owner=None, data=None, deps=(), source, locator):
    return {"id": source_id(kind, model, *parts), "kind": kind, "model": model,
            "name": parts[-1], "owner": owner, "data": data or {},
            "depends_on": sorted(set(deps)),
            "provenance": [_provenance(source, locator)]}


def _xml(element):
    """Lossless XML information relevant here, excluding formatting whitespace."""
    return {"tag": element.tag, "attributes": dict(element.attrib),
            "text": element.text.strip() if element.text and element.text.strip() else None,
            "children": [_xml(child) for child in element]}


def _compare(identifier, expected, actual, source):
    missing, unexpected = sorted(expected - actual), sorted(actual - expected)
    return {"id": identifier, "status": "MISMATCH" if missing or unexpected else "MATCH",
            "expected_count": len(expected), "actual_count": len(actual),
            "missing": missing, "unexpected": unexpected,
            "provenance": [_provenance(source, "/")]}


def _extract_ecore(sources, paths):
    facts, classes, raw, model_sources, classifiers = [], {}, {}, {}, {}
    for source in sorted(sources, key=lambda item: item["id"]):
        model, sid = source["model"], source["id"]
        model_sources[model] = sid
        root = ET.parse(paths[sid]).getroot()
        _require(not root.findall("eSubpackages"), "INV-COV.UNSUPPORTED_EPACKAGE", sid)
        seen = set()
        for node in root.findall("eClassifiers"):
            name = node.attrib.get("name")
            _require(name and name not in seen, "INV-ID.DUPLICATE_ECORE_CLASSIFIER", f"{sid}/{name}")
            seen.add(name)
            _require(name not in classifiers, "INV-ID.AMBIGUOUS_ECORE_CLASSIFIER", name)
            classifiers[name] = (model, node.attrib.get(XSI_TYPE))
            raw[(model, name)] = node
            feature_names = [n.attrib.get("name") for n in node.findall("eStructuralFeatures")]
            _require(None not in feature_names and len(feature_names) == len(set(feature_names)),
                     "INV-ID.DUPLICATE_ECORE_PROPERTY", f"{sid}/{name}")
            for feature in node.findall("eStructuralFeatures"):
                _require(feature.attrib.get(XSI_TYPE) in {"ecore:EAttribute", "ecore:EReference"},
                         "INV-COV.UNSUPPORTED_ECORE_FEATURE", f"{sid}/{name}")
            parents = node.attrib.get("eSuperTypes", "").split()
            _require(len(parents) == len(set(parents)), "INV-ID.DUPLICATE_INHERITANCE", name)
        parsed = parse_ecore(paths[sid], model=model)
        _require(not (parsed.keys() & classes.keys()), "INV-ID.DUPLICATE_ECORE_CLASS", sid)
        classes.update(parsed)

    def cls_id(name):
        _require(name in classes, "INV-REF.UNKNOWN_ECORE_CLASS", name)
        return source_id("class", classes[name].model, name)

    def prop_id(field):
        return source_id(field.kind.lower(), classes[field.declared_in].model,
                         field.declared_in, field.name)

    external = {}

    def resolve_type(raw_type, model, sid, locator):
        _require(bool(raw_type), "INV-REF.MISSING_ECORE_TYPE", f"{sid}/{locator}")
        uri = raw_type.split()[-1]
        name = uri.rsplit("#//", 1)[-1]
        if name in classifiers:
            target_model, kind = classifiers[name]
            kind = {"ecore:EClass": "class", "ecore:EEnum": "enum", "ecore:EDataType": "datatype"}[kind]
            return source_id(kind, target_model, name)
        _require("#//" in uri, "INV-REF.UNKNOWN_ECORE_TYPE", uri)
        external_model = ("ecore" if uri.startswith("http://www.eclipse.org/emf/2002/Ecore#//")
                          else "jts" if uri.split("#//")[0].endswith("jts.ecore") else model + "-external")
        external_kind = "external_type" if raw_type.startswith("ecore:EClass ") else "datatype"
        identifier = source_id(external_kind, external_model, name)
        if identifier not in external:
            external[identifier] = _fact(external_kind, external_model, [name],
                data={"definition_status": "EXTERNAL_DECLARATION_NOT_LOADED", "raw_types": [],
                      "ecore_kind": "EClass" if external_kind == "external_type" else "EDataType"},
                source=sid, locator=locator)
        item = external[identifier]
        item["data"]["raw_types"] = sorted(set([*item["data"]["raw_types"], raw_type]))
        provenance = _provenance(sid, locator)
        if provenance not in item["provenance"]:
            item["provenance"].append(provenance)
        return identifier

    for (model, name), node in sorted(raw.items()):
        sid, locator = model_sources[model], f"#//{name}"
        kind = node.attrib[XSI_TYPE]
        if kind == "ecore:EClass":
            entry = classes[name]
            supers = [cls_id(parent) for parent in entry.super_types]
            facts.append(_fact("class", model, [name], data={
                "abstract": entry.abstract, "interface": node.attrib.get("interface") == "true",
                "super_types": supers, "couchdb_document": entry.couchdb_document,
                "annotations": list(entry.annotations), "xml_attributes": dict(node.attrib)},
                deps=supers, source=sid, locator=locator))
            for rank, parent in enumerate(entry.super_types):
                facts.append(_fact("inheritance", model, [name, parent], owner=cls_id(name),
                    data={"child": cls_id(name), "parent": cls_id(parent), "rank": rank},
                    deps=[cls_id(name), cls_id(parent)], source=sid, locator=locator + "/@eSuperTypes"))
            for field in entry.declared_fields:
                feature = next(n for n in node.findall("eStructuralFeatures") if n.attrib["name"] == field.name)
                floc = locator + "/" + field.name
                type_id = resolve_type(field.raw_type, model, sid, floc + "/@eType")
                opposite = None
                if field.opposite:
                    _require(field.type in classes, "INV-REF.UNKNOWN_OPPOSITE_CLASS", floc)
                    target = next((f for f in resolve_effective_fields(field.type, classes)
                                   if f.name == field.opposite and f.kind == "REFERENCE"), None)
                    _require(target is not None, "INV-REF.UNKNOWN_OPPOSITE", floc)
                    opposite = prop_id(target)
                data = {"type": type_id, "raw_type": field.raw_type,
                        "declared_in": cls_id(name), "lower": field.lower_bound,
                        "upper": field.upper_bound, "containment": field.containment,
                        "opposite": opposite, "opposite_raw": field.opposite_raw,
                        "default_value": field.default_value, "annotations": list(field.annotations),
                        "ordered": feature.attrib.get("ordered", "true") == "true",
                        "unique": feature.attrib.get("unique", "true") == "true",
                        "xml_attributes": dict(feature.attrib)}
                facts.append(_fact(field.kind.lower(), model, [name, field.name], owner=cls_id(name),
                    data=data, deps=[cls_id(name), type_id, *([opposite] if opposite else [])],
                    source=sid, locator=floc))
            signatures = set()
            for operation in node.findall("eOperations"):
                opname = operation.attrib["name"]
                signature = opname + "(" + ",".join(p.attrib.get("eType", "") for p in operation.findall("eParameters")) + ")"
                _require(signature not in signatures, "INV-ID.DUPLICATE_ECORE_OPERATION", f"{name}/{signature}")
                signatures.add(signature)
                opid = source_id("operation", model, name, signature)
                type_id = (resolve_type(operation.attrib["eType"], model, sid, locator + "/" + opname)
                           if "eType" in operation.attrib else None)
                facts.append(_fact("operation", model, [name, signature], owner=cls_id(name),
                    data={"type": type_id, "declaration": _xml(operation)},
                    deps=[cls_id(name), *([type_id] if type_id else [])], source=sid, locator=locator + "/" + opname))
                for rank, parameter in enumerate(operation.findall("eParameters")):
                    ptype = resolve_type(parameter.attrib.get("eType"), model, sid, locator + "/" + opname)
                    facts.append(_fact("parameter", model, [name, signature, parameter.attrib["name"]], owner=opid,
                        data={"type": ptype, "rank": rank, "declaration": _xml(parameter)},
                        deps=[opid, ptype], source=sid, locator=locator + "/" + opname + "/" + parameter.attrib["name"]))
        elif kind in {"ecore:EEnum", "ecore:EDataType"}:
            fkind = "enum" if kind == "ecore:EEnum" else "datatype"
            identifier = source_id(fkind, model, name)
            facts.append(_fact(fkind, model, [name], data={"declaration": _xml(node)}, source=sid, locator=locator))
            for rank, literal in enumerate(node.findall("eLiterals")):
                lname = literal.attrib["name"]
                facts.append(_fact("enum_literal", model, [name, lname], owner=identifier,
                    data={"rank": rank, "value": int(literal.attrib.get("value", "0")),
                          "literal": literal.attrib.get("literal", lname), "declaration": _xml(literal)},
                    deps=[identifier], source=sid, locator=locator + "/" + lname))
        else:
            raise ValueError(f"INV-COV.UNSUPPORTED_ECORE_CLASSIFIER: {sid}/{name}/{kind}")

    declared = {f["id"]: f for f in facts if f["kind"] in {"attribute", "reference"}}
    for name, entry in sorted(classes.items()):
        sid, model = model_sources[entry.model], entry.model
        effective = resolve_effective_fields(name, classes)
        for field in effective:
            declaration = declared[prop_id(field)]
            facts.append(_fact("application", model, [name, field.name], owner=cls_id(name),
                data={**declaration["data"], "declaration": declaration["id"],
                      "property_kind": field.kind.lower(), "inherited": name != field.declared_in,
                      "concrete": not entry.abstract},
                deps=[cls_id(name), declaration["id"]], source=sid, locator=f"#//{name}/effective/{field.name}"))
        facts.append(_fact("identity", model, [name], owner=cls_id(name), data={
            "couchdb_document_annotation": entry.couchdb_document,
            "declared_id_attributes": [prop_id(f) for f in effective
                                       if declared[prop_id(f)]["data"]["xml_attributes"].get("iD") == "true"],
            "runtime_identity": "NOT_DETERMINED_BY_ECORE_ALONE",
            "occurrence_inventory": "AGGREGATED_MOCK_ONLY"},
            deps=[cls_id(name)], source=sid, locator=f"#//{name}"))

    for fact in list(facts):
        if fact["kind"] not in {"attribute", "application"}:
            continue
        datatype = fact["data"].get("type", "")
        if not datatype.startswith("sirs-v2:datatype:jts/"):
            continue
        owner = fact["owner"]
        owner_name = next(name for name in classes if cls_id(name) == owner)
        parts = [owner_name, fact["name"]]
        if fact["kind"] == "application":
            parts.append("application")
        geo = _fact("geometry", fact["model"], parts, owner=owner,
            data={"property": fact["id"], "type": datatype,
                  "shape": "POINT" if datatype.endswith("/Point") else "UNKNOWN",
                  "dimensions": "UNKNOWN", "crs": "UNKNOWN", "loss_policy": "UNQUALIFIED"},
            deps=[fact["id"], datatype], source=fact["provenance"][0]["source"],
            locator=fact["provenance"][0]["locator"])
        facts.append(geo)
    facts.extend(external.values())
    return facts, classes


def _contract_facts(paths, classes, ecore_sources):
    c1, c2, c3 = (strict_load(paths["contract:" + name]) for name in ("c1", "c2", "c3"))
    validate_contracts(c1, c2)
    c3 = validate_structure_contract(c3)
    facts, comparisons = [], []
    models = {m["key"]: m["ecore_sha256"] for m in c3["source"]["models"]}
    actual_models = {s["model"]: s["sha256"] for s in ecore_sources if s["model"] in C3_MODELS}
    comparisons.append(_compare("C3.SOURCE_HASHES", {f"{k}:{v}" for k, v in models.items()},
                                {f"{k}:{v}" for k, v in actual_models.items()}, "contract:c3"))
    expected = {source_id("class", data["model"], name) for name, data in c3["classes"].items()}
    actual = {source_id("class", c.model, name) for name, c in classes.items() if c.model in C3_MODELS}
    comparisons.append(_compare("C3.CLASSES", expected, actual, "contract:c3"))
    expected_fields, actual_fields = set(), set()
    for name, entry in c3["classes"].items():
        for field in entry["declared_fields"]:
            expected_fields.add(canonical_bytes({k: v for k, v in field.items() if k != "label"}).decode())
    for name, entry in classes.items():
        if entry.model in C3_MODELS:
            for field in entry.declared_fields:
                actual_fields.add(canonical_bytes({k: v for k, v in field.for_class(name).items() if k != "label"}).decode())
    comparisons.append(_compare("C3.DECLARED_FIELDS", expected_fields, actual_fields, "contract:c3"))
    if "evidence:ecore-manifest" in paths:
        sid = "evidence:ecore-manifest"
        previous = strict_load(paths[sid])
        previous_hashes = {f"{m['key']}:{m['ecore_sha256']}" for m in previous["source"]["models"]}
        comparisons.append(_compare("ECORE_MANIFEST.SOURCE_HASHES", previous_hashes,
                                    {f"{k}:{v}" for k, v in actual_models.items()}, sid))
        previous_classes = {source_id("class", value["model"], name)
                            for name, value in previous["classes"].items()}
        comparisons.append(_compare("ECORE_MANIFEST.CLASSES", previous_classes, actual, sid))
        previous_fields = {canonical_bytes({k: v for k, v in field.items() if k != "label"}).decode()
                           for value in previous["classes"].values() for field in value["declared_fields"]}
        comparisons.append(_compare("ECORE_MANIFEST.DECLARED_FIELDS", previous_fields, actual_fields, sid))
    if "evidence:catalog-manifest" in paths:
        sid = "evidence:catalog-manifest"
        previous = strict_load(paths[sid])["catalogs"]
        vocabulary_classes = {name: spec for name, spec in c2["classes"].items()
                              if spec["nomenclature"] is not None}
        comparisons.append(_compare("CATALOG_MANIFEST.CATALOGS", set(previous), set(vocabulary_classes), sid))
        fields = ("count", "source", "source_sha256", "json_sha256", "module", "version_module")
        expected_provenance = {canonical_bytes({"class": name, **{k: spec.get(k) for k in fields}}).decode()
                               for name, spec in previous.items()}
        actual_provenance = {canonical_bytes({"class": name, **{k: spec["provenance"].get(k) for k in fields}}).decode()
                             for name, spec in vocabulary_classes.items()}
        comparisons.append(_compare("CATALOG_MANIFEST.PROVENANCE", expected_provenance, actual_provenance, sid))
        expected_counts = {f"{name}:{spec['count']}" for name, spec in previous.items()}
        actual_counts = {f"{name}:{len(c1['nomenclatures'][spec['nomenclature']]['values'])}"
                         for name, spec in vocabulary_classes.items()}
        comparisons.append(_compare("CATALOG_MANIFEST.VALUE_COUNTS", expected_counts, actual_counts, sid))
    primitive_provenance = defaultdict(list)
    for name, group in sorted(c1["nomenclatures"].items()):
        identifier = source_id("catalog", "c1", name)
        facts.append(_fact("catalog", "c1", [name], data={k: v for k, v in group.items() if k != "values"},
            deps=[source_id("catalog", "c1", rel["nomenclature"]) for rel in group["relations"]],
            source="contract:c1", locator="/nomenclatures/" + name))
        for field, specification in sorted(group["properties"].items()):
            datatype = source_id("datatype", "c1", specification["type"])
            locator = f"/nomenclatures/{name}/properties/{field}"
            primitive_provenance[specification["type"]].append(_provenance("contract:c1", locator + "/type"))
            facts.append(_fact("catalog_attribute", "c1", [name, field], owner=identifier,
                data={"type": datatype, "required": specification["required"],
                      "nullable": specification["nullable"], "catalog_kind": group["kind"]},
                deps=[identifier, datatype], source="contract:c1", locator=locator))
        for relation in sorted(group["relations"], key=lambda rel: (rel["field"], rel["nomenclature"])):
            field, target = relation["field"], relation["nomenclature"]
            target_id = source_id("catalog", "c1", target)
            attribute_id = source_id("catalog_attribute", "c1", name, field)
            target_attribute = source_id("catalog_attribute", "c1", target, "id")
            # C1 explicitly resolves a relation's field against target value IDs.
            # Required/nullable remain property constraints, not invented inverse bounds.
            facts.append(_fact("catalog_relation", "c1", [name, field], owner=identifier,
                data={"source": identifier, "target": target_id, "property": attribute_id,
                      "target_property": target_attribute, "inverse_cardinality": "UNDECLARED",
                      "declaration": relation},
                deps=[identifier, target_id, attribute_id, target_attribute],
                source="contract:c1", locator=f"/nomenclatures/{name}/relations[field={field}]"))
        for value in sorted(group["values"], key=lambda value: value["id"]):
            deps = [identifier]
            deps.extend(source_id("catalog_attribute", "c1", name, field) for field in group["properties"])
            deps.extend(source_id("catalog_value", "c1", rel["nomenclature"], value[rel["field"]])
                        for rel in group["relations"] if value.get(rel["field"]) is not None)
            deps.extend(source_id("catalog_relation", "c1", name, rel["field"]) for rel in group["relations"])
            facts.append(_fact("catalog_value", "c1", [name, value["id"]], owner=identifier,
                data={"catalog_kind": group["kind"], "value": value}, deps=deps,
                source="contract:c1", locator=f"/nomenclatures/{name}/values[id={value['id']}]"))
    for primitive, provenances in sorted(primitive_provenance.items()):
        fact = _fact("datatype", "c1", [primitive],
            data={"definition_status": "DECLARED_C1_PRIMITIVE", "type": primitive,
                  "ecore_equivalence": "NOT_ASSERTED"},
            source="contract:c1", locator=provenances[0]["locator"])
        fact["provenance"] = provenances
        facts.append(fact)
    # C2 remains evidence of current historical processing, never canonical approval.
    for section, values in sorted(c2.items()):
        if section in {"format", "version", "c1_version"}:
            continue
        entries = sorted(values.items()) if isinstance(values, dict) else [("all", values)]
        for name, value in entries:
            deps = []
            if section == "classes" and name in classes:
                deps.append(source_id("class", classes[name].model, name))
            if isinstance(value, dict) and value.get("nomenclature") in c1["nomenclatures"]:
                deps.append(source_id("catalog", "c1", value["nomenclature"]))
            def explicit_dependencies(item):
                if isinstance(item, dict):
                    if item.get("source_class") in classes:
                        source_class = item["source_class"]
                        deps.append(source_id("class", classes[source_class].model, source_class))
                    if item.get("nomenclature") in c1["nomenclatures"]:
                        catalog = item["nomenclature"]
                        deps.append(source_id("catalog", "c1", catalog))
                        if item.get("id") in {v["id"] for v in c1["nomenclatures"][catalog]["values"]}:
                            deps.append(source_id("catalog_value", "c1", catalog, item["id"]))
                    for child in item.values():
                        explicit_dependencies(child)
                elif isinstance(item, list):
                    for child in item:
                        explicit_dependencies(child)
            explicit_dependencies(value)
            facts.append(_fact("contract_mapping", "c2", [section, name],
                data={"section": section, "value": value, "authority": "HISTORICAL_CONTRACT_ONLY"},
                deps=deps, source="contract:c2", locator=f"/{section}/{name}"))
    return facts, comparisons


def _inventory_facts(paths, facts, classes, sources):
    by_id = {fact["id"]: fact for fact in facts}
    hashes = {source["model"]: source["sha256"] for source in sources}
    declared_expected, applied_expected, inherited_expected = set(), set(), set()
    inventory_to_stable, differences = {}, []
    with paths["inventory:ecore"].open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            model, kind = row["modele_source"], row["ligne_type"]
            cls = row["classe_source_qualifiee"].rsplit(".", 1)[-1]
            declaring = row["classe_declarante_qualifiee"].rsplit(".", 1)[-1]
            target = row["classe_cible_qualifiee"].rsplit(".", 1)[-1]
            if kind == "HERITAGE":
                identifier = source_id("inheritance", model, cls, target)
                collection = inherited_expected
            elif kind in {"DECLARATION", "APPLICATION"}:
                identifier = (source_id("reference", model, declaring, row["propriete"])
                              if kind == "DECLARATION" else source_id("application", classes[cls].model, cls, row["propriete"]))
                collection = declared_expected if kind == "DECLARATION" else applied_expected
            else:
                raise ValueError(f"INV-COV.UNKNOWN_MOD1R_ROW: {kind}")
            _require(identifier not in collection, "INV-ID.DUPLICATE_MOD1R_ROW", identifier)
            collection.add(identifier)
            inventory_to_stable[row["identifiant"]] = identifier
            if row["ecore_sha256"] != hashes.get(model):
                differences.append(identifier + ":source_sha256")
            actual = by_id.get(identifier)
            if actual and kind != "HERITAGE":
                expected_data = {"type": source_id("class", classes[target].model, target),
                                 "lower": int(row["borne_min"]), "upper": int(row["borne_max"]),
                                 "containment": row["containment"] == "oui"}
                differences.extend(identifier + ":" + key for key, value in expected_data.items()
                                   if actual["data"].get(key) != value)
    declarations = {f["id"] for f in facts if f["kind"] == "reference"}
    applications = {f["id"] for f in facts if f["kind"] == "application"
                    and f["data"]["property_kind"] == "reference" and f["data"]["concrete"]}
    inheritances = {f["id"] for f in facts if f["kind"] == "inheritance"}
    comparisons = [_compare("MOD1R.DECLARATIONS", declared_expected, declarations, "inventory:ecore"),
                   _compare("MOD1R.APPLICATIONS", applied_expected, applications, "inventory:ecore"),
                   _compare("MOD1R.INHERITANCE", inherited_expected, inheritances, "inventory:ecore"),
                   _compare("MOD1R.METADATA", set(), set(differences), "inventory:ecore")]
    observations = {}
    with paths["inventory:mock"].open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            application = inventory_to_stable.get(row["identifiant_application"])
            declaration = inventory_to_stable.get(row["identifiant_declaration"])
            _require(application and declaration, "INV-REF.UNKNOWN_MOD1R_APPLICATION", row["identifiant_application"])
            key = (application, row["ligne_type"])
            if key not in observations:
                observations[key] = {"rows": 0, "resolution_identite": Counter(),
                    "type_cible_compatible": Counter(), "opposite_observe": Counter(),
                    "etat_champ": Counter(), "forme_champ": Counter(), "declaration": declaration}
            item = observations[key]
            item["rows"] += 1
            for column in ("resolution_identite", "type_cible_compatible", "opposite_observe", "etat_champ", "forme_champ"):
                item[column][row[column]] += 1
    result = []
    for (application, kind), data in sorted(observations.items()):
        result.append(_fact("observation", "mock", [application, kind], owner=application,
            data={**data, "row_kind": kind, "semantics": "OBSERVED_ONLY_NOT_TARGET_CARDINALITY"},
            deps=[application, data["declaration"]], source="inventory:mock",
            locator=f"application={application};ligne_type={kind}"))
    return result, comparisons


def extract_history(manifest, source_paths):
    """Extract the pinned full source universe, failing closed on missing inputs.

    Physical source locations are supplied by the caller; only logical handles
    appear in the result. No database, Java compilation, mutable cache or LLM.
    """
    sources = manifest["sources"]
    ids = [source["id"] for source in sources]
    _require(len(ids) == len(set(ids)), "INV-ID.DUPLICATE_MANIFEST_SOURCE", "sources")
    paths = {key: path if hasattr(path, "read_bytes") else Path(path)
             for key, path in source_paths.items()}
    for source in sources:
        sid = source["id"]
        _require(sid in paths and paths[sid].is_file(), "INV-VER.SOURCE_MISSING", sid)
        _require(file_digest(paths[sid]) == source["sha256"], "INV-VER.SOURCE_HASH", sid)
    ecore_sources = [source for source in sources if source["kind"] == "ecore"]
    _require({source["model"] for source in ecore_sources} == MODELS and len(ecore_sources) == 8,
             "INV-COV.ECORE_UNIVERSE", "eight historical models required")
    _require(set(manifest["expected_models"]) == MODELS, "INV-COV.ECORE_UNIVERSE", "expected_models")
    required = {"contract:c1", "contract:c2", "contract:c3", "inventory:ecore", "inventory:mock"}
    _require(required <= set(ids), "INV-VER.REQUIRED_SOURCE_MISSING", ",".join(sorted(required - set(ids))))
    facts, classes = _extract_ecore(ecore_sources, paths)
    contract_facts, comparisons = _contract_facts(paths, classes, ecore_sources)
    facts.extend(contract_facts)
    observations, inventory_comparisons = _inventory_facts(paths, facts, classes, ecore_sources)
    facts.extend(observations)
    comparisons.extend(inventory_comparisons)
    identifiers = [fact["id"] for fact in facts]
    _require(len(identifiers) == len(set(identifiers)), "INV-ID.DUPLICATE_EXTRACTED_ID", "facts")
    known = set(identifiers)
    for fact in facts:
        _require(set(fact["depends_on"]) <= known, "INV-REF.UNRESOLVED_EXTRACTED_DEPENDENCY", fact["id"])
        fact["provenance"].sort(key=lambda p: (p["source"], p["locator"]))
    by_model = defaultdict(Counter)
    for fact in facts:
        by_model[fact["model"]][fact["kind"]] += 1
    kinds = Counter(fact["kind"] for fact in facts)
    counts = {"by_kind": dict(sorted(kinds.items())),
              "by_model": {k: dict(sorted(v.items())) for k, v in sorted(by_model.items())},
              "class_natures": {"abstract": sum(c.abstract for c in classes.values()),
                                "concrete": sum(not c.abstract for c in classes.values()),
                                "interface": sum(f["kind"] == "class" and f["data"]["interface"] for f in facts)},
              "ecore": {"models": 8, "classes": kinds["class"], "attributes": kinds["attribute"],
                        "references": kinds["reference"], "inheritances": kinds["inheritance"],
                        "enums": kinds["enum"], "enum_literals": kinds["enum_literal"],
                        "reference_applications": sum(f["kind"] == "application" and f["data"]["property_kind"] == "reference"
                                                      and f["data"]["concrete"] for f in facts)},
              "catalogs": {kind: {"catalogs": sum(f["kind"] == "catalog" and f["data"]["kind"] == kind for f in facts),
                                  "values": sum(f["kind"] == "catalog_value" and f["data"]["catalog_kind"] == kind for f in facts)}
                           for kind in ("vocabulary", "canonical")},
              "mock_rows": sum(f["data"]["rows"] for f in observations)}
    limitations = [
        {"code": "C3.FIVE_MODELS_ONLY", "source": "contract:c3", "detail": "aot-cot, carto and reglementary extracted directly; not certified by C3."},
        {"code": "GEO.SOURCE_SHAPE_PARTIAL", "source": "ecore:core", "detail": "Geometry is unconstrained; Point only proves shape. CRS and dimensions are unknown."},
        {"code": "TYPE.EXTERNAL_DEFINITIONS", "source": "ecore:core", "detail": "Ecore/JTS external datatype references retained; external datatype implementations not loaded."},
        {"code": "IDENTITY.RUNTIME_NOT_ECORE", "source": "inventory:mock", "detail": "Runtime IDs and document/component occurrence roles observed only through MOD-1R; Ecore alone does not establish all identity behavior."},
        {"code": "MOCK.NOT_EXHAUSTIVE", "source": "inventory:mock", "detail": "Aggregated MOD-1R snapshot only, no database rescan and no completeness inference from absent occurrences."},
        {"code": "C2.NOT_TARGET_AUTHORITY", "source": "contract:c2", "detail": "Current mappings and SQL names are historical evidence, not approved master-model destinations or bindings."},
    ]
    return {"facts": sorted(facts, key=lambda f: f["id"]), "counts": counts,
            "comparisons": sorted(comparisons, key=lambda c: c["id"]),
            "limitations": sorted(limitations, key=lambda item: item["code"])}
