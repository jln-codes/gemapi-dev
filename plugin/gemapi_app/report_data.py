"""Read-only SQL and result preparation for prestation reports.

All UUIDs and years are validated before interpolation. SQL identifiers are
fixed by this module; user text is escaped through ``literal``.
"""

import json
from uuid import UUID

from .watercourse_labels import label_sql, labels_query_sql, section_label_sql
from .record import SITE_CHILDREN, TARGETS, literal


PATRIMONY = {key: (target.label, "prestations", target.fk, key)
             for key, target in TARGETS.items()}
MULTI_FILTERS = ("years", "technicians", "communes", "types", "statuses", "providers",
                 "ah", "site_terrestre", *PATRIMONY)
# The report editor filters by business objects; child relations remain in the
# SQL relation set so a selected parent includes all of its prestations.
REPORT_FILTERS = ("years", "technicians", "communes", "types", "statuses", "providers",
                  "cours_eau", "digues", "ah", "site_terrestre")
MANAGEMENT_FILTERS = MULTI_FILTERS
GROUPS = {
    "cours_eau": "Cours d'eau", "digues": "Digues",
    "year": "Année", "commune": "Commune", "ah": "Aménagement hydraulique", "site_terrestre": "Site terrestre", "type": "Type de prestation",
    "technician": "Technicien", "provider": "Prestataire", "status": "Statut",
}
PATRIMONY_GROUPS = frozenset(("cours_eau", "digues", "ah", "site_terrestre"))
COMPLETION = {"all", "completed", "incomplete"}
COMPLETED_PREDICATE = "s.code = 'TERMINE'"
INCOMPLETE_PREDICATE = "s.code IN ('PLANIFIE','EN_COURS','ANNULE')"
ROW_FIELDS = (
    "id", "intitule", "date_debut", "date_fin", "type", "technician",
    "provider", "statut", "description", "quantite", "unite", "own_length",
    "own_area", "site_child_length", "site_child_area", "has_geometry",
    "objects", "reference_measures",
    "reference_count", "communes", "parents",
    "photo_1", "photo_1_commentaire", "photo_2", "photo_2_commentaire",
    "own_wkt", "reference_geometries", "position", "rive", "own_points",
)


def normalized_filters(filters):
    """Accept only expected values; completion never depends on date_fin."""
    if set(filters) - (set(MANAGEMENT_FILTERS) | {"completion", "groups"}):
        raise ValueError("Critère de sélection inconnu.")
    result = {key: () for key in MANAGEMENT_FILTERS}
    for key in MANAGEMENT_FILTERS:
        values = filters.get(key, ())
        if key == "years":
            years = {int(value) for value in values}
            if any(year < 1000 or year > 9999 for year in years):
                raise ValueError("Année hors limites.")
            result[key] = tuple(sorted(years))
        else:
            result[key] = tuple(sorted({str(UUID(str(value))) for value in values}))
    result["completion"] = filters.get("completion", "all")
    groups = tuple(value for value in filters.get("groups", ()) if value not in (None, "none"))
    if (result["completion"] not in COMPLETION or len(groups) > 3 or
            len(groups) != len(set(groups)) or any(group not in GROUPS for group in groups) or
            sum(group in PATRIMONY_GROUPS for group in groups)>1):
        raise ValueError("État ou regroupement de rapport inconnu.")
    result["groups"] = groups
    return result


