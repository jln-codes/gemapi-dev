"""Target geometry decisions, distinct from source facts and runtime transforms."""

from __future__ import annotations


PENDING = "A_APPROUVER"
APPROVED = frozenset({"DECIDE", "CONSEQUENCE_NECESSAIRE"})
SHAPES = ("POINT", "LINESTRING", "POLYGON", "MULTIPOINT", "MULTILINESTRING",
          "MULTIPOLYGON", "GEOMETRYCOLLECTION")
TECHNICAL_GEOMETRY_TARGETS = frozenset("da:component:" + name for name in (
    "largeur-lit", "pente-lit", "domanialite-lit", "regime-ecoulement-lit", "occupation-riveraine-lit"))
MORPHOLOGY_TARGETS = frozenset({"da:concept:element-morphologique-lit"} | {
    "da:type:element-morphologique-lit/" + name
    for name in ("ile", "banc", "atterrissement", "indetermine")
})
SHAPE_VALUES = frozenset((*SHAPES, "GEOMETRY", "AUCUNE", PENDING))


def geometry_errors(geometry, *, historical=False):
    """Semantic checks shared by CSV import and ordinary model validation.

    A narrower target never transforms historical coordinates. An unexpected
    source remains retained/invalid under the existing geometry-import contract.
    """
    if geometry is None:
        return []
    errors = []
    shapes = geometry.get("target_shapes")
    required = geometry.get("required_for_valid")
    if (not isinstance(shapes, list) or not shapes or
            any(not isinstance(s, str) or s not in SHAPE_VALUES for s in shapes) or
            len(shapes) != len(set(shapes))):
        return [("SHAPES_INVALID", "Formes cibles absentes, inconnues ou dupliquées.")]
    if any(s in shapes for s in (PENDING, "AUCUNE", "GEOMETRY")) and len(shapes) != 1:
        errors.append(("SHAPES_INCOMPATIBLE", "A_APPROUVER, AUCUNE et GEOMETRY s'utilisent seuls."))
    if type(required) is not bool and required != PENDING:
        errors.append(("REQUIRED_INVALID", "La contrainte valid=true exige OUI, NON ou A_APPROUVER."))
    if shapes == ["AUCUNE"] and required is not False:
        errors.append(("REQUIRED_INCOMPATIBLE", "AUCUNE exige explicitement NON."))
    technical = geometry.get("policy") == "preserve_in_technical_support"
    if technical and (shapes != ["AUCUNE"] or required is not False):
        errors.append(("TECHNICAL_POLICY_INVALID", "La conservation technique seule exige AUCUNE et NON côté métier."))
    if shapes == ["AUCUNE"] and historical and not technical:
        errors.append(("HISTORICAL_LOSS", "Un propriétaire historiquement spatial ne peut être déclaré sans géométrie."))
    if geometry.get("policy") not in {"preserve_if_present", "preserve_in_technical_support"}:
        errors.append(("TRANSFORMATION_UNQUALIFIED", "Ce cycle autorise uniquement la conservation exacte des géométries sources."))
    if geometry.get("status") in APPROVED and (PENDING in shapes or required == PENDING):
        errors.append(("APPROVAL_INCOMPLETE", "Un contrat approuvé ne peut contenir une décision manquante."))
    return errors


def geometry_approved(geometry):
    return (geometry is not None and geometry.get("status") in APPROVED and
            not geometry_errors(geometry))


def preservation_geometry_supported(rule, targets):
    """Recognize preservation policies without authorizing any transformation.

    L02-B's qualified facet is executable only for its approved morphology
    targets. This does not certify SQL, occurrences or object validity.
    """
    if rule.get('geometry_policy') in {
        'preserve_if_present_pending_contract', 'absent_by_source_contract',
        'preserve_in_technical_support',
    }:
        return True
    outputs = rule.get('outputs', [])
    def morphology_geometry(target):
        geometry = target.get('geometry') or {}
        return (geometry_approved(geometry) and
                set(geometry['target_shapes']) == {'POINT', 'LINESTRING', 'POLYGON'} and
                geometry.get('required_for_valid') is True and
                geometry.get('policy') == 'preserve_if_present')
    return bool(rule.get('geometry_policy') == 'preserve_qualified' and outputs and
                'decision:MOD-1O-L02-B' in rule.get('decision_refs', []) and
                all(output['target'] in MORPHOLOGY_TARGETS and
                    morphology_geometry(targets.get(output['target'], {}))
                    for output in outputs))


def geometry_obligations(targets):
    """Approving expected shapes does not qualify CRS, binding or occurrences."""
    obligations = {"GEOMETRY_SOURCE_RETAIN_UNCHANGED", "GEOMETRY_CRS_DIMENSIONS_UNQUALIFIED",
                   "GEOMETRY_OCCURRENCE_VALIDATION_UNIMPLEMENTED"}
    for target in targets:
        geometry = target.get("geometry")
        if not geometry_approved(geometry):
            obligations.add("GEOMETRY_TARGET_CONTRACT_UNQUALIFIED")
            continue
        if geometry.get("policy") == "preserve_in_technical_support":
            obligations.add("GEOMETRY_RETAIN_TECHNICAL_ONLY")
            continue
        obligations.add("GEOMETRY_UNEXPECTED_RETAIN_INVALID")
        if geometry["required_for_valid"]:
            obligations.add("GEOMETRY_MISSING_RETAIN_INVALID")
    return sorted(obligations)
