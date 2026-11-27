"""Independent property/domain inventory; physical facts never approve semantics.

The native contract, the eight historical declarations and the initialized
catalogues are different observation universes. Their keys are intentionally
different until an explicit correspondence proves that two observations denote
the same target property. Reciprocal Ecore roles, however, are one source
association. No registry target is used as an enumeration input here.
"""
from __future__ import annotations

import ast
from collections import defaultdict

from digues_app.references import ReferenceContracts
from digues_app.references.canonical import CATALOG_NOMENCLATURES, canonical_reference_rows

from .common import strict_load
from .inventory_common import candidate
from .sql_evidence import parse_ddl


SOURCE_PATHS = {
    "inventory:native-models": "webapp/backend/digues_webapp/models.py",
    "inventory:canonical-catalogs": "digues_app/references/canonical.py",
    "inventory:reference-initialization": "digues_app/references/initialization.py",
    "inventory:reference-relational": "digues_app/references/relational.py",
    "inventory:reference-vegetation": "digues_app/references/vegetation.py",
    "inventory:reference-contract-api": "digues_app/references/__init__.py",
    "inventory:reference-validation": "digues_app/references/validation.py",
    "inventory:target-schema-reader": "digues_app/target/schema.py",
}

SCOPE = "scope:patrimoine"

# Semantic owners are explicitly selected, never inferred from SQL names.
# Unknown owners remain candidates even where an application already edits them.
TABLE_OWNERS = {
    "systemes": "da:concept:systeme-endiguement",
    "digues": "da:concept:digue",
    "troncons": "da:concept:troncon",
    "desordres": "da:concept:desordre",
    "equipements_mesure": "da:concept:equipement-mesure",
    "mobilier": "candidate:unknown:patrimoine/mobilier",
    "reseaux_techniques": "candidate:unknown:patrimoine/reseau-technique",
    "ouvrages_particuliers": "da:concept:ouvrage-particulier",
    "cheminements": "da:concept:cheminement",
    "amenagements_hydrauliques": "da:concept:amenagement-hydraulique",
    "prestations": "da:concept:prestation",
    "vegetation": "da:concept:vegetation",
    "observations": "da:component:observation",
    "photos": "da:component:photo",
    "organismes": "da:concept:organisme",
    "contacts": "da:concept:contact",
    "bornes_reperage": "candidate:unknown:patrimoine/borne-reperage",
    "systemes_reperage": "candidate:unknown:patrimoine/systeme-reperage",
    "segments_hydrographiques": "da:concept:troncon-lit",
    "largeurs_lit": "da:component:largeur-lit",
    "pentes_lit": "da:component:pente-lit",
    "domanialites_lit": "da:component:domanialite-lit",
    "regimes_ecoulement_lit": "da:component:regime-ecoulement-lit",
    "occupations_riveraines_lit": "da:component:occupation-riveraine-lit",
    "elements_morphologiques_lit": "da:concept:element-morphologique-lit",
    "ouvrages_hydrauliques": "da:concept:ouvrage-hydraulique",
    "ouvrages_voirie": "da:concept:ouvrage-voirie",
    "ouvrages_telecom_energie": "da:concept:ouvrage-telecom-energie",
    "reseaux_hydrauliques": "da:concept:reseau-hydraulique",
    "structures_protection": "da:concept:structure-protection",
    "profils_longs": "da:concept:profil-long",
    "profils_travers": "da:concept:profil-travers",
    "leves_profils_travers": "da:concept:leve-profil",
    "positions_profils_travers": "da:component:position-profil",
    "evenements_hydrauliques": "da:concept:evenement-hydraulique",
    "objets_mesures_hydrauliques": "da:concept:objet-mesure-hydraulique",
    "plans_gestion_vegetation": "da:concept:plan-gestion",
}

# Stable observation keys, not physical tables or new canonical approvals.
TRONCON_ROLE_IDENTITIES = {
    "troncons_unifies": ("da:concept:troncon", None),
    "troncons": ("da:concept:troncon-digue",
                 {"column": "type_physique_id", "value": "INDETERMINE"}),
    "berges": ("da:concept:berge", {"column": "type_physique_id", "value": "BERGE"}),
    "structures_digue": ("da:component:structure-digue", {"column": "est_structure", "value": True}),
}

# The Zone's SQL relationship endpoint is decided before its native API models
# exist; keep native field inventory tied to the DTOs actually implemented.
ASSOCIATION_ENDPOINT_OWNERS = {**TABLE_OWNERS, "zones_stockage": "da:concept:zone-stockage"}

NATIVE_MODELS = {
    "systemes": ("SystemeEndiguementCreate", "SystemeEndiguementUpdate"),
    "digues": ("DigueCreate", "DigueUpdate"),
    "troncons": ("TronconCreate", "TronconUpdate"),
    "desordres": ("DesordreCreate", "PointDesordreUpdate"),
    "mobilier": ("MobilierCreate", "MobilierUpdate"),
    "equipements_mesure": ("EquipementMesureCreate", "EquipementMesureUpdate"),
    "reseaux_techniques": ("ReseauTechniqueCreate", "ReseauTechniqueUpdate"),
    "ouvrages_particuliers": ("OuvrageParticulierCreate", "OuvrageParticulierUpdate"),
    "cheminements": ("CheminementCreate", "CheminementUpdate"),
    "amenagements_hydrauliques": ("AmenagementHydrauliqueCreate", "AmenagementHydrauliqueUpdate"),
    "prestations": ("PrestationCreate", "PrestationUpdate"),
    "vegetation": ("VegetationCreate", "VegetationUpdate"),
    "observations": ("ObservationCreate", "ObservationUpdate"),
}

CATALOG_TARGETS = {
    "types_evenement_hydraulique": "da:catalog:type-evenement-hydraulique",
    "frequences_evenement_hydraulique": "da:catalog:frequence-evenement-hydraulique",
    "orientations_vent": "da:catalog:orientation-vent",
    "references_hauteur": "da:catalog:reference-hauteur",
}

# Association families explicitly selected for the task. A row witnesses their
# existence and physical endpoints, not an approval of their complete rules.
ASSOCIATION_TABLES = (
    "amenagements_hydrauliques__troncons", "amenagements_hydrauliques__desordres",
    "amenagements_hydrauliques__gestionnaires", "contact_organisme_periods",
    "troncons_gestionnaires", "systemes_reperage__bornes", "troncons_bornes",
    "cheminements__desordres", "cheminements__troncons", "cheminements__vegetation",
    "desordres__troncons", "equipements_mesure__desordres", "mobilier__desordres",
    "ouvrages_particuliers__desordres", "ouvrages_telecom_energie__desordres",
    "ouvrages_telecom_energie__reseaux_techniques", "ouvrages_telecom_energie__vegetation",
    "ouvrages_voirie__cheminements", "ouvrages_voirie__desordres", "ouvrages_voirie__vegetation",
    "plans_gestion__vegetation", "prestations_intervenants", "prestations_objets",
    "prestations_troncons", "reseaux_hydrauliques__desordres",
    "reseaux_hydrauliques__stations_pompage", "reseaux_hydrauliques__vegetation",
    "reseaux_ouverts__reseaux_fermes", "reseaux_techniques__desordres",
    "segments_hydrographiques__ouvrages", "segments_hydrographiques__bornes",
    "segments_hydrographiques__systemes", "vegetation__desordres",
    "vegetation__amenagements_hydrauliques", "vegetation__troncons",
    "ouvrages_hydrauliques__desordres", "ouvrages_hydrauliques__objets_lies",
    "profils_travers__leves", "prestations_evenements_hydrauliques",
    "positions_profils_travers__leves", "rattachements_reseaux_fermes", "evenements_hydrauliques__objets",
)

DECIDED_ASSOCIATIONS = {
    "contact_organisme_periods": ("da:relation:contact-organisme-periode", "decision:FM-06"),
    "troncons_gestionnaires": ("da:relation:troncon-gestionnaire", "decision:FM-06"),
    "profils_travers__leves": ("da:relation:profil-travers-leve", "decision:FM-11A"),
    "vegetation__amenagements_hydrauliques": ("da:relation:vegetation-amenagement-hydraulique", "decision:B4-vegetation-links"),
    "vegetation__troncons": ("da:relation:vegetation-troncon-digue", "decision:B4-vegetation-links"),
    "desordres__troncons": ("da:relation:troncon-desordre", "decision:MOD1L-existing-nn"),
    "vegetation__desordres": ("da:relation:vegetation-desordre", "decision:MOD1L-existing-nn"),
}


def _prov(source, locator):
    return {"source": source, "locator": locator}


def _catalog_id(table):
    return "candidate:catalog:native/" + table.split(".", 1)[-1]


def _historical_owner(source):
    return "candidate:unknown:historical/" + source.split(":", 2)[-1]


def _physical(table, columns, *, gaps=None):
    return {"status": "BINDING_PARTIEL", "supports": [{"source": "sql:business",
            "table": table, "columns": sorted(set(columns))}],
            "gaps": gaps or ["Canonical meaning, complete cardinalities and application invariants unapproved."]}


def _fk_catalogs(tables):
    return {(table, col): constraint["references"]
            for table, spec in tables.items() for constraint in spec["constraints"]
            if constraint["kind"] == "foreign_key" and constraint["references"].startswith("referentiel.")
            for col in constraint["columns"]}


