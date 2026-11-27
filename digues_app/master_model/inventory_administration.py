"""Independent administrative candidates and explicit technical dispositions.

The application vocabulary, contracts and DDL witnesses have different roles.
Only B6 establishes the two already decided targets below; an implemented table,
permission or endpoint never approves a member of the canonical universe.
No credentials, database contents or environment configuration are inspected.
"""

from __future__ import annotations

import ast
from uuid import UUID, uuid5

from .inventory_common import candidate


SOURCE_PATHS = {
    "evidence:multi-tenancy": "docs/architecture/multi-tenancy.md",
    "evidence:control-catalog": "digues_app/control/catalog.py",
    "evidence:control-photo-policy": "digues_app/control/photo_policy.py",
    "evidence:control-contact-associations": "digues_app/control/contact_associations.py",
    "evidence:admin-basemap-caches": "webapp/backend/digues_webapp/admin_basemap_caches.py",
    "evidence:admin-directory": "webapp/backend/digues_webapp/admin_directory.py",
    "evidence:control-tests": "tests/test_control.py",
    "evidence:control-postgres-tests": "tests/test_control_postgres.py",
    "evidence:environment-data": "docs/integrations/environment-data.md",
    "evidence:ai-assistant": "docs/integrations/ai-assistant.md",
    "evidence:cartography": "docs/architecture/cartography.md",
    "evidence:media": "docs/architecture/media-storage.md",
}

SCOPE = "scope:administration"


def _id(kind, key):
    return f"candidate:{kind}:administration/{key}"


def _proof(source, locator):
    return {"source": source, "locator": locator}


def _binding(table, columns=(), source="sql:control"):
    return {
        "status": "BINDING_PARTIEL",
        "supports": [{"source": source, "table": table, "columns": list(columns)}],
        "gaps": ["Localisation physique seulement ; règles canoniques et qualification complète non approuvées."],
    }


def _entry(kind, key, definition, *, locator="§2 Vocabulaire et invariants", **kwargs):
    provenance = kwargs.pop("provenance", [_proof("evidence:multi-tenancy", locator)])
    kwargs.setdefault("origin", "native_only")
    kwargs.setdefault("preservation", [
        "Préserver identité, périmètre autorisé et données existantes ; aucune fusion implicite avec le Patrimoine.",
    ])
    kwargs.setdefault("questions", ["Appartenance, clé typée et contrat canonique à approuver ; existence applicative démontrée."])
    return candidate(_id(kind, key), kind, SCOPE, definition, provenance, **kwargs)


def _constant(node, values):
    """Bounded reading of catalog constants, without importing runtime modules."""
    if isinstance(node, ast.Name):
        return values[node.id]
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        result = [_constant(value, values) for value in node.elts]
        return set(result) if isinstance(node, ast.Set) else result
    if isinstance(node, ast.Dict):
        return {_constant(key, values): _constant(value, values)
                for key, value in zip(node.keys, node.values)}
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id in {"frozenset", "MappingProxyType"} and len(node.args) == 1 and not node.keywords:
            return _constant(node.args[0], values)
        if node.func.id == "UUID" and len(node.args) == 1 and not node.keywords:
            return str(UUID(_constant(node.args[0], values)))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return set(_constant(node.left, values)) | set(_constant(node.right, values))
    return ast.literal_eval(node)


def _catalog_constants(path, wanted):
    values = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in wanted:
                values[name] = _constant(node.value, values)
    if set(values) != set(wanted):
        raise ValueError("INV-COV.ADMIN_CATALOG_CONSTANT_MISSING")
    return values


