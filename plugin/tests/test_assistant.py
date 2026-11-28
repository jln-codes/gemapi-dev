"""Simulated Mistral responses and local reference resolution; no API or database required."""

import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gemapi_app.assistant import (BOOL_FIELDS, TEXT_FIELDS, Draft, candidates, load_catalog,
                                     parse_date, parse_response, read_api_key, request_payload, type_candidates, configured_key_path, KEY_PATH_SETTING)


def uid(number):
    return f"00000000-0000-0000-0000-{number:012d}"


CATALOG = {
    "type_prestation": [(uid(1), "Débroussaillage", "Entretien")],
    "prestataire": [(uid(2), "Veolia", "")],
    "technicien": [(uid(3), "Jean Dubois", ""), (uid(4), "Pierre Martin", "")],
    "statut": [(uid(5), "Terminé", "")],
    "patrimoine": [(uid(6), "Rivière du Buissons", "AH du Nord")],
}
FULL = {"famille": "ah_ouvrage", "patrimoine": "Rivière du Buissons",
        "type_prestation": "débroussaillage", "prestataire": "Veolia", "technicien": "Jean Dubois",
        "statut": "Terminé", "date_debut": "11 novembre 2026", "date_fin": "11 novembre 2026"}
PHRASE = ("Je souhaite créer une prestation de débroussaillage réalisée sur l'ouvrage hydraulique "
          "Rivière du Buissons. Le statut est Terminé. La date de début est le 11 novembre 2026. "
          "Le prestataire était Veolia et le technicien en charge Jean Dubois.")

COMPLETE_PHRASE = PHRASE + " La date de fin est le 11 novembre 2026."


def proposal(**fields):
    return {**{key: None for key in TEXT_FIELDS + BOOL_FIELDS}, "correspondances": None,
            "preuve_dates": None, "preuve_geometrie": None, **fields}


def response(**fields):
    return json.dumps({"choices": [{"finish_reason": "stop", "message": {
        "content": json.dumps(proposal(**fields))}}]}).encode()


