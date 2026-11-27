"""Adaptation PostgreSQL → modèle de fiche de désordre.

C'est la seule couche autorisée à interroger PostgreSQL pour une fiche. Elle
construit un :class:`~digues_app.reports.disorder_sheet.DisorderSheetData` que le
renderer consomme sans jamais toucher à la base.

La fiche historique lisait un document CouchDB unique dans lequel les
observations et les photos étaient imbriquées. Le modèle PostgreSQL les
normalise (``observations``, ``photos``, ``link_desordres_troncons``), ce qui
impose quelques adaptations explicitées dans les requêtes ci-dessous :

* le tronçon n'est plus référencé par un ``linearId`` unique mais par une
  relation N-N ; la fiche conserve le libellé historique lorsque le désordre
  n'est lié qu'à un tronçon ;
* les photos directes du désordre ont été migrées dans une observation
  synthétique « Photos migrées » (``migration/media.py:481``) ; la priorité
  historique ``photos du désordre, sinon photos de la première observation``
  est reproduite ;
* la latitude/longitude historique était un champ calculé par SIRS
  (``latitudeMin``/``longitudeMin``, classés ``TECHNIQUE_IGNORE`` par la
  migration) : elle est recalculée ici par PostGIS ;
* ``Observation.author``, la source historique de la colonne « Observateur »,
  est également ``TECHNIQUE_IGNORE`` et la table des Contacts n'est pas migrée :
  la colonne reste vide et s'affiche « - » comme une valeur absente ;
* ``Observation.commentaire``, la source historique de la colonne
  « Évolution / État », n'est pas reprise par la migration : la colonne reste
  vide et s'affiche « - » ;
* les libellés de type viennent de ``ref_types_desordre.libelle``, source
  canonique PostgreSQL. La copie figée ``TYPE_DESORDRE_MAP`` de ``sirs-suite``
  n'est pas reproduite : ses 16 libellés plus courts sont des données
  historiques obsolètes, pas une différence de présentation. Aucune table de
  compatibilité n'est introduite et le référentiel n'est jamais modifié pour un
  ancien PDF (voir ``tests/test_reports_referential_decision.py``).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from .disorder_sheet import (
    DisorderMapContext,
    DisorderPhoto,
    DisorderSheetData,
    build_disorder_sheet,
)


WEB_MERCATOR_SRID = 3857

#: Libellé de l'observation synthétique qui porte les photos directes d'un
#: objet après migration (``digues_app/migration/media.py:481``).
SYNTHETIC_PHOTO_OBSERVATION_DESIGNATION = "Photos migrées"


class DisorderSheetNotFound(LookupError):
    """Le désordre demandé n'existe pas dans PostgreSQL."""


DISORDER_SQL = f"""
    SELECT
        d.designation,
        d.commentaire,
        d.type_desordre_id,
        d.cote_id,
        d.position_id,
        ST_X(ST_Transform(points.representative_point, 4326)) AS longitude,
        ST_Y(ST_Transform(points.representative_point, 4326)) AS latitude,
        ST_AsText(ST_Force2D(ST_Transform(points.position_point, {WEB_MERCATOR_SRID}))) AS marker_wkt
    FROM public.desordres AS d
    LEFT JOIN LATERAL (
        SELECT
            CASE
                WHEN d.geometry IS NULL THEN NULL
                WHEN GeometryType(d.geometry) = 'POINT' THEN d.geometry
                WHEN GeometryType(d.geometry) = 'LINESTRING' THEN ST_StartPoint(d.geometry)
                ELSE NULL
            END AS position_point,
            CASE
                WHEN d.geometry IS NULL THEN NULL
                WHEN GeometryType(d.geometry) = 'POINT' THEN d.geometry
                WHEN GeometryType(d.geometry) = 'LINESTRING' THEN ST_StartPoint(d.geometry)
                ELSE ST_PointOnSurface(d.geometry)
            END AS representative_point
    ) AS points ON true
    WHERE d.id = %s
"""


