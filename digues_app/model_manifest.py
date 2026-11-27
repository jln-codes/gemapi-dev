"""Extraction déterministe des modèles structurels SIRS V2 versionnés."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REFERENCE_DIR = Path("private/sirs-v2/models/2.55")
DEFAULT_ECORE_PATH = DEFAULT_REFERENCE_DIR / "core.ecore"
DEFAULT_LABELS_PATH: Path | None = None
DEFAULT_VEGETATION_ECORE_PATH = DEFAULT_REFERENCE_DIR / "vegetation.ecore"
DEFAULT_VEGETATION_LABELS_PATH: Path | None = None
DEFAULT_DEPENDANCE_ECORE_PATH = DEFAULT_REFERENCE_DIR / "dependance.ecore"
DEFAULT_DEPENDANCE_LABELS_PATH: Path | None = None
DEFAULT_LIT_ECORE_PATH = DEFAULT_REFERENCE_DIR / "lit.ecore"
DEFAULT_BERGE_ECORE_PATH = DEFAULT_REFERENCE_DIR / "berge.ecore"
DEFAULT_MANIFEST_PATH = DEFAULT_REFERENCE_DIR / "model-manifest.json"

SIRS_V2_VERSION = "2.55"
SIRS_V2_SOURCE_REPOSITORY = "https://github.com/Geomatys/SIRS-Digues-V2.git"
SIRS_V2_SOURCE_COMMIT = "ff06d0b8aff2093811b83e7c062897d54814c285"

XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"


@dataclass(frozen=True)
class EcoreField:
    name: str
    kind: str
    type: str | None
    raw_type: str | None
    declared_in: str
    lower_bound: int
    upper_bound: int
    upper_bound_raw: str
    many: bool
    containment: bool | None
    opposite: str | None
    opposite_raw: str | None
    default_value: str | None
    annotations: tuple[dict[str, object], ...]
    label: str | None = None

    def for_class(self, class_name: str) -> dict[str, object]:
        return {
            "name": self.name,
            "label": self.label,
            "kind": self.kind,
            "type": self.type,
            "raw_type": self.raw_type,
            "declared_in": self.declared_in,
            "inherited": self.declared_in != class_name,
            "lower_bound": self.lower_bound,
            "upper_bound": self.upper_bound,
            "upper_bound_raw": self.upper_bound_raw,
            "many": self.many,
            "containment": self.containment,
            "opposite": self.opposite,
            "opposite_raw": self.opposite_raw,
            "default_value": self.default_value,
            "annotations": list(self.annotations),
        }


@dataclass(frozen=True)
class EcoreClass:
    name: str
    model: str
    abstract: bool
    super_types: tuple[str, ...]
    couchdb_document: bool
    annotations: tuple[dict[str, object], ...]
    declared_fields: tuple[EcoreField, ...]
    class_labels: dict[str, str]


@dataclass(frozen=True)
class ModelSource:
    """Source structurelle minimale d'un modèle ou plugin SIRS V2."""

    key: str
    family: str
    ecore_path: Path
    labels_path: Path | None = None
    version: str = SIRS_V2_VERSION
    source_repository: str = SIRS_V2_SOURCE_REPOSITORY
    source_commit: str = SIRS_V2_SOURCE_COMMIT


DEFAULT_MODEL_SOURCES = (
    ModelSource("core", "core", DEFAULT_ECORE_PATH),
    ModelSource("dependance", "dependance", DEFAULT_DEPENDANCE_ECORE_PATH),
    ModelSource("vegetation", "vegetation", DEFAULT_VEGETATION_ECORE_PATH),
    ModelSource("lit", "lit", DEFAULT_LIT_ECORE_PATH),
    ModelSource("berge", "berge", DEFAULT_BERGE_ECORE_PATH),
)