def _concepts():
    return [
        _entry("concept", "structure-administrative",
               "Organisation administrative propriétaire d'espaces métier ; distincte d'un Organisme patrimonial.",
               binding=_binding("public.structures", ("id", "slug", "name", "status")),
               details={"distinct_from": ["da:concept:organisme"]}),
        _entry("concept", "espace-metier",
               "Espace métier d'identité logique UUID, appartenant à exactement une structure, distinct de son nom PostgreSQL.",
               binding=_binding("public.business_databases", ("id", "structure_id", "slug", "display_name", "status")),
               depends_on=[_id("concept", "structure-administrative")]),
        _entry("concept", "utilisateur",
               "Identité applicative de l'Administration, distincte d'un Contact métier et d'un compte PostgreSQL.",
               target="da:concept:utilisateur", origin="UNKNOWN", status="CONSEQUENCE_NECESSAIRE",
               decision_refs=["decision:B6"],
               provenance=[_proof("evidence:consolidation", "B6"),
                           _proof("evidence:multi-tenancy", "§2 Utilisateur ; Attribution des objets à un Contact"),
                           _proof("ecore:core", "#//Utilisateur")],
               evidence_keys=["sirs-v2:class:core/Utilisateur"],
               binding=_binding("public.users", ("id", "login", "display_name", "status")),
               questions=["Clé canonique non scellée ; aucune équivalence des comptes, mots de passe ou rôles historiques et natifs n'est déduite."],
               details={"distinct_from": ["da:concept:contact"], "historical_equivalence": "A_APPROUVER"}),
        _entry("concept", "profil-habilitation",
               "Version identifiée d'un ensemble de permissions de base ; distincte des Profils de levé métier.",
               binding=_binding("public.roles", ("id", "code", "scope_kind", "display_name", "version")),
               details={"distinct_from": ["da:concept:profil-long", "da:concept:profil-travers"],
                        "identity": ["code", "version"]}),
        _entry("concept", "permission-habilitation",
               "Capacité administrative nommée et périmétrée ; son existence ne vaut ni attribution ni fonctionnalité implémentée.",
               binding=_binding("public.permissions", ("code", "scope_kind", "description")),
               details={"identity": ["code", "scope_kind"],
                        "catalog": _id("catalog", "permissions-habilitation")}),
    ]


def _endpoint(target, role, minimum=None, maximum=None):
    return {"target": target, "role": role, "direction": "participant",
            "cardinality": None if minimum is None else {"min": minimum, "max": maximum}}


def _relations():
    structure, espace, user, profile = (_id("concept", name) for name in (
        "structure-administrative", "espace-metier", "utilisateur", "profil-habilitation"))
    permissions = _id("concept", "permission-habilitation")
    return [
        _entry("relation", "structure-espace-metier",
               "Chaque espace métier possède une structure propriétaire ; cette propriété n'accorde aucun accès métier implicite.",
               depends_on=[structure, espace], binding=_binding("public.business_databases", ("id", "structure_id")),
               details={"endpoints": [_endpoint(structure, "structure", 1, 1), _endpoint(espace, "espace", 0, "*")],
                        "association_identity": None, "cardinality_authority": "APPLICATION_CONTRACT_ONLY",
                        "conditions": ["Structure/base cohérentes ; aucune permission métier héritée."]}),
        _entry("relation", "membership-base",
               "Affectation explicite d'un utilisateur à une base avec une version de profil database ; identité composée utilisateur/base.",
               depends_on=[espace, user, profile, structure],
               binding=_binding("public.memberships", ("user_id", "business_database_id", "structure_id", "role_id", "role_scope", "status", "created_by")),
               details={"endpoints": [_endpoint(user, "utilisateur", 0, "*"), _endpoint(espace, "base", 0, "*"),
                                      _endpoint(profile, "profil_par_association", 1, 1)],
                        "association_identity": ["user_id", "business_database_id"],
                        "cardinality_authority": "APPLICATION_CONTRACT_ONLY",
                        "conditions": ["Structure cohérente ; profil database ; membership active et permission exigées à chaque accès."]}),
        _entry("relation", "profil-permission",
               "Association d'une version de profil aux capacités nommées dans le même périmètre ; un code seul n'identifie pas une permission.",
               depends_on=[profile, permissions], locator="§4 Contraintes complémentaires ; §5 Profils et permissions installés",
               binding=_binding("public.role_permissions", ("role_id", "scope_kind", "permission_code")),
               details={"endpoints": [_endpoint(profile, "profil", 0, "*"), _endpoint(permissions, "permission", 0, "*")],
                        "association_identity": ["role_id", "permission_code"],
                        "cardinality_authority": "APPLICATION_CONTRACT_ONLY",
                        "conditions": ["Les deux extrémités ont le même scope_kind ; aucune capacité nouvelle n'est créée."]}),
        _entry("relation", "utilisateur-contact",
               "Association facultative Utilisateur–Contact dans une base autorisée, utilisée pour l'auteur natif ; inverse non approuvée.",
               target="da:relation:utilisateur-contact", status="CONSEQUENCE_NECESSAIRE", decision_refs=["decision:B6"],
               depends_on=[user, espace, "da:concept:contact"],
               provenance=[_proof("evidence:consolidation", "B6 ; cardinalité inverse Utilisateur–Contact"),
                           _proof("evidence:multi-tenancy", "§2 Attribution des objets à un Contact"),
                           _proof("evidence:control-contact-associations", "assign ; verified_contact"),
                           _proof("evidence:control-postgres-tests", "test_contact_association_requires_membership_contact_and_revision")],
               binding=_binding("public.user_contact_associations", ("user_id", "business_database_id", "contact_id", "verified_by", "verified_at")),
               questions=["Cardinalité inverse Contact–Utilisateur A_APPROUVER ; aucune unicité globale de contact_id déduite.",
                          "Clé canonique non scellée."],
               details={"endpoints": [_endpoint(user, "utilisateur"), _endpoint("da:concept:contact", "contact")],
                        "association_identity": ["user_id", "business_database_id"],
                        "context": espace, "inverse_status": "A_APPROUVER",
                        "application_contact_cardinality": {"min": 0, "max": 1},
                        "conditions": ["Membership active ; Contact existant dans la même base ; aucune FK inter-base.",
                                       "Absence d'association => auteur NULL ; changement sans réattribution des objets historiques.",
                                       "L'import n'utilise jamais le compte déclencheur comme auteur historique."]}),
    ]


