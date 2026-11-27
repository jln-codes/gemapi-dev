"""Declared historical Indéfini defaults; no data mutation or SQL.

Only absent/JSON null reference classifications are eligible. Exact historical
identities and their target correspondence come from validated C1/C2, never a
nullable column, a string prefix, or the literal text ``undefined``.
"""
from collections.abc import Mapping
from contextvars import ContextVar
from functools import lru_cache, wraps
from inspect import signature

from . import ReferenceContractError, load_contracts

_ACTIVE = ContextVar('historical_undefined_policy', default=None)
CONTRACT = 'MIG-UNDEFINED-01'


class UndefinedPolicy:
    def __init__(self, contracts, documents=None):
        self.contracts = contracts
        self.c2 = contracts.c2
        self.specs = self.c2['historical_undefined']['catalogs']
        self.documents = documents or {}
        self.source_classes = {}
        self.source_objects = {}
        from .canonical import canonical_reference_rows
        physical={table:{row[columns.index('id')] for row in rows}
                  for table,(columns,rows) in canonical_reference_rows(contracts).items()}
        for spec in self.specs.values():
            for table in spec['identity_targets']:
                if not {c['id'] for c in spec['candidates']} <= physical.get(table,set()):
                    raise ReferenceContractError('HISTORICAL_UNDEFINED_TARGET_MISSING: ' + table)
        for cls, rows in self.documents.items():
            for document in rows:
                self.source_objects[id(document)] = cls
                identifier = document.get('_id', document.get('id'))
                if isinstance(identifier, str):
                    self.source_classes.setdefault(identifier, set()).add(cls)
        self.by_id = {}
        for cls in self.specs:
            for row in contracts.standard_values(cls):
                self.by_id.setdefault(row['id'], set()).add(cls)

    def candidate(self, reference_class):
        spec = self.specs.get(reference_class)
        if spec is None:
            return None
        if len(spec['candidates']) != 1:
            raise ReferenceContractError('HISTORICAL_UNDEFINED_AMBIGUOUS: ' + reference_class)
        candidate = spec['candidates'][0]
        local = [d for d in self.documents.get(reference_class, ()) if d.get('_id') == candidate['id']]
        if len(local) > 1:
            raise ReferenceContractError('HISTORICAL_UNDEFINED_AMBIGUOUS: ' + reference_class)
        if local:
            standard = next(r for r in self.contracts.standard_values(reference_class) if r['id'] == candidate['id'])
            declared = local[0].get('@class')
            if declared is not None and (not isinstance(declared, str) or declared.rsplit('.', 1)[-1] != reference_class):
                raise ReferenceContractError('HISTORICAL_UNDEFINED_LOCAL_CONFLICT: ' + reference_class)
            if local[0].get('_deleted') or any(k in local[0] and (local[0][k] != standard.get(k) or type(local[0][k]) is not type(standard.get(k)))
                    for k in ('libelle', 'designation', 'abrege', 'valid')):
                raise ReferenceContractError('HISTORICAL_UNDEFINED_LOCAL_CONFLICT: ' + reference_class)
        return candidate['id']

    def catalog(self, known_ids, *, reference_class=None):
        known_ids=frozenset(known_ids)
        if reference_class is None:
            candidates = set().union(*(self.by_id.get(i, set()) for i in known_ids)) if known_ids else set()
            if len(candidates) != 1:
                return None
            reference_class = candidates.pop()
        spec = self.specs.get(reference_class)
        if spec is None or not spec['identity_targets']:
            return None
        identifier = self.candidate(reference_class)
        if identifier not in known_ids:
            raise ReferenceContractError('HISTORICAL_UNDEFINED_TARGET_MISSING: ' + reference_class)
        return identifier

    def mapped(self, mapping):
        c2 = self.c2
        if mapping not in c2['historical_undefined']['mapped_classifications']:
            return None
        spec = c2['mappings'][mapping]
        identifier = self.candidate(spec['source_class'])
        if identifier is None:
            return None
        entry = next((e for e in spec['entries'] if e['key'] == identifier), None)
        if entry is None or entry['action'] != 'mapped' or not entry['targets']:
            raise ReferenceContractError('HISTORICAL_UNDEFINED_MAPPING_MISSING: ' + mapping)
        return identifier


@lru_cache(maxsize=1)
def standard_policy():
    return UndefinedPolicy(load_contracts())


def catalog_default(known_ids, *, reference_class=None):
    return (_ACTIVE.get() or standard_policy()).catalog(known_ids, reference_class=reference_class)


