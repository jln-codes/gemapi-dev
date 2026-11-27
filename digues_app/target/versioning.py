"""Additive technical versions; independent of historical CouchDB dates/revisions."""

VERSIONED_TABLES = (
    "systemes", "digues", "troncons", "desordres", "mobilier",
    "equipements_mesure",
    "reseaux_techniques",
    "ouvrages_particuliers",
    "cheminements",
    "vegetation",
    "amenagements_hydrauliques",
    "prestations",
    "observations", "photos",
)

# Every physical UPDATE advances the row version, including SQL outside FastAPI.
# Complex aggregate writes may advance it more than once in one transaction.
VERSIONING_DDL = (
    *(f"ALTER TABLE public.{table} ADD COLUMN IF NOT EXISTS version BIGINT "
      "NOT NULL DEFAULT 1 CHECK (version > 0 AND version <= 9007199254740991)"
      for table in VERSIONED_TABLES),
    """
    CREATE OR REPLACE FUNCTION public.avancer_version_ressource()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        NEW.version := OLD.version + 1;
        RETURN NEW;
    END;
    $$
    """,
    *(f"CREATE OR REPLACE TRIGGER maj_version_ressource "
      f"BEFORE UPDATE ON public.{table} FOR EACH ROW "
      "EXECUTE FUNCTION public.avancer_version_ressource()"
      for table in VERSIONED_TABLES),
    """
    CREATE OR REPLACE FUNCTION public.avancer_version_desordre_agrege()
    RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE old_id uuid; new_id uuid; target_id uuid;
    BEGIN
        IF TG_OP <> 'INSERT' THEN old_id := OLD.desordre_id; END IF;
        IF TG_OP <> 'DELETE' THEN new_id := NEW.desordre_id; END IF;
        FOR target_id IN
            SELECT DISTINCT id FROM unnest(ARRAY[old_id, new_id]) AS ids(id)
            WHERE id IS NOT NULL ORDER BY id
        LOOP
            -- Touch only version: never retrigger geometry/repérage calculations.
            UPDATE public.desordres SET version = version WHERE id = target_id;
        END LOOP;
        RETURN NULL;
    END;
    $$
    """,
    *(f"CREATE OR REPLACE TRIGGER maj_version_desordre_agrege "
      f"AFTER INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW "
      "EXECUTE FUNCTION public.avancer_version_desordre_agrege()"
      for table in ("public.desordres__troncons", "public.localisations_desordres")),
)