def _catalogs(paths):
    catalog = _catalog_constants(paths["evidence:control-catalog"],
                                 {"CATALOG_VERSION", "ROLE_NAMESPACE", "PERMISSIONS", "READ_PERMISSIONS", "EDIT_PERMISSIONS", "ROLES"})
    photo = _catalog_constants(paths["evidence:control-photo-policy"],
                               {"PHOTO_QUALITY_PROFILES", "PHOTO_QUALITY_PARAMETERS_VERSION", "PHOTO_QUALITY_PARAMETERS"})
    values = [{"id": f"{scope}/{code}", "code": code, "scope": scope, "label": label,
               "origin": "native_only"} for code, scope, label in catalog["PERMISSIONS"]]
    roles = [{"id": str(uuid5(UUID(catalog["ROLE_NAMESPACE"]), f"{code}:v{catalog['CATALOG_VERSION']}")), "code": code, "scope": scope,
              "label": label, "version": catalog["CATALOG_VERSION"], "permissions": sorted(permissions),
              "origin": "native_only"} for code, scope, label, permissions in catalog["ROLES"]]
    shared = {"value_status": "INITIALIZED_APPLICATION_VALUES_NOT_CANONICAL_APPROVAL",
              "indefini": "NOT_DEFINED", "validity_activation": "DISTINCT_NOT_INTERCHANGEABLE"}
    result = [
        _entry("catalog", "permissions-habilitation", "Domaine des capacités administratives, identifiées par code ET périmètre.",
               owner=_id("concept", "permission-habilitation"),
               locator="§5 Profils et permissions installés", binding=_binding("public.permissions", ("code", "scope_kind", "description", "catalog_version")),
               provenance=[_proof("evidence:control-catalog", "PERMISSIONS ; CATALOG_VERSION"), _proof("evidence:multi-tenancy", "§5"),
                           _proof("evidence:control-tests", "test_standard_permissions_delete_purge_and_scopes")],
               details={**shared, "values": sorted(values, key=lambda row: row["id"]), "version": catalog["CATALOG_VERSION"],
                        "conditions": ["Une permission cataloguée ne prouve ni fonctionnalité implémentée ni attribution.",
                                       "business.purge n'est attribuée à aucun profil standard."]}),
        _entry("catalog", "profils-habilitation", "Versions natives des profils d'habilitation, distinctes de l'enum Role historique.",
               locator="§5 Profils et permissions installés", depends_on=[_id("concept", "profil-habilitation")],
               binding=_binding("public.roles", ("id", "code", "scope_kind", "version")),
               provenance=[_proof("evidence:control-catalog", "ROLES ; standard_role_id ; CATALOG_VERSION"), _proof("evidence:multi-tenancy", "§5")],
               details={**shared, "values": sorted(roles, key=lambda row: row["id"]),
                        "historical_equivalence": "A_APPROUVER", "identity_rule": "UUIDv5 ROLE_NAMESPACE, code:v<version>"}),
        _entry("catalog", "profils-qualite-photo", "Profils de préparation Photo ; paramètres déterministes versionnés, sans modification des originaux antérieurs.",
               owner=_id("configuration", "politique-photo"),
               provenance=[_proof("evidence:control-photo-policy", "PHOTO_QUALITY_PROFILES ; PHOTO_QUALITY_PARAMETERS ; PHOTO_QUALITY_PARAMETERS_VERSION")],
               binding=_binding("public.platform_settings", ("photo_quality_profile",)),
               details={**shared, "values": [{"id": key, "code": key, "label": label, "origin": "native_only",
                                               "parameters": photo["PHOTO_QUALITY_PARAMETERS"][key]}
                                              for key, label in sorted(photo["PHOTO_QUALITY_PROFILES"].items())],
                        "parameters_version": photo["PHOTO_QUALITY_PARAMETERS_VERSION"]}),
    ]
    return result