def report_context(records, filters):
    """Human-readable scope and grouping, based only on applied restrictions."""
    selected = normalized_filters(filters)
    count = len({str(record["id"]) for record in records})
    completion = selected["completion"]
    scope = {"all": "Prestations", "completed": "Prestations terminées",
             "incomplete": "Prestations non terminées"}[completion]
    restrictions = []
    years = selected["years"]
    if years:
        restrictions.append(("année " if len(years) == 1 else "années ") +
                            ", ".join(map(str, years)))
    for key, singular, plural in (
            ("statuses", "statut sélectionné", "statuts sélectionnés"),
            ("technicians", "technicien sélectionné", "techniciens sélectionnés"),
            ("providers", "prestataire sélectionné", "prestataires sélectionnés"),
            ("types", "type sélectionné", "types sélectionnés"),
            ("communes", "commune sélectionnée", "communes sélectionnées"),
            ("ah", "aménagement hydraulique sélectionné", "aménagements hydrauliques sélectionnés"),
            ("site_terrestre", "site terrestre sélectionné", "sites terrestres sélectionnés"),
            ("ah_stockage", "stockage AH sélectionné", "stockages AH sélectionnés"),
            ("ah_ouvrage", "ouvrage AH sélectionné", "ouvrages AH sélectionnés"),
            ("ah_sections", "section AH sélectionnée", "sections AH sélectionnées"),
            ("cours_eau", "cours d'eau sélectionné", "cours d'eau sélectionnés"),
            ("digues", "digue sélectionnée", "digues sélectionnées"),
            *((key, TARGETS[key].label + " sélectionné(e)", "objets sélectionnés") for key in SITE_CHILDREN)):
        if selected[key]:
            restrictions.append(singular if len(selected[key]) == 1 else plural)
    if completion == "all" and not restrictions:
        scope = "Toutes les prestations"
    elif restrictions:
        scope += " — " + "; ".join(restrictions)
    scope += f" — {count} intervention" + ("" if count == 1 else "s")
    group_names = {"year": "année", "commune": "commune",
                   "cours_eau": "cours d'eau", "digues": "digue",
                   "type": "type de prestation", "technician": "technicien",
                   "provider": "prestataire", "status": "statut",
                   "ah": "aménagement hydraulique",
                   "site_terrestre": "site terrestre"}
    groups = selected["groups"]
    organization = ("Organisation : " + " → ".join(group_names[group] for group in groups)
                    if groups else "Organisation : sans regroupement")
    return "Périmètre : " + scope, organization


def resolve_report_editor(connection, configured_login=None):
    """Resolve the connection login against technicians, without using the record owner."""
    login_rows = connection.executeSql("SELECT current_user::text")
    current_role = str(login_rows[0][0]) if login_rows and login_rows[0] else ""
    login = str(configured_login or current_role).strip()
    if not login:
        return "", None
    rows = connection.executeSql(
        "SELECT concat_ws(' ',prenom,nom) FROM gemapi.techniciens "
        f"WHERE lower(login)=lower({literal(login)}) AND actif=true ORDER BY id LIMIT 1")
    return login, (str(rows[0][0]).strip() if rows and rows[0][0] else None)


