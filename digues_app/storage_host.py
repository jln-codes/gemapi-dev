"""Linux host storage observer. Standard library only; no daemon or cleanup.

Run explicitly: python -m digues_app.storage_host --config ... --output ...
Only output JSON publication writes files. No sync flag is used with FIEMAP.
"""
from __future__ import annotations
import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import socket
import stat
import struct
import time
from uuid import UUID, uuid4

LABELS = {'system': 'Système et infrastructure', 'docker': 'Docker, containerd et BuildKit',
          'logs': 'Journaux système', 'common': 'Ressources communes', 'other': 'Autres / non attribués'}
ISSUES = {'unavailable_root', 'other_filesystem', 'mount_boundary', 'unsafe_entry', 'hardlink',
          'unsupported_extents', 'shared_or_unknown_extents', 'changed_file', 'scan_bound',
          'overlapping_extents', 'directory_alias', 'filesystem_drift', 'exceeds_used',
          'docker_unavailable', 'containerd_not_configured', 'base_identity_unverified', 'unmeasured_metadata'}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def secure_open(path, flags=os.O_RDONLY):
    """Resolve every directory component with O_NOFOLLOW; no realpath adoption."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Absolute path required')
    parent = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent); parent = child
        return os.open(path.name or '/', flags | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    finally:
        os.close(parent)


def bounded_json(path, maximum=8*1024**2):
    fd = secure_open(path)
    with os.fdopen(fd, 'rb') as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode): raise ValueError('Regular JSON file required')
        raw = source.read(maximum+1)
    if len(raw)>maximum: raise ValueError('JSON bound exceeded')
    return json.loads(raw)


def space(path):
    fd = secure_open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        v, meta = os.fstatvfs(fd), os.fstat(fd)
        return dict(device=meta.st_dev, fsid=str(v.f_fsid), capacity_bytes=v.f_blocks*v.f_frsize,
            used_bytes=(v.f_blocks-v.f_bfree)*v.f_frsize, reserved_bytes=(v.f_bfree-v.f_bavail)*v.f_frsize,
            available_bytes=v.f_bavail*v.f_frsize)
    finally: os.close(fd)


def filesystem_id(fsid):
    return 'host-fs-' + hashlib.sha256(str(fsid).encode()).hexdigest()[:20]


def extents(fd):
    """Return data extents only. Ignore xattr trees, encoded/shared/delayed data.

    ABI from linux/fiemap.h: 32-byte request, 56-byte extent. Flags=0 avoids
    syncing files. Unknown flags fail closed rather than implying exclusivity.
    """
    result, start = [], 0
    for _ in range(32):
        count = 128
        buffer = bytearray(32+56*count)
        struct.pack_into('=QQIIII', buffer, 0, start, 2**64-1-start, 0, 0, count, 0)
        fcntl.ioctl(fd, 0xC020660B, buffer, True)  # FS_IOC_FIEMAP, read mapping only
        mapped = struct.unpack_from('=QQIIII', buffer)[3]
        if mapped>count: raise ValueError('Invalid extent count')
        if not mapped:
            if os.fstat(fd).st_blocks and not result: raise ValueError('Unmapped allocated blocks')
            return result
        for i in range(mapped):
            logical, physical, length, _, _, flags, *_ = struct.unpack_from('=QQQQQIIII', buffer, 32+56*i)
            if flags & ~0x801 or not physical or not length:
                raise ValueError('Shared or unknown extent')
            result.append((physical, physical+length))
            if flags & 1: return result
            if logical+length<=start: raise ValueError('Invalid extent progression')
            start = logical+length
    raise ValueError('Extent bound exceeded')


class Docker:
    """Local Unix socket, fixed GET endpoints. No environment-selected remote daemon."""
    def __init__(self, path, deadline=None): self.path,self.deadline=path,deadline

    def get(self, path):
        if not (path in {'/info','/system/df','/containers/json?all=1'} or re.fullmatch(r'/containers/[a-f0-9]{12,64}/json',path)):
            raise ValueError('Unsupported Docker read')
        remaining=3 if self.deadline is None else min(3,self.deadline-time.monotonic())
        if remaining<=0: raise TimeoutError()
        connection = http.client.HTTPConnection('localhost', timeout=remaining)
        connection.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        connection.sock.settimeout(remaining)
        try:
            connection.sock.connect(self.path)
            connection.request('GET',path)
            response=connection.getresponse()
            raw=response.read(16*1024**2+1)
            if response.status!=200 or len(raw)>16*1024**2: raise ValueError('Docker observation unavailable')
            return json.loads(raw)
        finally: connection.close()


@dataclass
class Rule:
    path: Path
    category: str
    base: dict | None = None
    media_marker: bool = False

    @property
    def key(self): return 'base-'+self.base['id'].replace('-','') if self.base else self.category


def _mounts():
    result={}
    try:
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            left,right=line.split(' - ',1)
            point=re.sub(r'\\([0-7]{3})', lambda m:chr(int(m[1],8)),left.split()[4])
            result[Path(point)]=right.split()[0]
    except (OSError,ValueError,IndexError): pass
    return result


def validate_targets(value, installation):
    if not isinstance(value,dict): raise ValueError('Invalid target catalogue')
    if value.get('contract_version')!=1 or value.get('installation_id')!=installation: raise ValueError('Wrong target catalogue')
    bases=value.get('bases')
    if not isinstance(bases,list) or len(bases)>1000: raise ValueError('Target catalogue bound')
    seen=set()
    for base in bases:
        for field in ('id','structure_id','media_namespace'):
            if str(UUID(base[field]))!=base[field]: raise ValueError('Noncanonical identity')
        if base['id'] in seen: raise ValueError('Repeated base')
        seen.add(base['id'])
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,62}',base['slug']): raise ValueError('Invalid slug')
    return bases


def discover(config, targets, docker, deadline=None):
    rules=[Rule(Path(r['path']),r['category']) for r in config.get('roots',[])]
    if any(r.category not in LABELS or not r.path.is_absolute() or '..' in r.path.parts for r in rules):
        raise ValueError('Invalid observation root')
    details,issues=[],set()
    app=dict(config.get('application',{}))
    d=config.get('docker',{})
    if d.get('enabled',False):
        try:
            info=docker.get('/info');usage=docker.get('/system/df')
            if not isinstance(info,dict) or not isinstance(usage,dict): raise ValueError('Invalid Docker metadata')
            rules.append(Rule(Path(info['DockerRootDir']),'docker'))
            rules.append(Rule(Path(info['DockerRootDir'])/'volumes','other'))
            containers=docker.get('/containers/json?all=1')
            if len(containers)>500: raise ValueError('Container bound')
            image_ids={c.get('ImageID') for c in containers}
            images=usage.get('Images') or []
            build=usage.get('BuildCache') or []
            def declared_total(values):
                return sum(values) if all(type(v) is int and v>=0 for v in values) else None
            build_size=declared_total([i.get('Size') for i in build])
            volume_size=declared_total([v.get('UsageData',{}).get('Size') for v in usage.get('Volumes') or []])
            eligibility_known=all(type(i.get(k)) is bool for i in build for k in ('InUse','Shared'))
            details.extend([
                dict(id='docker_images',status='measured',logical_bytes=usage.get('LayersSize'),count=len(images),
                     active_count=sum(i.get('Id') in image_ids for i in images)),
                dict(id='buildkit',status='measured' if eligibility_known else 'partial',
                     logical_bytes=build_size,count=len(build),
                     reclaimable_bytes=sum(i['Size'] for i in build if i.get('InUse') is False and i.get('Shared') is False) if eligibility_known and build_size is not None else None),
                dict(id='docker_volumes',status='measured',logical_bytes=volume_size,count=len(usage.get('Volumes') or []))])
            now=datetime.now(timezone.utc)
            ages=[]
            for item in build:
                try:
                    last=datetime.fromisoformat(item['LastUsedAt'].replace('Z','+00:00'))
                    age=(now-last).total_seconds()
                    # Creation is not evidence of last use; zero/invalid dates
                    # and future dates are never described as old usage.
                    if last.year>=1970 and age>=0: ages.append(age)
                except (ValueError,KeyError,TypeError,AttributeError): pass
            if ages: details.append(dict(id='buildkit_age',status='measured',count=sum(age>=30*86400 for age in ages),oldest_seconds=max(0,int(max(ages)))))
            logs=0;logs_complete=True
            for c in containers:
                meta=docker.get('/containers/'+c['Id']+'/json')
                log=meta.get('LogPath')
                if log:
                    try:
                        fd=secure_open(log)
                        try:
                            meta_log=os.fstat(fd)
                            if stat.S_ISREG(meta_log.st_mode): logs+=meta_log.st_size
                            else:logs_complete=False
                        finally: os.close(fd)
                    except OSError: issues.add('unavailable_root');logs_complete=False
                labels=meta.get('Config',{}).get('Labels') or {}
                if labels.get('com.docker.compose.project')!=d.get('project'): continue
                for mount in meta.get('Mounts',[]):
                    source=mount.get('Source','');destination=mount.get('Destination')
                    if not source.startswith('/'): continue
                    # A bound configuration file is not a directory inventory root.
                    if mount.get('Type')=='bind':
                        try:
                            descriptor=secure_open(source)
                            try:
                                if not stat.S_ISDIR(os.fstat(descriptor).st_mode): continue
                            finally:os.close(descriptor)
                        except OSError:
                            issues.add('unavailable_root');continue
                    if destination=='/var/lib/digues/media': app.setdefault('media_root',source)
                    if destination=='/var/cache/digues-app/basemaps': app.setdefault('cache_root',source)
                    if destination=='/var/lib/postgresql/data': app.setdefault('postgres_root',source)
                    category = 'common' if destination in {'/data','/config','/var/lib/digues/runtime-config','/var/lib/digues/profiles','/var/lib/digues/provisioning','/var/lib/digues/import-secrets','/var/lib/digues/import-worker-private'} else 'other'
                    rules.append(Rule(Path(source),category))
            details.append(dict(id='docker_logs',status='measured' if logs_complete else 'partial',logical_bytes=logs if logs_complete else None))
        except (OSError,ValueError,KeyError,TypeError,AttributeError,RecursionError,http.client.HTTPException):
            issues.add('docker_unavailable')
            if details:
                for detail in details: detail['status']='partial'
            else: details.append(dict(id='docker_images',status='unavailable'))
        for root in d.get('containerd_roots',[]): rules.append(Rule(Path(root),'docker'))
        if not d.get('containerd_roots'): issues.add('containerd_not_configured')
    for role in ('media','cache','postgres'):
        root=app.get(role+'_root')
        if root:
            path=Path(root)
            if not path.is_absolute() or '..' in path.parts: raise ValueError('Invalid application root')
            rules.append(Rule(path,'other'))
            if role=='postgres': rules.append(Rule(path/'pg_wal','common'))
            if role=='cache':
                for suffix in ('ign/epure','ign/plan','ign/ortho','ign/lidar-hd/mnt','common'):
                    rules.append(Rule(path/suffix,'common'))
    for base in targets:
        if deadline is not None and time.monotonic()>deadline: issues.add('scan_bound');break
        media=app.get('media_root')
        if media:
            path=Path(media)/base['id']
            try:
                marker=bounded_json(path/'namespace.json',8192)
                if marker!={'schema_version':2,'business_database_id':base['id'],'media_namespace':base['media_namespace']}: raise ValueError()
                rules.append(Rule(path,'bases',base,media_marker=True))
            except (OSError,ValueError): issues.add('base_identity_unverified')
        cache=app.get('cache_root')
        if cache:
            path=Path(cache)/'instances'/config['installation_id']/'structures'/base['structure_id']/'bases'/base['id']
            # Retired I/S/B public caches are still common resources, never
            # exclusive business files. Unknown descendants remain unassigned.
            for suffix in ('ign/epure','ign/plan','ign/ortho','ign/lidar-hd/mnt'):
                rules.append(Rule(path/suffix,'common'))
    # Equal paths with different ownership cannot silently override one another.
    unique={}
    for r in rules:
        if not r.path.is_absolute() or '..' in r.path.parts: raise ValueError('Invalid discovered root')
        if r.path in unique and unique[r.path].key!=r.key:
            # Auto-discovered generic volume roots are refined by explicit app roots/known leaves.
            previous=unique[r.path]
            if previous.category!='other' and r.category!='other': raise ValueError('Conflicting root ownership')
            if r.category=='other': continue
        unique[r.path]=r
    return list(unique.values()),details,issues


def collect(config, targets=None, *, docker=None, mapper=extents, clock=time.monotonic):
    if not isinstance(config,dict): raise ValueError('Invalid configuration')
    if config.get('schema_version')!=1: raise ValueError('Invalid configuration version')
    installation=str(UUID(config['installation_id']))
    if installation!=config['installation_id']: raise ValueError('Noncanonical installation')
    before=space(config['filesystem_path'])
    started=timestamp();deadline=clock()+min(120,max(1,config.get('max_seconds',60)))
    remaining=min(200000,max(1,config.get('max_entries',100000)))
    bases=validate_targets(targets,installation) if targets else []
    rules,details,issues=discover(config,bases,docker or Docker(config.get('docker',{}).get('socket','/var/run/docker.sock'),deadline),deadline)
    mounts=_mounts()
    roots=sorted({r.path for r in rules},key=lambda p:len(p.parts))
    roots=[p for p in roots if not any(p!=q and p.is_relative_to(q) for q in roots)]
    ordered=sorted(rules,key=lambda r:len(r.path.parts),reverse=True)
    records={};seen_dirs=set();unsupported=0
    observed_inodes=set();observed_logical=observed_allocated=0
    anchor=Path(config['filesystem_path'])
    fs_kind=next((kind for point,kind in sorted(mounts.items(),key=lambda item:len(item[0].parts),reverse=True) if anchor.is_relative_to(point)), 'unknown')
    proof_supported=mapper is not extents or fs_kind in {'ext4','xfs'}
    def rule(path): return next(r for r in ordered if path.is_relative_to(r.path))
    def step():
        nonlocal remaining
        remaining-=1
        if remaining<0 or clock()>deadline: raise TimeoutError()
    def walk(fd,path):
        nonlocal unsupported, observed_logical, observed_allocated
        step();meta=os.fstat(fd)
        if (meta.st_dev,meta.st_ino) in seen_dirs: issues.add('directory_alias');return
        seen_dirs.add((meta.st_dev,meta.st_ino))
        with os.scandir(fd) as entries:
            for entry in entries:
                step();name=path/entry.name
                try:
                    m=entry.stat(follow_symlinks=False)
                    if m.st_dev!=before['device']: issues.add('other_filesystem');continue
                    if name in mounts: issues.add('mount_boundary');continue
                    if not (stat.S_ISREG(m.st_mode) or stat.S_ISDIR(m.st_mode)):
                        issues.add('unsafe_entry');continue
                    child=os.open(entry.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|(os.O_DIRECTORY if stat.S_ISDIR(m.st_mode) else 0),dir_fd=fd)
                    try:
                        opened=os.fstat(child)
                        if (m.st_dev,m.st_ino)!=(opened.st_dev,opened.st_ino): issues.add('changed_file');continue
                        if stat.S_ISDIR(opened.st_mode): walk(child,name);continue
                        key=(opened.st_dev,opened.st_ino)
                        if key not in observed_inodes:
                            observed_inodes.add(key);observed_logical+=opened.st_size;observed_allocated+=opened.st_blocks*512
                        if not proof_supported:
                            issues.add('unsupported_extents');continue
                        if opened.st_nlink!=1 or key in records:
                            records.pop(key,None);issues.add('hardlink');continue
                        try: ranges=mapper(child)
                        except ValueError: issues.add('shared_or_unknown_extents');continue
                        except OSError: unsupported+=1;issues.add('unsupported_extents');continue
                        after=os.fstat(child)
                        if (opened.st_size,opened.st_mtime_ns,opened.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns):
                            issues.add('changed_file');continue
                        records[key]=(name,after,ranges,rule(name))
                    finally:os.close(child)
                except OSError:issues.add('unavailable_root')
    for root in roots:
        try:
            fd=secure_open(root,os.O_RDONLY|os.O_DIRECTORY)
            try:
                if os.fstat(fd).st_dev!=before['device']:issues.add('other_filesystem');continue
                walk(fd,root)
            finally:os.close(fd)
        except TimeoutError:issues.add('scan_bound');break
        except OSError:issues.add('unavailable_root')
    # The entry bound limits traversal; previously seen records can still be
    # revalidated within the same time deadline, yielding a useful partial scan.
    remaining=len(records)
    # Revalidate both inode metadata and maps. No filesystem freeze or forced sync.
    for key,(path,original,ranges,owner) in list(records.items()):
        try:
            step();fd=secure_open(path)
            try:
                m=os.fstat(fd)
                if (m.st_dev,m.st_ino,m.st_nlink,m.st_size,m.st_mtime_ns,m.st_ctime_ns)!=(original.st_dev,original.st_ino,1,original.st_size,original.st_mtime_ns,original.st_ctime_ns) or mapper(fd)!=ranges:
                    raise ValueError()
            finally:os.close(fd)
        except TimeoutError:issues.add('scan_bound');records.clear();break
        except (OSError,ValueError):issues.add('changed_file');records.pop(key,None)
    # Ownership markers are checked again after observing data, rather than
    # adopting a namespace that changed identity during the inventory.
    checked_markers={}
    for key,(_,_,_,owner) in list(records.items()):
        if not owner.media_marker:continue
        if clock()>deadline:issues.add('scan_bound');records.clear();break
        if owner.path not in checked_markers:
            try:
                checked_markers[owner.path]=bounded_json(owner.path/'namespace.json',8192)=={
                    'schema_version':2,'business_database_id':owner.base['id'],'media_namespace':owner.base['media_namespace']}
            except (OSError,ValueError):checked_markers[owner.path]=False
        if not checked_markers[owner.path]:
            issues.add('base_identity_unverified');records.pop(key,None)
    spans=sorted((a,b,key) for key,(_,_,ranges,_) in records.items() for a,b in ranges)
    component=[];end=-1;bad=set()
    def finish():
        if len(component)>1:bad.update(component)
    for a,b,key in spans:
        if a>=end:finish();component=[];end=b
        else:end=max(end,b)
        component.append(key)
    finish()
    if bad:issues.add('overlapping_extents')
    for key in bad:records.pop(key,None)
    if 'directory_alias' in issues:records.clear()
    after=space(config['filesystem_path'])
    if any(before[k]!=after[k] for k in ('device','fsid','capacity_bytes')) or abs(before['used_bytes']-after['used_bytes'])>max(64*1024**2,before['capacity_bytes']//1000):
        issues.add('filesystem_drift');records.clear()
    totals={};owners={}
    for _,_,ranges,owner in records.values():
        totals[owner.key]=totals.get(owner.key,0)+sum(b-a for a,b in ranges);owners[owner.key]=owner
    if sum(totals.values())>min(before['used_bytes'],after['used_bytes']):
        issues.add('exceeds_used');totals.clear()
    fsid=filesystem_id(after['fsid']);categories=[];attributions=[]
    for key,n in sorted(totals.items()):
        if not n:continue
        owner=owners[key];allocation='extents-'+uuid4().hex
        category=dict(id=key,category=owner.category,label=owner.base['display_name'] if owner.base else LABELS[owner.category],
            filesystem_id=fsid,bytes=n,unit='bytes',basis='physical',provenance='FIEMAP : extents de données non partagés, observés deux fois',allocation_ids=[allocation],base_id=None,base_slug=None)
        if owner.base:
            category.update(base_id=owner.base['id'],base_slug=owner.base['slug'])
            attributions.append(dict(allocation_id=allocation,base_id=owner.base['id'],structure_id=owner.base['structure_id'],media_namespace=owner.base['media_namespace']))
        categories.append(category)
    issues.add('unmeasured_metadata')
    details.append(dict(id='observed_files',status='partial',logical_bytes=observed_logical,allocated_bytes=observed_allocated,count=len(observed_inodes)))
    for detail in details:
        for key,value in list(detail.items()):
            if key not in {'id','status'} and (type(value) is not int or not 0<=value<=2**53-1):
                detail[key]=None;detail['status']='partial'
    return dict(schema_version=2,installation_id=installation,filesystem_fsid=after['fsid'],started_at=started,finished_at=timestamp(),
        storage=dict(contract_version=1,status='degraded' if len(issues)>1 else 'ok',
            filesystem=dict(id=fsid,label='Filesystem hôte de l’installation',unit='bytes',**{k:after[k] for k in ('capacity_bytes','used_bytes','available_bytes','reserved_bytes')}),
            measured_at=timestamp(),freshness='current',completeness='incomplete',provenance='Collecteur hôte en lecture seule',categories=categories),
        attributions=attributions,details=details,issues=sorted(issues))


def publish(path, value):
    path=Path(path)
    parent=secure_open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    name='.storage-'+uuid4().hex+'.tmp'
    try:
        try:
            existing=os.stat(path.name,dir_fd=parent,follow_symlinks=False)
            if not stat.S_ISREG(existing.st_mode) or existing.st_nlink!=1: raise ValueError('Unsafe publication target')
        except FileNotFoundError: pass
        fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o640,dir_fd=parent)
        try:
            with os.fdopen(fd,'w',encoding='utf8') as output:
                json.dump(value,output,ensure_ascii=False);output.write('\n');output.flush();os.fsync(output.fileno())
            os.replace(name,path.name,src_dir_fd=parent,dst_dir_fd=parent);os.fsync(parent)
        finally:
            try:os.unlink(name,dir_fd=parent)
            except FileNotFoundError:pass
    finally:os.close(parent)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True);parser.add_argument('--targets');parser.add_argument('--output',required=True)
    args=parser.parse_args()
    try:
        value=collect(bounded_json(Path(args.config).absolute()),bounded_json(Path(args.targets).absolute()) if args.targets else None)
        publish(Path(args.output).absolute(),value)
    except (OSError,ValueError,KeyError,TypeError,RecursionError):
        parser.exit(1,'Collecte indisponible : configuration, droits ou mesure invalides.\n')
    print('Snapshot de stockage publié ; aucune maintenance exécutée.')

if __name__=='__main__':main()