# These meanings come from application contracts, not from the presence of a
# column. Synchronization counters, passwords, OIDs and timestamps are therefore
# not manufactured as administrative/business attributes.
_ADMIN_ATTRIBUTES = (
    ("concept", "structure-administrative", "public.structures", "slug", "Identifiant lisible stable de la structure", "text", None),
    ("concept", "structure-administrative", "public.structures", "name", "Nom affiché de la structure", "text", None),
    ("concept", "structure-administrative", "public.structures", "status", "Activation ou archivage administratif de la structure", "text", None),
    ("concept", "espace-metier", "public.business_databases", "slug", "Identifiant lisible stable de la base", "text", None),
    ("concept", "espace-metier", "public.business_databases", "display_name", "Nom affiché de la base métier", "text", None),
    ("concept", "espace-metier", "public.business_databases", "status", "État administratif de la base, distinct de sa validité métier", "text", None),
    ("concept", "utilisateur", "public.users", "login", "Identifiant de connexion applicatif normalisé", "text", None),
    ("concept", "utilisateur", "public.users", "display_name", "Nom affiché du compte applicatif", "text", None),
    ("concept", "utilisateur", "public.users", "status", "Activation du compte applicatif", "text", None),
    ("concept", "utilisateur", "public.users", "is_superadmin", "Capacité d'administration d'instance sans membership métier implicite", "boolean", None),
    ("concept", "profil-habilitation", "public.roles", "code", "Code du profil d'habilitation", "text", None),
    ("concept", "profil-habilitation", "public.roles", "display_name", "Nom affiché du profil d'habilitation", "text", None),
    ("concept", "profil-habilitation", "public.roles", "scope_kind", "Portée structure ou database du profil", "text", None),
    ("concept", "profil-habilitation", "public.roles", "version", "Version identitaire du profil ; aucun changement silencieux d'une affectation existante", "integer", None),
    ("relation", "membership-base", "public.memberships", "status", "Activation de l'affectation explicite à la base", "text", None),
    ("configuration", "politique-photo", "public.platform_settings", "photo_quality_profile", "Profil de préparation des nouvelles Photos", "text", "profils-qualite-photo"),
    ("configuration", "politique-photo", "public.platform_settings", "photo_max_file_size_bytes", "Taille maximale fonctionnelle d'une nouvelle Photo, bornée par le garde-fou serveur", "integer", None),
)


