"""Positive PostgreSQL AI policy, shared by provisioning and web introspection.

Only reviewed columns are granted: adding a table OR a column grants nothing.
Business labels describe infrastructure; free text, actors, source IDs, photos,
raw provenance and journal payloads are deliberately not included. This is an
access policy, not an anonymizer of incorrectly entered infrastructure labels.
"""

from types import MappingProxyType

AI_POLICY_VERSION = 1

AI_SELECT_COLUMNS = MappingProxyType({name: tuple(columns.split()) for name, columns in {
    "public.systemes": "id libelle population_protegee classement niveau_protection zone_protegee valid",
    "public.digues": "id systeme_endiguement_id libelle valid",
    "public.troncons": "id digue_id systeme_reperage_defaut_id libelle date_debut date_fin type_rive_id type_troncon_id typologie_troncon_id geometry valid",
    "public.systemes_reperage": "id troncon_id libelle valid",
    "public.bornes_reperage": "id libelle geometry fictive date_debut date_fin valid",
    "public.desordres": "id type_desordre_id categorie_desordre_id cote_id position_id source_id date_debut date_fin geometry valid",
    "public.observations": "id desordre_id troncon_id ouvrage_hydraulique_id equipement_mesure_id cheminement_id mobilier_id reseau_technique_id amenagement_hydraulique_id zone_stockage_id vegetation_id ouvrage_particulier_id structure_amenagement_hydraulique_id organe_protection_collective_id composant_berge_id urgence_id date_observation suite_apporter_id nombre_desordres valid",
    "public.localisations_desordres": "id desordre_id troncon_id systeme_reperage_id borne_debut_id distance_debut_m position_debut_relative offset_debut_m pr_debut borne_fin_id distance_fin_m position_fin_relative offset_fin_m pr_fin valid",
    "public.amenagements_hydrauliques": "id designation libelle date_debut date_fin valid",
    "public.zones_stockage": "id amenagement_hydraulique_id type_id designation libelle date_debut date_fin capacite_stockage profondeur_moyenne superficie geometry valid",
    "public.cheminements": "id type_cheminement_id libelle date_debut date_fin largeur statut_acces dimension_horizontale dimension_verticale geometry valid",
    "public.equipements_mesure": "id type_id date_debut geometry troncon_id valid",
    "public.mobilier": "id type_id date_debut geometry troncon_id valid",
    "public.reseaux_techniques": "id type_id date_debut geometry troncon_id valid",
    "public.vegetation": "id famille_vegetation invasif disposition sous_type date_debut etat_sanitaire_id classe_hauteur_id classe_diametre_id geometry valid",
    "public.plans_gestion_vegetation": "id libelle annee_debut annee_fin valid",
    "public.vegetation__troncons": "vegetation_id troncon_id",
    "public.vegetation__amenagements_hydrauliques": "vegetation_id amenagement_hydraulique_id",
    "public.plans_gestion__vegetation": "plan_id vegetation_id",
    "public.amenagements_hydrauliques__troncons": "id amenagement_hydraulique_id troncon_id",
    "public.cheminements__desordres": "id cheminement_id desordre_id",
    "public.cheminements__troncons": "id cheminement_id troncon_id",
    "public.desordres__troncons": "id desordre_id troncon_id",
    "public.systemes_reperage__bornes": "id systeme_reperage_id borne_id valeur_pr valid",
    "public.troncons_bornes": "troncon_id borne_id",
    "referentiel.categories_desordre": "id libelle abrege valid",
    "referentiel.types_desordre": "id categorie_id libelle abrege valid",
    "referentiel.urgences": "id libelle valid",
    **{name: "id code abrege libelle valid" for name in (
        "referentiel.classes_diametre_vegetation", "referentiel.classes_hauteur_vegetation",
        "referentiel.etats_sanitaires_vegetation",
        "referentiel.types_amenagement_hydraulique", "referentiel.types_cheminement",
        "referentiel.types_equipement_mesure", "referentiel.types_mobilier",
        "referentiel.types_ouvrage_hydraulique", "referentiel.types_reseau_technique",
    )},
    **{name: "id code libelle valid" for name in (
        "referentiel.familles_vegetation", "referentiel.dispositions_vegetation",
        "referentiel.sous_types_vegetation",
    )},
    "public.desordres_points_saisie": "id type_desordre_id date_debut date_fin geometry valid coord_x coord_y longitude_4326 latitude_4326",
    "public.localisations_desordres_lecture": "id desordre_id type_geometrie_desordre nombre_troncons reperage_disponible troncon_id troncon_libelle systeme_reperage_id systeme_reperage_libelle borne_debut_id borne_debut_libelle distance_debut_m position_debut_relative pr_debut borne_fin_id borne_fin_libelle distance_fin_m position_fin_relative pr_fin resume_localisation valid",
    "public.bornes_systemes_reperage_lecture": "id systeme_reperage_id borne_id libelle role_spatial libelle_affichage valeur_pr valid",
    # Existing tools: administrative geometry and curated, versioned documentation.
    "technique.territoires_administratifs": "id libelle geometry",
    "technique.documents_connaissance": "id source_type path title",
    "technique.fragments_connaissance": "id document_id ordinal heading content search_vector",
}.items()})

