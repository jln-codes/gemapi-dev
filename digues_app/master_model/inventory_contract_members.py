"""Atomic subjects of acquired FM/B3 clauses, independent of target presence.

These declarations complement the source-property inventory. A native field and
a decision subject are separate observations until their equivalence is proved;
they must not be merged merely because a label or SQL column resembles another.
"""

from .inventory_common import candidate


SOURCE_PATHS = {}
SCOPE = "scope:patrimoine"


def typed_troncon_members():
    """Keep the A1 evidence and apply the exact A3 classification succession."""
    family = "candidate:concept:patrimoine/troncon"
    categories = [("", "Tronçons typés", "concept"),
                  ("digue", "Ancienne catégorie Tronçon de digue", "type"),
                  ("pied", "Ancienne catégorie Pied", "type"),
                  ("berge", "Berge", "type"), ("talus", "Talus", "type"),
                  ("talus-risberme", "Talus de risberme", "type"),
                  ("crete", "Crête", "type"), ("franc-bord", "Franc-bord", "type"),
                  ("risberme", "Risberme", "type"), ("indetermine", "Indéterminé", "type")]
    rows = []
    for code, label, kind in categories:
        suffix = "/" + code if code else ""
        retired = code in {"digue", "pied"}
        target = "da:" + kind + ":troncon" + suffix
        rows.append(candidate(
            "candidate:" + kind + ":patrimoine/troncon" + suffix,
            kind, SCOPE, label + (" : catégorie remplacée, sans suppression des occurrences historiques."
                                 if retired else " : classification approuvée ; projection historique distincte et non activée."),
            [{"source": "instruction:mod-1p-a3", "locator": "§1–5 : classification, positions, conservation et successions"}],
            owner=family if code else None, target=target, status="DECIDE",
            decision_refs=["decision:MOD1P-A3"],
            evidence_keys=["decision-category:mod1p/troncon" + suffix],
            preservation=["Identités, propriétés, références, provenance et géométries historiques conservées."],
            questions=["Destination et qualification des occurrences, propriétaires et bindings restent à établir avant projection."],
            details={"classification": "REPLACED" if retired else "DECIDE",
                     "activation": "BLOCKED_PENDING_DECISIONS", "physical_binding": "A_APPROUVER"}))
    rows.append(candidate(
        "candidate:component:patrimoine/troncon/trace-position", "component", SCOPE,
        "Tracé de position identitaire, sans sous-type autonome ni parent inventé.",
        [{"source": "instruction:mod-1p-b3", "locator": "§5 positions et rectificatif provenance exclusivement technique"}],
        owner=family, target="da:component:troncon/trace-position", status="DECIDE",
        decision_refs=["decision:MOD1P-B3"],
        evidence_keys=["decision-component:mod1p/troncon/trace-position"],
        preservation=["UUID, propriétés, géométrie, CRS, références et document source conservés sans fusion."],
        questions=["Contrat géométrique cible et associations non démontrées restent ouverts."],
        details={"classification": "DECIDE", "physical_binding": "PARTIAL",
                 "owner_cardinality": {"min": 0, "max": 1},
                 "source_classes": ["berge/PiedBerge", "berge/SommetBerge", "core/PiedDigue"],
                 "business_origin_markers": False}))
    rows.append(candidate(
        "candidate:attribute:decision/troncon/franc-bord/largeur", "attribute", SCOPE,
        "Largeur numérique métier du Franc-bord ; aucune valeur n'est déduite des catégories historiques.",
        [{"source": "instruction:mod-1p-b4", "locator": "§3 largeur numérique métier, absence de valeur Ecore directe"}],
        owner="candidate:type:patrimoine/troncon/franc-bord",
        target="da:attribute:troncon/franc-bord/largeur", status="DECIDE",
        decision_refs=["decision:MOD1P-B4"],
        evidence_keys=["decision-attribute:troncon/franc-bord/largeur"],
        preservation=["Absence de largeur historique exacte conservée comme valeur non renseignée."],
        questions=["L'alimentation native et la qualification d'une éventuelle source numérique restent distinctes."],
        details={"name": "largeur", "role": "acquired_business_attribute", "value_type": "number",
                 "unit": None, "catalog": None, "cardinality": None, "storage": "public.troncons.largeur"}))
    return rows


def _proof(decision):
    return [{"source": "evidence:db-1" if decision.startswith("DB1") else "evidence:consolidation",
             "locator": "§2 " + decision}]


def _endpoint(target, role, bounds=None, **kwargs):
    return {"target": target, "role": role, "direction": "participant", "cardinality": bounds, **kwargs}


