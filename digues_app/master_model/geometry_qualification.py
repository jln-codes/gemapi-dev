"""Read-only, reproducible geometry evidence; no independent registry or report.

Historical worksheets remain allegations/proposals, never decision inputs.
Results are returned to stdout by the CLI, including unresolved records.
"""

from collections import Counter, defaultdict
import csv
from hashlib import sha256
import io
import re
import subprocess
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from .common import digest
from .geometry import OWNER_KINDS, check_import, csv_bytes, geometry_rows, owner_universe


def historical_table(payload, suffix):
    """Read exact cells, including sparse XLSX cells, formulas and annotations."""
    if suffix == ".csv":
        text = payload.decode("utf-8-sig")
        header = text.splitlines()[0]
        delimiter = "\t" if "\t" in header else ";"
        values = list(csv.reader(io.StringIO(text), delimiter=delimiter, strict=True))
        columns = values[0]
        if len(columns) != len(set(columns)):
            raise ValueError("Historical CSV has duplicate headers")
        if any(len(row) != len(columns) for row in values):
            raise ValueError("Historical CSV has inconsistent row widths")
        return {"columns": columns, "records": [dict(zip(columns, row)) for row in values[1:]],
                "extra_cells": [], "annotations": []}
    with ZipFile(io.BytesIO(payload)) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared = ["".join(si.itertext()) for si in ET.fromstring(archive.read("xl/sharedStrings.xml"))]
        sheets, extras, records, columns = [], [], [], []
        for name in sorted(n for n in archive.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)):
            cells = {}
            root = ET.fromstring(archive.read(name))
            ns = {"s": root.tag.split("}", 1)[0].lstrip("{")}
            for cell in root.findall(".//s:sheetData/s:row/s:c", ns):
                value = cell.find("s:v", ns)
                value = value.text if value is not None else ""
                if cell.get("t") == "s":
                    value = shared[int(value)]
                elif cell.get("t") == "inlineStr":
                    value = "".join(t.text or "" for t in cell.findall("s:is//s:t", ns))
                formula = cell.find("s:f", ns)
                cells[cell.attrib["r"]] = {"value": value or "", "formula": formula.text if formula is not None else None}
            def index(address):
                letters, number = re.fullmatch(r"([A-Z]+)(\d+)", address).groups()
                col = 0
                for letter in letters:
                    col = col * 26 + ord(letter) - ord("A") + 1
                return int(number), col
            header_cells = sorted((index(a)[1], c["value"]) for a, c in cells.items() if index(a)[0] == 1 and c["value"])
            sheet_columns = [value for _, value in header_cells]
            if not sheet_columns or len(sheet_columns) != len(set(sheet_columns)):
                raise ValueError("Historical XLSX sheet has missing or duplicate headers")
            sheets.append({"part": name, "columns": sheet_columns})
            columns.extend(c for c in sheet_columns if c not in columns)
            positions = {column for column, _ in header_cells}
            row_numbers = sorted({index(a)[0] for a in cells if index(a)[0] > 1})
            for number in row_numbers:
                by_col = {index(a)[1]: c["value"] for a, c in cells.items() if index(a)[0] == number}
                record = {key: by_col.get(col, "") for col, key in header_cells}
                if any(record.values()):
                    records.append(record)
            for address, cell in sorted(cells.items()):
                if (index(address)[1] not in positions and cell["value"]) or cell["formula"] is not None:
                    extras.append({"part": name, "cell": address, **cell})
        annotations = [{"part": name, "xml": archive.read(name).decode("utf-8")}
                       for name in sorted(archive.namelist()) if "comment" in name.lower() and name.endswith(".xml")]
    return {"columns": columns, "records": records, "extra_cells": extras, "annotations": annotations, "sheets": sheets}