def _relations_sql(include_indirect=False):
    """Direct patrimony; optional report-only AH recognition without fan-out."""
    parts = []
    for key, (_, link_table, link_id, object_table) in PATRIMONY.items():
        if key == "ah_sections":
            parts.append(
                "SELECT l.id AS prestation_id,'ah_sections'::text AS famille,o.id AS objet_id,"
                f"{section_label_sql()} AS nom,o.geom_reference AS geom,true AS direct,"
                "'ah'::text AS parent_kind,a.id AS parent_id,a.nom AS parent_nom,"
                "ARRAY[o.id] AS section_ids FROM gemapi.prestations l "
                "JOIN gemapi.ah_sections o ON o.id=l.ah_section_id "
                "JOIN gemapi.cours_eau r ON r.id=o.cours_eau_id JOIN gemapi.ah a ON a.id=o.ah_id")
            # A second, non-direct metadata row represents the recorded section's
            # course. It does not create a second direct attachment or quantity.
            parts.append(
                "SELECT l.id,'ah_sections'::text,o.id,"
                f"{section_label_sql()},NULL::geometry,false,"
                f"'cours_eau'::text,r.id,{label_sql('r')},ARRAY[o.id] "
                "FROM gemapi.prestations l JOIN gemapi.ah_sections o ON o.id=l.ah_section_id "
                "JOIN gemapi.cours_eau r ON r.id=o.cours_eau_id JOIN gemapi.ah a ON a.id=o.ah_id")
            continue
        if key == "digues":
            parts.append(
                "SELECT l.id AS prestation_id, 'digues'::text AS famille, d.id AS objet_id, "
                "d.nom, dr.geom_reference AS geom, true AS direct, "
                "'digues'::text AS parent_kind, d.id AS parent_id, "
                "d.nom AS parent_nom, ARRAY[]::uuid[] AS section_ids FROM gemapi.prestations l "
                "JOIN gemapi.digues d ON d.id=l.digue_id "
                "JOIN gemapi.digues_reperes dr ON dr.digue_id=d.id"
            )
            continue
        if key in ("ah_stockage", "ah_ouvrage"):
            parent = "JOIN gemapi.ah parent ON parent.id=o.ah_id"
            parent_cols = "'ah'::text AS parent_kind, parent.id AS parent_id, parent.nom AS parent_nom"
        elif key in SITE_CHILDREN:
            parent = "LEFT JOIN gemapi.sites_terrestres parent ON parent.id=o.site_terrestre_id"
            parent_cols = "'site_terrestre'::text AS parent_kind, parent.id AS parent_id, parent.nom AS parent_nom"
        elif key == "sites_terrestres":
            parent = ""
            parent_cols = "'site_terrestre'::text AS parent_kind, o.id AS parent_id, o.nom AS parent_nom"
        elif key == "cours_eau":
            parent = ""
            parent_cols = "'cours_eau'::text AS parent_kind, o.id AS parent_id, o.nom AS parent_nom"
        else:
            parent = ""
            parent_cols = "NULL::text AS parent_kind, NULL::uuid AS parent_id, NULL::text AS parent_nom"
        name = label_sql("o") if key == "cours_eau" else f"o.{TARGETS[key].name}"
        if key == "cours_eau":
            parent_cols = f"'cours_eau'::text AS parent_kind, o.id AS parent_id, {name} AS parent_nom"
        parts.append(
            f"SELECT l.id AS prestation_id, {literal(key)} AS famille, o.id AS objet_id, "
            f"{name} AS nom, o.{TARGETS[key].geometry} AS geom, true AS direct, {parent_cols}, ARRAY[]::uuid[] AS section_ids FROM gemapi.{link_table} l "
            f"JOIN gemapi.{object_table} o ON o.id = l.{link_id} {parent}"
        )
    if include_indirect:
        parts.append(
            "SELECT p.id AS prestation_id, 'cours_eau'::text AS famille, "
            f"p.cours_eau_id AS objet_id, {label_sql('c')} AS nom, NULL::geometry AS geom, false AS direct, "
            "'ah'::text AS parent_kind, a.id AS parent_id, a.nom AS parent_nom, "
            "array_agg(DISTINCT s.id ORDER BY s.id) AS section_ids "
            "FROM gemapi.prestations p "
            "JOIN gemapi.ah_sections_carte s ON s.cours_eau_id=p.cours_eau_id "
            "JOIN gemapi.ah a ON a.id=s.ah_id "
            "JOIN gemapi.cours_eau c ON c.id=p.cours_eau_id "
            "WHERE p.cours_eau_id IS NOT NULL AND s.etat_reference='CONFORME' "
            "AND s.cours_eau_qualifie IS TRUE "
            "AND ST_Intersects(p.geom,s.geom_reference) "
            "GROUP BY p.id,p.cours_eau_id,c.id,c.nom,a.id,a.nom"
        )
    return " UNION ALL ".join(parts)


def _object_names_sql():
    return (
        "(SELECT string_agg(r.famille || ' : ' || "
        "CASE WHEN r.famille<>'ah_sections' AND r.parent_kind IN ('ah','site_terrestre') AND r.parent_id<>r.objet_id "
        "THEN COALESCE(r.parent_nom,'Sans nom') || ' — ' || COALESCE(r.nom,'Sans nom') "
        "ELSE COALESCE(r.nom,'Sans nom') END, "
        "'; ' ORDER BY r.famille,r.nom,r.objet_id) FROM ("
        "SELECT DISTINCT ON (famille,objet_id) famille,objet_id,nom,parent_kind,parent_id,parent_nom FROM relations "
        "WHERE prestation_id=p.id AND direct ORDER BY famille,objet_id,nom) r)"
    )