def _project_path(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with _project_path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _type_name(raw_type: str | None) -> str | None:
    if not raw_type:
        return None
    if "#//" in raw_type:
        return raw_type.rsplit("#//", 1)[-1]
    return raw_type.rsplit("/", 1)[-1].rsplit("#", 1)[-1]


def _opposite_name(raw_opposite: str | None) -> str | None:
    if not raw_opposite:
        return None
    return raw_opposite.rsplit("/", 1)[-1]


def _parse_super_types(raw_super_types: str | None) -> tuple[str, ...]:
    if not raw_super_types:
        return ()
    return tuple(
        item.rsplit("#//", 1)[-1]
        for item in raw_super_types.split()
        if item.strip()
    )


def _parse_annotations(element: ElementTree.Element) -> tuple[dict[str, object], ...]:
    annotations: list[dict[str, object]] = []
    for child in element:
        if _strip_namespace(child.tag) != "eAnnotations":
            continue
        details = {
            detail.attrib.get("key"): detail.attrib.get("value", "")
            for detail in child
            if _strip_namespace(detail.tag) == "details"
            and detail.attrib.get("key") is not None
        }
        annotations.append(
            {
                "source": child.attrib.get("source"),
                "details": dict(sorted(details.items())),
            }
        )
    return tuple(annotations)


def _couchdb_document(annotations: tuple[dict[str, object], ...]) -> bool:
    for annotation in annotations:
        if annotation.get("source") != "couchDBDocument":
            continue
        details = annotation.get("details")
        if isinstance(details, dict) and "document" in details:
            return str(details["document"]).lower() == "true"
        return True
    return False


def _unescape_java_property(value: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char != "\\" or index + 1 >= len(value):
            result.append(char)
            index += 1
            continue
        escaped = value[index + 1]
        if escaped == "u" and index + 5 < len(value):
            codepoint = value[index + 2 : index + 6]
            try:
                result.append(chr(int(codepoint, 16)))
                index += 6
                continue
            except ValueError:
                pass
        replacements = {
            "t": "\t",
            "n": "\n",
            "r": "\r",
            "f": "\f",
        }
        result.append(replacements.get(escaped, escaped))
        index += 2
    return "".join(result)


def _ends_with_continuation(line: str) -> bool:
    backslashes = 0
    for char in reversed(line):
        if char != "\\":
            break
        backslashes += 1
    return backslashes % 2 == 1


def _split_property(line: str) -> tuple[str, str]:
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char in "=:" or char.isspace():
            key = line[:index].rstrip()
            remainder = line[index:]
            remainder = remainder.lstrip()
            if remainder[:1] in ("=", ":"):
                remainder = remainder[1:].lstrip()
            return key, remainder
    return line, ""


def load_labels(labels_path: Path | None) -> dict[str, dict[str, str]]:
    if labels_path is None:
        return {}
    selected = _project_path(labels_path)
    if not selected.is_dir():
        raise FileNotFoundError(f"Répertoire de libellés absent : {labels_path}")
    labels: dict[str, dict[str, str]] = {}
    for path in sorted(selected.glob("*.properties")):
        entries: dict[str, str] = {}
        logical_lines: list[str] = []
        pending = ""
        for raw_line in path.read_text(encoding="iso-8859-1").splitlines():
            line = raw_line.lstrip()
            if not pending and (not line or line.startswith(("#", "!"))):
                continue
            if pending:
                pending += line
            else:
                pending = line
            if _ends_with_continuation(pending):
                pending = pending[:-1]
                continue
            logical_lines.append(pending)
            pending = ""
        if pending:
            logical_lines.append(pending)
        for line in logical_lines:
            key, value = _split_property(line)
            if key:
                entries[_unescape_java_property(key)] = _unescape_java_property(value)
        labels[path.stem] = entries
    return labels


def parse_ecore(
    ecore_path: Path,
    labels_path: Path | None = None,
    *,
    model: str = "core",
) -> dict[str, EcoreClass]:
    selected = _project_path(ecore_path)
    if not selected.is_file():
        raise FileNotFoundError(f"Snapshot Ecore absent : {ecore_path}")
    labels = load_labels(labels_path)
    root = ElementTree.parse(selected).getroot()
    classes: dict[str, EcoreClass] = {}
    for classifier in root.findall("eClassifiers"):
        if classifier.attrib.get(XSI_TYPE) != "ecore:EClass":
            continue
        class_name = classifier.attrib["name"]
        class_labels = labels.get(class_name, {})
        annotations = _parse_annotations(classifier)
        fields: list[EcoreField] = []
        for feature in classifier.findall("eStructuralFeatures"):
            feature_type = feature.attrib.get(XSI_TYPE)
            if feature_type == "ecore:EAttribute":
                kind = "ATTRIBUTE"
            elif feature_type == "ecore:EReference":
                kind = "REFERENCE"
            else:
                continue
            raw_upper_bound = feature.attrib.get("upperBound", "1")
            upper_bound = int(raw_upper_bound)
            raw_type = feature.attrib.get("eType")
            fields.append(
                EcoreField(
                    name=feature.attrib["name"],
                    kind=kind,
                    type=_type_name(raw_type),
                    raw_type=raw_type,
                    declared_in=class_name,
                    lower_bound=int(feature.attrib.get("lowerBound", "0")),
                    upper_bound=upper_bound,
                    upper_bound_raw=raw_upper_bound,
                    many=upper_bound == -1 or upper_bound > 1,
                    containment=(
                        feature.attrib.get("containment") == "true"
                        if kind == "REFERENCE"
                        else None
                    ),
                    opposite=_opposite_name(feature.attrib.get("eOpposite")),
                    opposite_raw=feature.attrib.get("eOpposite"),
                    default_value=feature.attrib.get("defaultValueLiteral"),
                    annotations=_parse_annotations(feature),
                    label=class_labels.get(feature.attrib["name"]),
                )
            )
        classes[class_name] = EcoreClass(
            name=class_name,
            model=model,
            abstract=classifier.attrib.get("abstract") == "true",
            super_types=_parse_super_types(classifier.attrib.get("eSuperTypes")),
            couchdb_document=_couchdb_document(annotations),
            annotations=annotations,
            declared_fields=tuple(fields),
            class_labels={
                key: class_labels[key]
                for key in ("class", "classPlural", "classAbrege")
                if key in class_labels
            },
        )
    return classes


def resolve_effective_fields(
    class_name: str,
    classes: dict[str, EcoreClass],
    stack: tuple[str, ...] = (),
) -> tuple[EcoreField, ...]:
    if class_name in stack:
        cycle = " -> ".join((*stack, class_name))
        raise ValueError(f"Cycle d'héritage Ecore détecté : {cycle}")
    ecore_class = classes[class_name]
    fields: list[EcoreField] = []
    seen: set[str] = set()
    for super_type in ecore_class.super_types:
        if super_type not in classes:
            continue
        for field in resolve_effective_fields(super_type, classes, (*stack, class_name)):
            if field.name not in seen:
                fields.append(field)
                seen.add(field.name)
    for field in ecore_class.declared_fields:
        if field.name not in seen:
            fields.append(field)
            seen.add(field.name)
    return tuple(fields)


def _source_entry(source: ModelSource) -> dict[str, object]:
    return {
        "key": source.key,
        "family": source.family,
        "version": source.version,
        "ecore": source.ecore_path.as_posix(),
        "ecore_sha256": sha256_file(source.ecore_path),
        "labels": source.labels_path.as_posix() if source.labels_path else None,
        "source_repository": source.source_repository,
        "source_commit": source.source_commit,
    }


def _validate_references(classes: dict[str, EcoreClass]) -> None:
    missing_targets = sorted({
        field.type
        for ecore_class in classes.values()
        for field in ecore_class.declared_fields
        if field.kind == "REFERENCE"
        and field.type
        and field.type not in classes
    })
    if missing_targets:
        raise ValueError(
            "Cibles Ecore introuvables : " + ", ".join(missing_targets)
        )
    invalid_opposites: list[str] = []
    for ecore_class in classes.values():
        for field in ecore_class.declared_fields:
            if not field.opposite_raw or not field.type:
                continue
            target_fields = {
                item.name for item in resolve_effective_fields(field.type, classes)
            }
            if field.opposite not in target_fields:
                invalid_opposites.append(
                    f"{ecore_class.name}.{field.name}->{field.type}.{field.opposite}"
                )
    if invalid_opposites:
        raise ValueError(
            "eOpposite Ecore introuvables : " + ", ".join(sorted(invalid_opposites))
        )


def build_manifest(
    *,
    model_sources: tuple[ModelSource, ...] = DEFAULT_MODEL_SOURCES,
) -> dict[str, object]:
    classes: dict[str, EcoreClass] = {}
    source_entries: list[dict[str, object]] = []
    for source in model_sources:
        source_classes = parse_ecore(
            source.ecore_path,
            source.labels_path,
            model=source.key,
        )
        duplicates = classes.keys() & source_classes.keys()
        if duplicates:
            raise ValueError(
                "Classes Ecore dupliquées entre modèles : "
                + ", ".join(sorted(duplicates))
            )
        classes.update(source_classes)
        source_entries.append(_source_entry(source))
    missing_supers = sorted({
        super_type
        for ecore_class in classes.values()
        for super_type in ecore_class.super_types
        if super_type not in classes
    })
    if missing_supers:
        raise ValueError(f"Supertypes Ecore introuvables : {', '.join(missing_supers)}")
    _validate_references(classes)
    class_entries: dict[str, object] = {}
    for class_name in sorted(classes):
        ecore_class = classes[class_name]
        class_entries[class_name] = {
            "name": ecore_class.name,
            "model": ecore_class.model,
            "abstract": ecore_class.abstract,
            "label": ecore_class.class_labels.get("class"),
            "class_labels": ecore_class.class_labels,
            "super_types": list(ecore_class.super_types),
            "couchdb_document": ecore_class.couchdb_document,
            "annotations": list(ecore_class.annotations),
            "declared_fields": [
                field.for_class(class_name) for field in ecore_class.declared_fields
            ],
            "effective_fields": [
                field.for_class(class_name)
                for field in resolve_effective_fields(class_name, classes)
            ],
        }
    return {
        "model_version": SIRS_V2_VERSION,
        "source": {
            "models": source_entries,
        },
        "generator": {
            "implementation": "digues_app.model_manifest",
            "historical_plugin": "fr.sirs.maven:gen-maven-plugin",
            "historical_goals": ["fxmodel", "fxmodel2sql"],
            "model_package": "fr.sirs.core.model",
        },
        "summary": {
            "class_count": len(class_entries),
            "model_count": len(source_entries),
            "couchdb_document_class_count": sum(
                1
                for ecore_class in classes.values()
                if ecore_class.couchdb_document
            ),
        },
        "classes": class_entries,
    }


def write_manifest(
    output_path: Path = DEFAULT_MANIFEST_PATH,
    *,
    model_sources: tuple[ModelSource, ...] = DEFAULT_MODEL_SOURCES,
) -> dict[str, object]:
    manifest = build_manifest(model_sources=model_sources)
    selected = _project_path(output_path)
    selected.parent.mkdir(parents=True, exist_ok=True)
    selected.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest
