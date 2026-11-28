"""Eight verbatim user trials; simulated JSON and read-only current Demo references."""
import copy
import json
from pathlib import Path
import re
import sqlite3
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gemapi_app.assistant import Draft, SCHEMA, parse_response, request_payload, spoken_result
from test_assistant import proposal, response, CATALOG, FULL, COMPLETE_PHRASE

ROOT = Path(__file__).resolve().parents[2]
TRIALS = (
    ("Je voudrais créer une intervention de débroussaillage sur le remblai 1, Zec Nav. Paysages et Milieux a effectué les travaux le 11 novembre 2026 et a terminé les travaux le 12 novembre 2026, sous la responsabilité de Jean Dupont. Le chantier est donc Terminé. Les broussailles empêchaient de circuler facilement le long du remblai.",
     'ah_ouvrage', 'Remblai1 ZEC Nave', 'Débroussaillage', 'Paysages et Milieux', 'Jean Dupont', 'Terminé',
     '2026-11-11', '2026-11-12', 'a terminé les travaux le 12 novembre 2026', 'achevement', 'remblai 1, Zec Nav'),
    ("Sur le remblai 2, ZEC Clarence, la régie technique des milieux réalise un chantier d'élagage du 19 au 21 novembre 2026. David Petit est le responsable. Le statut est en cours. Plusieurs branches mortes menacent de tomber sur le passage.",
     'ah_ouvrage', 'Remblai2 ZEC Clarence', 'Élagage', 'Régie technique des milieux', 'David Petit', 'En cours',
     '2026-11-19', '2026-11-21', 'du 19 au 21 novembre 2026', 'previsionnelle', 'remblai 2, ZEC Clarence'),
    ("Travaux hydrauliques du bassin est intervenu les 8 et 9 octobre 2026 sur le remblai RC Bois des Aulnes pour retirer les embâcles. Eric Martin suivait le chantier, maintenant terminé. Les branches accumulées gênaient les accès d'entretien.",
     'ah_ouvrage', 'Remblai RC Bois des Aulnes', "Retrait d'embâcles", 'Travaux hydrauliques du bassin', 'Eric Martin', 'Terminé',
     '2026-10-08', '2026-10-09', 'les 8 et 9 octobre 2026', 'periode', 'remblai RC Bois des Aulnes'),
    ("Programmer une prestation de curage du bassin ZEC NAV entre le 5 et le 12 décembre 2026. Entreprise Rivage Nord, technicien Julien Dubois. Statut planifié. Des dépôts de vase sont visibles dans le fond du bassin et doivent être évacués.",
     'ah_stockage', 'Bassin ZEC Nave', 'Curage', 'Entreprise Rivage Nord', 'Julien Dubois', 'Planifié',
     '2026-12-05', '2026-12-12', 'entre le 5 et le 12 décembre 2026', 'previsionnelle', 'bassin ZEC NAV'),
    ("La société Atelier des eaux a fait une fauche sur le bassin RC Prévert du 11/09/2026 au 12/09/2026. David Petit était chargé du suivi. C'est Terminé. L'herbe était particulièrement haute près de l'entrée.",
     'ah_stockage', 'Bassin RC Pré Vert', 'Fauche', 'Atelier des Eaux', 'David Petit', 'Terminé',
     '2026-09-11', '2026-09-12', 'du 11/09/2026 au 12/09/2026', 'periode', 'bassin RC Prévert'),
    ("Prévoir une étude sur le bassin ZEC Val des Prés du 1er au 3 février 2027. L'entreprise Ecologie Terrain réalisera cette prestation, avec Eric Martin comme technicien responsable. Statut planifié. Il faudra examiner les possibilités d'amélioration de l'infiltration dans le bassin.",
     'ah_stockage', 'Bassin ZEC Val des Prés', 'Étude', 'Écologie Terrain', 'Eric Martin', 'Planifié',
     '2027-02-01', '2027-02-03', 'du 1er au 3 février 2027', 'previsionnelle', 'bassin ZEC Val des Prés'),
    ("Je souhaite planifier une restauration écologique sur la fourré humide · zone naturelle du secteur 01, dans l'espace naturel du Bois des Aulnes, du 15 au 26 décembre 2026. La régie technique des milieux interviendra avec Julien Dubois comme responsable. Plusieurs secteurs présentent une végétation dégradée.",
     'vegetation', 'Fourré humide · Zone naturelle du secteur 01', 'Restauration écologique', 'Régie technique des milieux', 'Julien Dubois', 'Planifié',
     '2026-12-15', '2026-12-26', 'du 15 au 26 décembre 2026', 'previsionnelle', 'Fourré secteur un du Bois des Aulnes'),
    ("La haie · haie du secteur 06, dans le bois du Petit Marais, on l'a fait élaguer par Paysages et Milieux. C'était du 10 au 12 novembre 2026, avec Jean Dupont pour le suivi. C'est terminé. Certaines branches débordaient sur le chemin voisin.",
     'vegetation', 'Haie · Haie du secteur 06', 'Élagage', 'Paysages et Milieux', 'Jean Dupont', 'Terminé',
     '2026-11-10', '2026-11-12', 'du 10 au 12 novembre 2026', 'periode', 'Haie secteur six du Petit Marais'),
)


def demo_catalog(family):
    def rows(filename, query):
        path = ROOT / 'demo/gpkg' / (filename + '.gpkg')
        connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
        try:
            return connection.execute(query).fetchall()
        finally:
            connection.close()
    statuses = (ROOT / 'model/schema_v0.6.1.sql').read_text().partition(
        'INSERT INTO gemapi.statuts_prestations')[2].partition(';')[0]
    catalog = {
        'technicien': rows('techniciens', "SELECT id,prenom||' '||nom,'' FROM techniciens WHERE actif"),
        'prestataire': rows('referentiels', "SELECT id,nom,'' FROM prestataires WHERE actif"),
        'type_prestation': rows('referentiels', 'SELECT t.id,t.libelle,c.libelle FROM types_prestations t '
                               'JOIN prestations_categorie c ON c.id=t.categorie_id WHERE t.actif'),
        'statut': re.findall(r"\('([0-9a-f-]{36})',\s*'[A-Z_]+',\s*'([^']+)'\)", statuses),
    }
    catalog['statut'] = [(uid, label, '') for uid, label in catalog['statut']]
    catalog['patrimoines'] = {
        'ah_ouvrage': rows('ah', 'SELECT o.id,o.nom,a.nom FROM ah_ouvrage o JOIN ah a ON a.id=o.ah_id'),
        'ah_stockage': rows('ah', 'SELECT o.id,o.nom,a.nom FROM ah_stockage o JOIN ah a ON a.id=o.ah_id'),
        **{key: rows('sites_terrestres', f'SELECT o.id,o.designation,s.nom FROM {key} o '
                            'JOIN sites_terrestres s ON s.id=o.site_terrestre_id') for key in ('vegetation', 'mobilier', 'cheminements')},
    }
    catalog['patrimoine'] = catalog['patrimoines'][family]
    return catalog


def trial_proposal(index):
    text, family, name, activity, company, person, status, start, end, quote, kind, source = TRIALS[index]
    catalog = demo_catalog(family)
    target = next(row for row in catalog['patrimoine'] if row[1] == name)
    fields = dict(famille=family, patrimoine=name + ' — ' + target[2], type_prestation=activity, prestataire=company,
                  technicien=person, statut=status, date_debut=start, date_fin=end,
                  description=text.rsplit('. ', 1)[-1],
                  preuve_dates={'source': quote, 'nature': kind},
                  correspondances={'prestataire': None, 'technicien': None, 'patrimoine': {
                      'source': source, 'nature': 'phonetique_complete',
                      'candidats': [name + ' — ' + target[2]]}})
    if index == 3:
        fields['geometrie_demandee'] = True  # Reproduce the reported unjustified model flag.
    return text, catalog, fields


