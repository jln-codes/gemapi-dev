"""C4 level A (~1s): immutable reference identities and explicit intent DTOs.

Real writes/calculation/authorization are proved by the PostgreSQL matrices;
these cases target malformed/ambiguous commands before SQL is opened.
"""
import unittest
from uuid import uuid4
from pydantic import ValidationError
from digues_webapp.reperage import ReperageUpdate,Calculation,Axis


class ReperageContractTest(unittest.TestCase):
    def test_intent_null_omission_and_reference_identity(self):
        revision='a'*64
        patch=ReperageUpdate(reference_revision=revision,axe=None)
        self.assertIn('axe',patch.model_fields_set)
        self.assertNotIn('systeme_reperage_defaut_id',patch.model_fields_set)
        for body in ({},{'axe':None,'inverser_axe':True},
                     {'actions':[{'action':'modifier_systeme','id':str(uuid4())}]},
                     {'actions':[{'action':'modifier_systeme','id':str(uuid4()),'troncon_id':str(uuid4())}]},
                     {'actions':[{'action':'modifier_liaison','id':str(uuid4()),'borne_id':str(uuid4())}]},
                     {'actions':[{'action':'modifier_liaison','id':str(uuid4()),'ordre_borne':-1}]},
                     {'actions':[{'action':'creer_borne','coordinates':[1,2]}]},
                     {'actions':[{'action':'creer_systeme','valid':1}]}):
            with self.subTest(body=body),self.assertRaises(ValidationError):
                ReperageUpdate(reference_revision=revision,**body)

    def test_implicit_distance_never_is_a_calibrated_pr_or_physical_borne(self):
        self.assertEqual(Calculation(mode='abscisse',abscisse_m=0).reference,'implicite')
        for fields in ({'mode':'pr','pr':10},{'mode':'bornage','borne_id':str(uuid4()),'offset_m':0},
                       {'mode':'xy','coordinates':[True,1],'srid':3950},
                       {'mode':'abscisse','abscisse_m':float('inf')},
                       {'mode':'abscisse','abscisse_m':1,'reference':'explicite','systeme_reperage_id':str(uuid4())}):
            with self.subTest(fields=fields),self.assertRaises(ValidationError):Calculation(**fields)
        with self.assertRaises(ValidationError):Axis(srid=3950,coordinates=[[0,0],[1,2,3]])
