"""Bounded DDL witnesses, never business equivalences or live SQL execution.

CREATE TABLE, column/ALTER constraints and literal selectors are supported.
Unsupported SQL cannot satisfy a requirement. File hashes and versions are
checked before any location is counted; proof levels remain independent.
"""
from __future__ import annotations

import ast
from collections import Counter, defaultdict
from hashlib import sha256
import re

from .common import file_digest


def _split(text, separator=","):
    pieces, start, depth, quoted, i = [], 0, 0, False, 0
    while i < len(text):
        char = text[i]
        if char == "'":
            if quoted and i + 1 < len(text) and text[i + 1] == "'":
                i += 2
                continue
            quoted = not quoted
        elif not quoted:
            depth += (char == "(") - (char == ")")
            if char == separator and depth == 0:
                pieces.append(text[start:i].strip())
                start = i + 1
        i += 1
    pieces.append(text[start:].strip())
    return pieces


def _parentheses(text, start):
    depth, quoted, i = 0, False, start
    while i < len(text):
        c = text[i]
        if c == "'":
            if quoted and i + 1 < len(text) and text[i + 1] == "'":
                i += 2
                continue
            quoted = not quoted
        elif not quoted:
            depth += (c == "(") - (c == ")")
            if depth == 0:
                return text[start + 1:i], i + 1
        i += 1
    raise ValueError("INV-SQL.DDL_GRAMMAR: unclosed parentheses")


def normalized_sql(value):
    # Keep literal case; whitespace outside literals is insignificant.
    return "".join(part if n % 2 else re.sub(r"\s+", "", part).lower()
                   for n, part in enumerate(re.split("('(?:[^']|'')*')", value)))


def parse_ddl(text):
    """Extract table witnesses without executing DDL or guessing SQL names."""
    text = re.sub(r"--[^\n]*", "", text)
    tables = {}
    for match in re.finditer(r"\bCREATE TABLE\s+(\w+\.\w+)\s*\(", text, re.I):
        body, _ = _parentheses(text, match.end() - 1)
        name = match[1]
        if name in tables:
            raise ValueError("INV-SQL.DUPLICATE_TABLE: " + name)
        table = {"columns": {}, "constraints": [], "indexes": []}
        tables[name] = table
        for clause in _split(body):
            if re.match(r"(?:CONSTRAINT|PRIMARY|FOREIGN|UNIQUE|CHECK)\b", clause, re.I):
                _constraints(table, clause)
                continue
            column = re.match(r"(\w+)\s+(.+)", clause, re.S)
            if not column:
                raise ValueError("INV-SQL.DDL_GRAMMAR: " + name)
            cname, rest = column.groups()
            ctype = re.split(r"\s+(?:NOT NULL|NULL|DEFAULT|CONSTRAINT|PRIMARY KEY|UNIQUE|CHECK|REFERENCES)\b",
                             rest, maxsplit=1, flags=re.I)[0].strip()
            table["columns"][cname] = {"type": normalized_sql(ctype),
                                      "nullable": not bool(re.search(r"\bNOT NULL\b|\bPRIMARY KEY\b", rest, re.I))}
            _constraints(table, rest, cname)
    for match in re.finditer(r"ALTER TABLE\s+(?:ONLY\s+)?(\w+\.\w+)\s+ADD\s+([^;]+);", text, re.I):
        if match[1] in tables:
            _constraints(tables[match[1]], match[2])
    for match in re.finditer(r"CREATE\s+UNIQUE\s+INDEX\s+(\w+)\s+ON\s+(\w+\.\w+)"
                             r"(?:\s+USING\s+\w+)?\s*\(([^)]+)\)\s*(?:WHERE\s*([^;]+))?;", text, re.I):
        if match[2] in tables:
            tables[match[2]]["indexes"].append({"name": match[1],
                "columns": [c.strip() for c in match[3].split(",")],
                "predicate": match[4].strip() if match[4] else None})
    for table in tables.values():
        for constraint in table["constraints"]:
            if constraint["kind"] == "primary_key":
                for col in constraint["columns"]:
                    if col in table["columns"]:
                        table["columns"][col]["nullable"] = False
    return tables