def _catalogs(paths, tables):
    c1, c2 = strict_load(paths["contract:c1"]), strict_load(paths["contract:c2"])
    initialized = canonical_reference_rows(ReferenceContracts(c1, c2))
    consumers = defaultdict(list)
    for (table, col), domain in sorted(_fk_catalogs(tables).items()):
        consumers[domain].append({"table": table, "column": col,
            "owner": TABLE_OWNERS.get(table.removeprefix("public.")),
            "proof": "DECLARED_SQL_FOREIGN_KEY_NOT_CANONICAL_APPROVAL"})
    result = []
    for table, (columns, values) in sorted(initialized.items()):
        name = table.split(".", 1)[1]
        domains = list(c2["tables"].get(table, {}).get("nomenclatures", ()))
        domains.extend(CATALOG_NOMENCLATURES.get(name, ()))
        if name == "types_ouvrage_voirie":
            domains.append("ouvrage_voirie_dependance")
        domains = sorted(set(domains))
        historical = [{"domain": domain, "kind": c1["nomenclatures"][domain]["kind"],
                       "values": c1["nomenclatures"][domain]["values"]} for domain in domains]
        native_values = [dict(zip(columns, row, strict=True)) for row in values]
        historical_consumers = []
        for source_class, contract in sorted(c2["classes"].items()):
            if contract.get("nomenclature") in domains:
                historical_consumers.extend({"source_class": source_class, **consumer,
                    "proof": "C2_DECLARED_HISTORICAL_CONSUMER"} for consumer in contract.get("consumers", []))
        conversion_policies = []
        for mapping, contract in sorted(c2["mappings"].items()):
            source_domain = c2["classes"].get(contract.get("source_class"), {}).get("nomenclature")
            if source_domain in domains or any(t.get("nomenclature") in {*domains, name}
                    for entry in contract.get("entries", []) for t in entry.get("targets", [])):
                conversion_policies.append({"mapping": mapping,
                    **{key: contract.get(key) for key in ("on_absent", "on_null", "on_empty", "on_unknown")}})
        target = CATALOG_TARGETS.get(name)
        result.append(candidate(_catalog_id(table), "catalog", SCOPE,
            f"Domaine initialisé {name} ; identité de valeur composée du domaine et de son id, sans équivalence par libellé.",
            [_prov("inventory:canonical-catalogs", "canonical_reference_rows/" + table),
             _prov("sql:business", "CREATE TABLE " + table),
             *[_prov("contract:c1", "/nomenclatures/" + domain) for domain in domains]],
            origin="both" if domains else "native_only", target=target,
            status="DECIDE" if target else "A_APPROUVER",
            decision_refs=["decision:DB1-evenements"] if target else [],
            evidence_keys=["native-catalog:" + table],
            binding=_physical(table, columns),
            preservation=["Domain-qualified value identity, initialized codes and nulls", "Historical and native value sets stay distinct"],
            questions=[] if target else ["Canonical domain and allowed use require explicit approval."],
            details={"name": name, "columns": list(columns), "values": native_values,
                "historical_domains": historical, "consumers": consumers[table],
                "historical_consumers": historical_consumers,
                "undefined_policy": {"missing_is_undefined": False, "automatic_substitution": False,
                    "status": "A_APPROUVER", "condition": "Only explicit domain-specific approved rules permit an Indéfini value; absence/null are not values.",
                    "historical_conversion_rules": conversion_policies,
                    "initialized_undefined_ids": [row["id"] for row in native_values
                        if any(isinstance(value, str) and value.casefold() in {"indéfini", "indefini"}
                               for value in row.values())]},
                "validity_activation": {"validity_column": "valid" if "valid" in columns else None,
                    "activation_column": None, "equivalent": False},
                "coverage_basis": "INITIALIZER_DOMAINS_NOT_CANONICAL_FRONTIER"}))
    return result


