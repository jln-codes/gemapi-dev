"""Consume an operator-published readonly file. No host/Docker calls from HTTP."""
from datetime import datetime, timezone
import os
import re
import stat
from typing import Literal
from uuid import UUID
from pydantic import Field, model_validator
from digues_app.storage_host import filesystem_id, secure_open, ISSUES, LABELS
from .admin_storage import Strict, StorageSnapshot, StorageDetail


class Attribution(Strict):
    allocation_id: str = Field(pattern=r'^extents-[0-9a-f]{32}$')
    base_id: str
    structure_id: str
    media_namespace: str

class HostSnapshot(Strict):
    # v2 guarantees public IGN/MNT are common, including retired I/S/B trees.
    schema_version: Literal[2]
    installation_id: str
    filesystem_fsid: str = Field(pattern=r'^-?[0-9]+$')
    started_at: str
    finished_at: str
    storage: StorageSnapshot
    attributions: list[Attribution] = Field(max_length=1000)
    details: list[StorageDetail] = Field(max_length=32)
    issues: list[str] = Field(max_length=32)

    @model_validator(mode='after')
    def identities(self):
        if str(UUID(self.installation_id))!=self.installation_id or self.filesystem_fsid=='0': raise ValueError('Invalid identity')
        for item in self.attributions:
            for value in (item.base_id,item.structure_id,item.media_namespace):
                if str(UUID(value))!=value: raise ValueError('Invalid identity')
        if len({a.allocation_id for a in self.attributions})!=len(self.attributions): raise ValueError('Repeated attribution')
        if len({d.id for d in self.details})!=len(self.details): raise ValueError('Repeated detail')
        if any(issue not in ISSUES for issue in self.issues): raise ValueError('Unknown issue')
        for c in self.storage.categories:
            if c.category not in {*LABELS,'bases'} or len(c.allocation_ids)!=1 or not re.fullmatch(r'extents-[0-9a-f]{32}',c.allocation_ids[0]):
                raise ValueError('Invalid extent allocation')
            if c.id != ('base-'+c.base_id.replace('-','') if c.category=='bases' and c.base_id else c.category):
                raise ValueError('Noncanonical category identity')
        if not self.storage.filesystem or self.storage.filesystem.id!=filesystem_id(self.filesystem_fsid): raise ValueError('Invalid filesystem identity')
        return self


def read_snapshot(fallback, control, disk, *, now=None):
    def degraded(state): return {**fallback,'snapshot_state':state}
    path=os.environ.get('STORAGE_HOST_SNAPSHOT_FILE')
    if not path:return fallback
    try:
        parent=secure_open(os.path.dirname(path),os.O_RDONLY|os.O_DIRECTORY)
        try:
            parent_meta=os.fstat(parent)
            if parent_meta.st_mode & 0o022 or parent_meta.st_uid not in {0,os.geteuid()}:
                raise ValueError('Untrusted snapshot directory')
        finally:os.close(parent)
        fd=secure_open(path)
        try:
            meta=os.fstat(fd)
            if not stat.S_ISREG(meta.st_mode) or meta.st_mode & 0o022 or meta.st_uid not in {0,os.geteuid()} or meta.st_nlink!=1 or meta.st_size>8*1024**2:
                raise ValueError('Unsafe snapshot')
            # Read through this already-open descriptor, with a fixed byte limit.
            with os.fdopen(os.dup(fd),'rb') as source: raw=source.read(8*1024**2+1)
        finally:os.close(fd)
        import json
        if len(raw)>8*1024**2:raise ValueError('Large snapshot')
        host=HostSnapshot.model_validate(json.loads(raw))
        if host.installation_id!=control.get('_instance_id'):return degraded('identity_mismatch')
        expected=os.getenv('STORAGE_HOST_FILESYSTEM_ID') or disk.get('filesystem_fsid')
        if not expected or str(expected)!=host.filesystem_fsid:return degraded('filesystem_mismatch')
        now=now or datetime.now(timezone.utc)
        start=datetime.fromisoformat(host.started_at);end=datetime.fromisoformat(host.finished_at)
        measured=datetime.fromisoformat(host.storage.measured_at)
        if any(t.tzinfo is None for t in (start,end,measured)) or not 0<=(end-start).total_seconds()<=180 or abs((measured-end).total_seconds())>5 or (end-now).total_seconds()>300:
            raise ValueError('Inconsistent collection times')
        if (now-end).total_seconds()>86400:return degraded('stale')
        namespaces={str(v['id']):v for v in control.get('_namespaces',[])}
        proofs={a.allocation_id:a for a in host.attributions}
        categories=[];unverified=False
        for item in host.storage.categories:
            c=item.model_dump()
            # Browser labels/provenance come from this version and the control catalogue,
            # never arbitrary text, paths or Docker fields in the host JSON.
            c['provenance']='Collecteur hôte : extents de données exclusifs observés'
            if c['category']=='bases':
                proof=proofs.get(c['allocation_ids'][0]);base=namespaces.get(c['base_id'])
                if not proof or not base or proof.base_id!=c['base_id'] or proof.structure_id!=str(base['structure_id']) or proof.media_namespace!=str(base['media_namespace']):
                    unverified=True;continue
                c['label']=base['display_name'];c['base_slug']=base['slug']
            else:c['label']=LABELS[c['category']]
            categories.append(c)
        value=host.storage.model_dump()
        if unverified:value['status']='degraded'
        value.update(source='host_snapshot',snapshot_state='ready',max_age_seconds=86400,freshness='current',
            completeness='incomplete',categories=categories,issues=[*host.issues,*(['base_identity_unverified'] if unverified else [])],
            details=[d.model_dump() for d in host.details],provenance='Collecte hôte en lecture seule ; métadonnées et résidu non attribués')
        value['filesystem']['label']='Filesystem hôte de l’installation'
        return StorageSnapshot.model_validate(value).model_dump()
    except FileNotFoundError:return degraded('absent')
    except (OSError,ValueError,KeyError,TypeError,RecursionError):return degraded('invalid')