# PostGIS installs these PUBLIC-readable technical catalogs, not business data.
# Explicit columns prevent an extension/schema change silently broadening access.
AI_POSTGIS_COLUMNS = MappingProxyType({name: tuple(columns.split()) for name, columns in {
    "spatial_ref_sys": "srid auth_name auth_srid srtext proj4text",
    "geometry_columns": "f_table_catalog f_table_schema f_table_name f_geometry_column coord_dimension srid type",
    "geography_columns": "f_table_catalog f_table_schema f_table_name f_geography_column coord_dimension srid type",
}.items()})

AI_SCHEMA_OBJECTS = tuple(sorted({name.split(".", 1)[1] for name in AI_SELECT_COLUMNS} - {
    "territoires_administratifs", "documents_connaissance", "fragments_connaissance",
}))


def validate_ai_select_security(cursor):
    """Check effective ACLs (including PUBLIC and column grants), fail closed.

No business values are read. All non-system schemas are inspected; arbitrary
views, foreign tables, large objects and default grants must not open a bypass.
Common write/DDL/membership/function/CONNECT checks are performed by the caller.
"""
    cursor.execute("""SELECT n.nspname,c.relname,a.attname,
            has_table_privilege(c.oid,'SELECT')
        FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid
        WHERE n.nspname NOT IN ('pg_catalog','information_schema')
          AND n.nspname NOT LIKE 'pg_toast%%' AND n.nspname NOT LIKE 'pg_temp_%%'
          AND c.relkind IN ('r','p','v','m','f') AND a.attnum>0 AND NOT a.attisdropped
          AND has_column_privilege(c.oid,a.attnum,'SELECT')""")
    for schema, relation, column, table_grant in cursor.fetchall():
        allowed = AI_SELECT_COLUMNS.get(f"{schema}.{relation}", ())
        technical = AI_POSTGIS_COLUMNS.get(relation, ()) if schema == "public" else ()
        if (column not in (*allowed, *technical)
                or (table_grant and not technical)):
            raise ValueError("Périmètre SELECT IA non conforme à la politique positive.")
    cursor.execute("""SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_largeobject_metadata m
        WHERE m.lomowner=(SELECT oid FROM pg_roles WHERE rolname=current_user)
          OR EXISTS (SELECT 1 FROM aclexplode(m.lomacl) a
              WHERE a.grantee IN (0,(SELECT oid FROM pg_roles WHERE rolname=current_user))
                AND a.privilege_type='SELECT')),
        EXISTS (SELECT 1 FROM pg_catalog.pg_default_acl d,
          LATERAL aclexplode(d.defaclacl) a
          WHERE a.grantee IN (0,(SELECT oid FROM pg_roles WHERE rolname=current_user))
            AND a.privilege_type IN ('SELECT','EXECUTE'))""")
    if any(cursor.fetchone()):
        raise ValueError("Privilèges IA indirects non conformes.")
