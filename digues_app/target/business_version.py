"""Versioned business contract. Bump for DDL/reference/ACL policy changes.

Revision 9 selects an optional explicit referencing axis for every subtype.
The canonical Tronçon table and shared supports installed by revision 8 stay
unchanged. Incompatible prototype
schemas are recreated rather than upgraded in place.
The provisioning evidence remains private and separate from this read-only marker.
"""

from psycopg import sql

BUSINESS_SCHEMA_REVISION = 9
BUSINESS_SCHEMA_VERSIONS = {9: "digues-app-9"}
BUSINESS_SCHEMA_VERSION = BUSINESS_SCHEMA_VERSIONS[BUSINESS_SCHEMA_REVISION]
BUSINESS_ACCESS_LOCK = 728634985422


def read_business_metadata(connection):
    if connection.execute("SELECT to_regclass('digues_metadata.schema_version')").fetchone()[0] is None:
        return None
    rows = connection.execute("SELECT business_database_id,revision,version,upgraded_at "
                              "FROM digues_metadata.schema_version").fetchall()
    if len(rows) != 1:
        raise ValueError("invalid_business_metadata")
    return rows[0]


def write_business_metadata(connection, identifier, names):
    """Owner-only write; runtime/SQL diagnostic may read, AI gets no grant."""
    connection.execute("CREATE SCHEMA IF NOT EXISTS digues_metadata")
    connection.execute("REVOKE ALL ON SCHEMA digues_metadata FROM PUBLIC")
    connection.execute("""CREATE TABLE IF NOT EXISTS digues_metadata.schema_version (
        singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
        business_database_id uuid NOT NULL UNIQUE,
        revision integer NOT NULL CHECK(revision>0), version text NOT NULL,
        upgraded_at timestamptz NOT NULL DEFAULT clock_timestamp())""")
    existing = read_business_metadata(connection) if connection.execute(
        "SELECT EXISTS(SELECT 1 FROM digues_metadata.schema_version)").fetchone()[0] else None
    if existing and existing[0] != identifier:
        raise ValueError("business_identity_mismatch")
    connection.execute("""INSERT INTO digues_metadata.schema_version
        (business_database_id,revision,version) VALUES (%s,%s,%s)
        ON CONFLICT(singleton) DO UPDATE SET revision=excluded.revision,
        version=excluded.version,upgraded_at=clock_timestamp()
        WHERE schema_version.revision IS DISTINCT FROM excluded.revision
           OR schema_version.version IS DISTINCT FROM excluded.version""",
        (identifier, BUSINESS_SCHEMA_REVISION, BUSINESS_SCHEMA_VERSION))
    for kind in ("runtime", "sql_readonly"):
        role = sql.Identifier(names[kind])
        connection.execute(sql.SQL("GRANT USAGE ON SCHEMA digues_metadata TO {}").format(role))
        connection.execute(sql.SQL("GRANT SELECT ON digues_metadata.schema_version TO {}").format(role))
