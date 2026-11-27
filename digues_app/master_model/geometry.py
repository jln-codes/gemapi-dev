"""Deterministic, temporary CSV view of the master registry's geometry owners.

Only the registry is an authority. Source-only rows keep uncovered history
visible but cannot create target identities. No old worksheet is read here.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import csv
import io
import json
import os
from pathlib import Path
import re
import tempfile

from jsonschema import Draft202012Validator

from .common import canonical_bytes, digest, file_digest, strict_load
from .engine import (DEFAULT_AUDIT_DIR, PACKAGE, REPOSITORY, assemble,
                     normalized_registry)
from .geometry_contracts import APPROVED, PENDING, SHAPE_VALUES, geometry_errors


FORMAT_VERSION = "MOD-1F.1"
CSV_DIRECTORY = REPOSITORY / "private/digues-app/master-model"
# The current MOD-1F view is the only editable CSV in the private directory.
DEFAULT_CSV = CSV_DIRECTORY / "contrats-geometriques.csv"
DEFAULT_VIEW_CSV = DEFAULT_CSV
PROTECTED = (
    "format_version", "canonical_key", "owner", "label", "kind", "owner_status",
    "key_status", "contract_status", "qualification", "owner_provenance",
    "source_geometry_facts", "registry_revision", "registry_sha256", "model_digest",
)
EDITABLE = ("target_shapes", "required_for_valid", "justification")
COLUMNS = (*PROTECTED, *EDITABLE)
OWNER_KINDS = frozenset({"concept", "type", "subtype", "component", "geometry", "attribute"})


class GeometryExchangeError(ValueError):
    def __init__(self, diagnostics):
        self.diagnostics = sorted(diagnostics, key=lambda d: (d["key"], d["code"], d["message"]))
        super().__init__("; ".join(f"{d['code']} [{d['key']}]: {d['message']}" for d in self.diagnostics))


def _diagnostic(code, key, message):
    return {"code": "INV-GEO.CSV_" + code, "key": key, "message": message}


def _json(value):
    return canonical_bytes(value).decode("utf-8").rstrip("\n")


def owner_universe(model):
    """Exact registry links and resolved class mappings, never label matching.

    A direct proposed correspondence is kept even before qualification. Every
    historical owner with no registered destination gets a source-only row.
    Several candidate destinations are exposed, never selected by order.
    """
    registry = model["registry"]
    facts = {f["id"]: f for f in model["historical"]["facts"]}
    if len(facts) != len(model["historical"]["facts"]):
        raise GeometryExchangeError([_diagnostic("DUPLICATE_FACT", "", "Identité historique dupliquée.")])
    targets = {t["id"]: t for t in registry["targets"]}
    if len(targets) != len(registry["targets"]):
        raise GeometryExchangeError([_diagnostic("DUPLICATE_OWNER", "", "Propriétaire canonique dupliqué.")])
    spatial = defaultdict(list)
    for fact in facts.values():
        if fact["kind"] == "geometry":
            if fact["owner"] not in facts:
                raise GeometryExchangeError([_diagnostic("OWNER_MISSING", fact["id"], "Propriétaire historique absent.")])
            spatial[fact["owner"]].append(fact)
    destinations = defaultdict(set)
    links = defaultdict(list)

    def associate(source, target, evidence):
        fact = facts.get(source, {})
        owner = source if source in spatial else fact.get("owner") if fact.get("kind") == "geometry" else None
        if (owner in spatial and target in targets and targets[target]["kind"] in OWNER_KINDS
                and targets[target].get("state") != "ignored"):
            destinations[owner].add(target)
            links[target].append({"historical_owner": owner, "source": source,
                                  "target": target, **evidence})

    for row in registry.get("correspondences", []):
        associate(row["source"], row["target"], {"kind": "correspondence", "declaration": row})
    for rule in registry.get("rules", []):
        for source in rule["sources"]:
            for output in rule["outputs"]:
                associate(source, output["target"], {"kind": "rule", "declaration": rule})
    for row in model["mappings"]:
        if facts.get(row["source"], {}).get("kind") == "class":
            for target in row["targets"]:
                associate(row["source"], target, {"kind": "mapping", "declaration": row})
    owners = {t["id"] for t in targets.values()
              if t.get("geometry") is not None and t.get("state") != "ignored"}
    owners.update(t for values in destinations.values() for t in values)
    entries = {}
    for owner in sorted(owners):
        source_owners = sorted(s for s, values in destinations.items() if owner in values)
        history = sorted((f for s in source_owners for f in spatial[s]), key=lambda f: f["id"])
        issues = []
        if targets[owner].get("geometry") is None:
            issues.append("SANS_CONTRAT")
        if any(len(destinations[s]) > 1 for s in source_owners):
            issues.append("QUALIFICATION_AMBIGUE")
        if not history:
            issues.append("HISTORIQUE_NON_RATTACHE")
        entries[owner] = {"target": targets[owner], "historical": history, "issues": issues,
                          "links": sorted(links[owner], key=_json)}
    for source in sorted(spatial):
        if not destinations[source]:
            entries[source] = {"target": None, "historical": sorted(spatial[source], key=lambda f: f["id"]),
                               "issues": ["SANS_CIBLE_QUALIFIEE"], "links": []}
    covered = {f["id"] for e in entries.values() for f in e["historical"]}
    if covered != {f["id"] for group in spatial.values() for f in group}:
        raise GeometryExchangeError([_diagnostic("COVERAGE", "", "Couverture historique incomplète.")])
    return entries


def geometry_rows(model):
    registry = model["registry"]
    fingerprint = digest(normalized_registry(registry))
    facts = {f["id"]: f for f in model["historical"]["facts"]}
    rows = []
    for owner, entry in sorted(owner_universe(model).items()):
        target = entry["target"]
        record = target or facts[owner]
        geometry = target.get("geometry") if target else None
        rows.append({
            "format_version": FORMAT_VERSION,
            "canonical_key": "geometry:" + owner,
            "owner": owner, "label": record.get("label", record.get("name", owner)),
            "kind": record["kind"],
            "owner_status": target["status"] if target else "HISTORIQUE",
            "key_status": target["key_status"] if target else "IDENTITE_SOURCE",
            "contract_status": geometry["status"] if geometry else PENDING,
            "qualification": _json(entry["issues"]),
            "owner_provenance": _json(sorted(record["provenance"], key=lambda p: (p["source"], p["locator"]))),
            "source_geometry_facts": _json(entry["historical"]),
            "registry_revision": registry["revision"], "registry_sha256": fingerprint,
            "model_digest": model["model_digest"],
            "target_shapes": "|".join(sorted(geometry["target_shapes"])) if geometry else PENDING,
            "required_for_valid": ({True: "OUI", False: "NON", PENDING: PENDING}[
                geometry["required_for_valid"]] if geometry else PENDING),
            "justification": geometry["justification"] if geometry else "",
        })
    return sorted(rows, key=lambda r: r["canonical_key"])


def csv_bytes(model):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=COLUMNS, delimiter=";", lineterminator="\n")
    writer.writeheader()
    writer.writerows(geometry_rows(model))
    return stream.getvalue().encode("utf-8")


def export_geometry(model, path=DEFAULT_VIEW_CSV):
    path = Path(path)
    if not path.resolve().is_relative_to(CSV_DIRECTORY.resolve()):
        raise GeometryExchangeError([_diagnostic("OUTPUT_DIRECTORY", str(path), f"Destination attendue : {CSV_DIRECTORY}.")])
    payload = csv_bytes(model)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Exclusive creation also refuses dangling symlinks and concurrent exports.
        with path.open("xb") as handle:
            handle.write(payload)
    except FileExistsError:
        raise GeometryExchangeError([_diagnostic("EXISTS", str(path),
            "Fichier conservé. Exporter avec --output vers un nom libre dans le même dossier, puis rapprocher les propositions manuellement.")]) from None
    rows = geometry_rows(model)
    return {"status": "EXPORTED_NOT_CERTIFIED", "path": str(path), "rows": len(rows),
            "sha256": file_digest(path), "registry_sha256": rows[0]["registry_sha256"] if rows else digest(model["registry"]),
            "model_digest": model["model_digest"],
            "contract_status_counts": dict(sorted(Counter(r["contract_status"] for r in rows).items())),
            "diagnostics": [{"key": r["canonical_key"], "qualification": json.loads(r["qualification"])}
                            for r in rows if r["qualification"] != "[]"]}


def _read_rows(text):
    try:
        reader = csv.reader(io.StringIO(text.removeprefix("\ufeff"), newline=""), delimiter=";", strict=True)
        header = next(reader, [])
        if header != list(COLUMNS):
            raise GeometryExchangeError([_diagnostic("HEADER", "", "Colonnes absentes, supplémentaires, dupliquées ou réordonnées ; format MOD-1F attendu.")])
        rows, errors = [], []
        for values in reader:
            if len(values) != len(COLUMNS):
                errors.append(_diagnostic("ROW_WIDTH", str(reader.line_num), "Nombre de cellules incorrect, y compris pour une ligne vide."))
            else:
                rows.append(dict(zip(COLUMNS, values)))
        if errors:
            raise GeometryExchangeError(errors)
        return rows
    except csv.Error as error:
        raise GeometryExchangeError([_diagnostic("PARSE", "", str(error))]) from error


def _next_revision(revision):
    match = re.fullmatch(r"(.*)\+geometry\.(\d+)", revision)
    return f"{match[1]}+geometry.{int(match[2]) + 1}" if match else revision + "+geometry.1"


def _validation_signature(diagnostic):
    # Its message enumerates obligations, which intentionally change when a
    # target decision is recorded. The blocker itself must remain, with the
    # same source, provenance, severity, profiles and multiplicity.
    if diagnostic["code"] == "INV-MAP.SOURCE_PRESERVED_ONLY":
        return _json({k: v for k, v in diagnostic.items() if k != "message"})
    return _json(diagnostic)


def check_import(model, text, *, source_paths=None):
    """Validate the entire edited universe before producing an in-memory plan.

    Existing certification blockers remain visible. No unrelated blocker is
    waived: the candidate may not introduce a new model-validation diagnostic,
    apart from a deliberately partial geometry decision remaining pending.
    """
    expected_rows = geometry_rows(model)
    expected = {r["canonical_key"]: r for r in expected_rows}
    supplied = _read_rows(text)
    errors = []
    counts = Counter(r["canonical_key"] for r in supplied)
    for key in sorted(set(expected) - set(counts)):
        errors.append(_diagnostic("MISSING", key, "Ligne attendue absente."))
    for key, count in sorted(counts.items()):
        if count != 1:
            errors.append(_diagnostic("DUPLICATE", key, f"{count} lignes portent cette clé."))
        if key not in expected:
            errors.append(_diagnostic("UNKNOWN", key, "Clé étrangère à l'univers exporté."))
    if len(supplied) != len(expected):
        errors.append(_diagnostic("CARDINALITY", "", f"{len(supplied)} lignes reçues, {len(expected)} attendues."))
    universe = owner_universe(model)
    candidate = deepcopy(model["registry"])
    targets = {t["id"]: t for t in candidate["targets"]}
    changes = []
    for row in supplied:
        key = row["canonical_key"]
        original = expected.get(key)
        if original is None or counts[key] != 1:
            continue
        for column in PROTECTED:
            if row[column] != original[column]:
                code = "STALE" if column in {"format_version", "registry_revision", "registry_sha256", "model_digest"} else "PROTECTED"
                errors.append(_diagnostic(code, key, f"Colonne protégée différente : {column}. Réexporter depuis le registre courant."))
        if all(row[c] == original[c] for c in EDITABLE):
            continue
        owner = original["owner"]
        if owner not in targets:
            errors.append(_diagnostic("SOURCE_ONLY", key, "Qualifier une destination dans le registre avant toute décision cible."))
            continue
        if "QUALIFICATION_AMBIGUE" in universe[owner]["issues"]:
            errors.append(_diagnostic("AMBIGUOUS", key, "Résoudre les destinations concurrentes dans le registre avant d'éditer ce contrat."))
            continue
        tokens = row["target_shapes"].split("|")
        if any(t not in SHAPE_VALUES for t in tokens) or len(tokens) != len(set(tokens)):
            errors.append(_diagnostic("SHAPES_INVALID", key, "Formes vides, inconnues ou dupliquées ; aucune interprétation implicite."))
            continue
        if row["required_for_valid"] not in {"OUI", "NON", PENDING}:
            errors.append(_diagnostic("REQUIRED_INVALID", key, "Valeur attendue : OUI, NON ou A_APPROUVER ; le vide est interdit."))
            continue
        required = {"OUI": True, "NON": False, PENDING: PENDING}[row["required_for_valid"]]
        before = targets[owner].get("geometry")
        after = deepcopy(before) if before else {"status": PENDING, "policy": "preserve_if_present", "contract": None}
        after.update(target_shapes=sorted(tokens), required_for_valid=required, justification=row["justification"])
        # A comment or a permutation of shapes cannot approve a proposal.
        decision_changed = (before is None or after["target_shapes"] != sorted(before["target_shapes"])
                            or required != before["required_for_valid"])
        if decision_changed:
            after["status"] = "DECIDE" if PENDING not in tokens and required != PENDING else PENDING
        for code, message in geometry_errors(after, historical=bool(universe[owner]["historical"])):
            errors.append(_diagnostic(code, key, message))
        if before != after:
            targets[owner]["geometry"] = after
            changes.append({"owner": owner, "before": before, "after": after})
    # Validate unchanged contracts too: an invalid registry cannot be accepted
    # just because the worksheet faithfully repeated its invalid values.
    for owner, entry in universe.items():
        if owner in targets:
            for code, message in geometry_errors(targets[owner].get("geometry"), historical=bool(entry["historical"])):
                diagnostic = _diagnostic(code, "geometry:" + owner, message)
                if diagnostic not in errors:
                    errors.append(diagnostic)
    if errors:
        raise GeometryExchangeError(errors)
    schema = strict_load(PACKAGE / "registry.schema.json")
    schema_errors = sorted(Draft202012Validator(schema).iter_errors(candidate), key=lambda e: str(list(e.path)))
    if schema_errors:
        raise GeometryExchangeError([_diagnostic("REGISTRY_SCHEMA", str(list(e.path)), e.message) for e in schema_errors])
    from .validation import validate_model
    before_report = validate_model(model, expected_history=model["historical"], source_paths=source_paths)
    after_report = before_report
    if changes:
        candidate["revision"] = _next_revision(candidate["revision"])
        changed_model = assemble(model["manifest"], candidate, model["historical"])
        after_report = validate_model(changed_model, expected_history=model["historical"], source_paths=source_paths)
        baseline = Counter(_validation_signature(d) for d in before_report["diagnostics"])
        for diagnostic in after_report["diagnostics"]:
            signature = _validation_signature(diagnostic)
            if baseline[signature]:
                baseline[signature] -= 1
                continue
            if diagnostic["code"] != "INV-GEO.TARGET_CONTRACT_PENDING":
                errors.append(_diagnostic("MODEL_VALIDATION", diagnostic["element_id"],
                                          diagnostic["code"] + ": " + diagnostic["message"]))
        if errors:
            raise GeometryExchangeError(errors)
    return {"status": "CHECKED_NOT_CERTIFIED", "rows": len(supplied),
            "registry_before_sha256": digest(normalized_registry(model["registry"])),
            "registry_after_sha256": digest(normalized_registry(candidate)),
            "registry_revision": candidate["revision"], "changes": sorted(changes, key=lambda c: c["owner"]),
            "certifications": after_report["certifications"],
            "diagnostic_counts": dict(sorted(Counter(d["code"] for d in after_report["diagnostics"]).items()))}


def apply_import(plan, *, registry_path=PACKAGE / "registry.json",
                 lock_path=DEFAULT_AUDIT_DIR / ".geometry-import.lock",
                 temporary_directory=DEFAULT_AUDIT_DIR):
    """Apply only a fully checked plan, with a locked compare-before-replace.

    Imports cooperate through a permanent advisory lock (released on crashes).
    A manual editor must not write during application; drift before replacement
    is checked again. The temporary lives in audits/master-model, on the same filesystem.
    """
    import fcntl

    registry_path, lock_path = Path(registry_path), Path(lock_path)
    if registry_path.is_symlink():
        raise GeometryExchangeError([_diagnostic("REGISTRY_SYMLINK", str(registry_path), "Registre symbolique refusé.")])
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        original_bytes = registry_path.read_bytes()
        current = strict_load(registry_path)
        if digest(normalized_registry(current)) != plan["registry_before_sha256"]:
            raise GeometryExchangeError([_diagnostic("STALE", str(registry_path), "Le registre a changé depuis le contrôle ; aucune écriture.")])
        if not plan["changes"]:
            return {**plan, "status": "UNCHANGED", "registry_file_sha256": file_digest(registry_path)}
        indexed = {t["id"]: t for t in current["targets"]}
        for change in plan["changes"]:
            indexed[change["owner"]]["geometry"] = change["after"]
        current["revision"] = plan["registry_revision"]
        if digest(normalized_registry(current)) != plan["registry_after_sha256"]:
            raise GeometryExchangeError([_diagnostic("PLAN_CHANGED", "", "Le plan contrôlé a été altéré." )])
        payload = (json.dumps(current, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        Path(temporary_directory).mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="geometry-registry-", suffix=".json", dir=temporary_directory)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.chmod(registry_path.stat().st_mode & 0o777)
            if registry_path.read_bytes() != original_bytes:
                raise GeometryExchangeError([_diagnostic("STALE", str(registry_path), "Écriture concurrente détectée ; aucune application.")])
            os.replace(temporary, registry_path)
        finally:
            temporary.unlink(missing_ok=True)
        committed_digest = file_digest(registry_path)
    return {**plan, "status": "APPLIED_REBUILD_REQUIRED", "registry_file_sha256": committed_digest}