def git_history(repo, current_text):
    """Reachable Git versions, deduplicated by exact bytes; never restore/write."""
    def git(*args):
        result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
        if result.returncode:
            raise ValueError("GIT_HISTORY_UNAVAILABLE: " + result.stderr.decode(errors="replace"))
        return result.stdout
    paths = sorted({line for line in git("log", "--all", "--format=", "--name-only", "--", "docs/geometries").decode().splitlines()
                    if re.search(r"/(?:contrats-geometriques|GEO-[AB]-)[^/]*\.(csv|xlsx)$", line)})
    versions = {}
    for path in paths:
        revisions = sorted(set(git("log", "--all", "--format=%H", "--", path).decode().splitlines()))
        for revision in revisions:
            # A deletion commit has no blob. Its prior versions are still scanned.
            if not git("ls-tree", revision, "--", path).strip():
                continue
            payload = git("show", revision + ":" + path)
            fingerprint = sha256(payload).hexdigest()
            if fingerprint not in versions:
                versions[fingerprint] = {"sha256": fingerprint, "git_locations": [],
                                         **historical_table(payload, ".xlsx" if path.endswith(".xlsx") else ".csv")}
            versions[fingerprint]["git_locations"].append(revision + ":" + path)
    current = Counter(digest(r) for r in read_legacy(current_text))
    for version in versions.values():
        version["record_count"] = len(version["records"])
        remaining = current.copy()
        unique = []
        for row in version.pop("records"):
            key = digest(row)
            if remaining[key]:
                remaining[key] -= 1
            else:
                unique.append(row)
        version["rows_not_exactly_preserved_in_current"] = unique
        version["exactly_preserved_record_count"] = version["record_count"] - len(unique)
        version["unique_columns"] = sorted(set(version["columns"]) - set(LEGACY_COLUMNS))
        version["safe_content_equivalence_for_deletion"] = not (unique or version["extra_cells"] or version["annotations"])
    return {"scope": "reachable Git history of docs/geometries/{contrats-geometriques*,GEO-A-*,GEO-B-*}.csv and *.xlsx",
            "summary": {"paths": paths, "distinct_payloads": len(versions),
                        "payloads_with_unpreserved_content": sum(not v["safe_content_equivalence_for_deletion"] for v in versions.values())},
            "versions": [versions[k] for k in sorted(versions)]}


def coverage(model):
    facts = {f["id"]: f for f in model["historical"]["facts"]}
    historical = {key: f for key, f in facts.items() if f["kind"] == "geometry"}
    universe = owner_universe(model)
    rows = geometry_rows(model)
    details = []
    mappings = {m["source"]: m for m in model["mappings"]}
    observed = []
    for row in rows:
        entry = universe[row["owner"]]
        observed.extend(entry["historical"])
        owners = sorted({f["owner"] for f in entry["historical"]})
        traces = []
        for fact in entry["historical"]:
            prop = facts[fact["data"]["property"]]
            declaration = facts[prop["data"]["declaration"]] if prop["kind"] == "application" else prop
            traces.append({"geometry_fact": fact, "effective_property": prop,
                           "declaration": declaration})
        details.append({"canonical_key": row["canonical_key"], "owner": row["owner"],
                        "kind": row["kind"], "contract_status": row["contract_status"],
                        "owner_status": row["owner_status"], "key_status": row["key_status"],
                        "qualification": entry["issues"],
                        "historical_classes": [facts[key] for key in owners],
                        "class_mappings": [mappings[key] for key in owners if key in mappings],
                        "grouping": "DOCUMENTARY_BUNDLE_NO_PROPERTY_OR_OCCURRENCE_MERGE",
                        "links": entry["links"], "declaration_traces": traces})
    counts = Counter(f["id"] for f in observed)
    actual = {f["id"]: f for f in observed}
    missing = sorted(set(historical) - set(actual))
    extra = sorted(set(actual) - set(historical))
    changed = sorted(key for key in historical.keys() & actual.keys() if historical[key] != actual[key])
    targets = model["registry"]["targets"]
    outside = [{"target": t, "qualification": "ABSENCE_DE_CONTRAT_OU_LIEN_GEOMETRIQUE_ENREGISTRE",
                "owner_kind_eligible": t["kind"] in OWNER_KINDS,
                "spatiality": "NON_QUALIFIEE_NOT_NON_SPATIAL"}
               for t in targets if t["id"] not in universe and t.get("state") != "ignored"]
    retired = [{"target": t, "qualification": "RETIRED_TARGET_EVIDENCE_NOT_ACTIVE_OWNER"}
               for t in targets if t.get("state") == "ignored" and t.get("geometry") is not None]
    return {"summary": {
        "rows": len(rows), "unique_keys": len({r["canonical_key"] for r in rows}),
        "historical_owners": len({f["owner"] for f in historical.values()}),
        "covered_owners": len({f["owner"] for f in observed}),
        "historical_facts": len(historical), "covered_facts": len(actual),
        "fact_occurrences": len(observed),
        "historical_facts_sha256": digest(sorted(historical.values(), key=lambda f: f["id"])),
        "missing_facts": missing, "extra_facts": extra, "changed_facts": changed,
        "repeated_fact_ids": {k: n for k, n in sorted(counts.items()) if n > 1},
        "target_rows": sum(e["target"] is not None for e in universe.values()),
        "source_only_rows": sum(e["target"] is None for e in universe.values()),
        "declared_geometry_targets": sum(t.get("geometry") is not None and t.get("state") != "ignored" for t in targets),
        "retired_geometry_targets": len(retired),
        "declared_targets_outside_view": len(outside),
        "multi_class_rows": sum(len(d["historical_classes"]) > 1 for d in details),
        "contract_status_counts": dict(sorted(Counter(r["contract_status"] for r in rows).items())),
        "historical_coverage_exact": not (missing or extra or changed),
        "target_universe_exhaustiveness": "NOT_ESTABLISHED"},
        "rows": details, "targets_outside_view": outside, "retired_targets": retired}