def filter_option_queries(include_statuses=False, include_providers=False):
    queries = {
        "years": "SELECT DISTINCT annee FROM (SELECT EXTRACT(YEAR FROM date_debut)::int AS annee "
                 "FROM gemapi.prestations UNION ALL SELECT generate_series("
                 "EXTRACT(YEAR FROM p.date_debut)::int,EXTRACT(YEAR FROM p.date_fin)::int) "
                 "FROM gemapi.prestations p JOIN gemapi.statuts_prestations s "
                 "ON s.id=p.statut_id WHERE p.date_fin>=p.date_debut AND s.code='TERMINE') a "
                 "ORDER BY annee DESC",
        "technicians": "SELECT id::text,concat_ws(' ',prenom,nom) FROM gemapi.techniciens ORDER BY nom,prenom",
        "communes": "SELECT id::text,nom FROM gemapi.communes ORDER BY nom",
        "types": "SELECT id::text,libelle FROM gemapi.types_prestations ORDER BY libelle,id",
        "ah": "SELECT id::text,nom FROM gemapi.ah ORDER BY nom,id",
        "site_terrestre": "SELECT id::text,nom FROM gemapi.sites_terrestres ORDER BY nom,id",
    }
    if include_providers:
        queries["providers"] = "SELECT id::text,nom FROM gemapi.prestataires ORDER BY nom,id"
    if include_statuses:
        queries["statuses"] = (
            "SELECT id::text,libelle FROM gemapi.statuts_prestations "
            "ORDER BY CASE code WHEN 'PLANIFIE' THEN 1 WHEN 'EN_COURS' THEN 2 "
            "WHEN 'TERMINE' THEN 3 WHEN 'ANNULE' THEN 4 END"
        )
    for key, (_, _, _, table) in PATRIMONY.items():
        if key in ("ah_stockage", "ah_ouvrage"):
            queries[key] = (f"SELECT o.id::text,a.nom || ' — ' || COALESCE(o.nom,'Sans nom') "
                            f"FROM gemapi.{table} o JOIN gemapi.ah a ON a.id=o.ah_id ORDER BY 2,1")
        elif key == "ah_sections":
            queries[key] = (f"SELECT o.id::text,{section_label_sql()} FROM gemapi.ah_sections o "
                            "JOIN gemapi.cours_eau r ON r.id=o.cours_eau_id "
                            "JOIN gemapi.ah a ON a.id=o.ah_id ORDER BY 2,1")
        elif key == "cours_eau":
            queries[key] = labels_query_sql()
        elif key in SITE_CHILDREN:
            queries[key] = (f"SELECT o.id::text,s.nom || ' — ' || o.{TARGETS[key].name} FROM gemapi.{table} o "
                            "JOIN gemapi.sites_terrestres s ON s.id=o.site_terrestre_id ORDER BY 2,1")
        else:
            queries[key] = (f"SELECT id::text,COALESCE({TARGETS[key].name},'Sans nom') "
                            f"FROM gemapi.{table} ORDER BY 2,1")
    return queries


