"""Compose the manual authority with immutable, independently extracted facts."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from . import ENGINE_VERSION, SCHEMA_VERSION
from .common import GitEvidence, canonical_bytes, digest, engine_digest, file_digest, strict_load
from .resolution import resolve_mappings

PACKAGE = Path(__file__).resolve().parent
REPOSITORY = PACKAGE.parent.parent
DEFAULT_OUTPUT_DIR = PACKAGE
DEFAULT_MODEL_PATH = DEFAULT_OUTPUT_DIR / "model.json"
DEFAULT_AUDIT_DIR = REPOSITORY / "audits" / "master-model"
DEFAULT_DIAGNOSTICS_PATH = DEFAULT_AUDIT_DIR / "diagnostics.json"


def source_paths(manifest: dict, repo_root: Path, sirs_root: Path) -> dict[str, Path]:
    roots = {"repo": repo_root.resolve(), "sirs": sirs_root.resolve()}
    result = {}
    for source in manifest["sources"]:
        root = roots[source["root"]]
        path = (root / source["path"]).resolve()
        if not path.is_relative_to(root) or Path(source["path"]).is_absolute():
            raise ValueError(f"INV-VER.SOURCE_OUTSIDE_ROOT: {source['id']}")
        if source["id"] in result:
            raise ValueError(f"INV-ID.DUPLICATE_SOURCE: {source['id']}")
        result[source["id"]] = (GitEvidence(root, source["git_revision"], source["path"])
                                if "git_revision" in source else path)
    return result


def check_sources(manifest: dict, paths: dict[str, Path]) -> None:
    for source in manifest["sources"]:
        path = paths[source["id"]]
        if not path.is_file():
            raise ValueError(f"INV-VER.SOURCE_MISSING: {source['id']}")
        if file_digest(path) != source["sha256"]:
            raise ValueError(f"INV-VER.SOURCE_DIGEST_MISMATCH: {source['id']}")


def normalized_registry(registry: dict) -> dict:
    """Normalize explicitly unordered authoring collections, never data arrays."""
    result = deepcopy(registry)
    for collection in ("scopes", "decisions", "targets", "rules", "bindings", "projections",
                       "contracts", "limitations", "correspondences", "mapping_rules", "obligations",
                       "inventory_limitations"):
        for item in result.get(collection, []):
            for field in ("depends_on", "decision_refs", "sources", "rules", "bindings",
                          "owners", "approved_members", "constraints", "subjects", "profiles",
                          "limitation_refs", "supersedes", "superseded_by", "controls", "columns", "requirements"):
                if field in item and isinstance(item[field], list):
                    item[field] = sorted(item[field], key=canonical_bytes)
            if "outputs" in item:
                item["outputs"] = sorted(item["outputs"], key=lambda i: (i["role"], i["target"]))
            if "supersessions" in item:
                item["supersessions"].sort(key=canonical_bytes)
            if item.get("geometry") is not None:
                item["geometry"]["target_shapes"].sort()
            if collection == "mapping_rules" and item["operator"] == "evidence":
                item["parameters"]["kinds"].sort()
            item["provenance"] = sorted(item["provenance"], key=lambda i: (i["source"], i["locator"]))
        if collection in result:
            result[collection].sort(key=lambda i: i["id"])
    if "inventory" in result:
        from .inventory import normalize_members
        result["inventory"] = normalize_members(result["inventory"])
    return result


def assemble(manifest: dict, registry: dict, historical: dict) -> dict:
    registry = normalized_registry(registry)
    manifest = deepcopy(manifest)
    manifest["sources"].sort(key=lambda s: s["id"])
    manifest["expected_models"].sort()
    mappings = resolve_mappings(registry, historical)
    model = {
        "schema_version": SCHEMA_VERSION,
        "engine_version": ENGINE_VERSION,
        "engine_sha256": engine_digest(),
        "schema_sha256": file_digest(PACKAGE / "registry.schema.json"),
        "registry_revision": registry["revision"],
        "manifest": manifest,
        "registry": registry,
        "historical": historical,
        "mappings": sorted(mappings, key=lambda m: m["source"]),
    }
    # Impact edges are a pure projection of these declarations. Serializing
    # them duplicates tens of thousands of edges without adding authority.
    model["input_digest"] = digest({k: model[k] for k in
                                   ("engine_version", "engine_sha256", "manifest", "registry", "schema_sha256")})
    model["model_digest"] = digest(model)
    return model


def build(*, repo_root: Path = REPOSITORY, sirs_root: Path | None = None,
          manifest_path: Path = PACKAGE / "manifest.json",
          registry_path: Path = PACKAGE / "registry.json") -> dict:
    from jsonschema import Draft202012Validator
    from .extraction import extract_history

    manifest = strict_load(manifest_path)
    registry = strict_load(registry_path)
    schema = strict_load(PACKAGE / "registry.schema.json")
    Draft202012Validator.check_schema(schema)
    errors = sorted(Draft202012Validator(schema).iter_errors(registry), key=lambda e: str(list(e.path)))
    if errors:
        raise ValueError("INV-REF.REGISTRY_SCHEMA: " + errors[0].message)
    paths = source_paths(manifest, repo_root, sirs_root or repo_root.parent / "sirs-255-build")
    # extract_history checks the exact pinned bytes before parsing. Avoid a
    # second full pass over the source files here.
    return assemble(manifest, registry, extract_history(manifest, paths))


def check_model_freshness(model: dict, *, repo_root: Path = REPOSITORY,
                          sirs_root: Path | None = None,
                          manifest_path: Path = PACKAGE / "manifest.json",
                          registry_path: Path = PACKAGE / "registry.json",
                          check_source_hashes: bool = True) -> dict[str, Path]:
    """Reject a compiled artifact whose current pinned inputs have changed.

    This bounded check hashes source bytes but does not repeat Ecore/C1/C2/C3
    extraction. It is deliberately separate from validation so a malformed
    artifact can still receive all relevant invariant diagnostics.
    """
    current_manifest = strict_load(manifest_path)
    current_manifest["sources"].sort(key=lambda source: source["id"])
    current_manifest["expected_models"].sort()
    if model.get("manifest") != current_manifest:
        raise ValueError("INV-VER.MANIFEST_MISMATCH_REBUILD_REQUIRED")
    if model.get("registry") != normalized_registry(strict_load(registry_path)):
        raise ValueError("INV-VER.REGISTRY_MISMATCH_REBUILD_REQUIRED")
    if model.get("engine_sha256") != engine_digest():
        raise ValueError("INV-VER.ENGINE_MISMATCH_REBUILD_REQUIRED")
    if model.get("schema_sha256") != file_digest(PACKAGE / "registry.schema.json"):
        raise ValueError("INV-VER.SCHEMA_MISMATCH_REBUILD_REQUIRED")
    paths = source_paths(current_manifest, repo_root, sirs_root or repo_root.parent / "sirs-255-build")
    if check_source_hashes:
        check_sources(current_manifest, paths)
    return paths


def load_model(path: Path) -> dict:
    model = strict_load(path)
    if (model.get("schema_version") != SCHEMA_VERSION
            or model.get("engine_version") != ENGINE_VERSION
            or model.get("engine_sha256") != engine_digest()):
        raise ValueError("INV-VER.ENGINE_MISMATCH_REBUILD_REQUIRED")
    supplied = model.get("model_digest")
    actual = digest({k: v for k, v in model.items() if k != "model_digest"})
    if supplied != actual:
        raise ValueError("INV-REP.MODEL_DIGEST_MISMATCH")
    return model


def write_artifact(value: dict, path: Path) -> str:
    """Write diagnostic artifacts only into the untracked audits area."""
    path = Path(path)
    resolved = path.resolve()
    if resolved.is_relative_to(PACKAGE.resolve()):
        raise ValueError("INV-REP.CANNOT_OVERWRITE_AUTHORITY")
    if not resolved.is_relative_to((REPOSITORY / "audits").resolve()):
        raise ValueError("INV-REP.OUTPUT_OUTSIDE_GENERATED_DIRECTORIES")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_bytes(value)
    if not path.is_file() or path.read_bytes() != payload:
        path.write_bytes(payload)
    return file_digest(path)


def write_model(model: dict, path: Path = DEFAULT_MODEL_PATH, *, sirs_root: Path | None = None) -> str:
    """Generated model wrapper; authored authority files are never rewritten."""
    path = Path(path)
    resolved = path.resolve()
    if resolved == DEFAULT_MODEL_PATH.resolve():
        if model.get("model_digest") != digest({k: v for k, v in model.items() if k != "model_digest"}):
            raise ValueError("INV-REP.MODEL_DIGEST_MISMATCH")
        check_model_freshness(model, sirs_root=sirs_root)
        payload = canonical_bytes(model)
        if not path.is_file() or path.read_bytes() != payload:
            path.write_bytes(payload)
        return file_digest(path)
    return write_artifact(model, path)
