"""Derive the MOD-1N projection obligations from the existing master model.

This report is an inventory of executable logical rules and their remaining
obligations, not a second mapping authority. A source value preserved in a
logical witness is not thereby bound to a canonical attribute or SQL column.
Neither a located table nor a synthetic association certifies a real import.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import re

from .common import canonical_bytes, digest
from .occurrence_projection import APPROVED, ATTR, CONTRACT_ID, GEOMETRY, REF, validate_projection_contract
from .geometry_contracts import MORPHOLOGY_TARGETS, preservation_geometry_supported


def _producer(registry, parameters, source):
    """Describe only operators actually implemented by project_logical."""
    berge = parameters['berge']
    if source == parameters['lit_source']:
        return {'operator': 'removed_container', 'target': None, 'rule': CONTRACT_ID}
    if source in {berge['source'], berge['trait_source']}:
        return {'operator': 'project_then_exact_deduplicate', 'target': berge['target'], 'rule': CONTRACT_ID}
    for spec in parameters['structures']:
        if spec['source'] == source:
            return {'operator': 'preserve_identity_resolve_owner', 'target': spec['target'], 'rule': CONTRACT_ID,
                    'owner_path': spec['owner_path'], 'nature': spec['nature']}
    for key, target in [('digue_source', 'da:concept:digue'), ('troncon_source', 'da:concept:troncon-digue')]:
        if source == parameters[key]:
            return {'operator': 'preserve_identity', 'target': target, 'rule': CONTRACT_ID}
    rules = [r for r in registry['rules'] if source in r['sources']]
    decisions = {d['id']: d for d in registry['decisions']}
    if len(rules) == 1:
        rule = rules[0]
        if (rule['status'] in APPROVED and
                all(decisions.get(d, {}).get('status') in APPROVED for d in rule['decision_refs']) and
                rule['identity_policy'] == 'preserve_occurrences_no_merge' and
                rule['field_policy'] == 'preserve_all_pending_target_attributes' and
                preservation_geometry_supported(rule, {t['id']: t for t in registry['targets']}) and
                len(rule['outputs']) == 1 and rule['outputs'][0]['operation'] == 'preserve_occurrence'):
            producer = {'operator': 'preserve_identity', 'target': rule['outputs'][0]['target'], 'rule': rule['id'],
                    'parent_reference': rule['reference_policy']['parent_source'],
                    'parent_target': rule['reference_policy']['parent_target']}
            if (producer['target'] in MORPHOLOGY_TARGETS and
                    rule['reference_policy'].get('valid_parent_cardinality') == {'min': 0, 'max': 1} and
                    decisions.get('decision:MOD-1O-L02-B', {}).get('status') in APPROVED):
                producer['optional_parent'] = True
            return producer
    return {'operator': 'unprojected', 'target': None, 'rule': None}


def _target_chain(target, targets):
    chain, seen = [], set()
    while target in targets and target not in seen:
        seen.add(target)
        chain.append(target)
        target = targets[target].get('parent')
    return chain


def _property_projection(owner, declaration, parameters, producer, associations):
    berge = parameters['berge']
    if producer['operator'] == 'preserve_unprojected_a3':
        return {'operator': 'preserve_unprojected_a3', 'conversion': 'forbidden'}, ['TRONCON_PROJECTION_SUSPENDED_A3']
    if producer['operator'] == 'removed_container':
        return {'operator': 'preserve_source_without_target'}, ['LIT_ATTRIBUTE_DESTINATIONS_UNRESOLVED']
    if producer['operator'] == 'unprojected':
        return {'operator': 'preserve_unprojected_occurrence'}, ['OCCURRENCE_PRODUCER_MISSING']
    if producer.get('optional_parent'):
        if declaration == producer.get('parent_reference'):
            return {'operator': 'resolve_optional_parent', 'target': producer['parent_target'],
                    'cardinality': {'min': 0, 'max': 1}}, ['OWNER_SQL_BINDING_UNQUALIFIED']
        if declaration == GEOMETRY:
            return {'operator': 'preserve_absolute_geometry', 'target_field': 'geometry',
                    'conversion': 'forbidden'}, ['GEOMETRY_SQL_BINDING_UNQUALIFIED']
        if declaration.startswith((ATTR + 'core/Positionable/', REF + 'core/Positionable/')):
            return {'operator': 'preserve_source_value_without_relative_projection',
                    'value_location': 'source_values', 'runtime_applied': False}, ['TARGET_ATTRIBUTE_BINDING_UNQUALIFIED']
    if owner in {berge['source'], berge['trait_source']}:
        is_berge = owner == berge['source']
        if declaration == berge['geometry']['berge_source' if is_berge else 'trait_source']:
            return {'operator': 'preserve_absolute_geometry', 'target_field': 'geometry',
                    'conversion': 'forbidden'}, ['GEOMETRY_SQL_BINDING_UNQUALIFIED']
        if not is_berge and declaration == berge['parent_reference']:
            return {'operator': 'resolve_berge_family_and_representative', 'fan_out': False}, []
        for field in berge['fields']:
            if declaration != field['berge_source' if is_berge else 'trait_source']:
                continue
            if not is_berge and field['trait_origin'] == 'berge':
                return {'operator': 'preserve_source_value_target_inherited_from_berge',
                        'target_field': field['name'], 'producer_declaration': field['berge_source'],
                        'parent_reference': berge['parent_reference']}, ['TARGET_ATTRIBUTE_BINDING_UNQUALIFIED']
            return {'operator': 'copy_exact_value', 'target_field': field['name'],
                    'producer_declaration': declaration}, ['TARGET_ATTRIBUTE_BINDING_UNQUALIFIED']
    spec = next((s for s in parameters['structures'] if s['source'] == owner), None)
    if spec:
        if declaration == GEOMETRY:
            return {'operator': 'preserve_absolute_geometry', 'target_field': 'geometry',
                    'conversion': 'forbidden'}, ['GEOMETRY_SQL_BINDING_UNQUALIFIED']
        if declaration == ATTR + 'core/Positionable/valid':
            return {'operator': 'copy_exact_value', 'target_field': 'valid'}, ['TARGET_ATTRIBUTE_BINDING_UNQUALIFIED']
        if declaration == spec['side_source']:
            return {'operator': 'closed_side_mapping', 'target_field': 'cote',
                    'values': deepcopy(parameters['side_values']), 'unknown': 'preserve_and_diagnose'}, ['TARGET_ATTRIBUTE_BINDING_UNQUALIFIED']
        if declaration == parameters['structure_parent_reference']:
            return {'operator': 'resolve_owner', 'path': spec['owner_path'],
                    'references': [declaration] + ([parameters['troncon_digue_reference']]
                                                  if spec['owner_path'] == 'via_troncon_digue' else []),
                    'absence_invalidates': spec['owner_path'] == 'direct_berge'}, ['OWNER_SQL_BINDING_UNQUALIFIED']
    if declaration == producer.get('parent_reference'):
        return {'operator': 'resolve_required_parent', 'target': producer['parent_target']}, ['OWNER_SQL_BINDING_UNQUALIFIED']
    if declaration in {GEOMETRY, ATTR + 'core/TronconDigue/geometry'}:
        return {'operator': 'preserve_source_geometry_pending_contract', 'value_location': 'source_values',
                'conversion': 'forbidden'}, ['GEOMETRY_TARGET_CONTRACT_UNQUALIFIED', 'GEOMETRY_SQL_BINDING_UNQUALIFIED']
    if declaration in {ATTR + 'core/Positionable/' + name for name in ('prDebut', 'prFin', 'positionDebut', 'positionFin')}:
        return {'operator': 'preserve_source_position_plan_current_relative_value',
                'execution_condition': 'declared_spatial_association_and_calculable_projection',
                'failed_calculation': 'clear_current_projection_preserve_source'}, ['CORE_BORNAGE_RUNTIME_UNQUALIFIED']
    bounds = {REF + 'core/TronconDigue/borneIds',
              *[REF + 'core/Positionable/' + name for name in ('borneDebutId', 'borneFinId', 'systemeRepId')],
              *[ATTR + 'core/Positionable/' + name for name in ('borne_debut_aval', 'borne_debut_distance', 'borne_fin_aval', 'borne_fin_distance')]}
    if declaration in bounds:
        return {'operator': 'preserve_historical_bornage_plan',
                'value_location': 'source_values', 'runtime_applied': False}, ['CORE_BORNAGE_RUNTIME_UNQUALIFIED']
    pairs = [a for a in associations if a['source_class'] == owner and a['reference'] == declaration]
    if pairs:
        return {'operator': 'closed_association_pairs', 'evidence': sorted({a['evidence'] for a in pairs}),
                'destinations': sorted({a['destination_class'] for a in pairs}), 'fan_out': False}, ['RELATION_SQL_BINDING_UNQUALIFIED']
    kind = 'REFERENCE_DESTINATION_UNQUALIFIED' if declaration.startswith('sirs-v2:reference:') else 'TARGET_ATTRIBUTE_BINDING_UNQUALIFIED'
    return {'operator': 'preserve_source_value_pending_target'}, [kind]


def qualify_projections(model, *, source_paths=None, physical_evidence=None):
    """Return a deterministic source-complete report; never change model inputs.

