"""Catalogue versionné des requêtes proposées par l'outil SQL utilisateur."""

from __future__ import annotations

SQL_PRESETS: list[dict[str, str]] = [
    {
        "id": "demo_rows",
        "label": "Test — 10 lignes",
        "description": "Retourne 10 lignes de démonstration.",
        "sql": """SELECT
    n AS id,
    'Ligne ' || n AS libelle
FROM generate_series(1, 10) AS n;""",
    },
    {
        "id": "demo_datetime",
        "label": "Test — date et heure PostgreSQL",
        "description": "Teste la sérialisation des dates et heures PostgreSQL.",
        "sql": """SELECT
    CURRENT_DATE AS date_du_jour,
    CURRENT_TIMESTAMP AS date_heure;""",
    },
]


def list_sql_presets() -> list[dict[str, str]]:
    """Retourne une copie du catalogue afin qu'un appel ne puisse pas le modifier."""

    return [preset.copy() for preset in SQL_PRESETS]
