"""Contrats F4 Cheminement : DTO, relations et câblage frontend."""
import unittest
from pathlib import Path
from uuid import uuid4
from pydantic import ValidationError
from digues_webapp.models import CheminementCreate, CheminementUpdate
from digues_webapp.app import create_app

ROOT = Path(__file__).resolve().parents[1] / 'frontend'

class CheminementContractTest(unittest.TestCase):
    def test_create_and_update_validation(self):
        a, b = uuid4(), uuid4()
        row = CheminementCreate.model_validate({
            'type_cheminement_id': ' RefTypeCheminement:1 ',
            'designation': '', 'valid': False,
            'troncon_ids': [a,b], 'desordre_ids': [b],
            'geometry': {'type':'LineString','coordinates':[[2,48],[3,49]]},
        })
        self.assertEqual(row.type_cheminement_id, 'RefTypeCheminement:1')
        self.assertIsNone(row.designation)
        self.assertFalse(row.valid)
        polygon = {'type':'Polygon','coordinates':[[[2,48],[3,48],[3,49],[2,48]]]}
        access = CheminementCreate.model_validate({
            'type_cheminement_id': 'CAC', 'geometry': polygon,
        })
        self.assertEqual(access.geometry.type, 'Polygon')
        for payload in ({'type_cheminement_id':''},
                        {'type_cheminement_id':'T','troncon_ids':[a,a]},
                        {'type_cheminement_id':'T','usage_source_id':'V2'},
                        {'type_cheminement_id':'T','geometry':polygon},
                        {'type_cheminement_id':'T','geometry':{'type':'MultiPoint','coordinates':[[2,48]]}}):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                CheminementCreate.model_validate(payload)
        for payload in ({'geometry':None},{'troncon_ids':None},{'desordre_ids':[b,b]},
                        {'type_cheminement_id':None},{'valid':None},
                        {'type_cheminement_id':'T','geometry':polygon},{}):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                CheminementUpdate.model_validate(payload)
        self.assertEqual(CheminementUpdate(troncon_ids=[]).troncon_ids, [])
        self.assertEqual(CheminementUpdate(geometry=polygon).geometry.type, 'Polygon')

    def test_routes_frontend_and_reuse(self):
        app = create_app()
        paths = {(r.path, tuple(sorted(r.methods))) for r in app.routes if hasattr(r,'methods')}
        for method,path in (('GET','/api/cheminements'),
                            ('GET','/api/desordres/search'),
                            ('GET','/api/desordres/selection'),
                            ('POST','/api/cheminements'),
                            ('GET','/api/cheminements/{identifier}'),
                            ('PUT','/api/cheminements/{identifier}'),
                            ('GET','/api/cheminements/{identifier}/observations')):
            self.assertTrue(any(p==path and method in methods for p,methods in paths))
            contextual = '/api/bases/{business_database_id}' + path.removeprefix('/api')
            self.assertTrue(any(p==contextual and method in methods for p,methods in paths))
        editor = (ROOT/'js/cheminement-editor.js').read_text()
        form = (ROOT/'js/cheminement-form.js').read_text()
        for term in ('entityEditor.create(', 'entityEditor.requireGeometry(',
                     'allowedTypes: allowedGeometryTypes(typeSelect.value)',
                     'geometryTypes: ["LineString", "Polygon"]',
                     'Operation-Id', 'concurrency.headers('):
            self.assertIn(term, editor)
        for term in ('relationSelector.fill(form.elements.troncon_ids',
                     'relationSelector.attachSearch(form.elements.desordre_ids',
                     'RelationSelector.loadSelected', 'referenceData.load(',
                     'troncon_ids', 'desordre_ids'):
            self.assertIn(term, form)
        self.assertIn('cheminement-editor.js',(ROOT/'map.html').read_text())
        self.assertIn('cheminement-editor.js',(ROOT/'service-worker.js').read_text())