``source_paths`` has the same meaning as in ``qualify_bindings``. Callers that
already verified physical evidence can pass that result to avoid rereading DDL.
Without either, recorded binding claims remain explicitly unverified.
"""
    registry, historical = model['registry'], model['historical']
    contracts = [c for c in registry['contracts'] if c['id'] == CONTRACT_ID]
    diagnostics = []

    def fail(code, element, message, *, profile='occurrence-projection', provenance=None):
        diagnostics.append({'code': code, 'element_id': element, 'message': message,
                            'profiles': [profile], 'provenance': deepcopy(provenance or [])})

    result = {'status': 'LOGICAL_QUALIFICATION_ONLY', 'certified': False,
              'input_digest': model.get('input_digest'), 'model_digest': model.get('model_digest'),
              'source_outcomes': [], 'associations': [], 'bindings': [], 'diagnostics': diagnostics}
    if len(contracts) != 1:
        fail('INV-REF.PROJECTION_CONTRACT_MISSING_OR_DUPLICATE', CONTRACT_ID,
             'Exactly one existing MOD-1M contract is required.')
        result['summary'] = {'contract_valid': False, 'certified': False}
        result['digest'] = digest(result)
        return result
    contract = contracts[0]
    contract_diagnostics = validate_projection_contract(registry, contract, historical)
    for item in contract_diagnostics:
        fail(item['code'], item['element_id'], item['message'], provenance=item['provenance'])
    # Malformed contracts must not fall through to code expecting their fields.
    if any(d['code'] in {'INV-VER.PROJECTION_CONTRACT_SHAPE', 'INV-VER.TRONCON_PROJECTION_GATE_REJECTED'} for d in contract_diagnostics):
        result['summary'] = {'contract_valid': False, 'certified': False}
        result['digest'] = digest(result)
        return result
    p, facts = contract['parameters'], {f['id']: f for f in historical['facts']}
    mappings = {m['source']: m for m in model['mappings']}
    targets = {t['id']: t for t in registry['targets']}
    scope = {p['berge']['source'], p['berge']['trait_source'], p['lit_source'], p['digue_source'], p['troncon_source'],
             *[s['source'] for s in p['structures']],
             *[a[key] for a in p['associations'] for key in ('source_class', 'destination_class')]}
    from .typed_troncons import suspended_projection_sources, CONTRACT_ID as TRANSITION_CONTRACT
    suspended = suspended_projection_sources(registry)
    scope.update(suspended)
    producers = {source: _producer(registry, p, source) for source in sorted(scope)}
    for source in suspended:
        producers[source] = {'operator': 'preserve_unprojected_a3', 'target': None,
                             'rule': TRANSITION_CONTRACT}
    relevant_targets = {t for source in scope for t in mappings.get(source, {}).get('targets', [])}
    relevant_targets.update(pr['target'] for pr in producers.values() if pr['target'])
    relevant_targets.add('da:relation:digue-troncon-lit')
    relevant_targets.update(t['id'] for t in targets.values() if t.get('state') == 'ignored'
                            and set(t['decision_refs']) & set(contract['decision_refs']))
    relevant_targets = {parent for target in relevant_targets for parent in _target_chain(target, targets)}
    binding_rows = [b for b in registry['bindings'] if b['target'] in relevant_targets]
    binding_ids = {b['id'] for b in binding_rows}
    by_target = defaultdict(list)
    for binding in binding_rows:
        by_target[binding['target']].append(binding)
    if physical_evidence is None and source_paths is not None:
        from .sql_evidence import qualify_bindings
        physical_evidence = qualify_bindings(registry, model['manifest'], source_paths)
    physical_locations = {b['id']: b for b in (physical_evidence or {}).get('locations', [])}
    physical_diagnostics = [d for d in (physical_evidence or {}).get('diagnostics', [])
                            if d['element_id'] in binding_ids]

    for target_id in sorted(relevant_targets):
        target = targets[target_id]
        chain = _target_chain(target_id, targets)
        inherited = [b for t in chain for b in by_target[t]]
        geometry = target.get('geometry')
        issues = []
        if target.get('state') == 'ignored':
            issues.append('RETIRED_TARGET_BINDING_NOT_CURRENT')
        if not by_target[target_id]:
            issues.append('EXACT_TARGET_BINDING_MISSING')
        if not inherited or any(b['id'] not in physical_locations for b in inherited):
            issues.append('PHYSICAL_EVIDENCE_UNVERIFIED')
        for binding in inherited:
            location = physical_locations.get(binding['id'])
            if location and not location['location_verified']:
                issues.append('PHYSICAL_LOCATION_UNVERIFIED')
            if binding['physical_status'] != 'BINDING_COMPLET_DEMONTRE':
                issues.append('PHYSICAL_BINDING_INCOMPLETE')
            # SQL type restrictions are evidence; a generic geometry type does
            # not prove the required validity predicate or shape CHECK.
            if geometry and geometry.get('status') in APPROVED:
                columns = (location or {}).get('evidence', {}).get('columns', {})
                types = {k: v['type'] for k, v in columns.items()} if columns else binding['column_types']
                for column_type in types.values():
                    match = re.search(r'geometry\((\w+),', column_type.lower())
                    if match and match[1] not in {'geometry', *[s.lower() for s in geometry['target_shapes']]}:
                        issues.append('GEOMETRY_SQL_SHAPE_INCOMPATIBLE')
                issues.append('GEOMETRY_VALIDITY_CONSTRAINT_UNQUALIFIED')
        if target_id == 'da:component:structure-digue':
            for binding in inherited:
                evidence = physical_locations.get(binding['id'], {}).get('evidence', {})
                constraints = evidence.get('constraints') or binding['constraints']
                if any(c['kind'] == 'check' and 'num_nonnulls(troncon_id,segment_hydrographique_id,berge_id)=1'
                       in re.sub(r'\s+', '', c['expression']).lower() for c in constraints):
                    issues.append('OPTIONAL_DIGUE_OWNER_SQL_INCOMPATIBLE')
        if target_id == 'da:type:structure-digue/talus':
            # An inherited family location does not establish the two independent
            # approved attributes or the unified Talus identity partition.
            issues.append('INDEPENDENT_NATURE_SIDE_SQL_UNQUALIFIED')
        if target_id == 'da:relation:digue-troncon-lit' and not by_target[target_id]:
            issues.append('OPTIONAL_MANY_TO_MANY_SQL_UNQUALIFIED')
        result['bindings'].append({'target': target_id, 'target_state': target.get('state'),
            'binding_ids': sorted(b['id'] for b in inherited),
            'direct_binding_ids': sorted(b['id'] for b in by_target[target_id]),
            'records': deepcopy(sorted(inherited, key=lambda b: b['id'])),
            'physical_evidence': deepcopy([physical_locations[b['id']] for b in inherited if b['id'] in physical_locations]),
            'diagnostics': deepcopy([d for d in physical_diagnostics if d['element_id'] in {b['id'] for b in inherited}]),
            'issues': sorted(set(issues)), 'certified': False})
    binding_index = {b['target']: b for b in result['bindings']}

    selected = [f for f in facts.values() if f['id'] in scope or
                (f.get('owner') in scope and f['kind'] in {'application', 'geometry', 'identity'})]
    for source in sorted(scope - facts.keys()):
        fail('INV-REF.PROJECTION_SOURCE_MISSING', source, 'A declared source class is missing from the pinned historical facts.')
    for fact in sorted(selected, key=lambda f: f['id']):
        owner = fact['id'] if fact['kind'] == 'class' else fact['owner']
        producer, declaration = producers[owner], None
        limitations = []
        if fact['kind'] in {'class', 'identity'}:
            projection = deepcopy(producer)
            if producer['operator'] == 'unprojected':
                limitations.append('OCCURRENCE_PRODUCER_MISSING')
            elif producer['operator'] == 'preserve_unprojected_a3':
                limitations.append('TRONCON_PROJECTION_SUSPENDED_A3')
            elif producer['operator'] == 'removed_container':
                limitations.append('LIT_ATTRIBUTE_DESTINATIONS_UNRESOLVED')
            else:
                limitations.append('OCCURRENCE_SQL_PROJECTION_UNQUALIFIED')
        else:
            prop = facts.get(fact['data'].get('property'), {}) if fact['kind'] == 'geometry' else fact
            declaration = prop.get('data', {}).get('declaration', prop.get('id'))
            if declaration not in facts:
                fail('INV-REF.PROJECTION_DECLARATION_MISSING', fact['id'],
                     'Effective source property has no exact historical declaration.', provenance=fact['provenance'])
            projection, limitations = _property_projection(owner, declaration or '', p, producer, p['associations'])
            if fact['kind'] == 'geometry':
                projection['historical_geometry'] = deepcopy(fact['data'])
                projection['target_contract'] = deepcopy(targets.get(producer['target'], {}).get('geometry'))
                limitations.append('SOURCE_GEOMETRY_OCCURRENCES_UNQUALIFIED')
                if not producer.get('optional_parent'):
                    limitations.append('CORE_BORNAGE_RUNTIME_UNQUALIFIED')
        mapping = mappings.get(fact['id'])
        if mapping is None:
            fail('INV-MAP.PROJECTION_MAPPING_MISSING', fact['id'], 'No source disposition exists in the master mappings.')
        target_ids = sorted(set((mapping or {}).get('targets', []) + ([producer['target']] if producer['target'] else [])))
        result['source_outcomes'].append({'source': fact['id'], 'source_class': owner, 'kind': fact['kind'],
            'declaration': declaration, 'source_fact': deepcopy(fact),
            'declaration_fact': deepcopy(facts.get(declaration)) if declaration else None,
            'mapping': deepcopy(mapping), 'logical_projection': projection, 'target_ids': target_ids,
            'binding_ids': sorted({bid for target in target_ids for bid in binding_index.get(target, {}).get('binding_ids', [])}),
            'limitations': sorted(set(limitations)),
            'limitation_refs': sorted(l['id'] for l in registry['limitations']
                                     if {owner, fact['id'], *target_ids} & set(l['subjects'])),
            'certified': False})

    for pair in sorted(p['associations'], key=canonical_bytes):
        source, destination = producers[pair['source_class']], producers[pair['destination_class']]
        issues = []
        if source['target'] != pair['source_target']:
            issues.append('SOURCE_PRODUCER_UNAVAILABLE')
        if destination['target'] != pair['destination_target']:
            issues.append('DESTINATION_PRODUCER_UNAVAILABLE')
        if source.get('parent_reference') == pair['reference'] and source['parent_target'] != pair['destination_target']:
            issues.append('REQUIRED_PARENT_TARGET_INCOMPATIBLE')
        spec = next((s for s in p['structures'] if s['source'] == pair['source_class']), None)
        if (spec and spec['owner_path'] == 'direct_berge' and pair['reference'] == p['structure_parent_reference']
                and pair['destination_class'] != p['berge']['source']):
            issues.append('REQUIRED_PARENT_TARGET_INCOMPATIBLE')
        result['associations'].append({'pair': deepcopy(pair), 'source_producer': deepcopy(source),
            'destination_producer': deepcopy(destination),
            'status': 'BLOCKED' if issues or contract_diagnostics else 'LOGICAL_PAIR_ONLY',
            'issues': sorted(set(issues)),
            'reference_resolution': 'one_source_uuid_to_its_single_representative',
            'missing_or_ambiguous': 'preserve_absolute_values_and_diagnose',
            'physical_binding': 'UNQUALIFIED', 'certified': False})
    counts = Counter(l for row in result['source_outcomes'] for l in row['limitations'])
    pending = [('OCCURRENCE_PRODUCER_MISSING', 'INV-MAP.PROJECTION_PRODUCERS_PENDING'),
               ('TRONCON_PROJECTION_SUSPENDED_A3', 'INV-MAP.TRONCON_PROJECTION_SUSPENDED'),
               ('TARGET_ATTRIBUTE_BINDING_UNQUALIFIED', 'INV-MAP.PROJECTION_FIELDS_PENDING'),
               ('REFERENCE_DESTINATION_UNQUALIFIED', 'INV-REF.PROJECTION_RELATIONS_PENDING'),
               ('LIT_ATTRIBUTE_DESTINATIONS_UNRESOLVED', 'INV-MAP.LIT_DESTINATIONS_PENDING')]
    for limitation, code in pending:
        if counts[limitation]:
            fail(code, CONTRACT_ID, f'{counts[limitation]} source obligations remain {limitation}; see individual source outcomes.',
                 provenance=contract['provenance'])
    physical_issues = Counter(i for row in result['bindings'] for i in row['issues'])
    if physical_issues:
        fail('INV-SQL.PROJECTION_BINDINGS_UNQUALIFIED', CONTRACT_ID,
             'Physical projection remains unqualified: ' + ', '.join(sorted(physical_issues)),
             profile='physical-binding', provenance=contract['provenance'])
    result['summary'] = {'contract_valid': not contract_diagnostics, 'certified': False,
        'historical_facts': len(historical['facts']), 'historical_facts_sha256': digest(sorted(historical['facts'], key=lambda f: f['id'])),
        'source_classes': len(scope), 'source_outcomes': len(result['source_outcomes']),
        'source_kinds': dict(sorted(Counter(r['kind'] for r in result['source_outcomes']).items())),
        'source_outcomes_sha256': digest(result['source_outcomes']),
        'association_pairs': len(result['associations']),
        'logical_pairs': sum(a['status'] == 'LOGICAL_PAIR_ONLY' for a in result['associations']),
        'blocked_pairs': sum(a['status'] == 'BLOCKED' for a in result['associations']),
        'unimplemented_source_classes': sorted(s for s, pr in producers.items() if pr['operator'] == 'unprojected'),
        'suspended_source_classes': sorted(suspended),
        'limitation_counts': dict(sorted(counts.items())), 'physical_issue_counts': dict(sorted(physical_issues.items())),
        'physical_evidence': 'CHECKED' if physical_evidence is not None else 'NOT_CHECKED',
        'historical_geometry_owners': len({f['owner'] for f in facts.values() if f['kind'] == 'geometry'}),
        'historical_geometry_facts': sum(f['kind'] == 'geometry' for f in facts.values()),
        'core_bornage_interface': 'LOGICAL_PLANS_ONLY_RUNTIME_NOT_QUALIFIED'}
    result['source_evidence'] = {'sources': deepcopy([s for s in model['manifest']['sources']
        if s['kind'] == 'ecore' or s['id'] in {'contract:c1', 'contract:c2', 'contract:c3', 'inventory:ecore', 'inventory:mock'}]),
        'comparisons': deepcopy(historical['comparisons']),
        'authority': 'PINNED_MASTER_FACTS_NO_HISTORICAL_REAUDIT'}
    result['side_values'] = [{'source_fact': deepcopy(f),
        'target_value': None if suspended else p['side_values'].get(f['data']['value']['id']),
        'status': 'SUSPENDED_A3' if suspended else ('LOGICAL_VALUE_ONLY' if f['data']['value']['id'] in p['side_values'] else 'BLOCKED'),
        'unmapped_outcome': 'preserve_source_value_and_diagnose'}
        for f in sorted(facts.values(), key=lambda f: f['id']) if f['kind'] == 'catalog_value'
        and f['data']['value'].get('id', '').startswith('RefCote:')]
    result['diagnostics'] = sorted(diagnostics, key=canonical_bytes)
    result['digest'] = digest(result)
    return result