def _constraints(table, clause, column=None):
    for match in re.finditer(r"\b(PRIMARY KEY|UNIQUE)\b(?:\s*\(([^)]+)\))?", clause, re.I):
        columns = [c.strip() for c in match[2].split(",")] if match[2] else [column]
        if None not in columns:
            table["constraints"].append({"kind": "primary_key" if match[1].upper() == "PRIMARY KEY" else "unique",
                                          "columns": columns})
    for match in re.finditer(r"\bCHECK\s*\(", clause, re.I):
        expr, _ = _parentheses(clause, match.end() - 1)
        table["constraints"].append({"kind": "check", "expression": expr})
    fk = re.search(r"(?:FOREIGN KEY\s*\(([^)]+)\)\s*)?REFERENCES\s+(\w+\.\w+)\s*\(([^)]+)\)", clause, re.I)
    if fk:
        cols = [c.strip() for c in fk[1].split(",")] if fk[1] else [column]
        if None not in cols:
            table["constraints"].append({"kind": "foreign_key", "columns": cols,
                "references": fk[2], "reference_columns": [c.strip() for c in fk[3].split(",")]})


def _constant(node, env):
    if isinstance(node, ast.Name):
        return env[node.id]
    if isinstance(node, ast.JoinedStr):
        return "".join(str(_constant(n.value, env)) if isinstance(n, ast.FormattedValue)
                       else n.value for n in node.values)
    if isinstance(node, ast.Dict):
        return {_constant(k, env): _constant(v, env) for k, v in zip(node.keys, node.values)}
    if isinstance(node, (ast.Tuple, ast.List)):
        result = []
        for n in node.elts:
            result.extend(_constant(n.value, env)) if isinstance(n, ast.Starred) else result.append(_constant(n, env))
        return tuple(result)
    if isinstance(node, ast.GeneratorExp) and len(node.generators) == 1:
        gen = node.generators[0]
        if not isinstance(gen.target, ast.Name) or gen.ifs or gen.is_async:
            raise ValueError("INV-SQL.CONTROL_GRAMMAR")
        return tuple(_constant(node.elt, {**env, gen.target.id: value}) for value in _constant(gen.iter, env))
    if isinstance(node, ast.Call) and not node.keywords:
        if isinstance(node.func, ast.Name) and node.func.id == "tuple" and len(node.args) == 1:
            return tuple(_constant(node.args[0], env))
        if isinstance(node.func, ast.Attribute) and node.func.attr == "values" and not node.args:
            return tuple(_constant(node.func.value, env).values())
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        left, right = _constant(node.left, env), _constant(node.right, env)
        if type(left) is int and type(right) is int:
            return left * right
    return ast.literal_eval(node)


def control_tables(text, constants):
    """Read only constants and table f-strings from the pinned Python DDL."""
    env = dict(constants)

    for node in ast.parse(text).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in {"SCHEMA_VERSION", "TIMESTAMPS", "BUSINESS_DATABASE_SLUG_CONSTRAINT", "TABLE_DEFINITIONS",
                        "FUNCTION_DEFINITIONS", "MUTABLE_TABLES", "TRIGGER_DEFINITIONS", "INDEX_DEFINITIONS", "SCHEMA_DDL"}:
                env[name] = _constant(node.value, env)
    checksum = sha256("\n".join(env["SCHEMA_DDL"]).encode()).hexdigest()
    return str(env["SCHEMA_VERSION"]), parse_ddl(";\n".join(env["TABLE_DEFINITIONS"].values())), checksum