def _selection_conditions(filters, management=False):
    conditions = []
    completed, incomplete = COMPLETED_PREDICATE, INCOMPLETE_PREDICATE
    if filters["completion"] == "completed":
        conditions.append(completed)
    elif filters["completion"] == "incomplete":
        conditions.append(incomplete)
    if filters["years"]:
        years = ", ".join(map(str, filters["years"]))
        overlap = " OR ".join(
            f"(p.date_debut<=make_date({year},12,31) AND "
            f"p.date_fin>=make_date({year},1,1))" for year in filters["years"])
        conditions.append(f"(({completed} AND ({overlap})) OR "
                          f"({incomplete} AND EXTRACT(YEAR FROM p.date_debut)::int IN ({years})))")
    for key, column in (("technicians", "p.technicien_id"), ("statuses", "p.statut_id"),
                        ("providers", "p.prestataire_id"), ("types", "p.type_prestation_id")):
        if filters[key]:
            ids = ", ".join(literal(value, "::uuid") for value in filters[key])
            conditions.append(f"{column} IN ({ids})")
    if filters["communes"]:
        ids = ", ".join(literal(value, "::uuid") for value in filters["communes"])
        conditions.append(
            "EXISTS (SELECT 1 FROM gemapi.communes selected_c WHERE selected_c.id IN ("
            + ids + ") AND ST_Intersects(selected_c.geom," + _effective_geometry_sql() + "))")
    for key, parent_kind in (("ah", "ah"), ("site_terrestre", "site_terrestre")):
        if filters[key]:
            ids = ", ".join(literal(value, "::uuid") for value in filters[key])
            conditions.append(
                "EXISTS (SELECT 1 FROM relations chosen_r WHERE chosen_r.prestation_id=p.id "
                f"AND chosen_r.parent_kind={literal(parent_kind)} "
                f"AND chosen_r.parent_id IN ({ids}))")
    # Management presents AH storage and works as one family, with individual
    # children selected. Their choices are alternatives, like multiple objects
    # within any other family; report filters keep their existing semantics.
    ah_children = ("ah_stockage", "ah_ouvrage", "ah_sections")
    selected_ah_children = management and sum(bool(filters[key]) for key in ah_children)>1
    if selected_ah_children:
        alternatives = []
        for key in ah_children:
            if not filters[key]:
                continue
            ids = ", ".join(literal(value, "::uuid") for value in filters[key])
            alternatives.append(f"(chosen_r.famille={literal(key)} AND chosen_r.objet_id IN ({ids}))")
        conditions.append("EXISTS (SELECT 1 FROM relations chosen_r WHERE "
                          "chosen_r.prestation_id=p.id AND (" + " OR ".join(alternatives) + "))")
    for key in PATRIMONY:
        if selected_ah_children and key in ah_children:
            continue
        if filters[key]:
            ids = ", ".join(literal(value, "::uuid") for value in filters[key])
            if key == "cours_eau":
                predicate = (f"(chosen_r.direct AND chosen_r.famille='cours_eau' AND chosen_r.objet_id IN ({ids})) "
                             f"OR (chosen_r.parent_kind='cours_eau' AND chosen_r.parent_id IN ({ids}))")
            else:
                predicate = f"chosen_r.direct AND chosen_r.famille={literal(key)} AND chosen_r.objet_id IN ({ids})"
            conditions.append(
                "EXISTS (SELECT 1 FROM relations chosen_r WHERE chosen_r.prestation_id=p.id "
                f"AND ({predicate}))")
    return conditions


def build_management_query(filters, search_text=""):
    filters = normalized_filters(filters)
    conditions = _selection_conditions(filters, management=True)
    search_text = str(search_text).strip()
    if len(search_text) > 200:
        raise ValueError("Recherche trop longue (200 caractères maximum).")
    if search_text:
        haystack = ("concat_ws(' ',p.intitule,tp.libelle,pr.nom,s.libelle,"
                    "t.prenom,t.nom,to_char(p.date_debut,'DD/MM/YYYY'))")
        conditions.append(f"strpos(lower({haystack}),lower({literal(search_text)}))>0")
    relations = ("WITH relations AS (" + _relations_sql() + ") "
                 if any(filters[key] for key in (*PATRIMONY, "ah", "site_terrestre")) else "")
    return (
        relations + "SELECT id,titre,version FROM (SELECT DISTINCT ON (p.id) p.id::text AS id, "
        "COALESCE(NULLIF(btrim(p.intitule),''),tp.libelle || ' — ' || "
        "to_char(p.date_debut,'DD/MM/YYYY')) AS titre,p.date_debut,p.xmin::text AS version "
        "FROM gemapi.prestations p "
        "JOIN gemapi.statuts_prestations s ON s.id=p.statut_id "
        "JOIN gemapi.types_prestations tp ON tp.id=p.type_prestation_id "
        "JOIN gemapi.prestataires pr ON pr.id=p.prestataire_id "
        "LEFT JOIN gemapi.techniciens t ON t.id=p.technicien_id "
        + "WHERE " + (" AND ".join(conditions) if conditions else "true") +
        " ORDER BY p.id) matched ORDER BY date_debut DESC,id")


def _effective_geometry_sql():
    return "(SELECT carto.geom FROM gemapi.prestations_carte carto WHERE carto.id=p.id)"