SECTIONS_SQL = f"""
    SELECT
        COALESCE(NULLIF(BTRIM(t.libelle), ''), NULLIF(BTRIM(t.designation), ''), t.id::text),
        CASE WHEN ST_SRID(t.geometry)>0 AND EXISTS
            (SELECT 1 FROM spatial_ref_sys crs WHERE crs.srid=ST_SRID(t.geometry))
          THEN ST_AsText(ST_Force2D(ST_Transform(t.geometry, {WEB_MERCATOR_SRID})))
          ELSE NULL END AS geometry
    FROM public.desordres__troncons AS link
    JOIN public.troncons AS t ON t.id = link.troncon_id
    WHERE link.desordre_id = %s
    ORDER BY t.libelle NULLS LAST, t.id
"""


OBSERVATIONS_SQL = """
    SELECT
        o.id::text AS id,
        o.date_observation::text AS date,
        o.observateur_id
    FROM public.observations AS o
    WHERE o.desordre_id = %s
    ORDER BY o.date_observation DESC NULLS LAST, o.id
"""


PHOTOS_SQL = """
    SELECT
        p.id::text AS id,
        p.storage_key::text AS storage_key,
        o.id::text AS observation_id,
        o.designation AS observation_designation
    FROM public.photos AS p
    JOIN public.observations AS o ON o.id = p.observation_id
    WHERE o.desordre_id = %s
    ORDER BY p.date_photo DESC NULLS LAST, p.id
"""


REFERENCES_SQL = """
    SELECT 'type' AS family, id, libelle FROM referentiel.types_desordre
    UNION ALL
    SELECT 'cote', id, libelle FROM referentiel.cotes
    UNION ALL
    SELECT 'position', id, libelle FROM referentiel.positions
"""


#: Résolution d'un nœud patrimonial. Chaque requête renvoie le même tuple de
#: sept valeurs : ``(libellé du périmètre, système id, système libellé,
#: digue id, digue libellé, tronçon id, tronçon libellé)``. Les niveaux non
#: concernés sont ``NULL``.
SCOPE_NODE_SQL = {
    "amenagement_hydraulique": """
        SELECT COALESCE(NULLIF(BTRIM(a.libelle), ''),
                        NULLIF(BTRIM(a.designation), ''), a.id::text),
               NULL::text, NULL::text, NULL::text, NULL::text, NULL::text, NULL::text
        FROM public.amenagements_hydrauliques a WHERE a.id = %s::uuid
    """,
    "systeme": """
        SELECT
            NULLIF(BTRIM(s.libelle), ''),
            s.id::text,
            NULLIF(BTRIM(s.libelle), ''),
            NULL::text, NULL::text, NULL::text, NULL::text
        FROM public.systemes AS s
        WHERE s.id = %s::uuid
    """,
    "digue": """
        SELECT
            COALESCE(
                NULLIF(BTRIM(dg.libelle), ''),
                NULLIF(BTRIM(dg.designation), ''),
                dg.id::text
            ),
            s.id::text,
            NULLIF(BTRIM(s.libelle), ''),
            dg.id::text,
            COALESCE(
                NULLIF(BTRIM(dg.libelle), ''),
                NULLIF(BTRIM(dg.designation), ''),
                dg.id::text
            ),
            NULL::text, NULL::text
        FROM public.digues AS dg
        LEFT JOIN public.systemes AS s ON s.id = dg.systeme_endiguement_id
        WHERE dg.id = %s::uuid
    """,
    "troncon": """
        SELECT
            COALESCE(NULLIF(BTRIM(t.libelle), ''), t.id::text),
            s.id::text,
            NULLIF(BTRIM(s.libelle), ''),
            dg.id::text,
            COALESCE(
                NULLIF(BTRIM(dg.libelle), ''),
                NULLIF(BTRIM(dg.designation), ''),
                dg.id::text
            ),
            t.id::text,
            COALESCE(NULLIF(BTRIM(t.libelle), ''), t.id::text)
        FROM public.troncons AS t
        LEFT JOIN public.digues AS dg ON dg.id = t.digue_id
        LEFT JOIN public.systemes AS s ON s.id = dg.systeme_endiguement_id
        WHERE t.id = %s::uuid
    """,
}