class EntityResolutionTests(unittest.TestCase):
    def resolved_trial(self, index, source=None, kind='exacte', text=None, catalog=None):
        original, actual, fields = trial_proposal(index)
        actual = catalog or actual
        if source is not None:
            fields['correspondances']['patrimoine']['source'] = source
            fields['correspondances']['patrimoine']['nature'] = kind
        draft = Draft()
        draft.apply(proposal(**fields), user_message=text or original,
                    reference_context=json.loads(request_payload(Draft(), text or original, actual)
                                                 ['messages'][1]['content'])['referentiels'])
        return draft, draft.resolve(actual)

    def test_reformatted_canonical_source_recovers_literal_name_and_parent(self):
        for index in (6, 7):
            with self.subTest(trial=index + 1):
                text, catalog, fields = trial_proposal(index)
                canonical = fields['patrimoine']
                draft, result = self.resolved_trial(index, canonical)
                self.assertNotIn('patrimoine', draft.reference_diagnostics)
                self.assertIn('patrimoine', result['verified'])
                self.assertEqual(draft.matches['patrimoine'][0]['source'] in text, True)
                self.assertIsNotNone(result['values'])

    def test_parent_linking_words_and_spacing_are_identity_not_approximation(self):
        for index, source in ((6, "fourré humide · zone naturelle du secteur 01, dans l'espace naturel du Bois des Aulnes"),
                              (7, 'haie · haie du secteur 06, dans le bois du Petit Marais'),
                              (1, 'remblai 2, ZEC Clarence'), (4, 'bassin RC Prévert')):
            with self.subTest(source=source):
                draft, result = self.resolved_trial(index, source)
                self.assertIn('patrimoine', result['verified'])

    def test_parent_link_and_negated_observation_do_not_exclude_the_target(self):
        source = 'haie · haie du secteur 06, au bois du Petit Marais'
        message = 'Élagage sur la ' + source + ', qui ne gêne pas le passage.'
        draft, result = self.resolved_trial(7, source, text=message)
        self.assertIn('patrimoine', result['verified'])
        self.assertNotIn('patrimoine', draft.reference_diagnostics)

    def test_rejected_prior_name_cannot_validate_itself_on_an_independent_turn(self):
        text, catalog, fields = trial_proposal(6)
        bad = 'Fourré humide · Zone naturelle du secteur 01 — Bois du Petit Marais'
        fields['patrimoine'] = bad
        fields['correspondances']['patrimoine'] = {'source': bad, 'nature': 'exacte', 'candidats': [bad]}
        draft = Draft()
        context = json.loads(request_payload(draft, text, catalog)['messages'][1]['content'])['referentiels']
        draft.apply(proposal(**fields), user_message=text, reference_context=context)
        self.assertIn('patrimoine', draft.reference_diagnostics)
        draft.apply(proposal(patrimoine=bad, description='Accès difficile.',
                             correspondances=fields['correspondances']),
                    user_message='Ajouter : accès difficile.', reference_context=context)
        self.assertIn('patrimoine', draft.reference_diagnostics)
        self.assertNotIn('patrimoine', draft.resolve(catalog)['verified'])
        self.assertEqual(draft.names['description'], 'Accès difficile.')

    def test_wrong_canonical_parent_and_wrong_family_never_recovered(self):
        text, catalog, fields = trial_proposal(6)
        for bad in ('Fourré humide · Zone naturelle du secteur 01 — Bois du Petit Marais',
                    'Zone naturelle du secteur 06 — Bois du Petit Marais'):
            fields['patrimoine'] = bad
            fields['correspondances']['patrimoine'] = {
                'source': bad, 'nature': 'exacte', 'candidats': [bad]}
            draft = Draft()
            context = json.loads(request_payload(draft, text, catalog)['messages'][1]['content'])['referentiels']
            draft.apply(proposal(**fields), user_message=text, reference_context=context)
            result = draft.resolve(catalog)
            self.assertNotIn('patrimoine', result['verified'])
            self.assertEqual(set(result['verified']), {'technicien','prestataire','type_prestation','statut'})
            self.assertEqual(draft.names['description'], fields['description'])
        fields['famille'] = 'ah_ouvrage'
        draft = Draft()
        draft.apply(proposal(**fields), user_message=text, reference_context=context)
        self.assertIn('patrimoine', draft.reference_diagnostics)

    def test_reformatted_source_requires_current_context_and_excludes_negation(self):
        text, catalog, fields = trial_proposal(7)
        canonical = fields['patrimoine']
        for message in ('Changer uniquement le suivi.',
                        'Ne pas sélectionner la haie · haie du secteur 06, dans le bois du Petit Marais.',
                        'La haie · haie du secteur 06 est voisine. Le bois du Petit Marais est un autre site.'):
            with self.subTest(message=message):
                draft, result = self.resolved_trial(7, canonical, text=message)
                self.assertNotIn('patrimoine', result['verified'])
                self.assertIn('patrimoine', draft.reference_diagnostics)

    def test_repeated_identity_anchors_stay_pending_without_pairwise_search(self):
        text, catalog, fields = trial_proposal(7)
        canonical = fields['patrimoine']
        fragment = 'La haie · haie du secteur 06, dans le bois du Petit Marais. '
        draft, result = self.resolved_trial(7, canonical, text=fragment * 30)
        self.assertNotIn('patrimoine', result['verified'])
        self.assertIn('patrimoine', draft.reference_diagnostics)

    def test_approximation_and_invented_citation_keep_human_confirmation(self):
        for source, nature in [('Fourré humide · Zone naturelle du secteur 01 — Espace naturel du Bois des Aulnes', 'approximative'),
                               ('Nom source inventé', 'exacte'),
                               ('fourré du secteur', 'phonetique_complete')]:
            with self.subTest(source=source):
                draft, result = self.resolved_trial(6, source, nature)
                self.assertNotIn('patrimoine', result['verified'])