def build_report_query(filters, include_geometry=False, count_only=False):
    filters = normalized_filters(filters)
    where = " AND ".join(_selection_conditions(filters)) or "true"
    relations = "WITH relations AS (" + _relations_sql(include_indirect=True) + ") "
    if count_only:
        return (relations + "SELECT count(DISTINCT p.id) FROM gemapi.prestations p "
                "JOIN gemapi.statuts_prestations s ON s.id=p.statut_id WHERE " + where)
    # Keep every recognized parent in metadata, but scope AH groups to selected
    # parents. Selecting A must not produce an unrelated B group for shared work.
    ah_scope = ("x.parent_kind<>'ah' OR x.parent_id IN (" +
                ", ".join(literal(value, "::uuid") for value in filters["ah"]) + ")"
                if filters["ah"] else "true")
    child_target = "num_nonnulls(p.mobilier_id,p.cheminement_id,p.vegetation_id)>0"
    own_wkt = "ST_AsText(p.geom)" if include_geometry else "NULL::text"
    reference_wkt = (
        "(SELECT COALESCE(json_agg(json_build_object('famille',r.famille,"
        "'objet_id',r.objet_id::text,'nom',r.nom,'direct',r.direct,"
        "'wkt',ST_AsText(r.geom)) ORDER BY r.famille,r.objet_id)::text,'[]') "
        "FROM (SELECT DISTINCT ON (famille,objet_id) famille,objet_id,nom,geom,direct "
        "FROM relations WHERE prestation_id=p.id AND direct ORDER BY famille,objet_id) r)"
        if include_geometry else "'[]'::text")
    return (
        relations + "SELECT p.id::text,p.intitule,p.date_debut::text,p.date_fin::text,"
        "COALESCE(tp.libelle,'Non renseigné'),"
        "COALESCE(NULLIF(concat_ws(' ',t.prenom,t.nom),''),'Non renseigné'),"
        "COALESCE(pr.nom,'Non renseigné'),s.libelle,COALESCE(p.description,''),"
        "p.quantite::text,COALESCE(p.unite,''),"
        "CASE WHEN ST_Dimension(p.geom)=1 THEN p.longueur_m END,"
        "CASE WHEN ST_Dimension(p.geom)=2 THEN p.surface_m2 END,"
        f"CASE WHEN {child_target} AND p.geom IS NULL AND p.origine_mesures='Patrimoine de rattachement' THEN p.longueur_m END,"
        f"CASE WHEN {child_target} AND p.geom IS NULL AND p.origine_mesures='Patrimoine de rattachement' THEN p.surface_m2 END,"
        "(p.geom IS NOT NULL),"
        + _object_names_sql() + ","
        "(SELECT string_agg(COALESCE(r.nom,'Objet') || ' : ' || "
        "CASE WHEN ST_Dimension(r.geom)=1 THEN round(ST_Length(r.geom)::numeric,1)::text || ' m' "
        "WHEN ST_Dimension(r.geom)=2 THEN round(ST_Area(r.geom)::numeric,1)::text || ' m²' END,"
        "'; ' ORDER BY r.famille,r.nom,r.objet_id) FROM relations r "
        f"WHERE r.prestation_id=p.id AND r.direct AND NOT ({child_target})), "
        "(SELECT count(*) FROM relations r WHERE r.prestation_id=p.id AND r.direct),"
        "(SELECT string_agg(c.nom,'; ' ORDER BY c.nom) FROM gemapi.communes c "
        "WHERE ST_Intersects(c.geom," + _effective_geometry_sql() + ")),"
        "(SELECT COALESCE(json_agg(json_build_object('kind',x.parent_kind,'id',"
        "x.parent_id::text,'nom',x.parent_nom,'direct',x.direct,"
        f"'section_ids',x.section_ids,'in_scope',({ah_scope})) "
        "ORDER BY x.parent_kind,x.parent_id,x.direct)::text,'[]') FROM "
        "(SELECT DISTINCT parent_kind,parent_id,parent_nom,direct,section_ids FROM relations "
        "WHERE prestation_id=p.id AND parent_id IS NOT NULL) x),"
        "p.photo_1,p.photo_1_commentaire,p.photo_2,p.photo_2_commentaire,"
        f"{own_wkt},{reference_wkt},p.position,p.rive,"
        "CASE WHEN p.geom IS NOT NULL AND ST_Dimension(p.geom)=0 THEN p.nombre_points END "
        "FROM gemapi.prestations p JOIN gemapi.statuts_prestations s ON s.id=p.statut_id "
        "LEFT JOIN gemapi.types_prestations tp ON tp.id=p.type_prestation_id "
        "LEFT JOIN gemapi.techniciens t ON t.id=p.technicien_id "
        "LEFT JOIN gemapi.prestataires pr ON pr.id=p.prestataire_id "
        "WHERE " + where + " ORDER BY p.date_debut,p.intitule,p.id")


