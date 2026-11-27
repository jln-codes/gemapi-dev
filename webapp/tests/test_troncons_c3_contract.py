"""C3 level A: DTO presence semantics and direct JS consumers of receipts.

No SQL assertion is mocked here; real typed references/concurrency are exercised
by the companion PostgreSQL matrix. Node tests observe version snapshots and
invalidation/transport behavior, without changing editor layout or workflows.
"""
from pathlib import Path
import shutil
import subprocess
import unittest
from pydantic import ValidationError
from digues_webapp.models import TronconCreate,TronconUpdate

ROOT=Path(__file__).resolve().parents[2]


class CanonicalTronconContractTest(unittest.TestCase):
    def node(self,program):
        binary=shutil.which('node')
        if binary is None:self.skipTest('Node.js unavailable')
        subprocess.run([binary,'-e',program],cwd=ROOT,check=True,capture_output=True,text=True)

    def test_omitted_null_and_explicit_text_are_distinct_and_axis_is_not_editable(self):
        self.assertEqual(TronconUpdate(commentaire='').model_dump(exclude_unset=True),{'commentaire':''})
        self.assertEqual(TronconUpdate(geometry=None).model_dump(exclude_unset=True),{'geometry':None})
        self.assertNotIn('geometry',TronconUpdate(designation=' Test ').model_dump(exclude_unset=True))
        self.assertEqual(TronconCreate(type_physique_id='BERGE',libelle=' ').libelle,' ')
        for fields in ({},{'valid':None},{'type_physique_id':None},{'version':2},{'geometry_reperage':None},{'created_by':None}):
            with self.subTest(fields=fields),self.assertRaises(ValidationError):TronconUpdate(**fields)

    def test_composition_sends_observed_versions_for_added_and_removed_troncons(self):
        self.node(r'''
const assert=require('node:assert/strict');
const Form=require('./webapp/frontend/js/amenagement-hydraulique-form.js');
const form={elements:Object.fromEntries(['designation','libelle','commentaire','date_debut','date_fin','valid','troncon_ids','zone_ids','desordre_ids'].map(k=>[k,{value:'',selected:[]}]))};
const selector={fill(select,rows,selected){select.selected=[...selected]},fillDisorders(){},values(select){return select.selected},assignChanged(result){return result}};
const snapshot={properties:{id:'ah',troncon_ids:['old'],troncons:[{id:'old',version:3}]}};
const choices={troncons:[{id:'old',version:7},{id:'new',version:2}],zones:[]};
Form.fill(form,snapshot,choices,null,selector);
choices.troncons[1].version=99;
form.elements.troncon_ids.selected=['new'];
const payload=Form.payload(form,null,selector,snapshot);
assert.deepEqual(payload.troncon_ids,['new']);
assert.deepEqual(payload.troncon_versions,{old:3,new:2});
''')

    def test_related_receipts_publish_once_and_retry_failed_invalidation(self):
        self.node(r'''
const assert=require('node:assert/strict');
const Mutation=require('./webapp/frontend/js/resource-mutation.js');
const sent=[],calls=[];
const bus={subscribe(){return()=>{}},publish(type,payload){sent.push(payload)}};
const controller=Mutation.createController({bus,localInstanceId:'page',localClientId:'browser'});
let fail=true;
controller.registerInvalidator('open-detail',async event=>{
 calls.push(event.resource);
 if(event.resource==='troncons'&&fail)throw Error('temporary consumer failure');
});
const child={schemaVersion:1,eventId:'root-event',originClientId:'browser',originInstanceId:'page',resource:'troncons',action:'update',ids:['root'],versions:{root:2},occurredAt:1};
const event={...child,eventId:'ah-event',resource:'amenagements-hydrauliques',ids:['ah'],versions:{ah:2},relatedMutations:[child]};
(async()=>{
 await controller.publishMutation(event,{applyLocally:true});
 await new Promise(resolve=>setImmediate(resolve));
 assert.deepEqual(sent.map(e=>e.eventId),['ah-event','root-event']);
 fail=false;
 await controller.publishMutation(event,{applyLocally:true});
 assert.equal(calls.filter(r=>r==='troncons').length,2);
 assert.equal(sent.length,2);
 const second={...child,eventId:'new-root-event',versions:{root:3}};
 await controller.publishMutation({...event,relatedMutations:[second]},{applyLocally:true});
 assert.equal(sent.length,3);
 assert.equal(sent[2].eventId,'new-root-event');
 controller.close();
})().catch(error=>{console.error(error);process.exitCode=1});
''')