#: Prédicat de périmètre, par type de nœud patrimonial (liste blanche).
SCOPE_PREDICATES = {
    "amenagement_hydraulique": "t.amenagement_hydraulique_id = %s::uuid",
    "systeme": "dg.systeme_endiguement_id = %s::uuid",
    "digue": "t.digue_id = %s::uuid",
    "troncon": "t.id = %s::uuid",
}

#: Une ligne par couple (désordre, tronçon du périmètre). Le choix du tronçon
#: retenu et l'ordonnancement sont faits par le cœur du catalogue, en Python.
SCOPE_ROWS_SQL_TEMPLATE = """
    SELECT
        s.id::text AS systeme_id,
        NULLIF(BTRIM(s.libelle), '') AS systeme_label,
        dg.id::text AS digue_id,
        COALESCE(
            NULLIF(BTRIM(dg.libelle), ''),
            NULLIF(BTRIM(dg.designation), ''),
            dg.id::text
        ) AS digue_label,
        t.id::text AS troncon_id,
        COALESCE(NULLIF(BTRIM(t.libelle), ''), t.id::text) AS troncon_label,
        d.id::text AS disorder_id,
        NULLIF(BTRIM(d.designation), '') AS designation
    FROM public.desordres__troncons AS link
    JOIN public.troncons AS t ON t.id = link.troncon_id
    LEFT JOIN public.digues AS dg ON dg.id = t.digue_id
    LEFT JOIN public.systemes AS s ON s.id = dg.systeme_endiguement_id
    JOIN public.desordres AS d ON d.id = link.desordre_id
    WHERE {predicate}
"""


def resolve_observer_name(author: str | None) -> str:
    """Résout le nom de l'observateur, ou « - » tant qu'il est indisponible.

    La requête lit uniquement ``observations.observateur_id``, FK canonique
    vers Contacts. La mise en forme du nom reste non implémentée dans cette
    fiche ; une identité seule n'est pas affichée comme un nom.
    """

    return "-"


def _linestring_points(wkt: str | None) -> tuple[tuple[float, float], ...] | None:
    """Lit un ``LINESTRING`` WKT, comme l'implémentation historique."""

    if not wkt or not wkt.startswith("LINESTRING"):
        return None
    body = wkt[len("LINESTRING"):].strip().strip("()")
    points: list[tuple[float, float]] = []
    for chunk in body.split(","):
        parts = chunk.strip().split()
        if len(parts) < 2:
            continue
        points.append((float(parts[0]), float(parts[1])))
    return tuple(points) or None


def _point(wkt: str | None) -> tuple[float, float] | None:
    """Lit un ``POINT`` WKT."""

    if not wkt or not wkt.startswith("POINT"):
        return None
    parts = wkt[len("POINT"):].strip().strip("()").split()
    if len(parts) < 2:
        return None
    return (float(parts[0]), float(parts[1]))


def _referential_maps(connection: Any) -> dict[str, dict[str, str]]:
    """Charge les libellés de référentiel, indexés par code comme l'historique."""

    maps: dict[str, dict[str, str]] = {"type": {}, "cote": {}, "position": {}}
    for family, identifier, libelle in connection.execute(REFERENCES_SQL).fetchall():
        code = str(identifier).rsplit(":", 1)[-1]
        maps[family][code] = libelle
    return maps


