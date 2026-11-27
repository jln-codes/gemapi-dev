"""Shared deterministic encoding and source handles (not business decisions).

Integration contract:
* extraction.extract_history(manifest, source_paths) -> {facts, counts,
  comparisons, limitations}; fact = {id, kind, model, name, owner, data,
  depends_on, provenance}. Provenance items are {source, locator}.
* validation.validate_model(model, expected_history=None, source_paths=None)
  -> {diagnostics, certifications, counts}. Diagnostics contain invariant,
  code, severity, element_id, provenance, message, profiles.
* Model document: schema_version, engine_version, registry_revision, manifest,
  registry, historical, mappings, input_digest, model_digest.
* registry: schema_version, revision, scopes, decisions, targets, rules,
  bindings, projections, contracts, limitations, correspondences, mapping_rules,
  obligations. Bindings distinguish physical location from certification.
  Its strict JSON Schema is registry.schema.json.
* dependencies: {dependent, dependency, role}, derived on demand from model;
  impact follows reverse edges. They are not a second serialized authority.
* mappings: {source, outcome, qualification, rules, targets, reason, provenance,
  depends_on, obligations, classification}; replayed by resolution.py.
  UNRESOLVED, partial logical destinations and source-only preservation do not
  certify migration. registry.schema.json defines the closed result grammar.

"""

from __future__ import annotations

import hashlib
import json
import platform
import sys
import unicodedata
from pathlib import Path
from urllib.parse import quote
import subprocess


class GitEvidence:
    """Read an immutable documented decision without using the working copy."""

    def __init__(self, repository, revision, path):
        import re
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("INV-VER.GIT_REVISION_NOT_PINNED")
        self.repository, self.revision, self.path = repository, revision, path

    def read_bytes(self):
        result = subprocess.run(["git", "-C", str(self.repository), "show",
                                 f"{self.revision}:{self.path}"], capture_output=True, check=False)
        if result.returncode:
            raise ValueError(f"INV-VER.GIT_EVIDENCE_MISSING: {self.revision}:{self.path}")
        return result.stdout

    def is_file(self):
        try:
            self.read_bytes()
            return True
        except ValueError:
            return False

    def read_text(self, encoding="utf-8"):
        return self.read_bytes().decode(encoding)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"INV-ID.DUPLICATE_JSON_KEY: {key}")
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError(f"INV-REP.NONFINITE_NUMBER: {value}")


def strict_load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_pairs,
                      parse_constant=_nonfinite)


def canonical_bytes(value) -> bytes:
    """Object keys sorted; semantic collections are sorted by their producers.

    Ordered values (enum values, source arrays, geometry coordinates) MUST NOT
    be sorted here. No filesystem paths or execution dates are added.
    """
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def digest(value) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def engine_digest() -> str:
    """Fingerprint packaged engine code and the portable Python ABI.

    Optional compiler dependencies may be absent from a runtime image. Their
    installed patch versions cannot define a versioned model's identity.
    Registry, schema and pinned source hashes are checked separately.
    """
    package = Path(__file__).resolve().parent
    app = package.parent
    reused = ("__init__.py", "model_manifest.py", "references/__init__.py",
              "references/validation.py", "structure_contract/__init__.py",
              "target/__init__.py", "target/schema.py", "crs.py")
    paths = [*package.glob("*.py"), *(app / relative for relative in reused)]
    files = {str(path.relative_to(app)): file_digest(path) for path in sorted(paths)}
    runtime = {
        "python_abi": f"{sys.version_info.major}.{sys.version_info.minor}",
        "implementation": platform.python_implementation(),
    }
    return digest({"files": files, "runtime": runtime})


def source_id(kind: str, model: str, *parts: str, authority="sirs-v2") -> str:
    segments = (model, *parts)
    if any(not part or unicodedata.normalize("NFC", part) != part for part in segments):
        raise ValueError("INV-ID.INVALID_SOURCE_SEGMENT")
    return f"{authority}:{kind}:" + "/".join(quote(part, safe="-._~") for part in segments)