@lru_cache(maxsize=1)
def reference_fields():
    from digues_app.structure_contract import load_structure_contract
    return {(cls, f['name']): f['type'] for cls, spec in load_structure_contract()['classes'].items()
            for f in spec['effective_fields'] if f['kind'] == 'REFERENCE' and not f.get('many')}


def field_catalog_default(source_class, field, known_ids):
    policy = _ACTIVE.get() or standard_policy()
    ref = reference_fields().get((source_class, field))
    return policy.catalog(known_ids, reference_class=ref) if ref in policy.specs else None


def document_source_class(document):
    policy = _ACTIVE.get() or standard_policy()
    cls = policy.source_objects.get(id(document))
    if cls is None:
        candidates = policy.source_classes.get(document.get('_id', document.get('id')), set())
        if len(candidates) == 1:
            cls = next(iter(candidates))
        else:
            declared = document.get('@class')
            cls = declared.rsplit('.', 1)[-1] if isinstance(declared, str) else None
    return cls


def document_catalog_default(document, field, known_ids):
    policy = _ACTIVE.get() or standard_policy()
    cls = document_source_class(document)
    ref = reference_fields().get((cls, field))
    return policy.catalog(known_ids, reference_class=ref) if ref in policy.specs else None


def sql_catalog_default(cursor, table, *, document=None, field=None):
    from digues_app.target.schema import TABLE_DEFINITIONS
    if table not in TABLE_DEFINITIONS or not table.startswith('referentiel.'):
        return None
    cursor.execute(f'SELECT id FROM {table}')
    known={row[0] for row in cursor.fetchall()}
    return (document_catalog_default(document,field,known) if document is not None
            else catalog_default(known))


def is_field_default(source_class, field, value):
    policy = _ACTIVE.get() or standard_policy()
    ref = reference_fields().get((source_class, field))
    return (value is not None and ref in policy.specs
            and bool(policy.specs[ref]['identity_targets']) and policy.candidate(ref) == value)


def projection_defaults(function):
    @wraps(function)
    def run(*args, **kwargs):
        documents = args[2] if len(args)>2 else kwargs.get('documents', {})
        token = _ACTIVE.set(UndefinedPolicy(load_contracts(), documents))
        try:
            return function(*args, **kwargs)
        finally:
            _ACTIVE.reset(token)
    return run


def mapping_default(contracts, mapping):
    policy = _ACTIVE.get()
    if policy is not None and policy.c2 != contracts._c2:
        policy = UndefinedPolicy(contracts, policy.documents)
    if policy is None:
        policy = getattr(contracts, '_undefined_policy', None)
        if policy is None:
            policy = contracts._undefined_policy = UndefinedPolicy(contracts)
    return policy.mapped(mapping)


def preparation_defaults(function):
    """One immutable policy per preparation; standalone family calls also work."""
    document_argument = next(iter(signature(function).parameters))
    @wraps(function)
    def run(*args, **kwargs):
        documents = args[0] if args else kwargs.get(document_argument, {})
        if _ACTIVE.get() is not None:
            return function(*args, **kwargs)
        references = next((kwargs[k] for k in ('references', 'migration_references', 'reference_snapshot')
                           if kwargs.get(k) is not None), None)
        contracts = getattr(references, '_contracts', None) or load_contracts()
        token = _ACTIVE.set(UndefinedPolicy(contracts, documents if isinstance(documents, Mapping) else {}))
        try:
            return function(*args, **kwargs)
        finally:
            _ACTIVE.reset(token)
    return run


@lru_cache(maxsize=512)
def _table_bindings(source_class, table):
    from digues_app.structure_contract import load_structure_contract
    from digues_app.target.schema import TABLE_DEFINITIONS
    from .undefined_bindings import identity_bindings
    return tuple(binding for binding in identity_bindings(load_structure_contract()['classes'],
                 {source_class:(table.removeprefix('public.'),)}, TABLE_DEFINITIONS) if binding[2] == table)


def apply_catalog_defaults(cursor, values, *, source_class, table, document):
    """Fill only approved absent/null classification projections, never UUIDs.

    The binding requires C3, the reviewed writer alias and a physical catalogue
    FK. Query the actual target so a missing catalogue value fails closed.
    Existing non-null values and original documents are never modified.
    """
    policy = _ACTIVE.get() or standard_policy()
    for cls, field, _, column, reference_class, target in _table_bindings(source_class, table):
        if document.get(field) is not None or values.get(column) is not None:
            continue
        cursor.execute(f'SELECT id FROM {target}')
        default = policy.catalog((row[0] for row in cursor.fetchall()), reference_class=reference_class)
        if default is not None:
            values[column] = default
    return values
