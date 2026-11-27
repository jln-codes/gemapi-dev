"""Technical journal, deliberately excluded from the CouchDB import tables."""

RESOURCE_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS technique.evenements_ressources (
    sequence_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id UUID NOT NULL UNIQUE,
    resource TEXT NOT NULL CHECK (resource IN
        ('systemes', 'digues', 'troncons', 'desordres', 'mobilier',
         'equipements-mesure', 'reseaux-techniques', 'ouvrages-particuliers',
         'cheminements', 'vegetation', 'amenagements-hydrauliques', 'prestations',
         'observations', 'photos')),
    payload JSONB NOT NULL,
    CHECK (jsonb_typeof(payload) = 'object' AND payload ?& ARRAY[
        'schemaVersion', 'eventId', 'resource', 'action', 'ids',
        'originClientId', 'originInstanceId', 'occurredAt']),
    CHECK (payload->>'eventId' = event_id::text),
    CHECK (payload->>'resource' = resource),
    CHECK (payload->>'schemaVersion' = '1'),
    CHECK (payload->>'action' IN ('create', 'update', 'delete'))
)
"""