def _attributes():
    result = []
    for kind, key, table, column, definition, datatype, catalog in _ADMIN_ATTRIBUTES:
        owner = _id(kind, key)
        result.append(_entry("attribute", f"{key}/{column}", definition, owner=owner, depends_on=[owner],
                             locator="§2 ; §4 Schéma de contrôle ; §16 Administration des comptes et affectations",
                             binding=_binding(table, (column,)),
                             provenance=[_proof("evidence:multi-tenancy", "§2 ; §4 ; §16"),
                                         _proof("sql:control", f"TABLE_DEFINITIONS/{table.removeprefix('public.')}/{column}")],
                             details={"name": column, "role": definition, "value_type": datatype,
                                      "unit": "byte" if column.endswith("_bytes") else None,
                                      "catalog": _id("catalog", catalog) if catalog else None,
                                      "cardinality": {"min": 1, "max": 1}, "storage": "STORED",
                                      "cardinality_authority": "APPLICATION_CONTRACT_ONLY",
                                      "validity_activation": "DISTINCT_NOT_INTERCHANGEABLE"}))
        if key == "politique-photo":
            result[-1]["provenance"].append(_proof("evidence:control-photo-policy", "_contract ; update_admin_policy"))
    attributions = (
        ("relation", "membership-base", "public.memberships", "created_by", 0,
         "Compte créateur de l'affectation à la base, distinct de l'utilisateur affecté"),
        ("relation", "utilisateur-contact", "public.user_contact_associations", "verified_by", 1,
         "Compte ayant vérifié l'association au Contact ; distinct du Contact auteur métier"),
        ("configuration", "politique-photo", "public.platform_settings", "updated_by", 0,
         "Compte ayant modifié la politique Photo, sans réattribution des Photos"),
        ("mechanism", "journal-administratif", "public.admin_audit_log", "actor_user_id", 0,
         "Compte acteur de l'action administrative ; absent pour le bootstrap hors session"),
        ("mechanism", "job-provisioning", "public.admin_jobs", "initiated_by", 0,
         "Compte initiateur du job administratif, sans autorité métier implicite"),
        ("mechanism", "job-import-sirs", "public.business_import_jobs", "initiated_by", 1,
         "Compte initiateur de l'import ; ne devient jamais auteur des objets historiques"),
    )
    for kind, key, table, column, minimum, definition in attributions:
        owner, account = _id(kind, key), _id("concept", "utilisateur")
        result.append(_entry("attribute", f"{key}/{column}", definition, owner=owner,
                             depends_on=[owner, account], binding=_binding(table, (column,)),
                             provenance=[_proof("evidence:multi-tenancy", "§2 Attribution ; §4 ; §13 ; §16"),
                                         _proof("sql:control", f"TABLE_DEFINITIONS/{table.removeprefix('public.')}/{column}")],
                             details={"name": column, "role": definition, "value_type": "uuid", "unit": None,
                                      "catalog": None, "reference": account,
                                      "cardinality": {"min": minimum, "max": 1}, "storage": "STORED",
                                      "semantic_layer": "ADMINISTRATIVE_ATTRIBUTION_NOT_BUSINESS_AUTHOR",
                                      "cardinality_authority": "APPLICATION_CONTRACT_ONLY"}))
    return result