def _native_fields(paths, tables):
    tree = ast.parse(paths["inventory:native-models"].read_text(encoding="utf-8"))
    classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
    def fields(name):
        node, result = classes[name], {}
        for base in node.bases:
            if isinstance(base, ast.Name) and base.id in classes:
                result.update(fields(base.id))
        result.update({n.target.id: (name, n) for n in node.body
                       if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)})
        return result
    catalog_fks = _fk_catalogs(tables)
    result = []
    for table, names in sorted(NATIVE_MODELS.items()):
        observed = defaultdict(list)
        for name in names:
            for field, (declaring, node) in fields(name).items():
                observed[field].append((name, declaring, node))
        for field, declarations in sorted(observed.items()):
            annotations = sorted({ast.unparse(node.annotation) for _, _, node in declarations})
            # Transport identities and alternative geometry encodings remain
            # visible exclusions; they do not become business attributes.
            technical = field in {"id", "operation_id", "base_version", "client_id", "instance_id"}
            alternate_geometry = field in {"geometry_type", "line_endpoints", "x", "y", "longitude", "latitude"}
            links = any("UUID" in annotation or "PrestationObjectFamily" in annotation
                        or "ObservationParentInput" in annotation for annotation in annotations)
            kind = "unknown" if technical or alternate_geometry or links else "attribute"
            role = ("transport_identity" if technical else "alternate_geometry_input" if alternate_geometry
                    else "relation_input" if links else "catalog_reference" if ("public." + table, field) in catalog_fks
                    else "business_value")
            owner = TABLE_OWNERS[table]
            column = tables["public." + table]["columns"].get(field)
            domain = catalog_fks.get(("public." + table, field))
            card = {"min": 0 if any("None" in a for a in annotations) else 1,
                    "max": "*" if any("list[" in a or "dict[" in a for a in annotations) else 1}
            result.append(candidate(f"candidate:{kind}:native-property/{table}/{field}", kind, SCOPE,
                f"Propriété {field} des contrats natifs de {table}; les modes create/patch restent distingués.",
                [_prov("inventory:native-models", declaring + "/" + field) for declaring in sorted({d for _, d, _ in declarations})],
                owner=owner, origin="UNKNOWN", status="EXCLU" if technical else "A_APPROUVER",
                depends_on=[owner], evidence_keys=[f"native-property:{table}/{field}"],
                binding=_physical("public." + table, [field]) if column else {
                    "status": "INDETERMINE", "supports": [], "gaps": ["No exact same-name column; application transformation not qualified."]},
                preservation=["Presence/null distinctions and patch semantics"],
                questions=(["Native creation exists; import/native overlap is not proven by a same-name field."]
                           + (["Relation input must be reconciled with the independent relation inventory."] if links else [])
                           + (["Alternative geometry input is not an independent canonical attribute."] if alternate_geometry else [])),
                details={"name": field, "role": role, "value_type": annotations, "unit": {"hauteur_m": "m", "diametre_cm": "cm"}.get(field),
                    "catalog": _catalog_id(domain) if domain else None, "cardinality": card,
                    "storage": "stored" if column else "UNKNOWN", "source_owner": names[0],
                    "native_creation": True, "canonical_cardinality": None,
                    "contracts": [{"model": name, "declaration": declaring, "type": ast.unparse(node.annotation),
                        "default": ast.unparse(node.value) if node.value is not None else None}
                        for name, declaring, node in declarations]}))
    return result


def _historical_properties(historical, catalogs, paths):
    facts = {f["id"]: f for f in historical["facts"]}
    c2 = strict_load(paths["contract:c2"])
    native_domains = defaultdict(list)
    for catalog in catalogs:
        for domain in catalog["details"]["historical_domains"]:
            native_domains[domain["domain"]].append(catalog["id"])
    result, seen = [], set()
    for fact in sorted(facts.values(), key=lambda f: f["id"]):
        if fact["kind"] not in {"attribute", "reference"}:
            continue
        data, fid = fact["data"], fact["id"]
        owner = _historical_owner(fact["owner"])
        property_scope = "scope:administration" if fact["owner"] == "sirs-v2:class:core/Utilisateur" else SCOPE
        reference_property = fact["kind"] == "reference" and (
            data["type"].split("/")[-1].startswith("Ref") or fact["name"] in {"author", "lastUpdateAuthor"})
        if fact["kind"] == "attribute" or reference_property:
            role = ("author" if fact["name"] == "author" else "editor" if fact["name"] == "lastUpdateAuthor"
                    else "catalog_reference" if reference_property else "historical_value")
            source_catalog = c2["classes"].get(data["type"].split("/")[-1], {}).get("nomenclature") if reference_property else None
            domains = sorted(native_domains.get(source_catalog, []))
            result.append(candidate("candidate:attribute:historical/" + fid.split(":", 2)[-1], "attribute", property_scope,
                f"Déclaration historique {fact['name']} de {fact['owner']}; conservation sans canonisation du propriétaire ou du type.",
                fact["provenance"], owner=owner, origin="UNKNOWN", depends_on=[owner, fid],
                evidence_keys=[fid], preservation=["Historical value, source type, multiplicity, ordering and absence/null distinctions"],
                questions=["Canonical owner, type, unit, stored/derived semantics and native/import overlap need qualification."],
                details={"name": fact["name"], "role": role, "value_type": data["type"], "unit": None,
                    "catalog": domains[0] if len(domains) == 1 else None,
                    "catalog_candidates": domains, "historical_catalog_domain": source_catalog,
                    "cardinality": {"min": data["lower"], "max": "*" if data["upper"] == -1 else data["upper"]},
                    "canonical_cardinality": None, "storage": "UNKNOWN", "source_owner": fact["owner"],
                    "historical_declaration": fid, "historical_default": data.get("default_value"),
                    "ordered": data.get("ordered"), "unique": data.get("unique"),
                    "historical_annotations": data.get("annotations", [])}))
            continue
        opposite = facts.get(data.get("opposite"), {})
        reciprocal = opposite.get("kind") == "reference" and opposite.get("data", {}).get("opposite") == fid
        pair = tuple(sorted([fid, opposite["id"]])) if reciprocal else (fid,)
        if pair in seen:
            continue
        seen.add(pair)
        first = facts[pair[0]]
        ends = [{"target": _historical_owner(first["owner"]), "role": first["name"] + ":owner",
                 "direction": "source", "cardinality": None},
                {"target": _historical_owner(first["data"]["type"]), "role": first["name"],
                 "direction": "target", "cardinality": {"min": first["data"]["lower"],
                     "max": "*" if first["data"]["upper"] == -1 else first["data"]["upper"]}}]
        if reciprocal:
            other = facts[pair[1]]
            ends[0]["role"] = other["name"]
            ends[0]["cardinality"] = {"min": other["data"]["lower"], "max": "*" if other["data"]["upper"] == -1 else other["data"]["upper"]}
        result.append(candidate("candidate:relation:historical/" + pair[0].split(":", 2)[-1], "relation", SCOPE,
            "Association historique déclarée ; les rôles réciproques démontrés désignent une seule association candidate.",
            [p for source in pair for p in facts[source]["provenance"]], origin="UNKNOWN",
            depends_on=sorted(set([*pair, *(e["target"] for e in ends)])), evidence_keys=list(pair),
            preservation=["Endpoint identities, multiplicities, containment, ordering and asymmetric assertions"],
            questions=["Canonical endpoints/cardinalities and association identity require qualification."],
            details={"endpoints": ends, "association_identity": "UNKNOWN",
                "conditions": ["Historical bounds only; no native cardinality inferred."],
                "declarations": list(pair), "opposites": list(pair) if reciprocal else [],
                "unpaired_opposite": None if reciprocal else data.get("opposite"),
                "containment": any(facts[p]["data"].get("containment") for p in pair),
                "source_roles": [{"declaration": p, "owner": facts[p]["owner"], "type": facts[p]["data"]["type"],
                    "ordered": facts[p]["data"].get("ordered"), "unique": facts[p]["data"].get("unique")} for p in pair]}))
    return result


