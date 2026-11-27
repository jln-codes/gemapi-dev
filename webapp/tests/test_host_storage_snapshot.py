"""Level A/B: real atomic JSON files, public projection and identity guards.
No Docker/PG connection and no filesystem inventory on page load.
"""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from digues_app.storage_host import collect,publish,space
from digues_webapp.host_storage_snapshot import read_snapshot
from webapp.tests.test_admin_storage import snapshot
from digues_webapp.admin_storage import StorageSnapshot

ROOT=Path(__file__).resolve().parents[2]/'audits'

class HostSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT)))
        self.installation=str(uuid4());self.output=self.root/'snapshot.json'
        self.enterContext(patch.dict(os.environ,{'STORAGE_HOST_SNAPSHOT_FILE':str(self.output)}))
        self.config=dict(schema_version=1,installation_id=self.installation,filesystem_path=str(self.root),roots=[dict(path=str(self.root/'system'),category='system')])
        (self.root/'system').mkdir();(self.root/'system/data').write_bytes(b'fixture')
        self.fsid=space(self.root)['fsid']
        self.control={'_instance_id':self.installation,'_namespaces':[]}
        self.fallback=StorageSnapshot(**snapshot(count=0)).model_dump()
        self.disk={'filesystem_fsid':self.fsid}
        self.host=collect(self.config,mapper=lambda fd:[(4096,8192)])
        publish(self.output,self.host)

    def test_host_v2_projects_existing_public_v1_contract_with_no_private_text(self):
        self.host['storage']['categories'][0]['label']='PRIVATE_PATH_SECRET'
        self.host['storage']['provenance']='PRIVATE_PATH_SECRET'
        self.host['storage']['filesystem']['label']='PRIVATE_PATH_SECRET'
        publish(self.output,self.host)
        value=read_snapshot(self.fallback,self.control,self.disk)
        self.assertEqual(value['snapshot_state'],'ready');self.assertEqual(value['source'],'host_snapshot')
        self.assertEqual(value['max_age_seconds'],86400)
        self.assertEqual(value['categories'][0]['category'],'system')
        self.assertNotIn('PRIVATE_PATH_SECRET',json.dumps(value))
        self.assertNotIn(str(self.root),json.dumps(value))
        StorageSnapshot(**value)

    def test_old_ownership_snapshot_cannot_reintroduce_per_base_public_cache(self):
        old=deepcopy(self.host);old['schema_version']=1
        publish(self.output,old)
        value=read_snapshot(self.fallback,self.control,self.disk)
        self.assertEqual(value['snapshot_state'],'invalid')
        self.assertEqual(value['categories'],[])
        self.assertEqual(value['filesystem'],self.fallback['filesystem'])

    def test_absent_corrupt_permissions_old_and_identity_mismatch_preserve_statvfs(self):
        cases=[]
        self.output.unlink();cases.append(read_snapshot(self.fallback,self.control,self.disk)['snapshot_state'])
        self.output.write_text('{invalid');cases.append(read_snapshot(self.fallback,self.control,self.disk)['snapshot_state'])
        publish(self.output,self.host);self.output.chmod(0o666);cases.append(read_snapshot(self.fallback,self.control,self.disk)['snapshot_state'])
        self.output.chmod(0o640)
        cases.append(read_snapshot(self.fallback,{**self.control,'_instance_id':str(uuid4())},self.disk)['snapshot_state'])
        cases.append(read_snapshot(self.fallback,self.control,{'filesystem_fsid':'other'})['snapshot_state'])
        cases.append(read_snapshot(self.fallback,self.control,self.disk,now=datetime.now(timezone.utc)+timedelta(days=2))['snapshot_state'])
        self.assertEqual(cases,['absent','invalid','invalid','identity_mismatch','filesystem_mismatch','stale'])
        self.assertEqual(read_snapshot(self.fallback,self.control,self.disk,now=datetime.now(timezone.utc)+timedelta(days=2))['categories'],[])

    def test_invalid_version_sum_and_extent_sets_are_rejected(self):
        for change in (lambda h:h.update(schema_version=3),lambda h:h['storage']['filesystem'].update(used_bytes=0),
                       lambda h:h['storage']['categories'].append(h['storage']['categories'][0]),lambda h:h.update(issues=['PRIVATE_SECRET'])):
            value=deepcopy(self.host);change(value);publish(self.output,value)
            self.assertEqual(read_snapshot(self.fallback,self.control,self.disk)['snapshot_state'],'invalid')

    def test_untrusted_parent_links_and_deep_corruption_are_refused(self):
        self.root.chmod(0o777)
        self.assertEqual(read_snapshot(self.fallback,self.control,self.disk)['snapshot_state'],'invalid')
        self.root.chmod(0o700)
        link=self.root/'second.json';os.link(self.output,link)
        self.assertEqual(read_snapshot(self.fallback,self.control,self.disk)['snapshot_state'],'invalid')
        link.unlink()
        self.output.write_text('['*2000+']'*2000)
        self.assertEqual(read_snapshot(self.fallback,self.control,self.disk)['snapshot_state'],'invalid')
        self.host['storage']['categories'][0]['id']='private_label'
        publish(self.output,self.host)
        self.assertEqual(read_snapshot(self.fallback,self.control,self.disk)['snapshot_state'],'invalid')

    def test_missing_collector_keeps_existing_statvfs_and_old_contract_valid(self):
        with patch.dict(os.environ,{'STORAGE_HOST_SNAPSHOT_FILE':''}):
            self.assertEqual(read_snapshot(self.fallback,self.control,self.disk),self.fallback)
        StorageSnapshot(**snapshot(count=0))

    def test_base_identity_revalidated_and_external_pg_cannot_create_a_portion(self):
        b=dict(id=str(uuid4()),structure_id=str(uuid4()),media_namespace=str(uuid4()),slug='real-base',display_name='Base publique')
        media=self.root/'media'/b['id'];media.mkdir(parents=True)
        (media/'namespace.json').write_text(json.dumps(dict(schema_version=2,business_database_id=b['id'],media_namespace=b['media_namespace'])))
        config={**self.config,'application':{'media_root':str(media.parent)}}
        host=collect(config,dict(contract_version=1,installation_id=self.installation,bases=[b]),mapper=lambda fd:[(os.fstat(fd).st_ino*16384,os.fstat(fd).st_ino*16384+4096)])
        publish(self.output,host)
        value=read_snapshot(self.fallback,self.control,self.disk)
        self.assertFalse(any(c['category']=='bases' for c in value['categories']))
        control={**self.control,'_namespaces':[b]}
        value=read_snapshot(self.fallback,control,self.disk)
        self.assertEqual(next(c for c in value['categories'] if c['category']=='bases')['label'],'Base publique')
        self.assertEqual(next(c for c in value['categories'] if c['category']=='bases')['base_slug'],'real-base')
        self.assertFalse(any('postgres' in c['id'] for c in value['categories']))
