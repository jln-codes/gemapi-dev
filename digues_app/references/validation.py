"""Validation de C1/C2 en mémoire, sans accès aux sources historiques."""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
import re
from typing import Any


class ReferenceContractError(ValueError):
    """Contrat absent, mal formé ou incohérent ; aucun repli implicite."""


def _require(condition: bool, path: str, message: str) -> None:
    if not condition:
        raise ReferenceContractError(f"{path}: {message}")


def _object(value: Any, path: str) -> dict:
    _require(isinstance(value, dict), path, "objet attendu")
    return value


def _array(value: Any, path: str) -> list:
    _require(isinstance(value, list), path, "liste attendue")
    return value


def _text(value: Any, path: str) -> str:
    _require(isinstance(value, str) and bool(value), path, "texte non vide attendu")
    return value


def _keys(value: dict, required: set[str], path: str, optional: set[str] = frozenset()) -> None:
    _require(required <= value.keys(), path, f"propriétés obligatoires : {sorted(required - value.keys())}")
    _require(value.keys() <= required | optional, path, f"propriétés inconnues : {sorted(value.keys() - required - optional)}")


def _strings(value: Any, path: str) -> list[str]:
    items = _array(value, path)
    for item in items:
        _text(item, path)
    _require(len(set(items)) == len(items), path, "valeurs dupliquées")
    return items


def _header(data: Any, name: str, keys: set[str]) -> dict:
    data = _object(data, name)
    _keys(data, keys | {"format", "version"}, name)
    _require(data["format"] == f"digues-app.references.{name}", name, "format inconnu")
    _require(type(data["version"]) is int and data["version"] == 1, name, "version non prise en charge")
    return data


