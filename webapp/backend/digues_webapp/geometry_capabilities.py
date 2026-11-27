"""Small executable view of approved geometry decisions for existing editors.

The model is loaded from the installed package and checked against its packaged
authorities. No CSV, Ecore checkout or business database document is consulted.
Capabilities describe implemented operations, never the caller's permissions.
"""

from __future__ import annotations

from functools import lru_cache
from threading import Lock

from fastapi import HTTPException
from pydantic import ValidationError

from digues_app.master_model.common import digest
from digues_app.master_model.engine import (
    DEFAULT_MODEL_PATH, check_model_freshness, load_model,
)
from digues_app.master_model.geometry_contracts import geometry_approved

from .models import (
    OuvrageParticulierCreate, OuvrageParticulierUpdate,
    VegetationCreate, VegetationUpdate,
    TronconLitCreate, TronconLitUpdate,
    ElementMorphologiqueLitCreate, ElementMorphologiqueLitUpdate,
)


SHAPES = {"POINT": "Point", "LINESTRING": "LineString", "POLYGON": "Polygon"}
RESOURCES = {
    "ouvrages-particuliers": (
        "da:concept:ouvrage-particulier", OuvrageParticulierCreate,
        OuvrageParticulierUpdate, True,
    ),
    "vegetation": (
        "da:concept:vegetation", VegetationCreate, VegetationUpdate, False,
    ),
    "troncons-lit": (
        "da:concept:troncon-lit", TronconLitCreate, TronconLitUpdate, False,
    ),
    "elements-morphologiques-lit": (
        "da:concept:element-morphologique-lit", ElementMorphologiqueLitCreate,
        ElementMorphologiqueLitUpdate, False,
    ),
}
LIT_RESOURCES = frozenset({"troncons-lit", "elements-morphologiques-lit"})


_MODEL_LOAD_LOCK = Lock()


@lru_cache(maxsize=1)
def _load_geometry_model_cached() -> dict:
    model = load_model(DEFAULT_MODEL_PATH)
    check_model_freshness(model, check_source_hashes=False)
    return model


def load_geometry_model() -> dict:
    """One checked package per process, loaded before application readiness.

    lru_cache alone permits concurrent misses to run its wrapped function more
    than once. The lock also serializes cold loads from explicit callers.
    """
    with _MODEL_LOAD_LOCK:
        return _load_geometry_model_cached()


def _dto_geometry(model_type) -> tuple[set[str], bool]:
    schema = model_type.model_json_schema()
    shapes: set[str] = set()
    nullable = False

    def visit(node):
        nonlocal nullable
        if "$ref" in node:
            visit(schema["$defs"][node["$ref"].rsplit("/", 1)[1]])
        if node.get("type") == "null":
            nullable = True
        shape = node.get("properties", {}).get("type", {}).get("const")
        if shape:
            shapes.add(shape)
        for key in ("oneOf", "anyOf", "allOf"):
            for child in node.get(key, []):
                visit(child)

    visit(schema["properties"]["geometry"])
    return shapes, "geometry" in schema.get("required", []) and not nullable


def backend_geometry_capabilities(resource: str) -> dict:
    """Read shape support and presence from the real DTOs, not a second list."""
    _, create_model, update_model, _ = RESOURCES[resource]
    create_shapes, required = _dto_geometry(create_model)
    update_shapes, _ = _dto_geometry(update_model)
    try:
        update_model.model_validate({"geometry": None})
        remove = True
    except ValidationError:
        remove = False
    return {
        "allowedTypes": [value for value in SHAPES.values()
                         if value in create_shapes & update_shapes],
        "required": required, "remove": remove,
    }


