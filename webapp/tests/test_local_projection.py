import subprocess
import unittest


class LocalProjectionContractTest(unittest.TestCase):
    def test_validation_and_browser_refusal_are_controlled(self):
        subprocess.run(["node", "-e", r'''
const assert = require('node:assert/strict');
const P = require('./webapp/frontend/js/local-projection.js');
(async () => {
  // FR-1 niveau A : catalogue fermé, y compris dans une intention Photo.
  const codes=[2154,3942,3943,3944,3945,3946,3947,3948,3949,3950];
  for(const srid of codes) {
    const crs={authority:'EPSG',srid,code:`EPSG:${srid}`};
    assert.equal(P.validateBusinessCRS(crs),crs);
    assert.equal(P.canonicalPhotoLocalisation({mode:'point',srid,coordinates:[1700000,1200000]}).srid,srid);
    assert.throws(()=>P.validateBusinessCRS({...crs,code:'EPSG:4326'}));
  }
  for(const srid of [4326,3857,3941,3951,5490,9794,9842,'3942',true]) {
    assert.throws(()=>P.validateBusinessCRS({authority:'EPSG',srid,code:`EPSG:${srid}`}));
    if(srid!==4326)assert.throws(()=>P.canonicalPhotoLocalisation({mode:'point',srid,coordinates:[1,2]}));
  }
  const row={resource:'desordres',id:'D42',serverVersion:1,payload:{properties:{id:'D42',version:1}}};
  P.validateRecord(row);
  for(const resource of ['territoires-administratifs','meteo','vigicrues','photos']) {
    assert.throws(()=>P.validateRecord({...row,resource}));
  }
  assert.throws(()=>P.validateRecord({...row,serverVersion:2}));
  assert.throws(()=>P.validateRecord({...row,serverVersion:0}));
  assert.ok(P.validCursor('9223372036854775807'));
  assert.ok(!P.validCursor('9223372036854775808'));
  assert.ok(!P.validCursor('01'));
  const policy={contract_version:2,parameters_version:1,profile:'standard',policy_version:4,
    effective_parameters:{max_long_edge_px:1600,output_mime_type:'image/jpeg',encoder_quality:0.78}};
  assert.deepEqual(P.canonicalPhotoQualityPolicy(policy),policy);
  const metadata=P.photoQualityPolicyMetadata(policy,1234);
  assert.deepEqual(metadata,{key:'photoQualityPolicy',contractVersion:2,profile:'standard',policyVersion:4,
    parametersVersion:1,effectiveParameters:policy.effective_parameters,fetchedAt:1234});
  assert.deepEqual(P.photoQualityPolicyFromMetadata(metadata),{policy,fetchedAt:1234});
  const current={...policy,parameters_version:2,policy_version:5,max_file_size_bytes:1048576,effective_parameters:{...policy.effective_parameters,
    min_long_edge_px:960,min_encoder_quality:0.46,encoder_quality_step:0.08,dimension_scale_step:0.8,
    max_file_size_bytes:1048576}};
  assert.deepEqual(P.canonicalPhotoQualityPolicy(current),current);
  const cached=P.photoQualityPolicyMetadata(current,2345);
  assert.equal(cached.effectiveParameters.max_file_size_bytes,1048576);
  assert.deepEqual(P.photoQualityPolicyFromMetadata(cached),{policy:current,fetchedAt:2345});
  for(const invalid of [{...policy,profile:'inconnu'},{...policy,policy_version:0},
    {...policy,effective_parameters:{...policy.effective_parameters,max_long_edge_px:200}},
    {...policy,effective_parameters:{...policy.effective_parameters,encoder_quality:2}},
    {...current,effective_parameters:{...current.effective_parameters,max_file_size_bytes:0}},
    {...current,effective_parameters:{...current.effective_parameters,max_file_size_bytes:20971521}}]) {
    assert.throws(()=>P.canonicalPhotoQualityPolicy(invalid));
  }
  Object.defineProperty(globalThis,'indexedDB',{configurable:true,get(){throw new DOMException('Browser refusal','SecurityError')}});
  let store;
  assert.doesNotThrow(()=>{store=P.createStore()});
  await assert.rejects(store.open(),{name:'SecurityError'});
})().catch(error=>{console.error(error);process.exitCode=1});
'''], check=True)