def _technical():
    result = [
        _entry("configuration", "politique-photo", "Configuration d'instance versionnée de préparation des nouvelles Photos ; distincte du contenu métier Photo.",
               provenance=[_proof("evidence:control-photo-policy", "read_public_policy ; update_admin_policy")],
               binding=_binding("public.platform_settings", ("photo_quality_profile", "photo_max_file_size_bytes", "photo_quality_policy_version", "updated_by")),
               details={"conditions": ["Concurrence par expected_version ; audit administratif ; garde-fou serveur indépendant."]}),
        _entry("configuration", "politique-caches-fonds", "Configuration et budgets des caches de fonds cartographiques sous contrôle superadmin ; le renderer conserve leur gestion.",
               provenance=[_proof("evidence:admin-basemap-caches", "CacheGlobalChanges ; CacheFundChanges ; register_routes"),
                           _proof("evidence:cartography", "Administration des caches")],
               details={"conditions": ["Aucun accès métier accordé ; caches cartographiques séparés de la synchronisation métier."]}),
        _entry("configuration", "territoire-administratif", "Périmètre géographique technique de la base, hors journal et hors ligne métier ; distinct d'un objet patrimonial.",
               provenance=[_proof("evidence:agents", "§5 territoires_administratifs"), _proof("sql:business", "technique.territoires_administratifs")],
               binding=_binding("technique.territoires_administratifs", ("id", "libelle", "geometry"), "sql:business"),
               details={"conditions": ["Configuration technique avec signal local inter-onglets ; aucun versionnement ou SSE métier."]}),
        _entry("configuration", "crs-base", "CRS autoritaire de la base métier ; configuration spatiale commune, sans transformation des géométries historiques par l'inventaire.",
               locator="§1 ; §14 Provisioning, requested_srid",
               binding=_binding("technique.metadonnees_base", ("singleton", "srid"), "sql:business"),
               details={"conditions": ["CRS base explicite et immuable pendant un job ; respecter MOD-1F."]}),
    ]
    mechanisms = (
        ("marqueur-controle", "public.control_schema_metadata", "Version, identité d'instance et empreinte de l'installation du contrôle", "evidence:multi-tenancy", "§3–4", "sql:control"),
        ("journal-administratif", "public.admin_audit_log", "Journal administratif append-only d'actions autorisées ou refusées ; ce journal ne constitue pas un objet patrimonial", "evidence:multi-tenancy", "§4 ; §16 Journal", "sql:control"),
        ("job-provisioning", "public.admin_jobs", "Tâche reprenable de création explicite de base, identifiée et idempotente", "evidence:multi-tenancy", "§14 Création de bases", "sql:control"),
        ("job-import-sirs", "public.business_import_jobs", "Tâche d'import SIRS liée à une seule base et à une preuve source ; conserve le reçu de résultat", "evidence:multi-tenancy", "§13 Import SIRS M3", "sql:control"),
        ("barriere-import", "public.business_import_barriers", "Exclusion durable d'import par base et job pour empêcher les écritures concurrentes", "evidence:multi-tenancy", "§13 M1–M3", "sql:control"),
        ("transferts-photo", "public.pending_photo_transfers", "Manifeste de transfert historique Photo, lié au job et à la base, sans suppression de l'original", "evidence:media", "Manifeste historique M5a", "sql:control"),
        ("transferts-plans-seuil", "public.pending_seuil_plan_transfers", "Manifeste de transfert des plans de seuils, lié au job et à la base ; distinct du plan métier", "sql:control", "TABLE_DEFINITIONS/pending_seuil_plan_transfers", "sql:control"),
        ("authentification-locale", "public.local_credentials", "Vérificateur de mot de passe du compte applicatif ; secret technique jamais attribut métier ni export de données", "evidence:multi-tenancy", "§9 Identité locale", "sql:control"),
        ("session-serveur", "public.sessions", "Session opaque révocable liée à la version d'authentification ; ne porte aucune membership durable", "evidence:multi-tenancy", "§9 Identité locale", "sql:control"),
        ("eligibilite-import", "technique.import_eligibility", "Preuve d'éligibilité et reçu d'import transactionnel de la base métier ; absence de preuve signifie historique inconnu", "evidence:multi-tenancy", "§13 M1–M3", "sql:business"),
        ("journal-ressources", "technique.evenements_ressources", "Événement durable et séquence du journal métier, atomiques avec la mutation ; distinct de l'identité métier", "evidence:synchronisation", "Journal durable ; eventId et curseur", "sql:business"),
        ("recu-operation", "technique.operations_ressources", "Reçu d'opération idempotent, dans la transaction mutation/version/événement ; distinct de l'objet métier", "evidence:synchronisation", "Idempotence ; operationId", "sql:business"),
        ("cache-decouverte-hydrometrie", "technique.cache_decouverte_hydrometrie", "Cache de découverte hydrométrique identifié par territoire, provenance, politique et date, hors synchronisation métier", "evidence:environment-data", "Cache de découverte hydrométrie", "sql:business"),
        ("cache-structure-hydrometrie", "technique.cache_structure_hydrometrie", "Cache structurel TOPAGE/Hub'Eau, distinct des observations et prévisions dynamiques", "evidence:environment-data", "Cache structurel hydrométrie", "sql:business"),
        ("documents-connaissance", "technique.documents_connaissance", "Document du corpus documentaire de l'assistant avec provenance et empreinte ; ne définit pas un objet patrimonial", "evidence:ai-assistant", "Corpus documentaire ; provenance", "sql:business"),
        ("fragments-connaissance", "technique.fragments_connaissance", "Fragment ordonné de document indexé pour la recherche ; conserve son document source et son ordre", "evidence:ai-assistant", "Indexation et fragments", "sql:business"),
    )
    for key, table, definition, source, locator, ddl in mechanisms:
        result.append(_entry("mechanism", key, definition,
                             provenance=[_proof(source, locator), _proof(ddl, table)],
                             binding=_binding(table, source=ddl),
                             questions=["Mécanisme technique identifié, hors concepts patrimoniaux ; statut dans T(Administration) à approuver."],
                             details={"classification": "TECHNICAL_MECHANISM_WITH_OWN_INVARIANTS",
                                      "business_attributes_inferred": False}))
    memory_mechanisms = (
        ("cache-meteo", "Cache mémoire de météo limité par territoire et date ; observations/prévisions distinguées", "evidence:environment-data", "§2.2 ; §4.5"),
        ("cache-observations-previsions", "Caches dynamiques d'observations et prévisions hydrométriques, séparés du cache structurel", "evidence:environment-data", "Observations dynamiques ; prévisions ; caches mémoire"),
        ("cache-diagnostics", "Cache borné de diagnostics administratifs, partitionné par base/version/epoch ; ne fait pas autorité pour les permissions", "evidence:multi-tenancy", "§15 Diagnostics"),
        ("projection-hors-ligne", "Projection locale et intentions validées isolées par utilisateur et base, distinctes des brouillons non validés", "evidence:synchronisation", "IndexedDB ; projections ; intentions et brouillons"),
        ("transport-invalidations", "SSE et BroadcastChannel alimentent le même mécanisme de reprise ; réception distincte de consommation réussie", "evidence:synchronisation", "SSE ; BroadcastChannel ; curseur durable"),
    )
    for key, definition, source, locator in memory_mechanisms:
        result.append(_entry("mechanism", key, definition, provenance=[_proof(source, locator)],
                             questions=["Mécanisme technique identifié ; inventaire de ses paramètres et admission dans T(Administration) non scellés."],
                             details={"classification": "TECHNICAL_MECHANISM_WITH_OWN_INVARIANTS",
                                      "business_attributes_inferred": False}))
    excluded = (
        ("role-postgresql", "Compte et rôle PostgreSQL d'installation, runtime, SQL ou IA : infrastructure distincte de l'Utilisateur et du profil d'habilitation", "evidence:multi-tenancy", "§2 ; §14 Matrice de privilèges"),
        ("connexion-physique", "Cluster, nom physique, pool et profil de connexion privé : support d'un espace logique, jamais identité ni autorisation métier", "evidence:agents", "§4 Architecture, autorisations et portabilité"),
        ("stockage-media", "Disque, NAS ou stockage objet : infrastructure remplaçable derrière les références logiques et l'API médias", "evidence:agents", "§4 Médias"),
        ("fournisseur-hebergement", "Cloudflare, Supabase et autres hébergeurs : options techniques sans API métier imposée", "evidence:agents", "§4 Portabilité"),
        ("secrets-fournisseurs", "Credentials et clés de fournisseurs : secrets d'exécution privés, jamais attributs métier ni données du modèle", "evidence:agents", "§4 Secrets ; §6 Fournisseurs"),
        ("cache-shell-pwa", "Cache du shell et assets statiques : infrastructure de distribution séparée des données et intentions métier", "evidence:multi-tenancy", "§12 Service Worker et isolation"),
    )
    for key, definition, source, locator in excluded:
        result.append(_entry("infrastructure", key, definition, status="EXCLU",
                             provenance=[_proof(source, locator)],
                             preservation=["Exclusion de l'univers métier seulement ; préserver sécurité, isolation et données des mécanismes techniques."],
                             questions=[],
                             details={"exclusion_scope": "BUSINESS_UNIVERSE_ONLY", "deletion_authorized": False,
                                      "reason": "Frontière technique explicitement définie par le contrat cité ; aucune suppression de données ni de fonctionnalités."}))
    return result