def load_disorder_sheet(connection: Any, desordre_id: str | UUID) -> DisorderSheetData:
    """Construit le modèle de fiche d'un désordre PostgreSQL.

    ``connection`` est une connexion psycopg (celle de la webapp ou d'un script
    noyau) : le module n'ouvre et ne ferme aucune connexion lui-même.
    """

    row = connection.execute(DISORDER_SQL, (str(desordre_id),)).fetchone()
    if not row:
        raise DisorderSheetNotFound(str(desordre_id))

    (
        designation,
        commentaire,
        type_desordre_id,
        cote_id,
        position_id,
        longitude,
        latitude,
        marker_wkt,
    ) = row

    sections = connection.execute(SECTIONS_SQL, (str(desordre_id),)).fetchall()
    observations_rows = connection.execute(
        OBSERVATIONS_SQL, (str(desordre_id),)
    ).fetchall()
    photos_rows = connection.execute(PHOTOS_SQL, (str(desordre_id),)).fetchall()
    references = _referential_maps(connection)

    section_labels = [item[0] for item in sections if item[0] not in (None, "")]
    section = {"libelle": ", ".join(section_labels) if section_labels else None}

    # Observations : la colonne « Évolution / État » lit ``Observation.commentaire``
    # dans l'implémentation historique. Ce champ n'existe pas dans le modèle
    # PostgreSQL ; il vaut donc ``None`` et s'affiche « - ».
    observations = [
        {
            "date": item[1],
            "author": item[2],
            "commentaire": None,
        }
        for item in observations_rows
    ]

    first_observation_id = observations_rows[0][0] if observations_rows else None
    direct_photos = [
        item
        for item in photos_rows
        if item[3] == SYNTHETIC_PHOTO_OBSERVATION_DESIGNATION
    ]
    if direct_photos:
        selected_photos = direct_photos
    elif first_observation_id is not None:
        selected_photos = [
            item for item in photos_rows if item[2] == first_observation_id
        ]
    else:
        selected_photos = []
    photo = (
        DisorderPhoto(identifier=selected_photos[0][0], storage_key=selected_photos[0][1])
        if selected_photos
        else None
    )

    # La carte historique s'appuie sur un tronçon unique. En présence de
    # plusieurs tronçons liés, le premier de l'ordre d'affichage porte la carte
    # et tous les libellés restent visibles dans le titre de la fiche.
    map_context = None
    if sections:
        map_context = DisorderMapContext(
            section_line=_linestring_points(sections[0][1]),
            marker=_point(marker_wkt),
        )

    return build_disorder_sheet(
        {
            "designation": designation,
            "commentaire": commentaire,
            "type_desordre_id": type_desordre_id,
            "cote_id": cote_id,
            "position_id": position_id,
            "latitude": latitude,
            "longitude": longitude,
        },
        section,
        observer_name=resolve_observer_name,
        disorder_types=references["type"],
        sides=references["cote"],
        positions=references["position"],
        observations=observations,
        photos=[photo] if photo is not None else [],
        map_context=map_context,
    )


def load_scope_node(
    connection: Any, scope_kind: str, identifier: Any
) -> tuple[Any, ...] | None:
    """Résout le nœud patrimonial demandé, ou ``None`` s'il n'existe pas.

    Renvoie ``(libellé du périmètre, système id, système libellé, digue id,
    digue libellé, tronçon id, tronçon libellé)``.
    """

    query = SCOPE_NODE_SQL.get(scope_kind)
    if query is None:
        raise ValueError(f"Type de périmètre patrimonial inconnu : {scope_kind!r}")
    row = connection.execute(query, (str(identifier),)).fetchone()
    return tuple(row) if row else None


def load_scope_disorder_rows(
    connection: Any, scope_kind: str, identifier: Any
) -> list[tuple[Any, ...]]:
    """Couples (désordre, tronçon) du périmètre patrimonial.

    Plusieurs lignes pour un même désordre signalent un désordre multi-tronçons
    dans le périmètre ; le cœur en retient une seule, sans duplication.
    """

    predicate = SCOPE_PREDICATES.get(scope_kind)
    if predicate is None:
        raise ValueError(f"Type de périmètre patrimonial inconnu : {scope_kind!r}")
    query = SCOPE_ROWS_SQL_TEMPLATE.format(predicate=predicate)
    return connection.execute(query, (str(identifier),)).fetchall()