def _supplemental_properties(historical, tables):
    """Selected business values, not a promotion of arbitrary SQL columns."""
    facts = {f["id"]: f for f in historical["facts"]}
    catalogs = _fk_catalogs(tables)
    result = []
    # PDL = Plage de dépôt lit. These twelve dimensions/catalog references have
    # exact historical declarations as well as native physical columns.
    pdl = {
        "cote_fond": "coteFond", "cote_alerte": "coteAlerte", "volume_stockage": "volumeStockage",
        "longueur": "longueur", "largeur_max": "largeurMax", "pente_a_vide": "penteAVide",
        "hauteur": "hauteur", "largeur_en_crete": "largeurEnCrete", "type_fermeture_id": "typeFermetureId",
        "type_ouverture_id": "typeOuvertureLit", "type_ouvrage_fermeture_id": "typeElementAssocieFermetureId",
        "type_ouvrage_depot_id": "typeElementAssocieId",
    }
    for name, source_name in pdl.items():
        declared = next((f for f in facts.values() if f["kind"] in {"attribute", "reference"}
                         and f["owner"] == "sirs-v2:class:lit/PlageDepotLit" and f["name"] == source_name), None)
        if declared is None:
            raise ValueError("INV-UNIV.PDL_SOURCE_MISSING: " + source_name)
        table, owner = "public.ouvrages_hydrauliques", "da:type:ouvrage-hydraulique/plage-depot"
        column = tables[table]["columns"][name]
        domain = catalogs.get((table, name))
        result.append(candidate("candidate:attribute:pdl/" + name, "attribute", SCOPE,
            f"Propriété {source_name} de Plage de dépôt lit, support courant {name}; équivalence détaillée à qualifier.",
            [*declared["provenance"], _prov("sql:business", table + "/" + name)],
            owner=owner, origin="UNKNOWN", depends_on=[owner, declared["id"]],
            evidence_keys=["native-pdl-property:" + name], binding=_physical(table, [name]),
            preservation=["Historical value and absence/null semantics; domain-qualified reference identity"],
            questions=["Native API coverage, units and exact import/target correspondence remain to be qualified."],
            details={"name": name, "role": "catalog_reference" if domain else "business_measurement",
                "value_type": column["type"], "unit": None, "catalog": _catalog_id(domain) if domain else None,
                "cardinality": None, "physical_cardinality": {"min": 0 if column["nullable"] else 1, "max": 1},
                "storage": "stored", "source_owner": declared["owner"], "source_property": declared["id"],
                "family": "PDL", "physical_selector": {"column": "type_id", "value": "PDL"}}))
    # Coordinates are scalar values of structured points. Their identity is
    # deliberately unapproved in A's candidate owners; the SQL id is no proof.
    measurements = (
        ("points_profils", "leve-profil/point-xyz", ("ordre_point", "x", "y", "z"), "XYZ_LEVE_TRAVERS"),
        ("points_profils", "leve-profil/point-dz", ("ordre_point", "d", "z"), "DZ_LEVE_TRAVERS"),
        ("points_profils", "profil-long/point-xyz", ("ordre_point", "x", "y", "z"), "XYZ_PROFIL_LONG"),
        ("points_profils", "profil-long/point-prz", ("ordre_point", "d", "z"), "PRZ_PROFIL_LONG"),
        ("mesures_lignes_eau", "ligne-eau/mesure-xyz", ("x", "y", "z"), "XYZ"),
        ("mesures_lignes_eau", "ligne-eau/mesure-prz", ("d", "z"), "PRZ"),
        ("mesures_montees_eaux", "montee-eaux/mesure", ("date_mesure", "hauteur", "debit_max", "commentaire", "observateur_id", "reference_hauteur_id", "source_id"), None),
        ("parametres_hydrauliques_profils", "profil-long/parametre-hydraulique", ("pr_debut", "pr_fin", "debit_premier_debordement", "evenement_hydraulique_id"), "LONG"),
        ("parametres_hydrauliques_profils", "profil-travers/parametre-hydraulique", ("debit_pointe", "vitesse_pointe", "cote_eau", "commentaire", "evenement_hydraulique_id"), "TRAVERS"),
    )
    measurement_classes = {
        "leve-profil/point-xyz": "XYZLeveProfilTravers", "leve-profil/point-dz": "DZLeveProfilTravers",
        "profil-long/point-xyz": "XYZProfilLong", "profil-long/point-prz": "PrZProfilLong",
        "ligne-eau/mesure-xyz": "MesureLigneEauXYZ", "ligne-eau/mesure-prz": "MesureLigneEauPrZ",
        "montee-eaux/mesure": "MesureMonteeEaux",
        "profil-long/parametre-hydraulique": "ParametreHydrauliqueProfilLong",
        "profil-travers/parametre-hydraulique": "ParametreHydrauliqueProfilTravers",
    }
    measurement_fields = {
        "date_mesure": "date", "debit_max": "debitMax", "observateur_id": "observateurId",
        "reference_hauteur_id": "referenceHauteurId", "source_id": "sourceId",
        "debit_pointe": "debitPointe", "vitesse_pointe": "vitessePointe", "cote_eau": "coteEau",
        "pr_debut": "PrDebut", "pr_fin": "PrFin", "debit_premier_debordement": "DebitPremerDebordement",
        "evenement_hydraulique_id": "evenementHydrauliqueId",
    }
    for short_table, owner_code, names, selector in measurements:
        table, owner = "public." + short_table, "candidate:unknown:patrimoine/" + owner_code
        source_class = measurement_classes[owner_code]
        selector_column = "type_parametre" if short_table == "parametres_hydrauliques_profils" else "type_point"
        selector_proof = None
        if selector is not None:
            selector_proof = next((c["expression"] for c in tables[table]["constraints"]
                if c["kind"] == "check" and selector_column in c["expression"]
                and f"'{selector}'::text" in c["expression"]), None)
            if selector_column not in tables[table]["columns"] or selector_proof is None:
                raise ValueError("INV-UNIV.MEASURE_SELECTOR_MISSING: " + table + "/" + str(selector))
        for name in names:
            column, domain = tables[table]["columns"][name], catalogs.get((table, name))
            source_property = None if name == "ordre_point" else "sirs-v2:application:core/" + source_class + "/" + measurement_fields.get(name, name)
            application = facts.get(source_property)
            if source_property and (application is None or application["kind"] != "application"
                    or application["owner"] != "sirs-v2:class:core/" + source_class):
                raise ValueError("INV-UNIV.MEASURE_PROPERTY_NOT_APPLICABLE: " + str(source_property))
            provenance = [_prov("sql:business", table + "/" + name)]
            dependencies = [owner]
            if application:
                provenance.extend(application["provenance"])
                dependencies.append(application["id"])
            result.append(candidate("candidate:attribute:measure/" + owner_code + "/" + name, "attribute", SCOPE,
                f"Champ physique {name} candidat pour {owner_code}; applicabilité canonique et identité autonome du propriétaire non approuvées.",
                provenance, owner=owner, origin="UNKNOWN", depends_on=dependencies,
                evidence_keys=["native-measure-property:" + owner_code + "/" + name],
                binding=_physical(table, [name, *([selector_column] if selector else [])], gaps=[
                    "Canonical applicability and source/target correspondence remain unapproved.",
                    "A selector domain and exclusive parent FKs do not prove selector/parent consistency."]),
                preservation=["Numeric value and point sequence; reference identity when present"],
                questions=["Canonical type, units, owner identity and source/target correspondence require qualification."],
                details={"name": name, "role": "sequence_order" if name == "ordre_point" else "catalog_reference" if domain else "measurement_value",
                    "value_type": column["type"], "unit": None, "catalog": _catalog_id(domain) if domain else None,
                    "cardinality": None, "physical_cardinality": {"min": 0 if column["nullable"] else 1, "max": 1},
                    "storage": "stored", "source_owner": "sirs-v2:class:core/" + source_class,
                    "source_property": source_property,
                    "source_declaration": application["data"]["declaration"] if application else None,
                    "historical_applicability": "EFFECTIVE_DECLARATION" if application else "A_APPROUVER",
                    "canonical_applicability": "A_APPROUVER",
                    "physical_selector": {"column": selector_column, "value": selector,
                        "constraint_expression": selector_proof} if selector else None}))
    # The author and editor are distinct business roles, proven by explicit
    # contact FKs. Do not treat arbitrary timestamps/versions as business fields.
    # Keep the observed role identities from the former extensions. Their
    # physical support is now the common root, restricted to the same subtype
    # population; this does not approve or merge their semantic owners.
    role_owners = [(name, name, owner, None) for name, owner in TABLE_OWNERS.items()
                   if name != "troncons"]
    role_owners.extend((identity, "troncons", owner, selector)
                      for identity, (owner, selector) in TRONCON_ROLE_IDENTITIES.items())
    for short_table, physical_table, owner, selector in sorted(role_owners):
        table = "public." + physical_table
        for name, role in (("created_by", "author"), ("edited_by", "editor")):
            spec = tables[table]
            if not any(c["kind"] == "foreign_key" and c["columns"] == [name]
                       and c["references"] == "public.contacts" for c in spec["constraints"]):
                continue
            column = spec["columns"][name]
            result.append(candidate(f"candidate:attribute:role/{short_table}/{role}", "attribute", SCOPE,
                f"Rôle {role} de {short_table}, référence de Contact distincte du compte Utilisateur.",
                [_prov("sql:business", table + "/" + name)], owner=owner, origin="UNKNOWN", depends_on=sorted({owner, "da:concept:contact"}),
                evidence_keys=[f"native-role:{short_table}/{role}"],
                binding=_physical(table, [name, *([selector["column"]] if selector else [])]),
                preservation=["Distinct author/editor roles and authorized business-base scope"],
                questions=["Historical actor correspondence and native producers are not inferred from the FK."],
                details={"name": name, "role": role, "value_type": "reference:Contact", "unit": None, "catalog": None,
                    "cardinality": None, "physical_cardinality": {"min": 0 if column["nullable"] else 1, "max": 1},
                    "storage": "stored", "source_owner": short_table, "reference_target": "da:concept:contact",
                    **({"physical_selector": selector} if selector else {})}))
    return result