def _digest(records: list[dict]) -> str:
    payload = json.dumps(records, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    return sha256(payload.encode("utf-8")).hexdigest()


def _validate_c1(c1: dict) -> dict[str, dict[str, dict]]:
    _text(c1["authority"], "C1.authority")
    groups = _object(c1["nomenclatures"], "C1.nomenclatures")
    indexes = {}
    counts: Counter = Counter()
    totals: Counter = Counter()
    for name, group in groups.items():
        path = f"C1.{_text(name, 'C1.nomenclatures')}"
        _object(group, path)
        _keys(group, {"kind", "properties", "unique", "relations", "values"}, path)
        _require(group["kind"] in ("vocabulary", "canonical"), path, "nature d'ensemble inconnue")
        props = _object(group["properties"], path + ".properties")
        for field, spec in props.items():
            _text(field, path)
            _object(spec, path + "." + field)
            _keys(spec, {"type", "required", "nullable"}, path + "." + field)
            _require(spec["type"] in ("string", "boolean", "integer"), path, "type de propriété inconnu")
            _require(type(spec["required"]) is bool and type(spec["nullable"]) is bool, path, "indicateurs booléens attendus")
        _require("id" in props and props["id"] == {"type": "string", "required": True, "nullable": False}, path, "identité textuelle obligatoire")
        if group["kind"] == "canonical":
            for field in ("code", "abrege", "libelle", "valid"):
                expected_type = "boolean" if field == "valid" else "string"
                _require(props.get(field) == {"type": expected_type, "required": True, "nullable": False}, path,
                         f"propriété canonique obligatoire : {field}")
        unique = _array(group["unique"], path + ".unique")
        _require(["id"] in unique, path, "unicité de l'identité manquante")
        if group["kind"] == "canonical":
            _require(["code"] in unique, path, "unicité du code canonique manquante")
        for fields in unique:
            _strings(fields, path + ".unique")
            _require(bool(fields) and set(fields) <= props.keys(), path, "clé d'unicité inconnue/vide")
        values = _array(group["values"], path + ".values")
        _require(bool(values), path, "ensemble de valeurs vide")
        for record in values:
            _object(record, path + ".values")
            _require(record.keys() <= props.keys(), path, "propriété de valeur non déclarée")
            for field, spec in props.items():
                if field not in record:
                    _require(not spec["required"], path, f"propriété absente : {field}")
                elif record[field] is None:
                    _require(spec["nullable"], path, f"null interdit : {field}")
                else:
                    expected = {"string": str, "boolean": bool, "integer": int}[spec["type"]]
                    _require(type(record[field]) is expected, path, f"type invalide : {field}")
            _text(record["id"], path + ".id")
        for fields in unique:
            # La présence fait partie de la clé : absent et null ne sont pas confondus.
            keys = [tuple((f in r, r.get(f)) for f in fields) for r in values]
            _require(len(set(keys)) == len(keys), path, f"collision de clé : {fields}")
        indexes[name] = {record["id"]: record for record in values}
        counts[group["kind"]] += 1
        totals[group["kind"]] += len(values)
    _require(counts == {"vocabulary": 100, "canonical": 14}, "C1", "périmètre V1 : 100 nomenclatures et 14 ensembles canoniques")
    _require(totals == {"vocabulary": 774, "canonical": 116}, "C1", "périmètre V1 : 774 valeurs historiques et 116 applicatives")
    for name, group in groups.items():
        for rel in _array(group["relations"], f"C1.{name}.relations"):
            _object(rel, name)
            _keys(rel, {"field", "nomenclature"}, name)
            field, target = rel["field"], rel["nomenclature"]
            _require(isinstance(field, str) and field in group["properties"], name, "champ de relation inconnu")
            _require(isinstance(target, str) and target in indexes, name, "destination de relation inconnue")
            for record in group["values"]:
                if field in record and record[field] is not None:
                    _require(record[field] in indexes[target], name, f"relation non résolue : {record['id']}.{field}")
    # Propriétés effectivement portées par le référentiel courant, pas une nouvelle nomenclature.
    geometry = {"native_selectable", "compatible_point", "compatible_linestring", "compatible_polygon"}
    _require("natures_vegetation" in groups, "C1", "natures végétation absentes")
    for record in groups["natures_vegetation"]["values"]:
        _require(all(type(record.get(f)) is bool for f in geometry), "C1.natures_vegetation", "indicateurs géométriques obligatoires")
    return indexes


def _validate_tables(c2: dict, indexes: dict) -> None:
    tables = _object(c2["tables"], "C2.tables")
    _require(len(tables) == 53, "C2.tables", "53 tables attendues")
    for table, spec in tables.items():
        _text(table, "C2.tables")
        _require(bool(re.fullmatch(r"(referentiel|source_v2)\.[a-z_]+", table)), table, "nom SQL invalide")
        _object(spec, table)
        _keys(spec, {"columns", "key_columns", "nomenclatures", "role", "limitations"}, table)
        columns = _strings(spec["columns"], table)
        keys = _strings(spec["key_columns"], table)
        _require(bool(keys) and set(keys) <= set(columns), table, "clé SQL invalide")
        names = _strings(spec["nomenclatures"], table)
        _require(bool(names) and set(names) <= indexes.keys(), table, "nomenclature cible inconnue")
        _text(spec["role"], table)
        _text(spec["limitations"], table)


def _validate_classes(c1: dict, c2: dict, indexes: dict) -> None:
    classes = _object(c2["classes"], "C2.classes")
    _require(len(classes) == 101, "C2.classes", "100 standards et une classe uniquement locale attendus")
    referenced = []
    for cls, spec in classes.items():
        path = "C2.classes." + _text(cls, "C2.classes")
        _object(spec, path)
        _keys(spec, {"nomenclature", "id_prefix", "model_available", "provenance", "consumers", "projections", "policy", "treatment", "limitations"}, path)
        _require(spec["id_prefix"] == cls + ":", path, "préfixe d'identité incohérent")
        _require(type(spec["model_available"]) is bool, path, "indicateur de modèle invalide")
        _require(isinstance(spec["policy"], str) and spec["policy"] in c2["policies"], path, "politique inconnue")
        _text(spec["treatment"], path)
        _text(spec["limitations"], path)
        provenance = _object(spec["provenance"], path + ".provenance")
        name = spec["nomenclature"]
        if name is None:
            _require(cls == "RefTypeOuvrageRegulationLit" and spec["policy"] == "local_only", path, "standard absent non déclaré")
            _text(provenance.get("unavailable_reason"), path)
        else:
            _require(isinstance(name, str) and name in indexes, path, "nomenclature inconnue")
            _require(c1["nomenclatures"][name]["kind"] == "vocabulary", path, "un standard doit référencer un vocabulaire")
            referenced.append(name)
            rows = c1["nomenclatures"][name]["values"]
            _require(all(r["id"].startswith(spec["id_prefix"]) for r in rows), path, "ID de classe incompatible")
            _require(type(provenance.get("count")) is int and provenance["count"] == len(rows), path, "compte de provenance incohérent")
            for field in ("source", "module", "version_module"):
                _text(provenance.get(field), path + "." + field)
            for field in ("source_sha256", "json_sha256"):
                _require(isinstance(provenance.get(field), str) and bool(re.fullmatch(r"[a-f0-9]{64}", provenance[field])), path, "empreinte invalide")
            _require(_digest(rows) == provenance["json_sha256"], path, "valeurs différentes de l'empreinte de provenance")
        for consumer in _array(spec["consumers"], path):
            _object(consumer, path)
            _keys(consumer, {"class", "field"}, path)
            _text(consumer["class"], path)
            _text(consumer["field"], path)
        seen = set()
        for projection in _array(spec["projections"], path):
            _object(projection, path)
            _keys(projection, {"table", "fields", "constants", "origin"}, path)
            table = projection["table"]
            _require(isinstance(table, str) and table in c2["tables"], path, "table de projection inconnue")
            _require(table not in seen, path, "projection dupliquée")
            seen.add(table)
            _require(name is None or name in c2["tables"][table]["nomenclatures"], path, "nomenclature étrangère à la table")
            _require(projection["origin"] in ("standard_and_local", "local_only"), path, "origine inconnue")
            if spec["policy"] == "local_only":
                _require(projection["origin"] == "local_only", path, "provenance locale fabriquée")
            fields = _object(projection["fields"], path)
            constants = _object(projection["constants"], path)
            _require(not fields.keys() & constants.keys(), path, "affectation contradictoire de colonne")
            _require(fields.keys() | constants.keys() <= set(c2["tables"][table]["columns"]), path, "colonne inconnue")
            for field in fields.values():
                _require(name is not None and isinstance(field, str) and field in c1["nomenclatures"][name]["properties"], path, "propriété source inconnue")
            if table == "referentiel.types_reseau_hydraulique":
                expected = {"RefReseauHydroCielOuvert": "OUVERT", "RefConduiteFermee": "FERME"}
                _require(constants == {"nature": expected.get(cls)}, path, "nature/classe de réseau incohérentes")
            else:
                _require(not constants, path, "constantes de projection non prévues en V1")
    _require(len(set(referenced)) == 100, "C2.classes", "définitions historiques dupliquées ou manquantes")


def _validate_policies(c2: dict) -> None:
    policies = _object(c2["policies"], "C2.policies")
    _keys(policies, {"local_over_standard", "local_only", "not_projected", "prestations_dual", "initialization"}, "C2.policies")
    priorities = {"local_over_standard": "local", "local_only": "local_only", "not_projected": "none",
                  "prestations_dual": "separate_branches", "initialization": "existing_rows"}
    for name, policy in policies.items():
        _object(policy, name)
        _text(policy.get("identity"), name)
        _require(policy.get("priority") == priorities[name], name, "priorité incohérente")
    dual = policies["prestations_dual"]
    _require(dual.get("branches") == ["standard", "local"], "C2.policies", "double provenance manquante")
    _require(dual.get("field_states") == ["absent", "null", "empty", "value"], "C2.policies", "états de présence incomplets")
    _require(dual.get("comparable_fields") == ["libelle", "designation", "valid", "dateMaj", "author", "lastUpdateAuthor"], "C2.policies", "champs de comparaison des prestations incohérents")
    _require(_strings(dual.get("tracked_fields"), "C2.policies") == sorted([
        "_id", "@class", "_rev", "libelle", "designation", "valid", "dateMaj", "author", "lastUpdateAuthor"
    ]), "C2.policies", "champs de présence des prestations incohérents")
    _text(dual.get("dates"), "C2.policies")
    _require(policies["local_only"].get("standard_fallback") is False, "C2.policies", "repli standard implicite")


def _validate_normalizations(c1: dict, c2: dict) -> None:
    norms = _array(c2["normalizations"], "C2.normalizations")
    _require(len(norms) == 1, "C2.normalizations", "une seule exception attestée en V1")
    norm = _object(norms[0], "C2.normalizations")
    _keys(norm, {"source_class", "id", "field", "source_value", "normalized_value", "commit", "extracted_json_sha256", "reason"}, "C2.normalizations")
    _require((norm["source_class"], norm["id"], norm["field"], norm["source_value"], norm["normalized_value"]) ==
             ("RefPrestation", "RefPrestation:30", "dateMaj", "2014-07-009", "2014-07-09"), "C2.normalizations", "exception de provenance non attestée")
    _require(norm["commit"] == "2056c9ced1b3d39386961acb11e8b90e02704703", "C2.normalizations", "commit de provenance incohérent")
    _text(norm["reason"], "C2.normalizations")
    spec = c2["classes"].get("RefPrestation")
    _require(spec is not None and spec["policy"] == "prestations_dual", "C2", "contrat prestations absent")
    rows = c1["nomenclatures"][spec["nomenclature"]]["values"]
    _require(len(rows) == 62, "C2.RefPrestation", "les 62 prestations sont requises")
    row = next((r for r in rows if r["id"] == norm["id"]), None)
    _require(row is not None and row.get(norm["field"]) == norm["normalized_value"], "C2.normalizations", "valeur normalisée absente")
    original = [dict(r, **{norm["field"]: norm["source_value"]}) if r["id"] == norm["id"] else r for r in rows]
    _require(_digest(original) == norm["extracted_json_sha256"] == "b32fc3721c234d53056b7314d6e606a53a93bccfce34deb9c83e9e1224fe75fb", "C2.normalizations", "autre différence masquée par la normalisation")


def _validate_mappings(c2: dict, indexes: dict) -> None:
    entities = _strings(c2["entities"], "C2.entities")
    for entity in entities:
        _require(bool(re.fullmatch(r"public\.[a-z_]+", entity)), "C2.entities", "destination d'objet invalide")
    mappings = _object(c2["mappings"], "C2.mappings")
    _require(len(mappings) == 17, "C2.mappings", "17 groupes de correspondances attendus en V1")
    actions = {"mapped", "reject", "fallback_warn", "source_only_warn", "null_warn", "null", "keep_desordre", "unresolved_warn"}
    for name, spec in mappings.items():
        path = "C2.mappings." + _text(name, "C2.mappings")
        _object(spec, path)
        _keys(spec, {"key_kind", "source_class", "selection", "evidence", "entries", "on_unknown", "on_null", "on_absent", "on_empty", "preserve_source_identity"}, path)
        _require(spec["key_kind"] in ("reference_id", "object_class"), path, "type de clé inconnu")
        _require(spec["preserve_source_identity"] is True, path, "identité source non conservée")
        _text(spec["selection"], path)
        _text(spec["evidence"], path)
        source = spec["source_class"]
        if spec["key_kind"] == "reference_id":
            _require(isinstance(source, str) and source in c2["classes"], path, "classe source inconnue")
            source_index = indexes[c2["classes"][source]["nomenclature"]]
        else:
            _require(source is None, path, "classe de référence inattendue")
            source_index = None
        entries = _array(spec["entries"], path)
        seen = set()
        for entry in entries:
            _object(entry, path)
            key = _text(entry.get("key"), path)
            _require(key not in seen, path, "clé de mapping dupliquée/contradictoire")
            seen.add(key)
            _require(source_index is None or key in source_index, path, "clé de mapping inconnue du référentiel")
        for entry in [*entries, *(spec[k] for k in ("on_unknown", "on_null", "on_absent", "on_empty"))]:
            _object(entry, path)
            _keys(entry, {"action", "targets"}, path,
                  {"key", "entity", "geometry", "precedence", "status", "status_if_local_known", "status_otherwise"})
            _require(entry["action"] in actions, path, "traitement inconnu")
            if "entity" in entry:
                _require(entry["entity"] in entities, path, "destination d'objet inconnue")
            if "geometry" in entry:
                _require(entry["geometry"] in ("point", "preserve", "degenerate"), path, "traitement géométrique inconnu")
            targets = _array(entry["targets"], path)
            seen_targets = set()
            single_targets = set()
            for target in targets:
                _object(target, path)
                _keys(target, {"nomenclature", "id"}, path)
                group, id_ = target["nomenclature"], target["id"]
                _require(isinstance(group, str) and group in indexes and isinstance(id_, str) and id_ in indexes[group], path, "destination/code cible inconnu")
                _require((group, id_) not in seen_targets, path, "cible dupliquée")
                _require(group == "problematiques_vegetation" or group not in single_targets, path, "cibles contradictoires")
                seen_targets.add((group, id_))
                single_targets.add(group)
            if entry["action"] not in ("mapped", "fallback_warn"):
                _require(not targets, path, "cible incompatible avec le traitement")


def _validate_uses(c2: dict) -> None:
    def visit(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                visit(key, path)
                visit(child, path + "." + key)
        elif isinstance(value, list):
            for child in value:
                visit(child, path)
        else:
            _text(value, path)
            if value.startswith("Ref"):
                _require(value in c2["classes"], path, "classe de référence inconnue")
            if value.startswith(("referentiel.", "source_v2.")):
                _require(value in c2["tables"], path, "table de référence inconnue")
    uses = _object(c2["reference_uses"], "C2.reference_uses")
    _require(bool(uses), "C2.reference_uses", "spécifications de sélection absentes")
    visit(uses, "C2.reference_uses")


def _validate_identity_resolution(c2: dict) -> None:
    rules = _object(c2["identity_resolution"], "C2.identity_resolution")
    _keys(rules, {"catalog_status", "core_optional", "dependance_destination"}, "C2.identity_resolution")
    for name, rule in rules.items():
        path = "C2.identity_resolution." + name
        _object(rule, path)
        _keys(rule, {"evidence", "consumers", "diagnostic", "absent", "null", "empty", "known", "unknown"}, path)
        _require(bool(_strings(rule["evidence"], path)) and bool(_strings(rule["consumers"], path)), path, "consommateurs/preuves absents")
        _text(rule["diagnostic"], path)
        for state in ("absent", "null", "empty", "known", "unknown"):
            outcome = _object(rule[state], path)
            _keys(outcome, {"target", "source", "status"}, path)
            expected_target = "input" if state == "known" and name != "dependance_destination" else "null"
            expected_source = "null" if state in {"absent", "null"} or (name == "core_optional" and state == "empty") else "input"
            status = {"absent": "NO_SOURCE_ID", "null": "NO_SOURCE_ID", "empty": "EMPTY_SOURCE_ID", "known": "RESOLVED", "unknown": "SOURCE_MISSING"}[state]
            if name == "core_optional":
                status = None
            elif name == "dependance_destination" and state == "known":
                status = "SOURCE_PRESERVED"
            _require(outcome == {"target": expected_target, "source": expected_source, "status": status}, path, "résolution incohérente")


def _validate_historical_undefined(c1, c2, indexes):
    spec = _object(c2['historical_undefined'], 'C2.historical_undefined')
    _keys(spec, {'contract', 'states', 'catalogs', 'mapped_classifications'}, 'C2.historical_undefined')
    _require(spec['contract'] == 'MIG-UNDEFINED-01' and spec['states'] == ['absent', 'null'],
             'C2.historical_undefined', 'portée de transformation invalide')
    for cls, declaration in _object(spec['catalogs'], 'undefined.catalogs').items():
        _require(cls in c2['classes'], 'undefined', 'classe historique inconnue')
        _keys(declaration, {'candidates', 'identity_targets'}, 'undefined.' + cls)
        source = c2['classes'][cls]['nomenclature']
        seen = set()
        for candidate in _array(declaration['candidates'], 'undefined.candidates'):
            _keys(candidate, {'id', 'libelle'}, 'undefined.candidate')
            identifier = _text(candidate['id'], 'undefined.id')
            _require(identifier not in seen, 'undefined', 'candidat historique dupliqué')
            seen.add(identifier)
            row = indexes.get(source, {}).get(identifier)
            _require(row is not None and row.get('libelle') == candidate['libelle'],
                     'undefined', 'preuve historique divergente')
            _require(candidate['libelle'] in {'Indéfini', 'Indefini', 'Ouvrage indéfini',
                "Type d'occupation indéfini", 'Type de régime indéfini', "Type de zone d'atterrissement indéfini"},
                'undefined', 'valeur non démontrée comme Indéfini')
        _require(bool(seen), 'undefined', 'preuve historique absente')
        historical_candidates={identifier for identifier,row in indexes.get(source, {}).items()
            if row.get('libelle') in {'Indéfini','Indefini','Ouvrage indéfini',
                "Type d'occupation indéfini",'Type de régime indéfini',"Type de zone d'atterrissement indéfini"}}
        _require(seen == historical_candidates, 'undefined', 'candidats historiques incomplets ; arbitrage nécessaire')
        for table in _strings(declaration['identity_targets'], 'undefined.identity_targets'):
            _require(table.startswith('referentiel.'), 'undefined', 'destination métier attendue')
    for mapping in _strings(spec['mapped_classifications'], 'undefined.mappings'):
        _require(mapping in c2['mappings'] and c2['mappings'][mapping]['source_class'] in spec['catalogs'],
                 'undefined', 'classification inconnue')
        declaration=spec['catalogs'][c2['mappings'][mapping]['source_class']]
        _require(len(declaration['candidates']) == 1, 'undefined', 'classification historique ambiguë')
        entry=next((e for e in c2['mappings'][mapping]['entries'] if e['key']==declaration['candidates'][0]['id']), None)
        if entry is not None and entry['action']=='mapped':
            for target in entry['targets']:
                label=indexes[target['nomenclature']][target['id']].get('libelle')
                _require(label in {'Indéfini','Indefini',"Type d'occupation indéfini",'Type de régime indéfini',
                    "Type de zone d'atterrissement indéfini",'Ouvrage hydraulique indéfini','Équipement de mesure indéfini',
                    'Cheminement indéfini','Mobilier indéfini','Réseau technique indéfini'},
                    'undefined', 'destination non démontrée comme Indéfini')


def _validate_contracts(c1: Any, c2: Any) -> None:
    c1 = _header(c1, "C1", {"authority", "nomenclatures"})
    c2 = _header(c2, "C2", {"c1_version", "classes", "tables", "mappings", "reference_uses", "policies", "normalizations", "entities", "identity_resolution", "historical_undefined"})
    _require(type(c2["c1_version"]) is int and c2["c1_version"] == c1["version"], "C2", "version C1 incompatible")
    indexes = _validate_c1(c1)
    _validate_tables(c2, indexes)
    _validate_policies(c2)
    _validate_classes(c1, c2, indexes)
    _validate_normalizations(c1, c2)
    _validate_mappings(c2, indexes)
    _validate_uses(c2)
    _validate_identity_resolution(c2)
    _validate_historical_undefined(c1, c2, indexes)


def validate_contracts(c1: Any, c2: Any) -> None:
    """Valide exclusivement les données fournies, sans IO ni transformation."""
    try:
        _validate_contracts(c1, c2)
    except ReferenceContractError:
        raise
    except (KeyError, TypeError, ValueError, RecursionError) as exc:
        # Une forme JSON inattendue reste une erreur de contrat, jamais un repli.
        raise ReferenceContractError(f"Structure C1/C2 invalide : {exc}") from exc
