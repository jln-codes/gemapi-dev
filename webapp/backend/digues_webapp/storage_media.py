"""Bounded metadata-only inventory of one attested media namespace.

No MediaStorage constructor/helpers: several of those create directories. Openat
and O_NOFOLLOW preserve the boundary even if a child is replaced during a scan.
"""
import json
import os
from pathlib import Path
import stat
import time
from uuid import UUID
from digues_app.media_ingestion import open_private_file


def _json(parent, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    with os.fdopen(fd, 'rb') as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError('Invalid marker')
        value = source.read(8193)
    if len(value) > 8192:
        raise ValueError('Large marker')
    return json.loads(value)


def _filesystem_type(device):
    try:
        identity = f'{os.major(device)}:{os.minor(device)}'
        kinds = {line.split(' - ', 1)[1].split()[0] for line in Path('/proc/self/mountinfo').read_text().splitlines()
                 if line.split()[2] == identity}
        return next(iter(kinds)) if len(kinds) == 1 else 'unknown'
    except (OSError, ValueError, IndexError):
        return 'unknown'


def inventory(root, target, *, max_entries=10000, seconds=2):
    """Unknown files are counted, links/mount crossings are never traversed.

    Allocated blocks are reported separately. Neither filesystem type nor link
    count proves globally unique extents/metadata blocks. No exclusive physical
    allocation is attested here; open-unlinked files are also outside the walk.
    """
    identifier, namespace = str(UUID(str(target['id']))), str(UUID(str(target['media_namespace'])))
    parent = open_private_file(Path(root))
    try:
        fd = os.open(identifier, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    finally:
        os.close(parent)
    try:
        first = os.fstat(fd)
        if not stat.S_ISDIR(first.st_mode):
            raise ValueError('Invalid namespace')
        marker = _json(fd, 'namespace.json')
        expected = dict(schema_version=2, business_database_id=identifier, media_namespace=namespace)
        if marker != expected:
            raise ValueError('Namespace mismatch')
        totals = {}
        bundles = {}
        seen = set()
        problems = set()
        remaining = max_entries
        deadline = time.monotonic() + seconds

        def add(info, category, bundle=None):
            key = (info.st_dev, info.st_ino)
            if key in seen:
                problems.add('repeated_inode')
                return
            seen.add(key)
            row = totals.setdefault(category, dict(logical_bytes=0, allocated_bytes=0, files=0))
            allocated = info.st_blocks * 512 if hasattr(info, 'st_blocks') else None
            if allocated is None:
                problems.add('allocation_unavailable')
            row['allocated_bytes'] += allocated or 0
            if stat.S_ISREG(info.st_mode):
                row['logical_bytes'] += info.st_size
                row['files'] += 1
                if info.st_nlink != 1:
                    problems.add('shared_hardlink')
                if bundle:
                    bundles[bundle] = bundles.get(bundle, 0) + info.st_size

        def walk(directory, prefix=(), category='metadata', bundle=None):
            nonlocal remaining
            before = os.fstat(directory)
            add(before, category)
            with os.scandir(directory) as entries:
                for entry in entries:
                    remaining -= 1
                    if remaining < 0 or time.monotonic() >= deadline:
                        problems.add('scan_bound')
                        return
                    try:
                        info = entry.stat(follow_symlinks=False)
                        if info.st_dev != first.st_dev:
                            problems.add('other_filesystem'); continue
                        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                            problems.add('unsafe_entry'); continue
                        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                        if stat.S_ISDIR(info.st_mode): flags |= os.O_DIRECTORY
                        child = os.open(entry.name, flags, dir_fd=directory)
                        try:
                            opened = os.fstat(child)
                            if (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
                                problems.add('changed_during_scan'); continue
                            parts = (*prefix, entry.name)
                            kind, object_key = category, bundle
                            if not prefix:
                                kind = {'objects': 'unknown', 'incoming': 'preparations', 'trash': 'collection_pending',
                                        'deleting': 'collection_pending', 'bindings': 'metadata', 'namespace.json': 'metadata'}.get(entry.name, 'unknown')
                            elif prefix == ('objects',) and stat.S_ISDIR(opened.st_mode):
                                try:
                                    value = _json(child, 'manifest.json')
                                    if not isinstance(value, dict): raise ValueError()
                                    if any(value.get(k) != v for k,v in expected.items()): raise ValueError()
                                    if str(UUID(str(value['storage_key']))) != entry.name: raise ValueError()
                                    kind = {'observation-photo': 'photos_and_revisions', 'seuil-plan': 'documents'}[value['owner']['kind']]
                                    object_key = entry.name
                                except (ValueError, KeyError, TypeError, OSError):
                                    kind, object_key = 'unknown', None
                            if stat.S_ISDIR(opened.st_mode):
                                walk(child, parts, kind, object_key)
                            else:
                                add(opened, kind, object_key)
                            after = os.fstat(child)
                            if (opened.st_mtime_ns, opened.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns):
                                problems.add('changed_during_scan')
                        finally: os.close(child)
                    except OSError:
                        problems.add('entry_unavailable')
            after = os.fstat(directory)
            if (before.st_mtime_ns, before.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns):
                problems.add('changed_during_scan')

        walk(fd)
        complete = not problems
        kind = _filesystem_type(first.st_dev)
        allocated = sum(v['allocated_bytes'] for v in totals.values())
        return dict(status='measured' if complete else 'partial',
            logical_bytes=sum(v['logical_bytes'] for v in totals.values()), allocated_bytes=allocated,
            physical_bytes=None, physical_exclusive=False, physical_limit='unique_blocks_not_attested',
            filesystem_id=f'fastapi-device:{first.st_dev}', filesystem_type=kind,
            breakdown=totals, issues=sorted(problems),
            provenance='Namespace média attesté : tailles des fichiers et blocs alloués, sans contenu binaire',
            _bundles=bundles)
    finally: os.close(fd)