def _native_relations(tables):
    result = []
    for name in ASSOCIATION_TABLES:
        table = "public." + name
        spec = tables[table]
        fks = [c for c in spec["constraints"] if c["kind"] == "foreign_key"
               and c["columns"] not in [["created_by"], ["edited_by"]]
               and c["references"].startswith("public.")]
        # L01: the composite FK checks that the already named participants
        # form an explicit pair. It does not add a third business participant.
        integrity_fks = [c for c in fks
                         if name == "segments_hydrographiques__ouvrages"
                         and c["columns"] == ["segment_id", "digue_id"]
                         and c["references"] == "public.segments_hydrographiques__digues"]
        fks = [c for c in fks if c not in integrity_fks]
        endpoints = [{"target": ASSOCIATION_ENDPOINT_OWNERS.get(c["references"].split(".", 1)[1]),
                      "role": "+".join(c["columns"]), "direction": "participant", "cardinality": None,
                      "physical_table": c["references"],
                      "physical_occurrence_cardinality": {"min": 0 if any(spec["columns"][col]["nullable"] for col in c["columns"]) else 1, "max": 1}}
                     for c in fks]
        # Preserve explicitly decided N:N without extending that decision to
        # nullable FKs or every other association found in the physical schema.
        decided_nn = name in {"desordres__troncons", "vegetation__desordres", "vegetation__troncons", "vegetation__amenagements_hydrauliques"}
        if decided_nn:
            for endpoint in endpoints:
                endpoint["cardinality"] = {"min": None if name in {"desordres__troncons", "vegetation__desordres"} else 0, "max": "*"}
        identity = next((c["columns"] for c in spec["constraints"] if c["kind"] == "primary_key"), [])
        result.append(candidate("candidate:relation:native/" + name, "relation", SCOPE,
            f"Famille applicative {name}, avec extrémités physiques explicitement constatées ; approbation canonique indépendante.",
            [_prov("sql:business", "CREATE TABLE " + table)], origin="UNKNOWN",
            target=DECIDED_ASSOCIATIONS.get(name, (None,))[0],
            status="DECIDE" if name in DECIDED_ASSOCIATIONS else "A_APPROUVER",
            decision_refs=[DECIDED_ASSOCIATIONS[name][1]] if name in DECIDED_ASSOCIATIONS else [],
            depends_on=sorted({e["target"] for e in endpoints if e["target"]}),
            evidence_keys=["native-relation:" + name],
            binding=_physical(table, [col for c in fks for col in c["columns"]]),
            preservation=["Endpoint identities, association keys and period occurrences when present"],
            questions=["Physical key is not proof of canonical identity or all business validity rules."]
                + (["Some endpoint owner/type remains unqualified."] if any(e["target"] is None for e in endpoints) else []),
            details={"endpoints": endpoints, "association_identity": "UNKNOWN", "physical_identity_columns": identity,
                "conditions": [c["expression"] for c in spec["constraints"] if c["kind"] == "check"],
                "declarations": [], "opposites": [], "decided_many_to_many": decided_nn,
                "canonical_identity": None}))
        if integrity_fks:
            result[-1]["details"]["integrity_foreign_keys"] = integrity_fks
    # The two hierarchy links are native contractual relations, not concepts
    # inferred from their FK-bearing tables.
    for name, parent, field in (("digues", "systemes", "systeme_endiguement_id"),
                                ("troncons", "digues", "digue_id")):
        table = "public." + name
        result.append(candidate("candidate:relation:native/" + name + "/" + field, "relation", SCOPE,
            f"Rattachement hiérarchique natif {name} vers {parent}, obligatoire à la création.",
            [_prov("inventory:native-models", NATIVE_MODELS[name][0] + "/" + field),
             _prov("sql:business", "CREATE TABLE " + table)], origin="UNKNOWN",
            depends_on=[TABLE_OWNERS[name], TABLE_OWNERS[parent]],
            evidence_keys=["native-relation:" + name + "/" + field], binding=_physical(table, [field]),
            questions=["Canonical inverse cardinality and lifecycle conditions are not closed by the native DTO."],
            details={"endpoints": [{"target": TABLE_OWNERS[name], "role": "child", "direction": "source", "cardinality": None},
                {"target": TABLE_OWNERS[parent], "role": "parent", "direction": "target", "cardinality": {"min": 1, "max": 1}}],
                "association_identity": "UNKNOWN", "conditions": ["Native create requires an active authorized parent."],
                "declarations": [], "opposites": []}))
    return result


