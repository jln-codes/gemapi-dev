"""Cross-process namespace leases. A retired UUID can never be republished.

The small fence is operation evidence outside the removed namespace, not media.
No global cache path is accepted here. Locks cover publication, not network I/O.
"""
from contextlib import contextmanager
from functools import wraps
import fcntl
import os
from pathlib import Path
import stat
from uuid import UUID


@contextmanager
def namespace_lease(root, identifier, *, exclusive=False):
    root=Path(root).absolute();identifier=UUID(str(identifier))
    for path in (root.parent,*root.parent.parents):
        if path.is_symlink(): raise OSError('Unsafe namespace parent')
    root.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    directory=root.parent/'.namespace-leases'
    directory.mkdir(mode=0o700,exist_ok=True)
    folder=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        fd=os.open(str(identifier),os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600,dir_fd=folder)
    finally: os.close(folder)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.getuid() or info.st_mode & 0o077:
            raise OSError('Unsafe namespace lease')
        fcntl.flock(fd,(fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)|fcntl.LOCK_NB)
        if not exclusive and os.fstat(fd).st_size:
            raise OSError('Retired media namespace')
        yield fd
    finally: os.close(fd)


def namespace_writer(function):
    @wraps(function)
    def wrapped(self,*args,**kwargs):
        if self.business_database_id is None: return function(self,*args,**kwargs)
        with namespace_lease(self.root,self.business_database_id):
            return function(self,*args,**kwargs)
    return wrapped