LEGACY_COLUMNS = (
    "origine_proposition", "nature_ligne", "objet_ou_famille_metier", "type_code",
    "type_libelle", "classe_source_v2", "geometrie_v2_prouvee",
    "geometrie_cible_ou_candidate", "statut_geometrie", "si_absente",
    "si_type_inattendu", "commentaire", "a_revoir",
)


def read_legacy(text):
    """Only parse; never import a legacy proposal as a target decision."""
    if not text.strip():
        raise ValueError("LEGACY_HEADER: empty work sheet")
    header = text.removeprefix("\ufeff").splitlines()[0]
    delimiter = "\t" if "\t" in header else ";"
    reader = csv.DictReader(io.StringIO(text.removeprefix("\ufeff")), delimiter=delimiter, strict=True)
    if reader.fieldnames != list(LEGACY_COLUMNS):
        raise ValueError("LEGACY_HEADER: expected the thirteen work-sheet columns")
    rows = list(reader)
    if any(None in row or None in row.values() for row in rows):
        raise ValueError("LEGACY_ROW_WIDTH: malformed work-sheet record")
    return rows


def reconcile(model, text):
    """Exact class tokens + referenced catalogue identifiers; no label joins.

    A worksheet content digest and occurrence number locate evidence, not a
    canonical business identity. Proposals and unresolved records are retained.
    """
    rows = read_legacy(text)
    facts = model["historical"]["facts"]
    classes = defaultdict(list)
    references = defaultdict(list)
    catalog_values = defaultdict(list)
    spatial = defaultdict(list)
    contracts = [f for f in facts if f["kind"] == "contract_mapping" and isinstance(f["data"].get("value"), dict)]
    destinations = defaultdict(set)
    for fact in facts:
        if fact["kind"] == "class":
            classes[fact["name"]].append(fact)
        elif fact["kind"] == "geometry":
            spatial[fact["owner"]].append(fact)
        elif fact["kind"] == "application" and fact["data"].get("property_kind") == "reference":
            references[fact["owner"]].append(fact)
        elif fact["kind"] == "catalog_value":
            value = fact["data"]["value"]
            catalog_values[(str(value.get("id", "")).split(":")[0], value.get("abrege"))].append(fact)
    for owner, entry in owner_universe(model).items():
        for fact in entry["historical"]:
            destinations[fact["owner"]].add("geometry:" + owner)
    records, seen = [], Counter()
    for row in rows:
        fingerprint = digest(row)
        seen[fingerprint] += 1
        record = {"record_sha256": fingerprint, "occurrence": seen[fingerprint],
                  "source_record": row, "approval": "A_APPROUVER",
                  "classes": [], "code_evidence": [], "view_keys": [], "diagnostics": []}
        if seen[fingerprint] > 1:
            record.update(outcome="DOUBLON_AVERE", reason="Contenu intégral identique à une occurrence précédente.")
        elif row["origine_proposition"] in {"CCTP_V3", "PROPOSITION_DIGUES_APP"}:
            record.update(outcome="PROPOSITION_NON_APPROUVEE", reason="Proposition sans identité source déclarée ; aucun rapprochement par libellé.")
        else:
            names = [n.strip() for n in re.split(r"[;/]", row["classe_source_v2"]) if n.strip()]
            for name in names:
                matches = classes[name]
                if len(matches) != 1:
                    record["diagnostics"].append({"code": "CLASS_IDENTITY_UNPROVEN", "token": name,
                                                  "candidates": [f["id"] for f in matches]})
                else:
                    record["classes"].append(matches[0])
            if not names:
                record["diagnostics"].append({"code": "NO_SOURCE_IDENTITY"})
            if row["nature_ligne"] == "GEOMETRIE_SPECIALISEE" and row["type_code"]:
                record["diagnostics"].append({"code": "SPECIALIZED_CODE_WITHOUT_PROPERTY_IDENTITY",
                                              "type_code": row["type_code"]})
            keys = set()
            for cls in record["classes"]:
                keys.update(destinations[cls["id"]])
                if not destinations[cls["id"]]:
                    record["diagnostics"].append({"code": "NO_GEOMETRY_FACT_OR_VIEW_ROW", "class": cls["id"]})
                if row["nature_ligne"] == "TYPE_METIER_EXISTANT" and row["type_code"]:
                    evidence, near_values = [], []
                    for ref in references[cls["id"]]:
                        target_name = ref["data"]["type"].rsplit("/", 1)[-1]
                        for value in catalog_values[(target_name, row["type_code"])]:
                            evidence.append({"reference": ref, "catalog_value": value})
                        for (prefix, abbreviation), values in list(catalog_values.items()):
                            if prefix == target_name and isinstance(abbreviation, str) and abbreviation != row["type_code"] and abbreviation.strip() == row["type_code"].strip():
                                near_values.extend(values)  # diagnostic only, never evidence for joining
                    if not evidence:
                        record["diagnostics"].append({"code": "TYPE_CODE_NOT_PROVED_IN_REFERENCED_CATALOGUE",
                                                      "class": cls["id"], "type_code": row["type_code"],
                                                      "whitespace_differences_not_equivalences": near_values})
                    record["code_evidence"].extend(evidence)
            record["view_keys"] = sorted(keys)
            if len(keys) != 1:
                record["diagnostics"].append({"code": "NO_UNIQUE_VIEW_DESTINATION", "candidates": sorted(keys)})
            record["outcome"] = ("INDETERMINEE" if record["diagnostics"] else
                                 "REGROUPEMENT_DEMONTRE" if len(record["classes"]) > 1 else "CORRESPONDANCE_UNIQUE")
            record["reason"] = "Identités et preuves ci-dessous ; aucune proposition cible promue."
        history = [f for cls in record["classes"] for f in spatial[cls["id"]]]
        class_names = {cls["name"] for cls in record["classes"]}
        value_ids = {e["catalog_value"]["data"]["value"]["id"] for e in record["code_evidence"]}
        record["historical_contract_evidence"] = [
            {"fact": fact, "entry": entry, "authority": "HISTORICAL_CONTRACT_ONLY"}
            for fact in contracts for entry in fact["data"].get("value", {}).get("entries", [])
            if entry.get("key") in class_names | value_ids]
        record["historical_geometry_facts"] = history
        record["shape_check"] = {
            "worksheet_claim": row["geometrie_v2_prouvee"],
            "exact_jts_types": sorted({f["data"]["type"] for f in history}),
            "extracted_shapes": sorted({f["data"]["shape"] for f in history}),
            "status": "WORKSHEET_CLAIM_NOT_A_TYPE_PROOF",
            "target_proposal": row["geometrie_cible_ou_candidate"],
            "target_proposal_approved": False,
            "reason": "Geometry générique ne prouve pas P/L/S ; absence de déclaration ne prouve pas Ø. Les valeurs originales restent distinctes."}
        if history and row["geometrie_v2_prouvee"] == "Ø":
            record["shape_check"]["scope_diagnostic"] = "EMPTY_GEOMETRY_CLAIM_WITH_SPATIAL_CLASS_REQUIRES_SCOPE_PROOF"
        elif not history and row["geometrie_v2_prouvee"] not in {"", "Ø", "INCONNU"}:
            record["shape_check"]["scope_diagnostic"] = "SHAPE_CLAIM_WITHOUT_CLASS_GEOMETRY_FACT"
        records.append(record)
    outcomes = Counter(r["outcome"] for r in records)
    return {"summary": {"records": len(records),
                         "outcomes": {key: outcomes[key] for key in ("CORRESPONDANCE_UNIQUE", "REGROUPEMENT_DEMONTRE", "PROPOSITION_NON_APPROUVEE", "DOUBLON_AVERE", "INDETERMINEE")},
                         "sha256": sha256(text.encode()).hexdigest(),
                         "different_source_and_target_claims": sum(r["geometrie_v2_prouvee"] != r["geometrie_cible_ou_candidate"] for r in rows)},
            "records": records}


def qualify(model, text, *, source_paths=None):
    # Reuse the import validator, including protected columns and freshness.
    plan = check_import(model, text, source_paths=source_paths)
    result = coverage(model)
    result["import_check"] = plan
    result["summary"].update(
        status="QUALIFIED_KNOWN_UNIVERSE_NOT_CERTIFIED",
        csv_sha256=sha256(text.encode("utf-8")).hexdigest(),
        deterministic_export_sha256=sha256(csv_bytes(model)).hexdigest(),
        registry_sha256=plan["registry_before_sha256"],
        model_digest=model["model_digest"],
        proposed_changes=len(plan["changes"]))
    return result