def inventory(historical, paths):
    """Enumerate independently of registry targets, then allow reconciliation."""
    del historical  # Historical evidence is cited; no account migration inferred.
    result = [*_concepts(), *_relations(), *_catalogs(paths), *_attributes(), *_technical()]
    return sorted(result, key=lambda item: item["id"])


def limitations():
    return [
        {"id": "inventory-limit:administration-mod1j-source", "scope": SCOPE,
         "families": ["MOD-1J : 54 éléments Administration/Contrôle annoncés"], "owners": [],
         "reason": "La liste nominative MOD-1J n'est pas disponible dans les autorités épinglées. La reconstruction indépendante des contrats ne prouve ni égalité avec ces 54 éléments ni exhaustivité de T(Administration).",
         "provenance": [_proof("instruction:mod-1l", "§6 Administration / Contrôle, 54 éléments") ]},
        {"id": "inventory-limit:administration-boundary", "scope": SCOPE,
         "families": ["Concepts et relations administratifs", "Configurations", "Catalogues administratifs", "Jobs/journaux/caches et mécanismes techniques"],
         "owners": [_id("concept", key) for key in ("structure-administrative", "espace-metier", "utilisateur", "profil-habilitation", "permission-habilitation")],
         "reason": "Existence applicative et classification documentées, sans décision fermant les membres de T(Administration). Les candidats et mécanismes ne sont pas promus en cibles approuvées par leur DDL.",
         "provenance": [_proof("evidence:consolidation", "B6"), _proof("evidence:multi-tenancy", "§2 ; §4–5") ]},
        {"id": "inventory-limit:administration-atomic-properties", "scope": SCOPE,
         "families": ["Attributs métier administratifs versus propriétés techniques des 19 tables de contrôle",
                      "Chronologie des attributions administratives et propriétés historiques Utilisateur",
                      "Catalogues de statuts, portées, étapes et résultats techniques",
                      "Paramètres des caches cartographiques et des caches mémoire",
                      "Équivalence historique Utilisateur/Role versus identité et habilitations natives"],
         "owners": [_id("relation", key) for key in ("membership-structure", "membership-base", "utilisateur-contact")]
                   + [_id("configuration", key) for key in ("politique-photo", "politique-caches-fonds")]
                   + [_id("mechanism", key) for key in ("journal-administratif", "job-provisioning", "job-import-sirs", "session-serveur")],
         "reason": "Les 25 attributs documentés, dont sept rôles d'attribution administrative, sont atomisés. L'identité technique, les timestamps, secrets, preuves de reprise et compteurs restent sous leurs mécanismes ; leurs rôles canoniques, ainsi que les domaines de statuts et paramètres non métier, demandent qualification distincte et ne sont pas déclarés attributs métier par simple présence SQL.",
         "provenance": [_proof("evidence:multi-tenancy", "§4 Schéma de contrôle"), _proof("sql:control", "TABLE_DEFINITIONS"),
                        _proof("evidence:admin-basemap-caches", "CacheGlobalChanges ; CacheFundChanges") ]},
        {"id": "inventory-limit:administration-user-contact-inverse", "scope": SCOPE,
         "families": ["Cardinalité inverse Contact–Utilisateur"], "owners": ["da:relation:utilisateur-contact"],
         "reason": "Cardinalité inverse A_APPROUVER. La PK native (user_id, business_database_id) ne démontre aucune unicité inverse et ne ferme pas le contrat canonique.",
         "provenance": [_proof("evidence:consolidation", "B6 ; Utilisateur–Contact"),
                        _proof("sql:control", "TABLE_DEFINITIONS/user_contact_associations") ]},
    ]
