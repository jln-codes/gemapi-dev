"""Exact installed schema descriptor used by initialization and native import."""
import hashlib,json

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()).hexdigest()

def schema(c):
    # Complete application catalogue, excluding extension-owned objects.
    return {name:c.execute(query).fetchall() for name,query in {
      'columns':"SELECT table_schema,table_name,column_name,data_type,udt_name,is_nullable,column_default,is_generated,generation_expression FROM information_schema.columns WHERE table_schema IN ('public','referentiel','technique') ORDER BY 1,2,ordinal_position",
      'types':"SELECT n.nspname,c.relname,a.attname,format_type(a.atttypid,a.atttypmod) FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('public','referentiel','technique') AND c.relkind IN ('r','v') AND a.attnum>0 AND NOT a.attisdropped ORDER BY 1,2,a.attnum",
      'constraints':"SELECT conrelid::regclass::text,conname,pg_get_constraintdef(oid) FROM pg_constraint WHERE connamespace IN ('public'::regnamespace,'referentiel'::regnamespace,'technique'::regnamespace) ORDER BY 1,2",
      'indexes':"SELECT schemaname,tablename,indexname,indexdef FROM pg_indexes WHERE schemaname IN ('public','referentiel','technique') ORDER BY 1,2,3",
      'functions':"SELECT n.nspname,p.proname,pg_get_function_identity_arguments(p.oid),pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname IN ('public','technique') AND p.prokind='f' AND NOT EXISTS(SELECT 1 FROM pg_depend d WHERE d.classid='pg_proc'::regclass AND d.objid=p.oid AND d.deptype='e') ORDER BY 1,2,3",
      'triggers':"SELECT t.tgrelid::regclass::text,t.tgname,t.tgenabled,pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid WHERE c.relnamespace IN ('public'::regnamespace,'technique'::regnamespace,'referentiel'::regnamespace) AND NOT t.tgisinternal ORDER BY 1,2",
      'views':"SELECT schemaname,viewname,definition FROM pg_views WHERE schemaname IN ('public','technique','referentiel') ORDER BY 1,2"
    }.items()}

def schema_digest(c):
    contract=schema(c)
    contract['acl']=c.execute("""SELECT n.nspname,c.relname,pg_get_userbyid(c.relowner),c.relacl::text
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('public','technique','referentiel') ORDER BY 1,2""").fetchall()
    contract['schema_acl']=c.execute("SELECT nspname,pg_get_userbyid(nspowner),nspacl::text FROM pg_namespace WHERE nspname IN ('public','technique','referentiel') ORDER BY 1").fetchall()
    contract['column_acl']=c.execute("SELECT n.nspname,c.relname,a.attname,a.attacl::text FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('public','technique','referentiel') AND a.attnum>0 AND NOT a.attisdropped ORDER BY 1,2,3").fetchall()
    contract['function_acl']=c.execute("SELECT n.nspname,p.proname,pg_get_function_identity_arguments(p.oid),pg_get_userbyid(p.proowner),p.proacl::text FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname IN ('public','technique','referentiel') ORDER BY 1,2,3").fetchall()
    return digest(contract)



def structure_digest(c):
    return digest(schema(c))


class SchemaCompatibilityError(ValueError):
    """Installed constraints cannot satisfy the current writers (safe code only)."""

    code = 'target_schema_invalid'


