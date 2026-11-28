"""Business watercourse names and Course (Patrimony) labels."""

def location_label(course, names=()):
    """Presentation only: retain the course label and deduplicate business names."""
    names = tuple(dict.fromkeys(name for value in names
                               if (name := text_name(value)) and name.strip()))
    return f"{course} ({', '.join(names)})" if names else course


def text_name(value):
    if value is None or (hasattr(value, 'isNull') and value.isNull()):
        return None
    return str(value)


def label_sql(alias):
    """The schema guarantees a nonblank business name; aliases are trusted."""
    return f"{alias}.nom"


def labels_query_sql(*, qualifications=False):
    qualification = (",gemapi.cours_eau_reference_admissible(c.geom,c.direction,c.valide)"
                     if qualifications else "")
    return f"SELECT c.id::text,c.nom{qualification} FROM gemapi.cours_eau c ORDER BY 2,1"


def section_label_sql(section="o", course="r", parent="a"):
    """Course (AH), with longitudinal bounds for distinct homonymous sections.

    Aliases are trusted code constants. Count all stored sections, including
    stale ones, so reopening an old association cannot merge its identity.
    """
    return (
        f"({label_sql(course)}) || ' (' || {parent}.nom || ')' || "
        "CASE WHEN (SELECT count(*) FROM gemapi.ah_sections siblings "
        f"WHERE siblings.ah_id={section}.ah_id AND siblings.cours_eau_id={section}.cours_eau_id)>1 "
        f"THEN ' — section [' || {section}.debut_fraction::text || "
        f"'–' || {section}.fin_fraction::text || ']' ELSE '' END"
    )


# Native field expression, with no identifier-based replacement.
QGIS_LABEL = '"nom"'