def inventory(historical, paths):
    del paths
    facts = {f["id"]: f for f in historical["facts"]}
    result = []
    attributes = [
        ("prescription/date-fin", "da:concept:prescription", "date", "B3", "Clôture de la définition de Prescription lors d'une modification réelle."),
        ("reseau-hydraulique/nature", "da:concept:reseau-hydraulique", "string", "DB1-reseaux", "Nature ouverte ou fermée du Réseau hydraulique, distincte de ses autres référentiels."),
        ("vegetation/invasif", "da:concept:vegetation", "boolean", "FM-05", "Caractère invasif indépendant de la famille et de la disposition."),
        ("vegetation/disposition", "da:concept:vegetation", "string", "FM-05", "Disposition isolée, en ligne ou en surface, distincte du type biologique."),
        ("seuil-lit/plan/designation", "da:component:seuil-lit/plan", "string", "PlanSeuilLit", "Désignation de la pièce technique identitaire du Seuil de lit."),
        ("seuil-lit/plan/fichier", "da:component:seuil-lit/plan", "string", "PlanSeuilLit", "Fichier à extension libre de la pièce technique ; chemin historique distinct de la référence média cible."),
    ]
    for relation in ("contact-organisme-periode", "troncon-gestionnaire"):
        for date in ("date-debut", "date-fin"):
            attributes.append((relation + "/" + date, "da:relation:" + relation, "date", "FM-06",
                               f"{date} de la période identitaire {relation}, sans inférence de chevauchement ou de validité."))
    for code, owner, value_type, decision, definition in attributes:
        result.append(candidate("candidate:attribute:decision/" + code, "attribute", SCOPE,
            definition, _proof(decision), owner=owner, target="da:attribute:" + code,
            status="DECIDE", decision_refs=["decision:" + decision], depends_on=[owner],
            evidence_keys=["decision-attribute:" + code],
            preservation=["Valeur métier, identité du propriétaire et distinction entre absence et null."],
            questions=["Clé, cardinalité, représentation persistée et correspondances physiques restent à qualifier."],
            details={"name": code.rsplit("/", 1)[-1], "role": "acquired_business_attribute",
                     "value_type": value_type, "unit": None, "catalog": None,
                     "cardinality": None, "storage": "UNKNOWN"}))

    def relation(code, decision, definition, endpoints, *, source=None, status="CONSEQUENCE_NECESSAIRE"):
        provenance = _proof(decision)
        dependencies = [e["target"] for e in endpoints if e["target"]]
        for e in endpoints:
            dependencies.extend(e.get("allowed_targets", []))
        if source:
            if source not in facts:
                raise ValueError("INV-UNIV.RELATION_SOURCE_MISSING: " + source)
            provenance.extend(facts[source]["provenance"])
            dependencies.append(source)
        result.append(candidate("candidate:relation:decision/" + code, "relation", SCOPE,
            definition, provenance, target="da:relation:" + code, status=status,
            decision_refs=["decision:" + decision], depends_on=sorted(set(dependencies)),
            evidence_keys=["decision-relation:" + code],
            preservation=["Extrémités et identités conservées ; aucune fusion des occurrences."],
            questions=["Clé, identité de l'association et règles de validité non closes."],
            details={"endpoints": endpoints, "association_identity": "UNKNOWN",
                     "conditions": ["La conséquence acquise n'approuve aucune autre règle de la table SQL."],
                     "declarations": [], "source_reference": source}))

    for code, source_class, source_field, left, right in (
        ("cheminement-acces-franchissement", "VoieAcces", "ouvrageFranchissementIds", "cheminement", "franchissement"),
        ("cheminement-acces-voirie", "VoieAcces", "ouvrageVoirieIds", "cheminement", "ouvrage-voirie"),
        ("cheminement-digue-franchissement", "VoieDigue", "ouvrageFranchissementIds", "cheminement", "franchissement"),
        ("franchissement-voirie", "OuvrageFranchissement", "ouvrageVoirieIds", "franchissement", "ouvrage-voirie"),
    ):
        relation(code, "FM-09", "Relation conservée de " + source_class + " vers " + right + ", sans fusion Cheminement/Franchissement.",
                 [_endpoint("da:concept:" + left, "source", {"min": 0, "max": "*"}),
                  _endpoint("da:concept:" + right, "destination", {"min": 0, "max": "*"})],
                 source=f"sirs-v2:reference:core/{source_class}/{source_field}")
    for code, source_class in (("ligne-eau", "LigneEau"), ("montee-eaux", "MonteeEaux"), ("laisse-crue", "LaisseCrue")):
        relation(code + "-evenement", "FM-10", "Lien explicite du relevé " + code + " à l'Événement hydraulique.",
                 [_endpoint("da:type:objet-mesure-hydraulique/" + code, "mesure", {"min": 0, "max": "*"}),
                  _endpoint("da:concept:evenement-hydraulique", "evenement", {"min": 0, "max": 1})],
                 source=f"sirs-v2:reference:core/{source_class}/evenementHydrauliqueId")
    relation("montee-eaux-equipement", "FM-10", "Lien du relevé Montée des eaux à son instrument distinct.",
             [_endpoint("da:type:objet-mesure-hydraulique/montee-eaux", "mesure"),
              _endpoint("da:concept:equipement-mesure", "instrument", {"min": 0, "max": 1})],
             source="sirs-v2:reference:core/MonteeEaux/echelleLimnimetriqueId")
    relation("plan-prescription", "B3", "Une Prescription réutilisable peut être affectée à plusieurs Plans ; les minima ne sont pas déduits.",
             [_endpoint("da:concept:plan-gestion", "plan", {"min": None, "max": "*"}),
              _endpoint("da:concept:prescription", "prescription", {"min": None, "max": "*"})], status="DECIDE")
    relation("plan-perimetre", "B3", "Un Plan concerne exactement un Système d'endiguement ou un Aménagement hydraulique, exclusivement.",
             [_endpoint("da:concept:plan-gestion", "plan"),
              _endpoint(None, "perimetre", {"min": 1, "max": 1},
                        allowed_targets=["da:concept:systeme-endiguement", "da:concept:amenagement-hydraulique"])], status="DECIDE")
    return result + typed_troncon_members()


def limitations():
    return []
