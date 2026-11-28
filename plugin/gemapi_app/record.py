"""Validate a prestation and build one atomic PostgreSQL statement.

The statement contains every write in a single data-modifying WITH query.
No SQL identifier comes from user input; values are quoted or validated.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import UUID


@dataclass(frozen=True)
class Target:
    label: str
    fk: str
    name: str = "nom"
    geometry: str = "geom"


TARGETS = {
    "ah_sections": Target("Section AH", "ah_section_id"),
    "ah_stockage": Target("Stockage AH", "ah_stockage_id"),
    "ah_ouvrage": Target("Ouvrage AH", "ah_ouvrage_id"),
    "cours_eau": Target("Cours d'eau", "cours_eau_id"),
    "digues": Target("Digue", "digue_id"),
    "sites_terrestres": Target("Site terrestre", "site_terrestre_id"),
    "troncons": Target("Tronçon", "troncon_id", "designation", "geometry"),
    "mobilier": Target("Mobilier", "mobilier_id", "designation", "geometry"),
    "cheminements": Target("Cheminement", "cheminement_id", "designation", "geometry"),
    "vegetation": Target("Végétation", "vegetation_id", "designation", "geometry"),
}
SITE_CHILDREN = ("mobilier", "cheminements", "vegetation")
LINKS = {table: ("prestations", target.fk) for table, target in TARGETS.items()}
CATEGORIES = tuple((target.label, table) for table, target in TARGETS.items())
POSITIONS = (
    "Indéfini", "Berge", "Talus côté rivière / stockage",
    "Crête", "Talus côté terre",
)
RIVES = ("Indéfinie", "Gauche", "Droite", "Les deux")
FIELDS = (
    "intitule", "type_prestation_id", "prestataire_id", "statut_id",
    "technicien_id",
    "date_debut", "date_fin", "quantite", "unite", "description", "position", "rive",
    "photo_1", "photo_1_commentaire", "photo_2", "photo_2_commentaire",
)


def validate(values, associations, geometry_wkt=None):
    values = {key: (values.get(key) or "").strip() for key in FIELDS}
    values["position"] = values["position"] or "Indéfini"
    values["rive"] = values["rive"] or "Indéfinie"
    if values["position"] not in POSITIONS:
        raise ValueError("Position inconnue.")
    if values["rive"] not in RIVES:
        raise ValueError("Rive inconnue.")
    if not values["type_prestation_id"]:
        raise ValueError("Le type de prestation est obligatoire.")
    if not values["prestataire_id"]:
        raise ValueError("Le prestataire est obligatoire.")
    if not values["statut_id"]:
        raise ValueError("Le statut est obligatoire.")
    if not values["technicien_id"]:
        raise ValueError("Le technicien responsable est obligatoire.")
    if not values["date_debut"] or not values["date_fin"]:
        raise ValueError("Les dates de début et de fin sont obligatoires.")
    for key in ("prestataire_id", "type_prestation_id", "statut_id", "technicien_id"):
        if values[key]:
            values[key] = str(UUID(values[key]))
    for key in ("date_debut", "date_fin"):
        if values[key]:
            values[key] = date.fromisoformat(values[key]).isoformat()
    if values["date_debut"] and values["date_fin"] and values["date_fin"] < values["date_debut"]:
        raise ValueError("La date de fin doit suivre la date de début.")
    if values["quantite"]:
        try:
            quantity = Decimal(values["quantite"].replace(",", "."))
        except InvalidOperation as error:
            raise ValueError("La quantité doit être un nombre.") from error
        if not quantity.is_finite():
            raise ValueError("La quantité doit être un nombre fini.")
        values["quantite"] = str(quantity)
    normalized = set()
    for table, object_id in associations:
        if table not in LINKS:
            raise ValueError("Catégorie patrimoniale inconnue.")
        normalized.add((table, str(UUID(str(object_id)))))
    families = {table for table, _ in normalized}
    if not families:
        raise ValueError("Sélectionnez au moins un rattachement patrimonial.")
    if len(families) > 1:
        raise ValueError("Une seule catégorie patrimoniale est autorisée par prestation.")
    family = next(iter(families))
    if family in ("cours_eau", "digues", "ah_sections", "troncons", "sites_terrestres") and not str(geometry_wkt or "").strip():
        raise ValueError("La géométrie propre est obligatoire pour un cours d'eau, une digue, une section AH, un tronçon ou un site terrestre.")
    if len(normalized) > 1:
        raise ValueError("Une prestation doit avoir un seul rattachement patrimonial.")
    if values["position"] != "Indéfini" and not families.intersection(("cours_eau", "digues", "ah_sections", "ah_ouvrage")):
        raise ValueError("Cette position nécessite un cours d'eau, une digue, une section AH ou un ouvrage hydraulique.")
    if values["rive"] != "Indéfinie" and not families.intersection(("cours_eau", "digues", "ah_sections", "ah_ouvrage")):
        raise ValueError("Cette rive nécessite un cours d'eau, une digue, une section AH ou un ouvrage hydraulique.")
    return values, sorted(normalized)


def literal(value, cast=""):
    if not value:
        return "NULL"
    if "\x00" in value:
        raise ValueError("Un champ contient un caractère non valide.")
    return "E'" + value.replace("\\", "\\\\").replace("'", "''") + "'" + cast


def build_insert(values, associations, geometry_wkt=None):
    values, associations = validate(values, associations, geometry_wkt)
    casts = {"type_prestation_id": "::uuid", "prestataire_id": "::uuid",
             "statut_id": "::uuid", "technicien_id": "::uuid",
             "date_debut": "::date", "date_fin": "::date", "quantite": "::numeric"}
    direct_columns = [key for table, key in LINKS.values() if table == "prestations"]
    direct_values = {LINKS[family][1]: uid for family, uid in associations}
    columns = ", ".join((*FIELDS, "geom", *direct_columns))
    # The target geometry column invokes PostGIS's input parser for EWKT.
    # This avoids relying on a PostGIS function in the connection search_path.
    geom_literal = literal("SRID=3950;" + geometry_wkt) if geometry_wkt else "NULL"
    parameters = ", ".join([*(literal(values[key], casts.get(key, "")) for key in FIELDS), geom_literal,
                            *(literal(direct_values.get(key), "::uuid") for key in direct_columns)])
    return ("WITH new_prestation AS (INSERT INTO gemapi.prestations "
            f"({columns}) VALUES ({parameters}) RETURNING id) "
            "SELECT id::text, 0 FROM new_prestation")


def build_update(prestation_id, version, previous_values, values,
                 previous_associations, associations, previous_geometry_wkt,
                 geometry_wkt):
    """Build one guarded, atomic edit without writing unchanged fields."""
    uid = str(UUID(str(prestation_id)))
    if not str(version).isdigit():
        raise ValueError("Version de la prestation absente ou invalide.")
    old_values, old_links = validate(
        previous_values, previous_associations, previous_geometry_wkt)
    new_values, new_links = validate(values, associations, geometry_wkt)
    old_links, new_links = set(old_links), set(new_links)
    casts = {"type_prestation_id": "::uuid", "prestataire_id": "::uuid",
             "statut_id": "::uuid", "technicien_id": "::uuid", "date_debut": "::date",
             "date_fin": "::date", "quantite": "::numeric"}
    assignments = [f"{key} = {literal(new_values[key], casts.get(key, ''))}"
                   for key in FIELDS if old_values[key] != new_values[key]]
    if previous_geometry_wkt != geometry_wkt:
        assignments.append("geom = " + (literal("SRID=3950;" + geometry_wkt)
                                        if geometry_wkt else "NULL"))
    old_direct = {LINKS[family][1]: uid for family, uid in old_links}
    new_direct = {LINKS[family][1]: uid for family, uid in new_links}
    for _, column in LINKS.values():
        if old_direct.get(column) != new_direct.get(column):
            assignments.append(f"{column} = {literal(new_direct.get(column), '::uuid')}")
    if not assignments:
        return None

    header = [
        "DECLARE current_version text; BEGIN",
        f"SELECT xmin::text INTO current_version FROM gemapi.prestations "
        f"WHERE id = '{uid}'::uuid FOR UPDATE;",
        "IF NOT FOUND THEN RAISE EXCEPTION 'SIG_PRESTATIONS_NOT_FOUND'; END IF;",
        f"IF current_version <> '{version}' THEN "
        "RAISE EXCEPTION 'SIG_PRESTATIONS_CONFLICT'; END IF;",
    ]
    for _, foreign_key in LINKS.values():
        expected = literal(old_direct.get(foreign_key), "::uuid")
        header.append(f"IF (SELECT {foreign_key} FROM gemapi.prestations WHERE id='{uid}'::uuid) "
                      f"IS DISTINCT FROM {expected} THEN RAISE EXCEPTION 'SIG_PRESTATIONS_CONFLICT'; END IF;")
    if assignments:
        header.append(f"UPDATE gemapi.prestations SET {', '.join(assignments)} "
                      f"WHERE id = '{uid}'::uuid;")
    header.append("END")
    body = "\n".join(header)
    delimiter = "$sig_prestations$"
    suffix = 1
    while delimiter in body:
        delimiter = f"$sig_prestations_{suffix}$"
        suffix += 1
    return f"DO {delimiter}\n{body}\n{delimiter};"


def build_delete(prestation_id, version):
    """Delete exactly one unchanged prestation under a PostgreSQL row lock."""
    uid = str(UUID(str(prestation_id)))
    if not str(version).isdigit():
        raise ValueError("Version de la prestation absente ou invalide.")
    return (
        "DO $sig_prestations_delete$\n"
        "DECLARE current_version text; deleted_count integer; BEGIN\n"
        "IF NOT has_table_privilege('gemapi.prestations', 'DELETE') THEN "
        "RAISE EXCEPTION 'SIG_PRESTATIONS_FORBIDDEN'; END IF;\n"
        "SELECT xmin::text INTO current_version FROM gemapi.prestations "
        f"WHERE id = '{uid}'::uuid FOR UPDATE;\n"
        "IF NOT FOUND THEN RAISE EXCEPTION 'SIG_PRESTATIONS_NOT_FOUND'; END IF;\n"
        f"IF current_version <> '{version}' THEN "
        "RAISE EXCEPTION 'SIG_PRESTATIONS_CONFLICT'; END IF;\n"
        "DELETE FROM gemapi.prestations "
        f"WHERE id = '{uid}'::uuid AND xmin::text = '{version}';\n"
        "GET DIAGNOSTICS deleted_count = ROW_COUNT;\n"
        "IF deleted_count <> 1 THEN RAISE EXCEPTION 'SIG_PRESTATIONS_CONFLICT'; END IF;\n"
        "END\n$sig_prestations_delete$;"
    )
