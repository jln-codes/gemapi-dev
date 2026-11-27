"""MOD-1M deterministic logical witnesses, never a production import.

The versioned master-registry contract is the only declaration of source/target
pairs. All original values survive, including those with no approved target.
The historical deduplication is replayable only with its historical registry.
Current B3 authorizes distinct UUIDs through its separate physical binding.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from pathlib import Path

from jsonschema import Draft202012Validator

from .common import canonical_bytes, digest, strict_load
from .decision_successions import resolve_decision_assertions
from .geometry_contracts import MORPHOLOGY_TARGETS
from .spatial import (
    SpatialContractError, exact_spatial_equal, plan_bornage, qualify_geometry,
    update_relative_projection,
)

APPROVED = {'DECIDE', 'CONSEQUENCE_NECESSAIRE'}
CONTRACT_ID = 'contract:mod1m/core-berge-lit-projection'
CLASS = 'sirs-v2:class:'
ATTR = 'sirs-v2:attribute:'
REF = 'sirs-v2:reference:'
GEOMETRY = ATTR + 'core/Positionable/geometry'
CRS = ATTR + 'core/Positionable/crsName'


def _failure(contract, code, message, element=None):
    return {'code': code, 'element_id': element or contract['id'],
            'provenance': deepcopy(contract.get('provenance', [])), 'message': message}


def validate_projection_contract(registry, contract, historical=None):
    """Check executable scope and exact historical evidence, without certification."""
    result = []
    def fail(code, message):
        result.append(_failure(contract, code, message))
    schema = strict_load(Path(__file__).with_name('registry.schema.json'))
    if not Draft202012Validator({'$defs':schema['$defs'],'$ref':'#/$defs/formal_contract'}).is_valid(contract):
        fail('INV-VER.PROJECTION_CONTRACT_SHAPE','Projection parameters must satisfy the closed master-registry schema.')
        return result
    if contract.get('id') != CONTRACT_ID or contract.get('version') != '1' or contract.get('status') not in APPROVED:
        fail('INV-VER.PROJECTION_CONTRACT_STALE', 'Only the approved MOD-1M projection contract version 1 is executable.')
    decisions = {d['id']:d for d in registry.get('decisions', [])}
    expected = {'decision:MOD1M-core','decision:MOD1M-berge','decision:MOD1M-lit'}
    if 'decision:MOD1P-B3' in decisions:
        expected.add('decision:MOD1P-B3')
    if 'decision:MOD-1O-L02-rectificatif' in decisions:
        expected.add('decision:MOD-1O-L02-rectificatif')
    if 'decision:MOD-1O-L02-B' in decisions:
        expected.add('decision:MOD-1O-L02-B')
    if (set(contract.get('decision_refs', [])) != expected or
            any(decisions.get(d, {}).get('status') not in APPROVED for d in expected)):
        fail('INV-MAP.PROJECTION_DECISIONS_UNAPPROVED', 'The three MOD-1M decisions must approve the projection.')
    p = contract.get('parameters', {})
    succession = resolve_decision_assertions(registry)
    from .typed_troncons import suspended_projection_sources
    try:
        suspended = suspended_projection_sources(registry)
    except ValueError:
        fail('INV-VER.TRONCON_PROJECTION_GATE_REJECTED', 'The approved A3 source gate is absent or altered.')
        return result
    seals = [a for name in ('decision:MOD1M-berge', 'decision:MOD1P-B3',
                          'decision:MOD-1O-L02-rectificatif', 'decision:MOD-1O-L02-B')
             for a in succession['assertions'].get(name, [])
             if a['subject']==CONTRACT_ID and a['predicate']=='contract_parameters_digest']
    if succession['diagnostics'] or len(seals)!=1 or seals[0]['value']!=digest(p):
        fail('INV-VER.PROJECTION_PARAMETERS_STALE', 'The closed projection parameters differ from their active approved decision.')
    if p.get('operator') != 'core_berge_lit_v1' or p.get('preservation') != 'all_source_values':
        fail('INV-VER.PROJECTION_OPERATOR_UNSUPPORTED', 'Unsupported projection semantics.')
        return result
    targets = {t['id']:t for t in registry.get('targets', [])}
    target_ids = {p['berge']['target'], 'da:concept:digue', 'da:concept:troncon-digue',
                  *[s['target'] for s in p['structures']],
                  *[a[key] for a in p['associations'] for key in ('source_target','destination_target')]}
    for rule in registry.get('rules', []):
        if (rule['status'] in APPROVED and
                all(decisions.get(d, {}).get('status') in APPROVED for d in rule['decision_refs'])):
            target_ids.update(output['target'] for output in rule['outputs'])
    suspended_targets = {s['target'] for s in p['structures'] if s['source'] in suspended}
    if p['berge']['source'] in suspended:
        suspended_targets.add(p['berge']['target'])
    if p['troncon_source'] in suspended:
        suspended_targets.add('da:concept:troncon-digue')
    for target in sorted(target_ids):
        deferred_by_a3 = (target in suspended_targets
            and targets.get(target, {}).get('state') == 'deferred'
            and {'subject': target, 'predicate': 'state', 'value': 'deferred'}
                in succession['assertions'].get('decision:MOD1P-A3', []))
        if (targets.get(target, {}).get('status') not in APPROVED
                or (targets.get(target, {}).get('state') != 'active' and not deferred_by_a3)):
            fail('INV-MAP.PROJECTION_TARGET_INACTIVE', 'Projection target must be active and approved: '+target)
    if p['side_values'] != {'RefCote:1':'Rivière-mer','RefCote:2':'Terre','RefCote:4':'Rivière-mer','RefCote:99':'Indéfini'}:
        fail('INV-MAP.SIDE_VALUE_MAPPING_UNAPPROVED', 'Only the four semantically established C1 sides are projected.')
    fields = p['berge']['fields']
    if len({f['name'] for f in fields}) != len(fields):
        fail('INV-MAP.PROJECTION_FIELD_COLLISION', 'Each target field has one declared producer.')
    own = {'date_debut','date_fin','dateMaj','valid','author','lastUpdateAuthor'}
    for field in fields:
        if field['trait_origin'] != ('self' if field['name'] in own else 'berge'):
            fail('INV-MAP.BERGE_FIELD_ORIGIN', 'Trait dates/authors/validity are their own; other projected attributes are inherited.')
    pairs = [canonical_bytes(a) for a in p['associations']]
    if len(pairs) != len(set(pairs)):
        fail('INV-REF.ASSOCIATION_PAIR_DUPLICATE', 'The association list must contain unique exact pairs.')
    if historical is not None:
        facts = {f['id']:f for f in historical['facts']}
        def assignable(actual, expected):
            pending, seen = [actual], set()
            while pending:
                current = pending.pop()
                if current == expected:
                    return True
                if current in seen:
                    continue
                seen.add(current)
                pending.extend(facts.get(current, {}).get('data', {}).get('super_types', []))
            return False
        expected_fields = {(f['name'],f['data']['declaration']) for f in facts.values() if f['kind']=='application' and f['owner']==p['berge']['source'] and f['data'].get('property_kind')=='attribute' and f['name']!='geometry'}
        if {(f['name'],f['berge_source']) for f in fields} != expected_fields:
            fail('INV-MAP.PROJECTION_FIELDS_INCOMPLETE', 'Every effective Berge attribute must participate in comparison after projection.')
        for field in fields:
            for key in ('berge_source','trait_source'):
                identifier = field[key]
                if identifier is not None and (facts.get(identifier, {}).get('kind') != 'attribute' or facts[identifier]['name'] != field['name']):
                    fail('INV-MAP.PROJECTION_FIELD_EVIDENCE', 'The projected field lacks its exact source declaration: '+str(identifier))
        for association in p['associations']:
            evidence = facts.get(association['evidence'], {})
            data = evidence.get('data', {})
            if (evidence.get('kind') != 'application' or evidence.get('owner') != association['source_class']
                    or data.get('property_kind') != 'reference' or data.get('declaration') != association['reference']
                    or data.get('upper') != association['source_upper']
                    or association['source_role'] != 'objet_source' or association['destination_role'] != evidence.get('name')
                    or not assignable(association['destination_class'], data.get('type'))):
                fail('INV-REF.ASSOCIATION_PAIR_UNPROVEN', 'Association pair lacks its exact MOD-1R/Ecore declaration: '+association['evidence'])
    return sorted(result, key=canonical_bytes)


def _one(value):
    candidates = value if isinstance(value, list) else ([] if value is None or value == '' else [value])
    return candidates[0] if len(candidates) == 1 and isinstance(candidates[0], str) else None


def _output(dataset, record, target, contract):
    return {'logical_key':[dataset,record['id'],'root'], 'target':target,'rule':contract['id'],
            'source_class':record['class'],'source_values':deepcopy(record['values']),
            'source_records':[deepcopy(record)],'source_ids':[record['id']],
            'target_values':{},'parent':None,'parent_resolved':True,
            'qualification':'LOGICAL_ONLY_TARGET_ATTRIBUTES_AND_SQL_UNQUALIFIED'}


def project_core_berge_lit(model, corpus, base_project):
    contract = next(c for c in model['registry']['contracts'] if c['id'] == CONTRACT_ID)
    failures = validate_projection_contract(model['registry'], contract, model.get('historical'))
    if failures:
        raise ValueError('INV-VER.PROJECTION_CONTRACT_REJECTED: '+','.join(d['code'] for d in failures))
    p, dataset = contract['parameters'], corpus['dataset']
    from .typed_troncons import suspended_projection_sources
    suspended = suspended_projection_sources(model['registry'])
    held = [deepcopy(r) for r in corpus['occurrences'] if r['class'] in suspended]
    records = {r['id']:r for r in corpus['occurrences'] if r['class'] not in suspended}
    handled = {p['berge']['source'],p['berge']['trait_source'],p['lit_source'],p['digue_source'],p['troncon_source'],*[s['source'] for s in p['structures']]}
    baseline = base_project(model, {'dataset':dataset,'occurrences':[r for r in records.values() if r['class'] not in handled]})
    baseline['unprojected'] = sorted(baseline['unprojected'] + held, key=lambda r:r['id'])
    outputs = {o['logical_key'][1]:o for o in baseline['outputs']}
    diagnostics = baseline['diagnostics']
    dispositions, mapping, resolved_references, associations = [], {}, [], []
    def diagnostic(code, record, message, *, blocking=True):
        diagnostics.append({'code':code,'element_id':record['id'],'source_class':record['class'],
                            'rule_id':contract['id'],'provenance':deepcopy(contract['provenance']),
                            'input_digest':model['input_digest'],'message':message,'blocking':blocking})
    for record in held:
        diagnostic('INV-MAP.TRONCON_PROJECTION_SUSPENDED', record,
                   'A3 retains the complete historical occurrence without a target, type fallback, identity merge or physical output until its projection is qualified.')
    for identifier, record in sorted(records.items()):
        for source in (ATTR+'core/Positionable/valid', ATTR+'core/TronconDigue/valid',
                       ATTR+'berge/TraitBerge/valid'):
            value=record['values'].get(source)
            if value is not None and type(value) is not bool:
                diagnostic('ATTRIBUTE_VALUE_UNQUALIFIED',record,
                    'Historical EBoolean must be bool or NULL; its unchanged value is retained: '+source)
        if record['class'] == p['lit_source']:
            dispositions.append({'source_id':identifier,'source_class':record['class'],'outcome':'removed_container',
                'target':None,'source_values':deepcopy(record['values']),
                'reason':'Lit has no active target; historical fields require individual approved destinations.',
                'obligations':['LIT_ATTRIBUTE_DESTINATIONS_UNRESOLVED'] if record['values'] else []})
            diagnostic('LIT_WITHOUT_TARGET', record, 'Historical Lit is retained as source evidence without an invented target container.', blocking=bool(record['values']))
        elif record['class'] in {p['digue_source'],p['troncon_source']}:
            target='da:concept:digue' if record['class']==p['digue_source'] else 'da:concept:troncon-digue'
            outputs[identifier]=_output(dataset,record,target,contract)
    berge = p['berge']
    groups = defaultdict(list)
    for identifier, record in sorted(records.items()):
        if record['class'] == berge['source']:
            groups[identifier].append(record)
        elif record['class'] == berge['trait_source']:
            parent=_one(record['values'].get(berge['parent_reference']))
            if parent not in records or records[parent]['class'] != berge['source']:
                parent=identifier
                diagnostic('BERGE_PARENT_UNRESOLVED',record,'Trait parent is missing, ambiguous or incompatible; inherited fields remain unknown.')
            groups[parent].append(record)
    for family, members in sorted(groups.items()):
        representatives=[]
        for record in sorted(members,key=lambda r:(r['class'] != berge['source'],r['id'])):
            is_berge=record['class']==berge['source']
            parent=record if is_berge else records.get(_one(record['values'].get(berge['parent_reference'])),{})
            if parent.get('class') != berge['source']:
                parent={}
            values={}
            for field in berge['fields']:
                producer=record if is_berge or field['trait_origin']=='self' else parent
                source=field['berge_source'] if is_berge or field['trait_origin']=='berge' else field['trait_source']
                values[field['name']]=deepcopy(producer.get('values',{}).get(source))
            geom=deepcopy(record['values'].get(berge['geometry']['berge_source' if is_berge else 'trait_source']))
            # The family's historical references remain declared source evidence;
            # they participate in equivalence, and are never multiplied to Traits.
            refs={k:deepcopy(v) for k,v in parent.get('values',{}).items() if k.startswith(REF)}
            if values.get('valid') is True and (not isinstance(geom,dict) or geom.get('type') != 'LineString'):
                diagnostic('GEOMETRY_MISSING' if geom is None else 'GEOMETRY_UNEXPECTED',record,'A valid Berge requires a LineString; absolute source geometry is retained.')
            if geom is not None:
                try:
                    exact_spatial_equal(geom, geom, left_crs=record['values'].get(CRS), right_crs=record['values'].get(CRS))
                except SpatialContractError as exc:
                    diagnostic(exc.code, record, 'The geometry CRS or coordinates cannot be qualified.')
            match=None
            for candidate in representatives:
                if canonical_bytes(candidate['comparison_values']) != canonical_bytes(values) or canonical_bytes(candidate['comparison_references']) != canonical_bytes(refs):
                    continue
                try:
                    equal=exact_spatial_equal(geom,candidate['geometry'],left_crs=record['values'].get(CRS),right_crs=candidate['source_values'].get(CRS))
                except SpatialContractError as exc:
                    diagnostic(exc.code,record,'Exact spatial equality is not provable; occurrences remain distinct.')
                    equal=False
                if equal:
                    match=candidate
                    break
            if match is None:
                result=_output(dataset,record,berge['target'],contract)
                result.update(target_values=deepcopy(values),geometry=geom,comparison_values=deepcopy(values),
                              comparison_references=refs,berge_family=family,valid_for_target=None)
                representatives.append(result);outputs[record['id']]=result;match=result
            else:
                match['source_records'].append(deepcopy(record));match['source_ids'].append(record['id'])
            mapping[record['id']]=match['logical_key']
        for index, candidate in enumerate(representatives):
            candidate['disambiguation_suffix']=None if len(representatives)==1 else str(index+1)
            candidate['source_records'].sort(key=lambda r:r['id']);candidate['source_ids'].sort()
    structures={s['source']:s for s in p['structures']}
    for identifier, record in sorted(records.items()):
        spec=structures.get(record['class'])
        if spec is None:
            continue
        output=_output(dataset,record,spec['target'],contract)
        output['geometry']=deepcopy(record['values'].get(GEOMETRY))
        output['valid_for_target']=None
        output['target_values']['valid']=deepcopy(record['values'].get(ATTR+'core/Positionable/valid'))
        if output['target_values']['valid'] is True and (not isinstance(output['geometry'],dict) or output['geometry'].get('type') not in {'LineString','Polygon'}):
            diagnostic('GEOMETRY_MISSING' if output['geometry'] is None else 'GEOMETRY_UNEXPECTED',record,'A valid structure requires LineString or Polygon; source geometry is retained.')
        if spec['nature'] is not None:
            raw=record['values'].get(spec['side_source']) if spec['side_source'] else None
            output['target_values'].update(nature=spec['nature'],cote=p['side_values'].get(raw) if isinstance(raw,str) else None)
            if raw is not None and (not isinstance(raw,str) or raw not in p['side_values']):
                diagnostic('SIDE_VALUE_UNRESOLVED',record,'Historical side has no approved equivalence; the source value remains intact.')
        parent_id=_one(record['values'].get(p['structure_parent_reference']))
        historical_parent=records.get(parent_id,{})
        owner_id=_one(historical_parent.get('values',{}).get(p['troncon_digue_reference']))
        if spec['owner_path']=='direct_berge':
            if historical_parent.get('class')==berge['source'] and parent_id in mapping:
                output['parent']=mapping[parent_id]
            else:
                output['parent_resolved']=False
                diagnostic('PARENT_UNRESOLVED',record,'Pied/Sommet retain their required Berge owner; unresolved reference is not invented.')
        elif (historical_parent.get('class') not in {p['troncon_source'],berge['source'],CLASS+'lit/TronconLit'}
                or records.get(owner_id,{}).get('class') != p['digue_source']):
            diagnostic('STRUCTURE_WITHOUT_DIGUE',record,'No unique Digue resolves through the historical Tronçon; structure and validity are preserved.',blocking=False)
        else:
            output['parent']=outputs[owner_id]['logical_key']
            output['owner_source_path']=[parent_id,owner_id]
        outputs[identifier]=output
    # Core roots are produced after generic preservation. Once present, a
    # known incompatible morphology reference is no longer "unresolved".
    # Preserve the original reference and diagnostic provenance; never bind it.
    rules_by_id = {rule['id']: rule for rule in model['registry']['rules']}
    for identifier, output in outputs.items():
        if output['target'] not in MORPHOLOGY_TARGETS or output.get('parent_resolved', True):
            continue
        policy = rules_by_id.get(output['rule'], {}).get('reference_policy', {})
        parent_id = _one(records[identifier]['values'].get(policy.get('parent_source')))
        if parent_id in outputs and outputs[parent_id]['target'] != policy.get('parent_target'):
            for entry in diagnostics:
                if (entry['element_id'] == identifier and entry['code'] == 'PARENT_UNRESOLVED'
                        and entry.get('rule_id') == output['rule']):
                    entry['code'] = 'PARENT_INCOMPATIBLE'
    for identifier, output in outputs.items():
        mapping.setdefault(identifier,output['logical_key'])
    # Resolve incoming references once, through the complete source UUID map.
    # Unprojected source objects stay unprojected; their links still have proof.
    allowed={(a['source_class'],a['reference'],a['destination_class']):a for a in p['associations']}
    for identifier, record in sorted(records.items()):
        for reference,value in sorted(record['values'].items()):
            if not reference.startswith(REF):
                continue
            if record['class']==berge['trait_source'] and reference==berge['parent_reference']:
                continue
            destinations=value if isinstance(value,list) else ([] if value is None else [value])
            source_bounds={a['source_upper'] for a in p['associations'] if a['source_class']==record['class'] and a['reference']==reference}
            if any(upper>=0 and len(destinations)>upper for upper in source_bounds):
                diagnostic('REFERENCE_CARDINALITY_UNRESOLVED',record,'Historical reference exceeds its approved source cardinality; source values remain intact and no target association is inferred.')
                continue
            for destination in destinations:
                if not isinstance(destination,str) or destination not in records:
                    diagnostic('REFERENCE_UNRESOLVED',record,'Historical reference remains unresolved: '+reference)
                    continue
                target_key=mapping.get(destination)
                if target_key is None:
                    diagnostic('REFERENCE_TARGET_UNPROJECTED',record,'Reference target has no active logical output: '+reference)
                    continue
                proof={'source_id':identifier,'reference':reference,'destination_source_id':destination,
                       'destination':deepcopy(target_key)}
                resolved_references.append(proof)
                pair=allowed.get((record['class'],reference,records[destination]['class']))
                source_key=mapping.get(identifier)
                if pair is None or source_key is None:
                    diagnostic('ASSOCIATION_PAIR_UNDECLARED',record,'Resolved identity has no executable approved target pair: '+reference)
                    continue
                actual_source=outputs[source_key[1]]['target'];actual_destination=outputs[target_key[1]]['target']
                if actual_source != pair['source_target'] or actual_destination != pair['destination_target']:
                    diagnostic('ASSOCIATION_TARGET_INCOMPATIBLE',record,'Projected endpoint differs from the closed association contract.')
                    continue
                associations.append({**proof,'source':deepcopy(source_key),
                    'source_role':pair['source_role'],'destination_role':pair['destination_role'],
                    'source_type':actual_source,'destination_type':actual_destination,'evidence':pair['evidence'],'spatial_projection':pair['spatial_projection']})
    bounds_fields={REF+'core/TronconDigue/borneIds',*[REF+'core/Positionable/'+f for f in ('borneDebutId','borneFinId','systemeRepId')],*[ATTR+'core/Positionable/'+f for f in ('borne_debut_aval','borne_debut_distance','borne_fin_aval','borne_fin_distance','positionDebut','positionFin','prDebut','prFin')]}
    target_contracts={target['id']:target.get('geometry') for target in model['registry']['targets']}
    physical_targets={berge['target'],*[s['target'] for s in p['structures']]}
    for identifier, output in sorted(outputs.items()):
        record=records[identifier]
        geometry=output.get('geometry',record['values'].get(GEOMETRY,record['values'].get(ATTR+'core/TronconDigue/geometry')))
        if (target_contracts.get(output['target']) or {}).get('policy') == 'preserve_in_technical_support':
            # L02: the exact source remains in source_values; no autonomous
            # target geometry, bornage computation or relative geometry plan.
            output['source_geometry_preservation'] = {
                'destination': 'technical_only', 'geometry': deepcopy(geometry),
                'crs': deepcopy(record['values'].get(CRS)), 'transformation': 'none'}
            continue
        if output['target'] in physical_targets or record['class'].startswith(CLASS+'lit/'):
            source_valid=output.get('target_values',{}).get('valid',record['values'].get(
                ATTR+'core/Positionable/valid',record['values'].get(ATTR+'core/TronconDigue/valid')))
            output['geometry_qualification']=qualify_geometry(geometry,target_contracts.get(output['target']),
                                                            crs=record['values'].get(CRS),valid=source_valid)
            output['valid_for_target']=None
            for entry in output['geometry_qualification']['diagnostics']:
                diagnostic(entry['code'],record,'Geometry facet only; source geometry is unchanged and import remains unqualified.',
                           blocking=entry['blocking'])
        if output['target'] in MORPHOLOGY_TARGETS:
            # L02-B geometry is autonomous: an association never derives a
            # bornage or relative projection from the parent geometry.
            continue
        if isinstance(geometry,dict) and geometry.get('type') in {'LineString','Polygon'}:
            bounds={k:deepcopy(v) for k,v in record['values'].items() if k in bounds_fields}
            output['bornage_plan']=plan_bornage(geometry,geometry,bounds,[],historical=True)
            for entry in output['bornage_plan']['diagnostics']:
                diagnostic(entry['code'],record,'Historical bornage plan cannot be qualified.')
        plans=[]
        for link in associations:
            if link['source'] != output['logical_key'] or not link['spatial_projection']:
                continue
            owner=outputs[link['destination'][1]]
            source_owner=records[owner['logical_key'][1]]
            source_record=records[link['source_id']]
            owner_geometry=owner.get('geometry',source_owner['values'].get(ATTR+'core/TronconDigue/geometry'))
            state={'geometry':deepcopy(geometry),'associations':[deepcopy(link)],
                   'projection':{k:deepcopy(v) for k,v in source_record['values'].items() if k in {
                       ATTR+'core/Positionable/prDebut',ATTR+'core/Positionable/prFin',
                       ATTR+'core/Positionable/positionDebut',ATTR+'core/Positionable/positionFin'}}}
            plan=update_relative_projection(state,owner_geometry,source_crs=source_record['values'].get(CRS),owner_crs=source_owner['values'].get(CRS))
            plans.append(plan)
            for entry in plan['diagnostics']:
                diagnostic(entry['code'],source_record,'Current relative projection is cleared; absolute geometry and declared association remain intact.',
                           blocking=entry.get('blocking',True))
        if plans:
            output['projection_plans']=plans
    uuid_mapping=[]
    for identifier, record in sorted(records.items()):
        key=mapping.get(identifier)
        uuid_mapping.append({'source_id':identifier,'source_class':record['class'],'target':deepcopy(key),
            'outcome':'merged' if key and key[1] != identifier else 'projected' if key else 'removed_container' if record['class']==p['lit_source'] else 'unprojected'})
    # Stable parent ordering covers the original B2 vertical and direct Digue owners.
    pending={key:{o['parent'][1]} if o.get('parent') else set() for key,o in outputs.items()}
    insertion=[]
    while pending:
        ready=sorted(k for k,v in pending.items() if not v)
        if not ready:
            raise ValueError('INV-REF.PROJECTION_CYCLE')
        insertion.extend(outputs[k]['logical_key'] for k in ready)
        for k in ready:del pending[k]
        for deps in pending.values():deps.difference_update(ready)
    result={**baseline,'corpus_digest':digest({'dataset':dataset,'occurrences':sorted(corpus['occurrences'],key=lambda r:r['id'])}),
        'outputs':[outputs[k] for k in sorted(outputs)],'insertion_order':insertion,
        'uuid_mapping':uuid_mapping,'dispositions':dispositions,
        'resolved_references':sorted(resolved_references,key=canonical_bytes),
        'associations':sorted(associations,key=canonical_bytes),'diagnostics':sorted(diagnostics,key=canonical_bytes)}
    result.pop('digest',None);result['digest']=digest(result)
    return result


def validate_projection_event(registry, contract_id, event, historical=None):
    """Replay a complete logical witness; never accept an asserted success flag."""
    contract=next((c for c in registry.get('contracts',[]) if c['id']==contract_id),None)
    if contract is None:
        return [{'code':'INV-REF.CONTRACT_MISSING','element_id':contract_id,'provenance':[],'message':'Unknown projection contract.'}]
    if not isinstance(event,dict) or set(event)!={'corpus','result'} or not isinstance(event.get('result'),dict):
        return [_failure(contract,'INV-VER.CONTRACT_EVENT_SHAPE','Expected exactly corpus and result.')]
    from .projection import project_logical
    try:
        model={'registry':registry,'input_digest':event['result'].get('input_digest')}
        if historical is not None:model['historical']=historical
        expected=project_logical(model,event['corpus'])
    except (KeyError,TypeError,ValueError) as exc:
        return [_failure(contract,'INV-MAP.PROJECTION_WITNESS_INVALID',str(exc))]
    if canonical_bytes(expected)!=canonical_bytes(event['result']):
        return [_failure(contract,'INV-MAP.PROJECTION_WITNESS_MISMATCH','Projection output does not reproduce the declared corpus and registry.')]
    if expected['unprojected'] or any(d.get('blocking',True) for d in expected['diagnostics']):
        return [_failure(contract,'INV-MAP.PROJECTION_WITNESS_BLOCKED','The faithfully reproduced witness still contains blocking projection diagnostics or unprojected occurrences.')]
    return []


def validate_berge_correspondence(registry, correspondence, historical=None):
    """Historical-only preservation exception; B3 retires all identity merging."""
    if any(d['id'] == 'decision:MOD1P-B3' for d in registry.get('decisions', [])):
        return False
    contract=next((c for c in registry.get('contracts',[]) if c['id']==CONTRACT_ID),None)
    return bool(contract and correspondence.get('preservation_contract')==CONTRACT_ID
                and correspondence.get('source') in {CLASS+'berge/Berge',CLASS+'berge/TraitBerge'}
                and correspondence.get('target')=='da:concept:berge'
                and not validate_projection_contract(registry,contract,historical))