def read_catalogs(manifest, paths):
    catalogs, errors = {}, []
    for source in manifest["sources"]:
        if source["kind"] != "ddl":
            continue
        sid, path = source["id"], paths.get(source["id"])
        if path is None or not path.is_file():
            errors.append((sid, "DDL_UNAVAILABLE"))
            continue
        if file_digest(path) != source["sha256"]:
            errors.append((sid, "STALE_DDL"))
            continue
        text = path.read_text(encoding="utf-8")
        if source.get("format") == "python-control":
            constants = {}
            for dep in source.get("dependencies", []):
                entry = next((s for s in manifest["sources"] if s["id"] == dep), None)
                dep_path = paths.get(dep)
                if not entry or not dep_path or file_digest(dep_path) != entry["sha256"]:
                    errors.append((sid, "STALE_DDL_DEPENDENCY"))
                    break
                for node in ast.parse(dep_path.read_text()).body:
                    if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "ABSOLUTE_MEDIA_LIMIT_BYTES":
                        # This dependency is a literal integer in the pinned source.
                        constants[node.targets[0].id] = _constant(node.value, {})
            else:
                version, tables, checksum = control_tables(text, constants)
                if version != source["version"]:
                    errors.append((sid, "DDL_VERSION_MISMATCH"))
                    continue
                if checksum != source["rendered_sha256"]:
                    errors.append((sid, "DDL_RENDERED_HASH_MISMATCH"))
                    continue
                catalogs[sid] = tables
        else:
            if source.get("version_source"):
                version_source = next((s for s in manifest["sources"] if s["id"] == source["version_source"]), None)
                version_path = paths.get(source["version_source"])
                if not version_source or not version_path or not version_path.is_file() or file_digest(version_path) != version_source["sha256"]:
                    errors.append((sid, "STALE_DDL_DEPENDENCY"))
                    continue
                constants = {n.targets[0].id: ast.literal_eval(n.value)
                             for n in ast.parse(version_path.read_text()).body
                             if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
                             and n.targets[0].id in {"BUSINESS_SCHEMA_REVISION", "BUSINESS_SCHEMA_VERSIONS"}}
                if constants["BUSINESS_SCHEMA_VERSIONS"][constants["BUSINESS_SCHEMA_REVISION"]] != source["version"]:
                    errors.append((sid, "DDL_VERSION_MISMATCH"))
                    continue
            catalogs[sid] = parse_ddl(text)
    return catalogs, errors


def constraint_present(expected, table):
    if expected["kind"] == "not_null":
        return all(c in table["columns"] and not table["columns"][c]["nullable"] for c in expected["columns"])
    for actual in table["constraints"]:
        if actual["kind"] != expected["kind"]:
            continue
        if actual["kind"] == "check":
            if normalized_sql(actual["expression"]) == normalized_sql(expected["expression"]):
                return True
        elif all(actual.get(k) == v for k, v in expected.items()):
            return True
    return False


def selector_present(selector, table):
    column, value = selector["column"], selector["value"]
    if column not in table["columns"]:
        return False
    for c in table["constraints"]:
        if c["kind"] != "check":
            continue
        expression = c["expression"]
        patterns = (r"\b" + re.escape(column) + r"\s+IN\s*\(([^)]*)\)",
                    r"\b" + re.escape(column) + r"\s*=\s*ANY\s*\(\s*ARRAY\[([^]]*)\]",
                    r"\b" + re.escape(column) + r"\s*=\s*('(?:[^']|'')*')")
        for pattern in patterns:
            for match in re.finditer(pattern, expression, re.I):
                values = [v.replace("''", "'") for v in re.findall(r"'((?:[^']|'')*)'", match[1])]
                if value in values:
                    return True
    return False