def build_geometry_capabilities(model: dict, resource: str, *, backend: dict | None = None) -> dict:
    """Project only an explicit active target; incomplete decisions grant nothing.

    Ouvrage particulier alone keeps its existing editor configuration while its
    model decision is pending. This exception is visible in the response and
    never marks the pending geometry contract as approved.
    """
    if resource not in RESOURCES:
        raise HTTPException(404, "Famille géométrique inconnue.")
    target_id, _, _, compatibility = RESOURCES[resource]
    target = next((item for item in model["registry"]["targets"]
                   if item["id"] == target_id), None)
    backend = backend or backend_geometry_capabilities(resource)
    geometry = target.get("geometry") if target else None
    active = bool(target and target.get("state") == "active"
                  and target.get("status") in {"DECIDE", "CONSEQUENCE_NECESSAIRE"})
    approved = active and geometry_approved(geometry)
    diagnostics = []
    allowed: list[str] = []
    required: bool | None = None
    enabled = False
    status = "unavailable"
    if approved:
        shapes = geometry["target_shapes"]
        required = geometry["required_for_valid"]
        unsupported = [shape for shape in shapes if shape not in SHAPES]
        allowed = [SHAPES[shape] for shape in SHAPES if shape in shapes]
        if (unsupported or not allowed
                or not set(allowed) <= set(backend["allowedTypes"])
                or (required is False and backend["required"] is True)):
            status = "divergent"
            diagnostics.append({"code": "BACKEND_GEOMETRY_CONTRACT_DIVERGENCE",
                                "message": "Le contrat approuvé dépasse les capacités de saisie du serveur."})
            allowed = []
        else:
            status, enabled = "approved", True
    else:
        diagnostics.append({"code": "GEOMETRY_CONTRACT_NOT_APPROVED",
                            "message": "Les formes et la présence de géométrie restent à qualifier dans le modèle maître."})
        pending = geometry is None or geometry.get("status") == "A_APPROUVER"
        if compatibility and active and pending:
            status, enabled = "compatibility", True
            allowed, required = backend["allowedTypes"], backend["required"]
            diagnostics.append({"code": "EXISTING_EDITOR_COMPATIBILITY",
                                "message": "Configuration de saisie existante conservée explicitement ; aucun contrat métier approuvé n'est déduit."})
    if resource not in LIT_RESOURCES:
        diagnostics.append({"code": "SQL_GEOMETRY_VALIDITY_NOT_ENFORCED",
                            "message": "Le stockage SQL conserve les géométries historiques ; les contraintes de saisie sont appliquées par l'API, et ne constituent pas une garantie pour une écriture SQL directe."})
    result = {
        "schemaVersion": 1, "resource": resource, "target": target_id,
        "modelDigest": model["model_digest"], "status": status,
        "allowedTypes": allowed, "required": required,
        "create": enabled, "edit": enabled, "replace": enabled,
        "convert": enabled, "remove": bool(enabled and not required and backend["remove"]),
        "validity": {
            "preserveExisting": True, "requiresConformingForValid": True,
            "nonEmpty": True, "nonDegenerate": True, "spatiallyValid": True,
            "dimensions": 2, "srid": "business",
        },
        "diagnostics": diagnostics,
    }
    if resource in LIT_RESOURCES:
        # L01/L02-B require geometry for valid=true, while preserving invalid
        # occurrences without geometry. This does not grant a geometry removal.
        result["requiredForValid"] = required
        result["allowMissingWhenInvalid"] = bool(approved)
    result["contractVersion"] = digest(result)
    return result


def geometry_capabilities(resource: str) -> dict:
    try:
        return build_geometry_capabilities(load_geometry_model(), resource)
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(503, "Le contrat géométrique est indisponible ou périmé.") from exc


def require_geometry_contract_version(resource: str, supplied: str | None) -> None:
    if supplied is None:
        return
    current = geometry_capabilities(resource)["contractVersion"]
    if supplied != current:
        raise HTTPException(409, detail={
            "code": "geometry-contract-changed",
            "message": "Le contrat géométrique a changé. Rechargez le formulaire en conservant votre brouillon.",
            "contractVersion": current,
        }, headers={"Geometry-Contract-Version": current})


def validate_geometry_write(resource: str, *, geometry_type: str | None,
                            valid: bool, is_create: bool, geometry_changed: bool) -> None:
    """Check final type/presence; PostGIS still validates coordinates and topology.

    An omitted geometry on an existing invalid object is retained verbatim.
    This is not an authorization to submit a new forbidden geometry or to mark
    a nonconforming historical object valid.
    """
    if not is_create and not geometry_changed and valid is False:
        return
    contract = geometry_capabilities(resource)
    if not contract["create" if is_create else "edit"]:
        raise HTTPException(422, "Le contrat géométrique ne permet pas cette saisie.")
    normalized = geometry_type.upper() if geometry_type else None
    if normalized is None:
        if contract["required"] and not (valid is False and contract.get("allowMissingWhenInvalid")):
            raise HTTPException(422, "La géométrie est obligatoire pour cette saisie.")
    elif normalized not in {value.upper() for value in contract["allowedTypes"]}:
        raise HTTPException(422, "Cette forme géométrique n'est pas autorisée par le contrat courant.")
