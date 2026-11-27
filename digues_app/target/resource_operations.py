"""Durable successful-operation receipts; not historical CouchDB data."""

RESOURCE_OPERATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS technique.operations_ressources (
    operation_id UUID PRIMARY KEY,
    fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 64),
    resource TEXT NOT NULL CHECK (resource IN
        ('desordres', 'mobilier', 'equipements-mesure', 'reseaux-techniques',
         'ouvrages-particuliers',
         'cheminements', 'vegetation', 'amenagements-hydrauliques', 'prestations',
         'observations', 'photos')),
    resource_id UUID NOT NULL,
    event_id UUID NOT NULL UNIQUE REFERENCES technique.evenements_ressources(event_id),
    response JSONB NOT NULL CHECK (jsonb_typeof(response) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

