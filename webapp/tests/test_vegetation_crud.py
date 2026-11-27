"""F5 : contrats DTO, routes et consommation du socle CRUD."""
import unittest
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4
from pydantic import ValidationError
from digues_webapp.models import VegetationCreate, VegetationUpdate
from digues_webapp.app import create_app

FRONT = Path(__file__).resolve().parents[1] / 'frontend'

class VegetationCrudContractTest(unittest.TestCase):
    def test_canonical_family_fields_relations_and_geometry(self):
        for geometry in ({'type':'Point','coordinates':[2,48]},
                         {'type':'LineString','coordinates':[[2,48],[3,49]]},
                         {'type':'Polygon','coordinates':[[[2,48],[3,48],[3,49],[2,48]]]}):
            obj=VegetationCreate.model_validate({'famille_vegetation':'ARBRES',
                'disposition':'ISOLE','sous_type':'ARBRE_SUR_PIED',
                'valid':False,'designation':'',
                'etat_sanitaire_id':'', 'geometry':geometry})
            self.assertEqual(obj.famille_vegetation,'ARBRES')
            self.assertIsNone(obj.designation)
            self.assertIsNone(obj.etat_sanitaire_id)
            self.assertFalse(obj.valid)
        complete=VegetationCreate.model_validate({
            'famille_vegetation':'BUISSONS_ARBUSTES','disposition':'LIGNE',
            'invasif':True,'densite':2.5,'hauteur_m':3.0,
            'designation':' Haie ', 'commentaire':' Suivi ',
            'date_debut':'2026-09-29','etat_sanitaire_id':'E1',
            'classe_hauteur_id':'H1','classe_diametre_id':'D1',
            'espece_id':' E1 ','problematique_ids':['DAN'],'valid':True,
            'geometry': {'type':'Point','coordinates':[2,48]},
        })
        self.assertEqual(complete.designation,'Haie')
        self.assertEqual(complete.commentaire,'Suivi')
        self.assertEqual((complete.etat_sanitaire_id,complete.classe_hauteur_id,
                          complete.classe_diametre_id),('E1','H1','D1'))
        self.assertEqual((complete.invasif,complete.densite,complete.hauteur_m,
                          complete.espece_id),(True,2.5,3.0,'E1'))
        self.assertEqual(complete.problematique_ids,['DAN'])
        self.assertEqual(complete.geometry.type, 'Point')
        related=uuid4()
        self.assertEqual(VegetationCreate(famille_vegetation='HERBACEES',
                         disposition='SURFACE',troncon_ids=[related],
                         geometry={'type':'Point','coordinates':[2,48]}).troncon_ids,
                         [related])
        base={'famille_vegetation':'HERBACEES','disposition':'SURFACE',
              'geometry':{'type':'Point','coordinates':[2,48]}}
        for payload in ({}, {'famille_vegetation':'INVASIVES'},
                        {'parcelle_gestion_id':str(uuid4())}, {'plan_id':str(uuid4())},
                        {'famille_vegetation':'ARBRES'},
                        {'sous_type':'SOUCHE'},
                        {'geometry':{'type':'MultiPoint','coordinates':[[2,48]]}},
                        {'geometry':{'type':'Point','coordinates':[200,48]}},
                        {'source_class':'ArbreVegetation'},
                        {'troncon_ids':[related,related]},
                        {'problematique_ids':['DAN','DAN']},
                        {'problematique_ids':['DAN',' DAN ']},
                        {'problematique_ids':[' ']},
                        {'troncon_ids':[uuid4(),uuid4()],
                         'amenagement_hydraulique_ids':['bad']}):
            with self.subTest(payload=payload),self.assertRaises(ValidationError):
                VegetationCreate.model_validate(payload if not payload else base | payload)
        for patch in ({}, {'geometry':None},{'parcelle_gestion_id':None},
                      {'famille_vegetation':None},{'disposition':None},
                      {'invasif':None},{'valid':None}):
            with self.subTest(patch=patch),self.assertRaises(ValidationError):
                VegetationUpdate.model_validate(patch)
        self.assertIsNone(VegetationUpdate(etat_sanitaire_id='').etat_sanitaire_id)
        self.assertIsNone(VegetationUpdate(commentaire='   ').commentaire)
        self.assertFalse(VegetationUpdate(valid=False).valid)
        self.assertIsNone(VegetationUpdate(classe_hauteur_id=None).classe_hauteur_id)

    def test_routes_and_shell(self):
        app=create_app()
        paths={(r.path,method) for r in app.routes if hasattr(r,'methods') for method in r.methods}
        for path,method in (('/api/vegetation','GET'),('/api/vegetation','POST'),
            ('/api/vegetation/{identifier}','GET'),('/api/vegetation/{identifier}','PUT'),
            ('/api/vegetation/relations/options','GET'),
            ('/api/vegetation/{identifier}/observations','GET')):
            self.assertIn((path,method),paths)
            contextual='/api/bases/{business_database_id}'+path.removeprefix('/api')
            self.assertIn((contextual,method),paths)
        editor=(FRONT/'js/vegetation-editor.js').read_text()
        form=(FRONT/'js/vegetation-form.js').read_text()
        for fragment in ('entityEditor.create(', 'entityEditor.requireGeometry(',
                         'concurrency.headers(', 'Operation-Id',
                         'resource: "vegetation"'):
            self.assertIn(fragment,editor)
        for fragment in ('relationSelector.fill(form.elements[name]', 'referenceData.load(',
                         'troncon_ids', 'amenagement_hydraulique_ids'):
            self.assertIn(fragment,form)
        self.assertNotIn('name="parcelle_gestion_id"',form)
        self.assertNotIn('name="problematique_id"',form)
        # The common compositor now renders these controls. Check the actual
        # descriptor rather than obsolete HTML strings in the adapter source.
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node is required for the shared form contract')
        subprocess.run([node, '-e', '''
const assert=require('node:assert/strict');
const form=require('./webapp/frontend/js/vegetation-form.js');
const fields=Object.fromEntries(Object.values(form.composition().fields).flat().map(f=>[f.name,f]));
assert.equal(fields.problematique_ids.multiple,true);
assert.equal(fields.famille_vegetation.required,true);
assert.equal(fields.invasif.kind,'checkbox');
'''],cwd=FRONT.parents[1],check=True)
        self.assertIn('vegetation-editor.js',(FRONT/'map.html').read_text())
        self.assertIn('vegetation-editor.js',(FRONT/'service-worker.js').read_text())