def qualify_bindings(registry, manifest, paths):
    """Report per-target physical witnesses, without inferring business equivalence."""
    catalogs, errors = read_catalogs(manifest, paths)
    diagnostics, results, occupied = [], [], defaultdict(list)
    targets = {t["id"]: t for t in registry["targets"]}
    target_bindings = Counter(b["target"] for b in registry["bindings"])

    def fail(row, code, message):
        diagnostics.append({"code": code, "element_id": row["id"], "message": message,
                            "provenance": row.get("provenance", [])})

    for sid, error in errors:
        fail({"id": sid}, error, "The pinned DDL authority is unavailable, stale or has a different version.")
    for row in registry["bindings"]:
        target = targets.get(row["target"], {})
        tables = catalogs.get(row["source"])
        table = (tables or {}).get(f"{row['schema']}.{row['table']}")
        valid = table is not None
        evidence = {"table": None, "bound_columns": [], "columns": {},
                    "constraints": [], "unique_indexes": [], "discriminator": None}
        if table is not None:
            evidence = {"table": f"{row['schema']}.{row['table']}",
                        "bound_columns": row["columns"],
                        "columns": table["columns"],
                        "constraints": table["constraints"],
                        "unique_indexes": table["indexes"],
                        "discriminator": row["discriminator"] if row["discriminator"]
                        and selector_present(row["discriminator"], table) else None}
        expected_status = {"LOCATED": "BINDING_PARTIEL", "ABSENT": "ABSENT",
                           "INCOMPATIBLE": "INCOMPATIBLE", "UNDETERMINED": "INDETERMINE"}
        physical_status = row.get("physical_status")
        if physical_status != expected_status[row["location_status"]]:
            # A complete binding needs a separate proof for every applicable
            # business obligation; a found table is never sufficient.
            if physical_status != "BINDING_COMPLET_DEMONTRE" or row["physical_gaps"] or target.get("key_status") != "APPROUVE":
                fail(row, "PHYSICAL_STATUS_UNSUPPORTED", "Physical status is not justified by the location and remaining gaps.")
        if physical_status == "BINDING_COMPLET_DEMONTRE":
            fail(row, "COMPLETE_PROOF_UNAVAILABLE", "Identity domain, all attributes, relations, geometry and projection require individually checked witnesses.")
        if not row.get("physical_gaps") and physical_status != "BINDING_COMPLET_DEMONTRE":
            fail(row, "PHYSICAL_GAPS_MISSING", "An incomplete binding needs its explicit missing obligations.")
        if target_bindings[row["target"]] != 1:
            valid = False
            fail(row, "BINDING_TARGET_COLLISION", "Multiple undecomposed physical claims name the same target.")
        if row["location_status"] == "LOCATED" and (row["table"] is None or not row["columns"]):
            valid = False
            fail(row, "LOCATION_INCOMPLETE", "A demonstrated location needs an explicit table and relevant columns.")
        if (row["location_status"] == "LOCATED") == (row["qualification"] == "NONE"):
            valid = False
            fail(row, "QUALIFICATION_STATUS_CONFLICT", "Qualification level contradicts the physical location status.")
        if set(row["columns"]) != set(row["column_types"]):
            valid = False
            fail(row, "COLUMN_TYPE_COVERAGE", "Every bound column needs exactly one explicit SQL type.")
        if row["location_status"] in {"ABSENT", "INCOMPATIBLE", "UNDETERMINED"}:
            fail(row, "PHYSICAL_" + row["location_status"], row["limitation"])
        if row["table"] is not None and tables is not None and table is None:
            fail(row, "TABLE_UNVERIFIED", "The declared schema/table does not exist in this DDL authority.")
        if table:
            for col in row["columns"]:
                if col not in table["columns"]:
                    valid = False
                    fail(row, "COLUMN_MISSING", f"Column {col} is absent.")
                elif table["columns"][col]["type"] != normalized_sql(row["column_types"].get(col, "")):
                    valid = False
                    fail(row, "TYPE_MISMATCH", f"Column {col} disagrees with its declared SQL type.")
            for constraint in row["constraints"]:
                if not constraint_present(constraint, table):
                    valid = False
                    fail(row, "CONSTRAINT_MISSING", f"Declared constraint is not demonstrated: {constraint}.")
            for requirement in row["requirements"]:
                if not constraint_present(requirement, table):
                    fail(row, "REQUIRED_CONSTRAINT_MISSING", f"Approved requirement is absent: {requirement}.")
            for constraint in row["constraints"]:
                if constraint["kind"] != "foreign_key":
                    continue
                foreign = (tables or {}).get(constraint["references"])
                pairs = zip(constraint["columns"], constraint["reference_columns"])
                if (not foreign or len(constraint["columns"]) != len(constraint["reference_columns"]) or
                        any(table["columns"].get(a, {}).get("type") != foreign["columns"].get(b, {}).get("type")
                            or a not in table["columns"] or b not in foreign["columns"] for a, b in pairs)):
                    valid = False
                    fail(row, "FOREIGN_KEY_TYPE_INCOMPATIBLE", "The declared FK columns and target types do not match in this database.")
            value_column = row.get("value_column")
            if value_column and value_column in table["columns"]:
                kind = target.get("value_type", {}).get("kind")
                compatible = {"boolean": {"boolean"}, "string": {"text", "charactervarying"},
                              "date": {"date"}, "integer": {"integer", "bigint"},
                              "number": {"doubleprecision", "numeric"}, "reference": {"text", "uuid"}}
                if kind not in compatible or table["columns"][value_column]["type"] not in compatible[kind]:
                    valid = False
                    fail(row, "BUSINESS_TYPE_INCOMPATIBLE", "The SQL column cannot implement the declared value type.")
            selector = row["discriminator"]
            if selector:
                if selector["column"] not in row["columns"] or not selector_present(selector, table):
                    valid = False
                    fail(row, "DISCRIMINATOR_UNVERIFIED", "No supported CHECK proves the literal discriminator.")
            if row["location_status"] == "LOCATED" and target.get("kind") in {"concept", "component", "type", "subtype", "catalog"}:
                occupied[(row["source"], row["schema"], row["table"])].append(row)
            if target.get("kind") == "component" and any(targets.get(owner, {}).get("kind") == "type" for owner in target.get("owners", [])):
                fail(row, "PARENT_TYPE_COMPATIBILITY_UNQUALIFIED", "A FK to the parent family does not constrain the approved type of that parent.")
            for owner in target.get("owners", []):
                owner_bindings = [b for b in registry["bindings"] if b["target"] == owner]
                if owner_bindings and all(b["location_status"] != "LOCATED" for b in owner_bindings):
                    fail(row, "OWNER_PHYSICAL_SUPPORT_MISSING", "Approved owner has no compatible physical support: " + owner)
        if row["qualification"] == "CERTIFIED":
            fail(row, "CERTIFICATION_UNSUPPORTED", "Complete bindings require semantic, occurrence and runtime evidence beyond this DDL reader.")
        if row["location_status"] == "LOCATED":
            fail(row, "BINDING_INCOMPLETE", row["limitation"])
        results.append({"id": row["id"], "target": row["target"],
                        "location_verified": valid and row["location_status"] == "LOCATED",
                        "qualification": row["qualification"], "physical_status": physical_status,
                        "evidence": evidence, "gaps": row.get("physical_gaps", []), "certified": False})
    for rows in occupied.values():
        for i, left in enumerate(rows):
            for right in rows[i + 1:]:
                def ancestors(tid):
                    seen = set()
                    while tid in targets and tid not in seen:
                        seen.add(tid)
                        tid = targets[tid].get("parent")
                    return seen
                related = left["target"] in ancestors(right["target"]) or right["target"] in ancestors(left["target"])
                def selectors(row):
                    parents = ancestors(row["target"])
                    return [r["discriminator"] for r in rows if r["target"] in parents and r["discriminator"]]
                disjoint = any(a["column"] == b["column"] and a["value"] != b["value"]
                               for a in selectors(left) for b in selectors(right))
                # Candidate catalogue specializations without a selector do not
                # prove disjoint rows; they deliberately remain collisions.
                if not related and not disjoint:
                    fail(right, "PHYSICAL_COLLISION_UNQUALIFIED", "Shared physical rows with " + left["target"] + "; no qualified inheritance or disjoint discriminator.")
    for obligation in registry.get("obligations", []):
        if obligation["status"] not in {"APPLICABLE", "PARTIALLY_SUPERSEDED"}:
            continue
        if "source_storage_absent" in obligation["controls"]:
            for sid, tables in catalogs.items():
                if sid == "sql:business" and any(t.startswith("source_v2.") for t in tables):
                    fail(obligation, "PERMANENT_SOURCE_STORAGE", "Business DDL must not create permanent source_v2 storage.")
        if "sql_business_endpoints" in obligation["controls"]:
            for row in registry["bindings"]:
                if any(c["kind"] == "foreign_key" and c["references"].startswith("source_v2.") for c in row["constraints"]):
                    fail(row, "NON_BUSINESS_ENDPOINT", "A permanent business relation cannot target a source archive.")
    # This is initializer evidence, explicitly separate from the SQL authority.
    for row in registry["bindings"]:
        witness = row.get("catalogue_evidence")
        if not witness:
            continue
        source = next((s for s in manifest["sources"] if s["id"] == witness["source"]), None)
        path = paths.get(witness["source"])
        if not source or path is None or not path.is_file() or file_digest(path) != source["sha256"]:
            fail(row, "CATALOGUE_EVIDENCE_STALE", "The separately pinned initializer evidence is unavailable or stale.")
            continue
        symbols = {n.targets[0].id: n.value for n in ast.parse(path.read_text()).body
                   if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)}
        values = [v[0] for v in ast.literal_eval(symbols[witness["symbol"]])]
        if sorted(values) != sorted(witness["approved_values"]):
            fail(row, "CATALOGUE_DOMAIN_INCOMPATIBLE", "Initializer values differ from the six approved structural types: " + ", ".join(sorted(set(values) - set(witness["approved_values"]))))
    return {"locations": results, "diagnostics": diagnostics,
            "counts": {"declared": len(results), "verified_locations": sum(r["location_verified"] for r in results),
                       "certified": 0, "qualification": dict(sorted(Counter(r["qualification"] for r in results).items())),
                       "physical_status": dict(sorted(Counter(r["physical_status"] for r in results).items()))}}