def require_writer_schema(c):
    """Read-only capability check, independent of the installed baseline/ACLs.

    Reuse the bounded canonical DDL reader. Compare declared columns' nullability
    and keys, not extension objects, owners, role names or deployment schemas.
    The vegetation validity CHECK is checked by its full expression. Revision 8
    additionally refuses removed Tronçon extensions, even with a forged baseline.
    Installed structure integrity remains a separate mandatory check.
    """
    from digues_app.master_model.sql_evidence import parse_ddl
    from .schema import SCHEMA_DDL_TEMPLATE, RETIRED_TRONCON_TABLES

    if c.execute("SELECT to_regproc('public._projection_axe_univoque') IS NOT NULL").fetchone() != (True,):
        raise SchemaCompatibilityError('target_schema_invalid')
    expected = parse_ddl(SCHEMA_DDL_TEMPLATE[0])
    columns = {(s + '.' + t, col): nullable for s, t, col, nullable in c.execute(
        """SELECT n.nspname,c.relname,a.attname,NOT a.attnotnull
        FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname IN ('public','referentiel','technique')
          AND c.relkind='r' AND a.attnum>0 AND NOT a.attisdropped""").fetchall()}
    if any(table in RETIRED_TRONCON_TABLES for table, _ in columns):
        raise SchemaCompatibilityError('target_schema_invalid')
    for table, definition in expected.items():
        for name, column in definition['columns'].items():
            if columns.get((table, name)) != column['nullable']:
                raise SchemaCompatibilityError('target_schema_invalid')
    # Compare PostgreSQL's canonical boolean expression, preserving grouping.
    # Never remove parentheses: a different grouping can change validity.
    row = c.execute("""SELECT pg_get_expr(conbin,conrelid),convalidated
        FROM pg_constraint WHERE conrelid=to_regclass('public.vegetation')
          AND conname='ck_vegetation_exploitable' AND contype='c'""").fetchone()
    canonical = next(item['expression'] for item in expected['public.vegetation']['constraints']
                     if item['kind'] == 'check' and 'NOT valid OR' in item['expression'])
    import re
    normalize = lambda value: re.sub(r'\s+', '', value).lower()
    expected_expression = '((NOT valid) OR ((geometry IS NOT NULL) AND (famille_vegetation IS NOT NULL) AND (disposition IS NOT NULL)))'
    if (normalize(canonical) != normalize('NOT valid OR (geometry IS NOT NULL AND famille_vegetation IS NOT NULL AND disposition IS NOT NULL)')
            or row is None or row[1] is not True
            or normalize(row[0]) != normalize(expected_expression)):
        raise SchemaCompatibilityError('target_schema_invalid')
    # PK/FK/UNIQUE requirements of all writer destinations come from the same
    # DDL, including ALTER declarations; no independent constraint registry.
    keys = c.execute("""SELECT n.nspname||'.'||r.relname,k.contype,
        ARRAY(SELECT a.attname FROM unnest(k.conkey) WITH ORDINALITY x(id,pos)
              JOIN pg_attribute a ON a.attrelid=k.conrelid AND a.attnum=x.id ORDER BY pos),
        rn.nspname||'.'||rr.relname,
        ARRAY(SELECT a.attname FROM unnest(k.confkey) WITH ORDINALITY x(id,pos)
              JOIN pg_attribute a ON a.attrelid=k.confrelid AND a.attnum=x.id ORDER BY pos)
        FROM pg_constraint k JOIN pg_class r ON r.oid=k.conrelid
        JOIN pg_namespace n ON n.oid=r.relnamespace
        LEFT JOIN pg_class rr ON rr.oid=k.confrelid LEFT JOIN pg_namespace rn ON rn.oid=rr.relnamespace
        WHERE n.nspname IN ('public','referentiel','technique') AND k.convalidated
          AND k.contype IN ('p','u','f')""").fetchall()
    observed = {(t, kind, tuple(cols), ref, tuple(refcols)) for t, kind, cols, ref, refcols in keys}
    kinds = {'primary_key': 'p', 'unique': 'u', 'foreign_key': 'f'}
    for table, definition in expected.items():
        for item in definition['constraints']:
            if item['kind'] in kinds:
                key = (table, kinds[item['kind']], tuple(item['columns']),
                       item.get('references'), tuple(item.get('reference_columns', ())))
                if key not in observed:
                    raise SchemaCompatibilityError('target_schema_invalid')
    if c.execute('SELECT schema_sha256 FROM technique.metadonnees_base').fetchone() != (structure_digest(c),):
        raise SchemaCompatibilityError('target_schema_invalid')