class ValidationTests(unittest.TestCase):
    def test_end_role_is_verified_in_original_message_not_rewritten_quote(self):
        for message in ('Travaux débutés le 11 novembre 2026 et terminés le 12 novembre 2026.',
                        'Début le 11 novembre 2026. Achèvement le 12 novembre 2026.',
                        'Intervention prévue du 11 au 12 novembre 2026, à proximité du bassin.'):
            with self.subTest(message=message):
                draft = Draft()
                draft.apply(proposal(date_debut='2026-11-11', date_fin='2026-11-12',
                                     preuve_dates={'source': 'a terminé les travaux le 12 novembre 2026',
                                                   'nature': 'achevement'}), user_message=message)
                self.assertFalse(draft.end_date_needs_confirmation)
        for message in ('Travaux le 11 novembre 2026. Le chantier est terminé.',
                        'Les travaux ne seront jamais terminés le 12 novembre 2026.',
                        'Fin probable le 12 novembre 2026.',
                        'Fin le 12 novembre 2026. Fin le 13 novembre 2026.',
                        'Fin le 12 novembre 2026 ou le 13 novembre 2026.'):
            with self.subTest(message=message):
                draft = Draft()
                draft.apply(proposal(date_debut='2026-11-11', date_fin='2026-11-12',
                                     preuve_dates={'source': 'terminés le 12 novembre 2026',
                                                   'nature': 'achevement'}), user_message=message)
                self.assertTrue(draft.end_date_needs_confirmation)

    def test_eight_verbatim_trials_syntax_resolution_and_draft(self):
        for index, trial in enumerate(TRIALS):
            with self.subTest(trial=index + 1):
                text, catalog, fields = trial_proposal(index)
                draft = Draft()
                context = json.loads(request_payload(draft, text, catalog)['messages'][1]['content'])['referentiels']
                raw = response(**fields)  # Stage 1: deterministic simulated JSON, not a claimed live answer.
                parsed = parse_response(raw)  # Stage 2: strict syntax.
                draft.apply(parsed, user_message=text, reference_context=context)
                result = draft.resolve(catalog)  # Stage 3: current Demo UUIDs, without database writes.
                self.assertEqual((draft.names['date_debut'], draft.names['date_fin']), trial[7:9])
                self.assertFalse(draft.end_date_needs_confirmation)
                self.assertEqual(draft.names['description'], fields['description'])
                self.assertFalse(draft.names.get('geometrie_demandee', False))
                if index < 6:
                    self.assertIsNotNone(result['values'], result['issues'])
                else:
                    self.assertIn('patrimoine', draft.reference_diagnostics)
                    self.assertEqual(draft.reference_diagnostics['patrimoine']['source'], trial[-1])
                    self.assertNotIn('patrimoine', result['verified'])
                    self.assertIn('patrimoine', result['pending'])
                    self.assertIsNone(result['values'])
                    self.assertEqual(set(result['verified']), {'technicien', 'prestataire', 'type_prestation', 'statut'})
                self.assertNotIn(trial[-1], spoken_result(draft, result))

    def test_six_date_formulations_and_fully_spoken_range(self):
        examples = [(t[9], t[7], t[8], t[10]) for t in TRIALS[:6]]
        examples += [('travaux le 11 novembre 2026 et terminés le 12 novembre 2026',
                      '2026-11-11', '2026-11-12', 'achevement'),
                     ('du onze au treize février deux mille vingt-sept', '2027-02-11', '2027-02-13', 'periode')]
        for source, start, end, kind in examples:
            with self.subTest(source=source):
                draft = Draft()
                draft.apply(proposal(date_debut=start, date_fin=end,
                                     preuve_dates={'source': source, 'nature': kind}), user_message=source)
                self.assertFalse(draft.end_date_needs_confirmation)

    def test_missing_ambiguous_negated_contradictory_and_forged_dates_require_review(self):
        examples = [
            ('Intervention le 11 novembre 2026. Statut Terminé.', 'du 11 au 12 novembre 2026'),
            ('Probablement du 11 au 12 novembre 2026.', 'du 11 au 12 novembre 2026'),
            ('Du 11 au 12 novembre 2026 ou le 13 novembre 2026.', 'Du 11 au 12 novembre 2026'),
            ('Les travaux ne sont pas terminés le 12 novembre 2026.', 'terminés le 12 novembre 2026'),
            ('Date de fin le 12 novembre 2026 ?', 'Date de fin le 12 novembre 2026'),
        ]
        for message, source in examples:
            with self.subTest(message=message):
                draft = Draft()
                draft.apply(proposal(date_debut='2026-11-11', date_fin='2026-11-12',
                                     preuve_dates={'source': source, 'nature': 'periode' if 'du ' in source.lower() else 'achevement'}),
                            user_message=message)
                self.assertTrue(draft.end_date_needs_confirmation)
        draft = Draft()
        draft.apply(proposal(**{**FULL, 'date_fin': None}), user_message=COMPLETE_PHRASE)
        self.assertNotIn('date_fin', draft.names)
        before = copy.deepcopy(vars(draft))
        with self.assertRaises(ValueError):
            draft.apply(proposal(date_fin='31 février 2026'), user_message='31 février 2026')
        self.assertEqual(vars(draft), before)
        draft.apply(proposal(date_fin='2026-11-10', preuve_dates={
            'source': 'du 11 au 10 novembre 2026', 'nature': 'periode'}), user_message='du 11 au 10 novembre 2026')
        self.assertIsNone(draft.resolve(CATALOG)['values'])

    def test_bad_source_preserves_fields_but_cannot_auto_select_canonical_uuid(self):
        draft = Draft()
        draft.apply(proposal(**FULL), user_message=COMPLETE_PHRASE)
        draft.apply(proposal(description='Observation utile.', technicien='Pierre Martin',
                             correspondances={'patrimoine': None, 'prestataire': None, 'technicien': {
                                 'source': 'Nom inventé', 'nature': 'exacte', 'candidats': ['Pierre Martin']}}),
                    user_message='Corriger le suivi. Observation utile.')
        result = draft.resolve(CATALOG)
        self.assertEqual(draft.names['description'], 'Observation utile.')
        self.assertIn('technicien', draft.reference_diagnostics)
        self.assertNotIn('technicien', result['verified'])
        self.assertIsNone(result['values'])
        self.assertNotIn('Nom inventé', ' '.join(result['issues']))
        draft.correct({'technicien': CATALOG['technicien'][1][0]}, {}, CATALOG)
        self.assertFalse(draft.reference_diagnostics)
        self.assertIsNotNone(draft.resolve(CATALOG)['values'])

    def test_geometry_flag_new_draft_corrections_and_explicit_renunciation(self):
        draft = Draft()
        draft.apply(proposal(**FULL), user_message=COMPLETE_PHRASE)
        text, catalog, fields = trial_proposal(3)
        draft.apply(proposal(**fields), user_message=text)
        self.assertFalse(draft.names.get('geometrie_demandee', False))
        draft.apply(proposal(geometrie_demandee=True), user_message="Je souhaite une étude près du point d'accès.")
        self.assertFalse(draft.names.get('geometrie_demandee', False))
        request = 'Je souhaite dessiner une géométrie propre.'
        draft.apply(proposal(geometrie_demandee=True, preuve_geometrie=request), user_message=request)
        self.assertTrue(draft.names['geometrie_demandee'])
        draft.apply(proposal(geometrie_demandee=False), user_message='Changer le technicien.')
        self.assertTrue(draft.names['geometrie_demandee'])
        renounce = 'Je renonce à la géométrie propre.'
        draft.apply(proposal(geometrie_demandee=False, preuve_geometrie=renounce), user_message=renounce)
        self.assertFalse(draft.names['geometrie_demandee'])
        self.assertFalse(Draft().names)

    def test_strict_evidence_contract_and_unchanged_corrections(self):
        self.assertEqual(set(SCHEMA['properties']), set(proposal()))
        self.assertEqual(set(SCHEMA['required']), set(proposal()))
        for fields in ({'preuve_dates': {'source': 'test', 'nature': 'unknown'}},
                       {'preuve_dates': {'source': 'test', 'nature': 'periode', 'extra': True}},
                       {'preuve_geometrie': {'source': 'test'}}):
            with self.assertRaises(ValueError):
                parse_response(response(**fields))
        draft = Draft()
        source = 'du 11 au 12 novembre 2026'
        draft.apply(proposal(**{**FULL, 'date_debut': '2026-11-11', 'date_fin': '2026-11-12',
                               'preuve_dates': {'source': source, 'nature': 'periode'}}), user_message=source)
        draft.apply(proposal(technicien='Pierre Martin'), user_message='Suivi Pierre Martin.')
        self.assertFalse(draft.end_date_needs_confirmation)
        self.assertEqual(draft.names['date_fin'], '2026-11-12')
        draft.apply(proposal(preuve_dates={'source': 'Fin incertaine.', 'nature': 'incertaine'}),
                    user_message='Fin incertaine.')
        self.assertTrue(draft.end_date_needs_confirmation)

    def test_explicit_verified_period_recovers_null_model_end_but_not_missing_or_conflicting_date(self):
        source = 'du 19 au 21 novembre 2026'
        proof = {'source': source, 'nature': 'periode'}
        draft = Draft()
        draft.apply(proposal(date_debut='2026-11-19', preuve_dates=proof), user_message=source)
        self.assertEqual(draft.names['date_fin'], '2026-11-21')
        self.assertFalse(draft.end_date_needs_confirmation)
        for message, end in (('Intervention le 19 novembre 2026. Statut Terminé.', None),
                             (source, '2026-11-22')):
            draft = Draft()
            draft.apply(proposal(date_debut='2026-11-19', date_fin=end, preuve_dates=proof), user_message=message)
            if end is None:
                self.assertNotIn('date_fin', draft.names)
            else:
                self.assertTrue(draft.end_date_needs_confirmation)


if __name__ == '__main__':
    unittest.main(verbosity=2)