class AssistantTests(unittest.TestCase):
    def full(self, **changes):
        draft = Draft()
        draft.apply(parse_response(response(**{**FULL, **changes})), user_message=COMPLETE_PHRASE +
                    (" La catégorie est " + changes["categorie"] + "." if changes.get("categorie") else ""))
        return draft

    def test_key_path_migration_and_standard_priority(self):
        class Settings:
            def __init__(self, values): self.values = values
            def value(self, key, default): return self.values.get(key, default)
            def setValue(self, key, value): self.values[key] = value
        old = "sig_prestations/mistral_key_path"
        with tempfile.TemporaryDirectory() as folder:
            key = Path(folder) / "private.txt"
            key.write_text("test-key")
            settings = Settings({old: str(key)})
            self.assertEqual(configured_key_path(settings), str(key))
            self.assertEqual(settings.values[KEY_PATH_SETTING], str(key))
            self.assertEqual(configured_key_path(Settings({"sig_prestations_dystopic/mistral_key_path": str(key)})), str(key))
            settings.values[KEY_PATH_SETTING] = "explicit-choice.txt"
            self.assertEqual(configured_key_path(settings), "explicit-choice.txt")
            missing = Settings({old: str(key.with_name("removed-checkout.txt"))})
            self.assertEqual(configured_key_path(missing), "")
            self.assertNotIn(KEY_PATH_SETTING, missing.values)
            self.assertEqual(configured_key_path(Settings({})), "")
            with patch.dict(os.environ, {"GEMAPI_APP_MISTRAL_KEY_PATH": str(key),
                                        "DYSTOPIC_MISTRAL_KEY_PATH": "missing.txt"}):
                self.assertEqual(read_api_key(), "test-key")

    def test_phrase_request_and_simulated_extraction_all_fields(self):
        payload = request_payload(Draft(), COMPLETE_PHRASE, CATALOG)
        self.assertEqual(payload["model"], "mistral-small-latest")
        self.assertEqual(payload["response_format"]["type"], "json_schema")
        self.assertNotIn(uid(6), json.dumps(payload))
        result = self.full().resolve(CATALOG)
        self.assertEqual(result["values"]["date_debut"], "2026-11-11")
        self.assertEqual(result["values"]["type_prestation_id"], uid(1))
        self.assertEqual(result["values"]["prestataire_id"], uid(2))
        self.assertEqual(result["values"]["technicien_id"], uid(3))
        self.assertEqual(result["values"]["statut_id"], uid(5))
        self.assertEqual(result["associations"], [("ah_ouvrage", uid(6))])

    def test_reported_cabbalr_exchange_with_fictitious_references(self):
        # Reproduce the reported extraction defect, without copying CABBALR data.
        for wording in ("prestation de", "intervention de", "chantier de", "travaux de"):
            with self.subTest(wording=wording):
                message = PHRASE.replace("prestation de débroussaillage", wording + " débroussaillage")
                draft = Draft()
                draft.apply(parse_response(response(**{**FULL, "type_prestation": wording + " débroussaillage",
                                                        "date_fin": None})), user_message=message)
                result = draft.resolve(CATALOG)
                self.assertEqual(result["verified"]["type_prestation"][0], uid(1))
                self.assertEqual(result["verified"]["type_prestation"][1], "Débroussaillage")
                for field in ("patrimoine", "prestataire", "technicien", "statut"):
                    self.assertEqual(result["verified"][field][0], CATALOG[field][0][0])
                self.assertEqual(draft.names["date_debut"], "2026-11-11")
                self.assertIsNone(result["values"])
                self.assertIn("date de fin", " ".join(result["issues"]))
                draft.apply(proposal(date_fin="MEME_JOUR"), user_message="Le même jour.")
                self.assertEqual(draft.resolve(CATALOG)["values"]["type_prestation_id"], uid(1))

    def test_types_exact_priority_and_multiple_contained_types(self):
        catalog = copy.deepcopy(CATALOG)
        catalog["type_prestation"] += [(uid(8), "Débroussaillage manuel", "Entretien"),
                                       (uid(9), "Curage", "Entretien")]
        result = self.full(type_prestation="Débroussaillage manuel").resolve(catalog)
        self.assertEqual(result["values"]["type_prestation_id"], uid(8))
        for name, expected in (("travaux de débroussaillage manuel", {uid(1), uid(8)}),
                               ("intervention de débroussaillage ou curage", {uid(1), uid(9)})):
            with self.subTest(name=name):
                result = self.full(type_prestation=name).resolve(catalog)
                self.assertIsNone(result["values"])
                self.assertEqual({row[0] for row in result["pending"]["type_prestation"]}, expected)

    def test_type_homonyms_and_category_require_explicit_choice(self):
        catalog = copy.deepcopy(CATALOG)
        catalog["type_prestation"].append((uid(8), "Débroussaillage", "Autre catégorie"))
        draft = self.full(type_prestation="intervention de débroussaillage")
        self.assertEqual(len(draft.resolve(catalog)["pending"]["type_prestation"]), 2)
        draft.choices["type_prestation"] = uid(8)
        self.assertEqual(draft.resolve(catalog)["values"]["type_prestation_id"], uid(8))
        draft.apply(proposal(categorie="Entretien"), user_message="La catégorie est Entretien.")
        self.assertEqual(draft.resolve(catalog)["values"]["type_prestation_id"], uid(1))
        inferred = self.full(type_prestation="intervention de débroussaillage")
        inferred.apply(proposal(categorie="Entretien"), user_message="Créer cette intervention.")
        self.assertIsNone(inferred.resolve(catalog)["values"])
        inferred.apply(proposal(categorie="Entretien"), user_message="La catégorie est Entretien.")
        self.assertEqual(inferred.resolve(catalog)["values"]["type_prestation_id"], uid(1))

    def test_type_word_boundaries_and_uncertain_wording_never_auto_select(self):
        for wording in ("prédébroussaillage", "débroussaillages", "travaux sans débroussaillage",
                        "pas de débroussaillage", "débroussaillage ou autre chose", "intervention"):
            with self.subTest(wording=wording):
                self.assertIsNone(self.full(type_prestation=wording).resolve(CATALOG)["values"])
        options, exact = type_candidates("CHANTIER DE DÉBROUSSAILLAGE.", CATALOG["type_prestation"])
        self.assertTrue(exact)
        self.assertEqual(options, CATALOG["type_prestation"])

    def test_hallucinated_same_day_requires_local_confirmation(self):
        for extracted_end in ("2026-11-11", "MEME_JOUR", "2026-11-12"):
            with self.subTest(extracted_end=extracted_end):
                draft = Draft()
                draft.apply(proposal(**{**FULL, "date_fin": extracted_end}), user_message=PHRASE)
                self.assertTrue(draft.end_date_needs_confirmation)
                self.assertIsNone(draft.resolve(CATALOG)["values"])
                draft.correct({}, {'date_fin': draft.names['date_fin']}, CATALOG)
                self.assertIsNotNone(draft.resolve(CATALOG)["values"])
                draft.apply(proposal(technicien="Pierre Martin"))
                self.assertFalse(draft.end_date_needs_confirmation)
                # A changed start invalidates that confirmation if a new same-day end is proposed.
                draft.apply(proposal(date_debut="2026-11-12", date_fin="2026-11-12"),
                            user_message="La date d'intervention était le 12 novembre 2026.")
                self.assertTrue(draft.end_date_needs_confirmation)

    def test_end_date_confirmation_uses_user_words_not_model_assertions(self):
        draft = self.full(date_fin=None)
        draft.apply(proposal(date_fin="MEME_JOUR", message="Date de fin confirmée."),
                    user_message="Peut-être le même jour, ce n'est pas sûr.")
        self.assertTrue(draft.end_date_needs_confirmation)
        draft.apply(proposal(), user_message="Oui.")
        self.assertFalse(draft.end_date_needs_confirmation)
        for message in ("La date de fin est le 11 novembre 2026, mais elle est incertaine.",
                        "La date de fin est le 11 novembre 2026 ?"):
            draft = Draft()
            draft.apply(proposal(**FULL), user_message=message)
            self.assertTrue(draft.end_date_needs_confirmation)
        for message in ("Le même jour.", "11 novembre 2026"):
            draft = self.full(date_fin=None)
            draft.apply(proposal(date_fin="2026-11-11"), user_message=message)
            self.assertIsNotNone(draft.resolve(CATALOG)["values"])

    def test_french_dates(self):
        for value, expected in [("1er février 2026", "2026-02-01"), ("le 11 novembre 2026", "2026-11-11"),
                                ("02/08/2026", "2026-08-02"), ("31 août 2026", "2026-08-31")]:
            self.assertEqual(parse_date(value), expected)
        for value in ("demain", "11 novembre", "31 février 2026"):
            with self.assertRaises(ValueError):
                parse_date(value)

    def test_missing_end_date_then_same_day(self):
        draft = self.full(date_fin=None)
        self.assertIsNone(draft.resolve(CATALOG)["values"])
        self.assertIn("date de fin", " ".join(draft.resolve(CATALOG)["issues"]))
        draft.apply(parse_response(response(date_fin="MEME_JOUR")), user_message="Le même jour.")
        self.assertEqual(draft.resolve(CATALOG)["values"]["date_fin"], "2026-11-11")
        with self.assertRaises(ValueError):
            Draft().apply(proposal(date_fin="MEME_JOUR"))

    def test_correction_preserves_other_fields(self):
        draft = self.full()
        draft.apply(parse_response(response(technicien="Pierre Martin")))
        result = draft.resolve(CATALOG)
        self.assertEqual(result["values"]["technicien_id"], uid(4))
        self.assertEqual(result["values"]["date_debut"], "2026-11-11")
        draft.apply(proposal(date_fin=""))
        self.assertIsNone(draft.resolve(CATALOG)["values"])

    def test_unknown_reference_never_fabricates(self):
        result = self.full(prestataire="Prestataire imaginaire").resolve(CATALOG)
        self.assertIsNone(result["values"])
        self.assertIn("Introuvable", " ".join(result["issues"]))

    def name_catalog(self):
        catalog = copy.deepcopy(CATALOG)
        catalog['technicien'] = [(uid(30), 'Benoît Quentin', '')]
        catalog['patrimoine'] = [(uid(31), 'Talus ZEC Ourton', 'AH de test')]
        catalog['patrimoines'] = {'ah_ouvrage': catalog['patrimoine'], 'ah_stockage': [], 'vegetation': []}
        return catalog

    def named_draft(self, catalog, fields, matches):
        draft = Draft()
        context = json.loads(request_payload(draft, PHRASE, catalog)['messages'][1]['content'])['referentiels']
        metadata = {key: None for key in ('technicien', 'prestataire', 'patrimoine')}
        metadata.update(copy.deepcopy(matches))
        for key, match in metadata.items():
            if match is not None:
                match.setdefault('source', {'technicien': 'Benoît Cantin', 'prestataire': 'Véollia',
                                            'patrimoine': 'Talus ZEC Hurton'}[key])
        source = COMPLETE_PHRASE + ' ' + ' '.join(match['source'] for match in metadata.values() if match)
        if fields.get('patrimoine', '').endswith(' — AH de test'):
            source += ' dans AH de test'
        defaults = {**FULL, 'technicien': catalog['technicien'][0][1], 'patrimoine': catalog['patrimoine'][0][1]}
        draft.apply(parse_response(response(**{**defaults, **fields, 'correspondances': metadata})),
                    user_message=source, reference_context=context)
        return draft

    def test_mistral_complete_unique_names_are_accepted_after_local_membership_checks(self):
        catalog = self.name_catalog()
        for family in ('ah_ouvrage', 'ah_stockage', 'mobilier', 'cheminements', 'vegetation'):
            catalog['patrimoines'] = {key: [] for key in ('ah_ouvrage', 'ah_stockage', 'mobilier', 'cheminements', 'vegetation')}
            catalog['patrimoines'][family] = catalog['patrimoine']
            fields = {'famille': family, 'technicien': 'Benoît Quentin',
                      'patrimoine': 'Talus ZEC Ourton — AH de test', 'prestataire': 'Veolia'}
            matches = {key: {'nature': 'phonetique_complete', 'candidats': [fields[key]]}
                       for key in ('technicien', 'prestataire', 'patrimoine')}
            draft = self.named_draft(catalog, fields, matches)
            result = draft.resolve(catalog)
            self.assertIsNotNone(result['values'])
            self.assertEqual(result['values']['technicien_id'], uid(30))
            self.assertEqual(result['values']['prestataire_id'], uid(2))
            self.assertEqual(result['associations'], [(family, uid(31))])

    def test_approximate_and_ambiguous_model_matches_require_grouped_choice(self):
        catalog = self.name_catalog()
        catalog['technicien'].append((uid(32), 'Benoît Quantin', ''))
        for nature, names in (('approximative', ['Benoît Quentin']),
                              ('ambigue', ['Benoît Quantin', 'Benoît Quentin'])):
            draft = self.named_draft(catalog, {'technicien': 'Benoît Quentin', 'date_fin': None},
                                     {'technicien': {'nature': nature, 'candidats': names}})
            result = draft.resolve(catalog)
            self.assertIsNone(result['values'])
            self.assertEqual([row[1] for row in result['pending']['technicien']], names)
            self.assertNotIn('technicien', result['verified'])
            corrected = draft.correct({'technicien': uid(30)}, {'date_fin': '12/11/2026'}, catalog)
            self.assertEqual(corrected['values']['technicien_id'], uid(30))
            self.assertNotIn('technicien', draft.matches)

    def test_invented_candidates_and_missing_or_truncated_context_never_auto_validate(self):
        catalog = self.name_catalog()
        for names in (['Benoît Inventé'], ['Benoît Quentin', 'Benoît Inventé']):
            draft = self.named_draft(catalog, {'technicien': 'Benoît Quentin'},
                                     {'technicien': {'nature': 'phonetique_complete', 'candidats': names}})
            self.assertIsNone(draft.resolve(catalog)['values'])
        fields = {'technicien': 'Benoît Quentin'}
        match = {'technicien': {'nature': 'phonetique_complete', 'candidats': ['Benoît Quentin']}}
        draft = self.named_draft(catalog, fields, match)
        current = copy.deepcopy(catalog)
        current['technicien'].append((uid(33), 'Autre technicien', ''))
        self.assertIsNone(draft.resolve(current)['values'])
        catalog['technicien'].extend((uid(100 + i), f'Technicien supplémentaire {i}', '') for i in range(40))
        draft = self.named_draft(catalog, fields, match)
        self.assertIsNotNone(draft.resolve(catalog)['values'])  # All names are now supplied, never truncated.
        invalid = {key: None for key in ('technicien', 'prestataire', 'patrimoine')}
        invalid['technicien'] = {'source': 'Benoît Cantin', 'nature': 'phonetique_complete', 'candidats': [uid(30)]}
        with self.assertRaises(ValueError):
            draft.apply(proposal(correspondances=invalid))

    def test_parent_context_and_family_prevent_homonym_auto_selection(self):
        catalog = self.name_catalog()
        catalog['patrimoine'].append((uid(34), 'Talus ZEC Ourton', 'AH du Sud'))
        # Both parents are present in the request; a bare place name is insufficient.
        draft = self.named_draft(catalog, {'patrimoine': 'Talus ZEC Ourton'},
                                 {'patrimoine': {'nature': 'phonetique_complete', 'candidats': ['Talus ZEC Ourton']}})
        self.assertIsNone(draft.resolve(catalog)['values'])
        draft = self.named_draft(catalog, {'patrimoine': 'Talus ZEC Ourton — AH de test'},
                                 {'patrimoine': {'nature': 'phonetique_complete', 'candidats': ['Talus ZEC Ourton — AH de test']}})
        self.assertEqual(draft.resolve(catalog)['associations'], [('ah_ouvrage', uid(31))])
        wrong_family = copy.deepcopy(catalog)
        wrong_family['patrimoine'] = []
        self.assertIsNone(draft.resolve(wrong_family)['values'])

    def test_exact_and_normalized_sources_override_conflicting_phonetic_proposals(self):
        catalog = self.name_catalog()
        catalog['technicien'].append((uid(32), 'Benoît Cantin', ''))
        for source, expected in (('Benoît Cantin', uid(32)), ('BENOIT QUENTIN', uid(30))):
            suggested = 'Benoît Quentin' if expected == uid(32) else 'Benoît Cantin'
            draft = self.named_draft(catalog, {'technicien': suggested},
                    {'technicien': {'source': source, 'nature': 'phonetique_complete', 'candidats': [suggested]}})
            result = draft.resolve(catalog)
            self.assertEqual(result['values']['technicien_id'], expected)
            self.assertEqual(draft.names['technicien'], next(row[1] for row in catalog['technicien'] if row[0] == expected))

    def test_model_source_must_be_copied_and_partial_names_never_prove_complete_phonetics(self):
        catalog = self.name_catalog()
        for source in ('Benoît', 'Quentin'):
            draft = self.named_draft(catalog, {'technicien': 'Benoît Quentin'},
                    {'technicien': {'source': source, 'nature': 'phonetique_complete', 'candidats': ['Benoît Quentin']}})
            self.assertIsNone(draft.resolve(catalog)['values'])
        draft = self.named_draft(catalog, {'patrimoine': 'Talus ZEC Ourton — AH de test'},
                {'patrimoine': {'source': 'Hurton', 'nature': 'phonetique_complete',
                               'candidats': ['Talus ZEC Ourton — AH de test']}})
        self.assertIsNone(draft.resolve(catalog)['values'])
        before = copy.deepcopy(vars(draft))
        metadata = {'technicien': {'source': 'Benoît Inventé', 'nature': 'exacte', 'candidats': ['Benoît Quentin']},
                    'prestataire': None, 'patrimoine': None}
        draft.apply(proposal(technicien='Benoît Quentin', correspondances=metadata), user_message='Autre demande.')
        self.assertIn('technicien', draft.reference_diagnostics)
        self.assertNotIn('technicien', draft.resolve(catalog)['verified'])
        for field in ('date_debut', 'date_fin', 'patrimoine', 'description'):
            self.assertEqual(draft.names.get(field), before['names'].get(field))

    def test_exact_priority_also_applies_to_companies_and_all_patrimonial_families(self):
        for family in ('ah_ouvrage', 'ah_stockage', 'mobilier', 'cheminements', 'vegetation'):
            catalog = self.name_catalog()
            catalog['prestataire'].append((uid(70), 'Véollia', ''))
            catalog['patrimoine'].append((uid(71), 'Talus ZEC Hurton', 'AH de test'))
            catalog['patrimoines'] = {family: catalog['patrimoine']}
            draft = self.named_draft(catalog, {'famille': family, 'prestataire': 'Veolia',
                    'patrimoine': 'Talus ZEC Ourton — AH de test'}, {
                    'prestataire': {'source': 'Véollia', 'nature': 'phonetique_complete', 'candidats': ['Veolia']},
                    'patrimoine': {'source': 'Talus ZEC Hurton', 'nature': 'phonetique_complete',
                                  'candidats': ['Talus ZEC Ourton — AH de test']}})
            result = draft.resolve(catalog)
            self.assertEqual(result['values']['prestataire_id'], uid(70))
            self.assertEqual(result['associations'], [(family, uid(71))])

    def test_source_normalization_does_not_hide_accent_homonyms(self):
        catalog = self.name_catalog()
        catalog['technicien'].append((uid(72), 'Benoit Quentin', ''))
        draft = Draft()
        message = COMPLETE_PHRASE + ' Suivi : Benoit Quentin.'
        context = json.loads(request_payload(draft, message, catalog)['messages'][1]['content'])['referentiels']
        metadata = {'technicien': {'source': 'Benoît Quentin', 'nature': 'phonetique_complete',
                                  'candidats': ['Benoît Quentin']}, 'prestataire': None, 'patrimoine': None}
        draft.apply(proposal(**{**FULL, 'patrimoine': 'Talus ZEC Ourton', 'technicien': 'Benoît Quentin',
                                'correspondances': metadata}), user_message=message, reference_context=context)
        result = draft.resolve(catalog)
        self.assertNotIn('technicien', result['verified'])
        self.assertEqual({row[0] for row in result['pending']['technicien']}, {uid(30), uid(72)})

    def test_interpretation_catalog_loads_all_allowed_families_read_only_and_without_ids_in_payload(self):
        queries = []
        class Connection:
            def executeSql(self, query):
                queries.append(query)
                return [(uid(1), 'Nom fictif', 'Parent fictif')]
        catalog = load_catalog(Connection(), None, for_interpretation=True)
        self.assertEqual(set(catalog['patrimoines']), {'ah_ouvrage', 'ah_stockage', 'mobilier', 'cheminements', 'vegetation'})
        self.assertEqual(len(queries), 9)
        self.assertTrue(all(query.startswith('SELECT ') for query in queries))
        payload = request_payload(Draft(), 'Une intervention sur Hurton.', catalog)
        context = json.loads(payload['messages'][1]['content'])['referentiels']
        self.assertEqual({row['famille'] for row in context['patrimoines']}, set(catalog['patrimoines']))
        self.assertNotIn(uid(1), json.dumps(payload))

    def test_grouped_review_is_atomic_and_rejects_unknown_or_expired_identifiers(self):
        draft = self.full(technicien='Benoît Cantin', date_fin=None, description='Accès difficile.')
        before = copy.deepcopy(vars(draft))
        for references, dates in (({'technicien': uid(999)}, {'date_fin': '12/11/2026'}),
                                  ({'technicien': uid(4)}, {'date_fin': ''}),
                                  ({'technicien': uid(4)}, {'date_fin': '10/11/2026'}),
                                  ({'technicien': uid(4)}, {'date_fin': '31 février 2026'}),
                                  ({'technicien': uid(4)}, {'date_fin': '11 novembre'}),
                                  ({'uuid': uid(4)}, {'date_fin': '12/11/2026'})):
            with self.subTest(references=references, dates=dates), self.assertRaises(ValueError):
                draft.correct(references, dates, CATALOG)
            self.assertEqual(vars(draft), before)
        current = copy.deepcopy(CATALOG)
        current['technicien'] = []
        with self.assertRaises(ValueError):
            draft.correct({'technicien': uid(4)}, {'date_fin': '12/11/2026'}, current)
        self.assertEqual(vars(draft), before)

    def test_grouped_review_handles_multiple_missing_fields_and_end_confirmation(self):
        draft = self.full(type_prestation='', prestataire='', technicien='', statut='',
                          date_debut='', date_fin='', description='Ronces devant la buse.')
        result = draft.correct({'type_prestation': uid(1), 'prestataire': uid(2),
                                'technicien': uid(4), 'statut': uid(5)},
                               {'date_debut': '11/11/2026', 'date_fin': '12/11/2026'}, CATALOG)
        self.assertEqual(result['values']['description'], 'Ronces devant la buse.')
        self.assertEqual(result['values']['date_fin'], '2026-11-12')
        uncertain = Draft()
        uncertain.apply(proposal(**FULL), user_message=PHRASE)
        self.assertTrue(uncertain.end_date_needs_confirmation)
        self.assertIsNone(uncertain.resolve(CATALOG)['values'])
        uncertain.correct({}, {'date_fin': '11/11/2026'}, CATALOG)
        self.assertFalse(uncertain.end_date_needs_confirmation)
        self.assertIsNotNone(uncertain.resolve(CATALOG)['values'])

    def test_homonyms_require_explicit_choice(self):
        catalog = copy.deepcopy(CATALOG)
        catalog["patrimoine"].append((uid(7), "Rivière du Buissons", "AH du Sud"))
        draft = self.full()
        result = draft.resolve(catalog)
        self.assertEqual(len(result["pending"]["patrimoine"]), 2)
        self.assertIsNone(result["values"])
        draft.choices["patrimoine"] = uid(7)
        self.assertEqual(draft.resolve(catalog)["associations"], [("ah_ouvrage", uid(7))])
        draft.apply(proposal(patrimoine="Un autre ouvrage"))
        self.assertNotIn("patrimoine", draft.choices)

    def test_site_homonyms_keep_the_parent_and_require_a_local_choice(self):
        catalog = copy.deepcopy(CATALOG)
        catalog["patrimoine"] = [(uid(71), "Haie", "Prairie"), (uid(72), "Haie", "Verger")]
        draft = self.full(famille="vegetation", patrimoine="Haie")
        result = draft.resolve(catalog)
        self.assertIsNone(result["values"])
        self.assertEqual({row[2] for row in result["pending"]["patrimoine"]}, {"Prairie", "Verger"})
        draft.choices["patrimoine"] = uid(72)
        self.assertEqual(draft.resolve(catalog)["associations"], [("vegetation", uid(72))])

    def test_approximate_match_requires_confirmation(self):
        self.assertIn("patrimoine", self.full(patrimoine="rivière du Buisson").resolve(CATALOG)["pending"])
        draft = self.full(prestataire="Veollia")
        self.assertIn("prestataire", draft.resolve(CATALOG)["pending"])
        self.assertIsNone(draft.resolve(CATALOG)["values"])
        draft.choices["prestataire"] = uid(2)
        self.assertIsNotNone(draft.resolve(CATALOG)["values"])

    def test_case_accents_category_and_single_object(self):
        self.assertIsNotNone(self.full(patrimoine="RIVIERE DU BUISSONS", categorie="entretien").resolve(CATALOG)["values"])
        self.assertIsNone(self.full(categorie="Construction").resolve(CATALOG)["values"])
        for family in ("cours_eau", "digues", "ah_sections", "sites_terrestres", "inconnue"):
            with self.subTest(family=family):
                self.assertIsNone(self.full(famille=family).resolve(CATALOG)["values"])
        for family in ("ah_ouvrage", "ah_stockage", "mobilier", "cheminements", "vegetation"):
            self.assertEqual(self.full(famille=family).resolve(CATALOG)["associations"], [(family, uid(6))])
        self.assertIsNone(self.full(plusieurs_objets=True).resolve(CATALOG)["values"])
        drawing = self.full()
        text = 'Je souhaite dessiner une géométrie propre.'
        drawing.apply(proposal(geometrie_demandee=True, preuve_geometrie=text), user_message=text)
        self.assertIsNone(drawing.resolve(CATALOG)["values"])

    def test_uuid_injection_rejected_and_local_choice_membership(self):
        for fields in ({"patrimoine": uid(999)}, {"prestataire_id": uid(999)}, {"famille": "gemapi.ah_ouvrage; DROP TABLE"}):
            with self.assertRaises(ValueError):
                self.full(**fields)
        draft = self.full(prestataire="Veollia")
        draft.choices["prestataire"] = uid(999)
        self.assertIsNone(draft.resolve(CATALOG)["values"])

    def test_invalid_patch_is_atomic(self):
        draft = self.full()
        old = dict(draft.names)
        with self.assertRaises(ValueError):
            draft.apply(proposal(technicien="Pierre Martin", date_fin="31 février 2026"))
        self.assertEqual(draft.names, old)

    def test_business_validation_unchanged(self):
        for changes in ({"date_fin": "2026-11-10"}, {"quantite": "NaN"}):
            self.assertIsNone(self.full(**changes).resolve(CATALOG)["values"])

    def test_only_reference_labels_and_draft_are_sent_to_model(self):
        draft = self.full()
        draft.choices["patrimoine"] = uid(6)
        payload = request_payload(draft, "Le même jour.", CATALOG)
        serialized = json.dumps(payload)
        self.assertNotIn(uid(6), serialized)
        self.assertNotIn("AH du Nord", serialized)
        references = json.loads(payload["messages"][1]["content"])["referentiels"]
        self.assertEqual(references["types_prestations"], [{"libelle": "Débroussaillage", "categorie": "Entretien"}])
        self.assertIn("Pierre Martin", references["techniciens"])
        self.assertNotIn("patrimoine", references)
        for rows in CATALOG.values():
            for row in rows:
                self.assertNotIn(row[0], serialized)

    def test_all_reference_names_complete_and_current(self):
        catalog = copy.deepcopy(CATALOG)
        catalog["type_prestation"] = [(uid(100 + i), "Type actif " + str(i), "Catégorie") for i in range(40)]
        catalog["prestataire"] = [(uid(200 + i), "Entreprise " + str(i), "") for i in range(31)]
        catalog["technicien"] = [(uid(300), "Nom " * 800, "")]
        payload = request_payload(Draft(), "Créer une intervention.", catalog)
        refs = json.loads(payload["messages"][1]["content"])["referentiels"]
        self.assertEqual(len(refs["types_prestations"]), 40)
        self.assertEqual(len(refs["prestataires"]), 31)
        self.assertEqual(len(refs["techniciens"]), 1)
        self.assertTrue(refs["complets"]["prestataire"])
        self.assertTrue(refs["complets"]["technicien"])
        self.assertEqual(refs["statuts_prestations"], ["Terminé"])
        catalog["type_prestation"] = [(uid(400), "Nouveau type de la connexion", "Autre catégorie")]
        current = json.loads(request_payload(Draft(), "Corriger le type.", catalog)["messages"][1]["content"])
        self.assertEqual(current["referentiels"]["types_prestations"][0]["libelle"], "Nouveau type de la connexion")

    def test_normalized_homonyms_remain_ambiguous_for_every_spelling(self):
        rows = [(uid(1), "Élagage", ""), (uid(2), "Elagage", "")]
        for spelling in ('Élagage', 'Elagage', 'ELAGAGE'):
            self.assertEqual(candidates(spelling, rows), (rows, True))
            self.assertEqual(type_candidates(spelling, rows), (rows, True))

    def test_outer_articles_and_number_spacing_do_not_change_unique_identity(self):
        rows = [(uid(1), 'Régie des Rives', ''), (uid(2), 'Remblai2 Test', 'Bassin Est')]
        for source, expected in [('la régie des rives', rows[0]), ('REGIE DES RIVES', rows[0]),
                                 ('Remblai 2, Test', rows[1]), ('le REMBLAI2 TEST', rows[1])]:
            with self.subTest(source=source):
                self.assertEqual(candidates(source, rows), ([expected], True))
        # A canonical article is significant; keep both readings when they exist.
        rows = [(uid(1), 'La Rive', ''), (uid(2), 'Rive', '')]
        self.assertEqual(candidates('La Rive', rows), (rows, True))
        self.assertEqual(candidates('Rive', rows), ([rows[1]], True))

    def test_explicitly_excluded_reference_never_wins_the_direct_path(self):
        for metadata in (None, {'prestataire': {'source': 'Veolia', 'nature': 'exacte', 'candidats': ['Veolia']},
                                'technicien': None, 'patrimoine': None}):
            draft = Draft()
            draft.apply(proposal(**{**FULL, 'correspondances': metadata}),
                        user_message=COMPLETE_PHRASE + ' Ne pas sélectionner Veolia.',
                        reference_context=json.loads(request_payload(Draft(), COMPLETE_PHRASE, CATALOG)
                                                     ['messages'][1]['content'])['referentiels'])
            result = draft.resolve(CATALOG)
            self.assertNotIn('prestataire', result['verified'])
            self.assertIn('prestataire', draft.reference_diagnostics)
            self.assertEqual(draft.names['date_debut'], '2026-11-11')
            self.assertIn('technicien', result['verified'])

    def test_independent_model_metadata_preserves_human_homonym_choice(self):
        catalog = copy.deepcopy(CATALOG)
        catalog['patrimoine'].append((uid(7), 'Rivière du Buissons', 'AH du Sud'))
        catalog['patrimoines'] = {'ah_ouvrage': catalog['patrimoine']}
        draft = self.full()
        draft.correct({'patrimoine': uid(7)}, {}, catalog)
        selected = draft.names['patrimoine']
        metadata = {'patrimoine': {'source': selected, 'nature': 'exacte', 'candidats': [selected]},
                    'prestataire': None, 'technicien': None}
        draft.apply(proposal(description='Accès difficile.', patrimoine=selected, correspondances=metadata),
                    user_message='Ajouter : accès difficile.',
                    reference_context=json.loads(request_payload(draft, 'Ajouter : accès difficile.', catalog)
                                                 ['messages'][1]['content'])['referentiels'])
        self.assertEqual(draft.choices['patrimoine'], uid(7))
        self.assertEqual(draft.resolve(catalog)['associations'], [('ah_ouvrage', uid(7))])

    def test_spoken_dates_and_ambiguities(self):
        for value, expected in (("onze novembre deux mille vingt-six", "2026-11-11"),
                                ("onze zéro neuf 2026", "2026-09-11"),
                                ("vingt et un zéro neuf deux mille vingt-six", "2026-09-21"),
                                ("premier février deux mille vingt-six", "2026-02-01"),
                                ("trente et un décembre mille neuf cent quatre-vingt-dix-neuf", "1999-12-31")):
            with self.subTest(value=value):
                self.assertEqual(parse_date(value), expected)
        for value in ("onze novembre vingt-six", "11 ou 12 novembre 2026", "onze zéro neuf", "demain",
                      "trente et un février deux mille vingt-six"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_date(value)

    def test_read_only_queries_and_fixed_identifiers(self):
        class Connection:
            def executeSql(self, query):
                self.query = query
                self_test.assertTrue(query.startswith("SELECT "))
                self_test.assertNotIn("injection", query)
                return [(uid(1), "nom", "contexte")]
        self_test = self
        connection = Connection()
        self.assertEqual(len(load_catalog(connection, "ah_ouvrage")), 5)
        self.assertEqual(len(load_catalog(connection, "injection")), 4)

    def test_missing_empty_key_and_explicit_local_path(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "absente"):
                read_api_key(repository_root=root)
            local = root / "MISTRAL_API_KEY.txt"
            local.write_text("", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_api_key(repository_root=root)
            local.write_text("fake-test-key\n", encoding="utf-8")
            self.assertEqual(read_api_key(repository_root=root), "fake-test-key")
            self.assertEqual(read_api_key(str(local)), "fake-test-key")
            with patch.dict(os.environ, {"DYSTOPIC_MISTRAL_KEY_PATH": str(local)}):
                self.assertEqual(read_api_key(repository_root=root / "installed"), "fake-test-key")
            for content in ('MISTRAL_API_KEY=fake-test-key', 'MISTRAL_API_KEY="fake-test-key"',
                            "export MISTRAL_API_KEY='fake-test-key'", '"fake-test-key"'):
                local.write_text(content, encoding='utf-8')
                self.assertEqual(read_api_key(str(local)), "fake-test-key")

    def test_invalid_response_and_message_limits(self):
        for raw in (b"error", b"{}", b'{"choices":null}', b'{"choices":[{"finish_reason":"length"}]}'):
            with self.assertRaisesRegex(ValueError, "invalide"):
                parse_response(raw)
        for text in ("", "a" * 8001):
            with self.assertRaises(ValueError):
                request_payload(Draft(), text, CATALOG)


if __name__ == "__main__":
    unittest.main()