def prepare_rows(raw_rows):
    records = {}
    for row in raw_rows:
        if len(row) != len(ROW_FIELDS):
            raise ValueError("Réponse PostgreSQL inattendue pour le rapport.")
        values = (None if value is None or
                  (hasattr(value, "isNull") and value.isNull()) else
                  value.value() if hasattr(value, "value") else value for value in row)
        record = dict(zip(ROW_FIELDS, values))
        key = str(UUID(str(record["id"])))
        if key in records:
            continue
        for field in ("own_length", "own_area", "site_child_length", "site_child_area"):
            record[field] = float(record[field]) if record[field] is not None else None
        if record["own_points"] is not None:
            record["own_points"] = int(record["own_points"])
        record["has_geometry"] = record["has_geometry"] in (True, "t", "true", 1)
        record["reference_count"] = int(record["reference_count"] or 0)
        for field in ("parents", "reference_geometries"):
            record[field] = json.loads(record[field] or "[]")
        records[key] = record
    return list(records.values())


def distinct_records(records):
    """One principal row per UUID before tables, totals and maps.

    SQL already aggregates section proofs per parent; duplicate transport rows
    must not turn multiple memberships into multiple prestations.
    """
    result = {}
    for record in records:
        # Anonymous measurement dictionaries are also used by callers.
        key = str(record["id"]) if record.get("id") is not None else id(record)
        result.setdefault(key, record)
    return list(result.values())


def group_label(criterion, value):
    """Display a business name while leaving the UUID grouping key untouched."""
    if criterion in PATRIMONY_GROUPS and value.endswith("]"):
        return value.rsplit(" [", 1)[0]
    return value


def direct_attachment_label(record):
    value = str(record.get("objects") or "")
    family, separator, name = value.partition(" : ")
    labels = {"cours_eau": "cours d’eau", "digues": "digue",
              "ah_stockage": "stockage AH", "ah_ouvrage": "ouvrage AH", "ah_sections": "section AH",
              "sites_terrestres": "site terrestre", **{key: TARGETS[key].label.lower() for key in (*SITE_CHILDREN, "troncons")}}
    return f"{name} ({labels[family]})" if separator and family in labels else value


def ah_recognition_lines(record, explain=False):
    explanation = (" par intersection avec une section hydrographique référencée" if explain else "")
    return ["Aménagement concerné : " + str(parent.get("nom") or "Sans nom") +
            " — reconnaissance indirecte" + explanation + "."
            for parent in record.get("parents") or []
            if parent["kind"] == "ah" and parent.get("direct") is False
            and parent.get("in_scope", True)]


def own_totals(records):
    records = distinct_records(records)
    return (sum(r.get("own_length") or 0 for r in records),
            sum(r.get("own_area") or 0 for r in records))


def format_measure(value):
    return f"{float(value):,.1f}".replace(",", " ").replace(".", ",")


def site_child_measure(record):
    """Describe measures borrowed from a direct target in a site-child family."""
    if record.get("site_child_length") is not None:
        return ("Longueur du patrimoine (géométrie parente) : " +
                format_measure(record["site_child_length"]) + " m")
    if record.get("site_child_area") is not None:
        return ("Surface du patrimoine (géométrie parente) : " +
                format_measure(record["site_child_area"]) + " m²")
    return None


def measure_lines(record, include_quantity=True):
    lines = []
    if record.get("own_length") is not None:
        lines.append("Longueur cartographiée : " + format_measure(record["own_length"]) + " m")
    if record.get("own_area") is not None:
        lines.append("Surface cartographiée : " + format_measure(record["own_area"]) + " m²")
    if record.get("own_points") is not None:
        lines.append(f"Nombre de points cartographiés : {record['own_points']}")
    if record.get("has_geometry") and not lines:
        lines.append("Géométrie ponctuelle")
    child_measure = site_child_measure(record)
    if child_measure:
        lines.append(child_measure)
    if not record.get("has_geometry") and record.get("reference_measures"):
        lines.append("Réf. patrimoniale : " + str(record["reference_measures"]))
    elif not record.get("has_geometry") and not child_measure:
        zone = next((r for r in record.get("reference_geometries") or []
                     if r.get("famille") in SITE_CHILDREN and r.get("direct", True)), None)
        if zone:
            point = str(zone.get("wkt") or "").upper().startswith(("POINT", "MULTIPOINT"))
            lines.append("Géométrie ponctuelle du patrimoine" if point else "Géométrie du patrimoine")
    if include_quantity and record.get("quantite") is not None:
        lines.append("Quantité déclarée : " + str(record["quantite"]) +
                     (" " + str(record["unite"]) if record.get("unite") else ""))
    return lines or ["Aucune mesure"]


