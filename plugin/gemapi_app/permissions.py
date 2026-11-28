"""Effective PostgreSQL permissions for the current project connection."""


def prestation_permissions(connection):
    rows = connection.executeSql(
        "SELECT has_table_privilege('gemapi.prestations', 'SELECT'), "
        "has_table_privilege('gemapi.prestations', 'INSERT'), "
        "has_table_privilege('gemapi.prestations', 'UPDATE'), "
        "has_table_privilege('gemapi.prestations', 'DELETE')")
    if len(rows) != 1 or len(rows[0]) != 4:
        raise ValueError("Droits PostgreSQL indisponibles.")
    def granted(value):
        return value is True or str(value).lower() in ("t", "true", "1")
    return dict(zip(("select", "insert", "update", "delete"),
                    (granted(value) for value in rows[0])))