def inventory(historical, paths):
    """Reconstruct source members without reading registry targets or bindings."""
    tables = parse_ddl(paths["sql:business"].read_text(encoding="utf-8"))
    catalogs = _catalogs(paths, tables)
    rows = [*catalogs, *_native_fields(paths, tables), *_supplemental_properties(historical, tables),
            *_historical_properties(historical, catalogs, paths), *_native_relations(tables)]
    return sorted(rows, key=lambda row: row["id"])


def limitations():
    """Precise unclosed boundaries; counts quoted by MOD-1J are not evidence."""
    owners = sorted(set(TABLE_OWNERS.values()) |
                    {owner for owner, _ in TRONCON_ROLE_IDENTITIES.values()})
    return [
        {"id": "inventory-limit:property-correspondences", "scope": SCOPE,
         "families": ["historical attributes", "native attributes", "simple references", "catalog consumers", "author/editor", "component properties"],
         "owners": owners,
         "reason": "Every EAttribute declaration, historical Ref*/author/editor reference and selected native contract field is atomic; canonical owner/type/unit/origin and cross-source equivalence remain unapproved. Exact historical declaring owners are the owner field of each historical inventory member. Inherited applications remain historical facts, not duplicate declarations.",
         "provenance": [_prov("inventory:native-models", "/"), _prov("contract:c3", "/classes")]},
        {"id": "inventory-limit:relation-family-reconciliation", "scope": SCOPE,
         "families": ["historical references", "native association families", "Profils/Positions/Levés", "hydraulic components", "repérage"],
         "owners": owners,
         "reason": "Reciprocal Ecore declarations are deduplicated; native families remain distinct source observations pending semantic reconciliation. The detailed 46-family MOD-1J artifact is unavailable, so no equality to that announced total is certified. Unknown physical endpoints are explicit nulls, not invented concepts.",
         "provenance": [_prov("sql:business", "/"), _prov("contract:c3", "/classes")]},
        {"id": "inventory-limit:outside-native-crud", "scope": SCOPE,
         "families": ["non-CRUD native properties", "measure ordering correspondence", "native geometry input transformations", "catalog activation", "native CRUD MOD-1J reconciliation"],
         "owners": ["da:concept:profil-long", "da:concept:profil-travers", "da:concept:leve-profil", "da:concept:objet-mesure-hydraulique", "da:component:position-profil"],
         "reason": "The twelve Plage de dépôt lit properties and selected point/measurement values and sequence orders are atomic with exact SQL supports. Other non-CRUD semantics/units, ordering correspondence and the announced 126-property MOD-1J reconciliation remain to qualify; canonical exhaustiveness is not certified by SQL columns.",
         "provenance": [_prov("sql:business", "/"), _prov("inventory:native-models", "/")]},
    ]