def report_warnings(records, filters, other_statuses=()):
    warnings = []
    if sum(r.get("own_length") is not None for r in records) > 1 or sum(
            r.get("own_area") is not None for r in records) > 1:
        warnings.append("Sommes des mesures cartographiées par prestation ; les recouvrements éventuels ne sont pas déduits.")
    if any(not r.get("has_geometry") and r.get("reference_count", 0) > 1 for r in records):
        warnings.append("AMB-01 : plusieurs objets de référence ; leurs mesures ne sont pas additionnées.")
    if any(r.get("communes") and "; " in r["communes"] for r in records):
        warnings.append("Certaines prestations touchent plusieurs communes : aucune mesure n'est répartie entre elles.")
    if filters.get("years"):
        warnings.append("Le filtre annuel n'effectue aucune ventilation des travaux ou des mesures entre années.")
    groups = normalized_filters(filters)["groups"]
    if "year" in groups:
        warnings.append("Regroupement annuel : année de début seulement ; les dates de fin et mesures ne sont pas ventilées entre années.")
    if "commune" in groups and any(r.get("communes") and "; " in r["communes"] for r in records):
        warnings.append("Les groupes communaux se recouvrent : une prestation peut figurer dans plusieurs groupes. Le total général compte chaque prestation une seule fois ; les sous-totaux ne s’additionnent pas.")
    if "ah" in groups and any(len(group_values(r, "ah")) > 1 for r in records):
        warnings.append("Les groupes AH se recouvrent : une prestation peut concerner plusieurs AH. "
                        "Le total général compte chaque prestation une seule fois ; "
                        "les sous-totaux ne s’additionnent pas et les quantités déclarées ne sont pas réparties.")
    if other_statuses:
        warnings.append("Autres statuts présents en base (nomenclature à valider) : "
                        + ", ".join(other_statuses) + ".")
    return warnings


def group_name(record, group):
    if group == "type":
        return record["type"]
    if group == "technician":
        return record["technician"]
    if group == "commune":
        names = record.get("communes") or ""
        return "Plusieurs communes" if "; " in names else (names or "Commune non déterminée")
    return "Toutes les prestations"


def group_values(record, criterion):
    if criterion == "year":
        return (str(record.get("date_debut") or "")[:4] or "Année non déterminée",)
    if criterion == "commune":
        names = tuple(dict.fromkeys(n.strip() for n in
                      str(record.get("communes") or "").split(";") if n.strip()))
        return names or ("Commune non déterminée",)
    if criterion in PATRIMONY_GROUPS:
        names = tuple(dict.fromkeys(
            f"{p['nom'] or 'Sans nom'} [{p['id']}]" for p in record.get("parents", [])
            if p["kind"] == criterion and p.get("in_scope", True)))
        return names or ("Sans rattachement",)
    field = {"type":"type","technician":"technician","provider":"provider",
             "status":"statut"}[criterion]
    return (str(record.get(field) or "Non renseigné"),)


def grouped_entries(records, groups):
    records = distinct_records(records)
    groups = tuple(groups)
    if not groups:
        return [((), r) for r in records]
    entries = []
    def visit(items, depth, path):
        if depth == len(groups):
            entries.extend((path, r) for r in sorted(
                items, key=lambda r: (r.get("date_debut") or "",r.get("type") or "",str(r["id"]))))
            return
        buckets = {}
        for record in items:
            for value in group_values(record, groups[depth]):
                buckets.setdefault(value,{})[str(record["id"])] = record
        for value in sorted(buckets, key=lambda n:(n.casefold(),n)):
            distinct = list(buckets[value].values())
            length, area = own_totals(distinct)
            visit(distinct, depth+1,
                  path+((groups[depth],value,len(distinct),length,area),))
    visit(list({str(r["id"]):r for r in records}.values()),0,())
    return entries

