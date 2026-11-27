"""Development compiler: build, validate and inspect the one master model."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import sys

from .common import canonical_bytes, strict_load
from .engine import (DEFAULT_DIAGNOSTICS_PATH, DEFAULT_MODEL_PATH, REPOSITORY,
                     build, check_model_freshness, load_model, source_paths,
                     write_artifact, write_model)
from .graph import impact


def _provenance(model):
    return {'state':'current_verified','scope':'model_declarations',
        'assurance':'declarations_only_not_corpus_or_transaction',
        'input_digest':model['input_digest'],'model_digest':model['model_digest'],
        'engine_sha256':model['engine_sha256'],'registry_revision':model['registry_revision']}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    compile_parser = commands.add_parser("build", help="rebuild pinned facts; does not certify")
    compile_parser.add_argument("--output", type=Path, default=DEFAULT_MODEL_PATH)
    compile_parser.add_argument("--sirs-root", type=Path, default=REPOSITORY.parent / "sirs-255-build")
    validate_parser = commands.add_parser("validate", help="exit 2 when requested certification is blocked")
    validate_parser.add_argument("--model", type=Path, default=DEFAULT_MODEL_PATH)
    validate_parser.add_argument("--output", type=Path, default=DEFAULT_DIAGNOSTICS_PATH,
                                 help="write the full validation report to this audit artifact")
    validate_parser.add_argument("--sirs-root", type=Path, default=REPOSITORY.parent / "sirs-255-build")
    validate_parser.add_argument("--summary", action="store_true")
    validate_parser.add_argument("--profile", choices=("target-structure", "historical-mapping", "physical-binding", "occurrence-projection", "application-invariants", "full"), default="full")
    impact_parser = commands.add_parser("impact", help="query only recorded dependencies")
    impact_parser.add_argument("id")
    impact_parser.add_argument("--model", type=Path, default=DEFAULT_MODEL_PATH)
    impact_parser.add_argument("--sirs-root", type=Path, default=REPOSITORY.parent / "sirs-255-build")
    coverage_parser = commands.add_parser("coverage", help="exact mapping partitions and dependency incidences; not certification")
    coverage_parser.add_argument("--model", type=Path, default=DEFAULT_MODEL_PATH)
    coverage_parser.add_argument("--sirs-root", type=Path, default=REPOSITORY.parent / "sirs-255-build")
    qualification_parser = commands.add_parser("qualify-projections", help="qualify the closed Core/Berge/Lit source outcomes and physical gaps; never certify import")
    qualification_parser.add_argument("--model", type=Path, default=DEFAULT_MODEL_PATH)
    qualification_parser.add_argument("--sirs-root", type=Path, default=REPOSITORY.parent / "sirs-255-build")
    qualification_parser.add_argument("--summary", action="store_true")
    project_parser = commands.add_parser("project", help="logical Core/Berge/Lit and B2 proof, never SQL or certification")
    project_parser.add_argument("--input", type=Path, required=True)
    project_parser.add_argument("--model", type=Path, default=DEFAULT_MODEL_PATH)
    project_parser.add_argument("--sirs-root", type=Path, default=REPOSITORY.parent / "sirs-255-build")
    geometry_parser = commands.add_parser("geometry", help="export/import the temporary geometry editing view")
    geometry_commands = geometry_parser.add_subparsers(dest="geometry_command", required=True)
    export_parser = geometry_commands.add_parser("export", help="create a complete CSV; never overwrite")
    from .geometry import DEFAULT_VIEW_CSV
    export_parser.add_argument("--output", type=Path, default=DEFAULT_VIEW_CSV)
    import_parser = geometry_commands.add_parser("import", help="check or explicitly apply a complete edited CSV")
    import_parser.add_argument("--input", type=Path, default=DEFAULT_VIEW_CSV)
    mode = import_parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="validate without changing the registry")
    mode.add_argument("--apply", action="store_true", help="validate again, then atomically apply decisions")
    qualify_parser = geometry_commands.add_parser("qualify", help="read-only identity, coverage and worksheet reconciliation to stdout")
    qualify_parser.add_argument("--input", type=Path, default=DEFAULT_VIEW_CSV)
    qualify_parser.add_argument("--legacy", type=Path, required=True,
                                help="historical worksheet supplied explicitly for read-only reconciliation")
    qualify_parser.add_argument("--git-history", action="store_true")
    qualify_parser.add_argument("--summary", action="store_true")
    for subparser in (export_parser, import_parser, qualify_parser):
        subparser.add_argument("--sirs-root", type=Path, default=REPOSITORY.parent / "sirs-255-build")
    args = parser.parse_args(argv)
    try:
        if args.command == "geometry":
            from .geometry import apply_import, check_import, export_geometry
            # Derive from authoritative pinned inputs, never trust a modified
            # compilation or a worksheet's self-reported universe/hash.
            model = build(sirs_root=args.sirs_root)
            if args.geometry_command == "export":
                result = export_geometry(model, args.output)
            elif args.geometry_command == "qualify":
                from .geometry_qualification import git_history, qualify, reconcile
                paths = source_paths(model["manifest"], REPOSITORY, args.sirs_root)
                result = qualify(model, args.input.read_text(encoding="utf-8-sig"), source_paths=paths)
                legacy = args.legacy.read_text(encoding="utf-8-sig")
                result["legacy"] = reconcile(model, legacy)
                if args.git_history:
                    result["git_history"] = git_history(REPOSITORY, legacy)
                if args.summary:
                    result = {"coverage": result["summary"], "legacy": result["legacy"]["summary"],
                              "certifications": result["import_check"]["certifications"],
                              **({"git_history": result["git_history"]["summary"]} if args.git_history else {})}
            else:
                paths = source_paths(model["manifest"], REPOSITORY, args.sirs_root)
                plan = check_import(model, args.input.read_text(encoding="utf-8-sig"), source_paths=paths)
                result = apply_import(plan) if args.apply else plan
            code = 0
        elif args.command == "build":
            model = build(sirs_root=args.sirs_root)
            byte_digest = write_model(model, args.output, sirs_root=args.sirs_root)
            result = {"status": "BUILT_NOT_CERTIFIED", "sha256": byte_digest,
                      "input_digest": model["input_digest"], "model_digest": model["model_digest"],
                      "counts": model["historical"]["counts"]}
            code = 0
        elif args.command == "coverage":
            from .resolution import mapping_summary
            from .inventory import inventory_summary
            from .occurrence_projection import CONTRACT_ID
            from .projection_qualification import qualify_projections
            model = load_model(args.model)
            paths = check_model_freshness(model, sirs_root=args.sirs_root)
            result = mapping_summary(model["mappings"], model["historical"])
            if "inventory" in model["registry"]:
                result["target_inventory"] = inventory_summary(model["registry"])
            if any(c["id"] == CONTRACT_ID for c in model["registry"].get("contracts", [])):
                result["projection_qualification"] = qualify_projections(model, source_paths=paths)["summary"]
            if any(c["kind"] == "typed_troncons_transition" for c in model["registry"].get("contracts", [])):
                from .typed_troncons import transition_summary
                result["typed_troncons"] = transition_summary(model["registry"], model["historical"])
            code = 0
        elif args.command == "qualify-projections":
            from .projection_qualification import qualify_projections
            model = load_model(args.model)
            paths = check_model_freshness(model, sirs_root=args.sirs_root)
            result = qualify_projections(model, source_paths=paths)
            code = 0 if result["summary"]["contract_valid"] else 2
            if args.summary:
                result = {key: result[key] for key in ("status", "certified", "summary", "digest")}
        elif args.command == "impact":
            model = load_model(args.model)
            check_model_freshness(model, sirs_root=args.sirs_root)
            result = impact(model, args.id)
            code = 0
        elif args.command == "project":
            from .projection import project_logical
            model = load_model(args.model)
            check_model_freshness(model, sirs_root=args.sirs_root)
            result = project_logical(model, strict_load(args.input))
            code = 2 if any(d.get("blocking", True) for d in result["diagnostics"]) else 0
        else:
            from .extraction import extract_history
            from .validation import validate_model
            # Do not reject artifact mutations before the validator can name
            # their individual coverage, reference and digest violations.
            model = strict_load(args.model)
            paths = check_model_freshness(model, sirs_root=args.sirs_root,
                                          check_source_hashes=False)
            expected = extract_history(model["manifest"], paths)
            result = validate_model(model, expected_history=expected, source_paths=paths)
            result["input_digest"] = model["input_digest"]
            result['provenance'] = _provenance(model)
            write_artifact(result, args.output)
            if args.summary:
                result["diagnostic_counts"] = dict(sorted(Counter(d["code"] for d in result.pop("diagnostics")).items()))
                if "physical_evidence" in result:
                    result["physical_evidence"] = {"counts": result["physical_evidence"]["counts"]}
                if "projection_qualification" in result:
                    result["projection_qualification"] = {key: result["projection_qualification"][key]
                                                           for key in ("status", "certified", "summary", "digest")}
                if "target_inventory" in result:
                    result["target_inventory"] = {k: v for k, v in result["target_inventory"].items()
                        if k in {"scopes", "members", "sha256", "atomic_attributes", "canonical_boundary_approved"}}
                if "typed_troncons" in result:
                    result["typed_troncons"].pop("inventory", None)
            code = 0 if result["certifications"][args.profile]["status"] == "CERTIFIED" else 2
        if args.command in {'build','coverage','validate','qualify-projections'}:
            result['provenance']=_provenance(model)
    except (ValueError, KeyError, TypeError, OSError) as error:
        result = {"status": "ERROR", "error": str(error)}
        if hasattr(error, "diagnostics"):
            result["diagnostics"] = error.diagnostics
        code = 2
    sys.stdout.buffer.write(canonical_bytes(result))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
