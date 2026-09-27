"""Consistent archive snapshot and explicit restore to a fresh destination."""
from __future__ import annotations
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from pathlib import Path
from storage import atomic_write_json, file_sha256, safe_target, native_filesystem_path

def _remove_work_tree(path, parent):
    resolved, boundary = Path(path).resolve(), Path(parent).resolve()
    if resolved == boundary or resolved.parent != boundary:
        raise ValueError('cleanup path is outside its named workspace')
    shutil.rmtree(native_filesystem_path(Path(path)))

class BackupService:
    def __init__(self,store):
        self.store = store

    def create(self,destination,keep=3):
        destination = Path(destination).absolute()
        if native_filesystem_path(destination).is_relative_to(native_filesystem_path(self.store.root)) or keep<1:
            raise ValueError('backup destination must be outside the archive; keep must be positive')
        destination = native_filesystem_path(destination)
        destination.mkdir(parents=True,exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix='.snapshot-',dir=destination))
        prefix = 'snapshot-'+self.store.archive_id+'-'
        final = destination/(prefix+uuid.uuid4().hex)
        try:
            with self.store.lock():
                self.store._recover()
                with self.store.connection() as source:
                    target = sqlite3.connect(temporary/'index.sqlite')
                    try:
                        source.backup(target)
                    finally:
                        target.close()
                shutil.copy2(self.store.root/'.assembly-format.json',temporary/'.assembly-format.json')
                cursor = safe_target(self.store.root,'.exchange-peer-cursors.json')
                if cursor.exists():
                    shutil.copy2(cursor,temporary/cursor.name)
                exclusions=safe_target(self.store.root,'excluded-conversations.json')
                if exclusions.exists():
                    shutil.copy2(exclusions,temporary/exclusions.name)
                peers=safe_target(self.store.root,'peer-index.sqlite')
                if peers.exists():
                    source=sqlite3.connect(peers)
                    target=sqlite3.connect(temporary/peers.name)
                    try:
                        source.backup(target)
                    finally:
                        target.close(); source.close()
                for directory in ('raw','summaries','replicas','collectors','legacy','native-bridge','core-sync','environment-sync'):
                    parent = self.store.root/directory
                    if not parent.exists():
                        continue
                    safe_target(self.store.root,directory)
                    for path in parent.rglob('*'):
                        rel = path.relative_to(self.store.root).as_posix()
                        safe_target(self.store.root,rel)
                        if path.is_file() and path.suffix != '.lock':
                            output = safe_target(temporary,rel)
                            output.parent.mkdir(parents=True,exist_ok=True)
                            shutil.copy2(path,output)
            files = {p.relative_to(temporary).as_posix():file_sha256(p) for p in temporary.rglob('*') if p.is_file()}
            atomic_write_json(temporary/'snapshot.json',{'format':'assembly.snapshot.v1','archive_id':self.store.archive_id,'files':files})
            os.replace(temporary,final)
            completed = sorted((p for p in destination.glob(prefix+'*') if p.is_dir() and not p.is_symlink() and (p/'snapshot.json').is_file()),key=lambda p:p.stat().st_mtime_ns,reverse=True)
            for old in completed[keep:]:
                if old != final and old.resolve().parent == destination.resolve():
                    _remove_work_tree(old,destination)
            return {'status':'completed','path':str(final),'files':len(files)}
        finally:
            if temporary.exists():
                _remove_work_tree(temporary,destination)

    @staticmethod
    def restore(snapshot,target):
        snapshot,target = native_filesystem_path(Path(snapshot)),native_filesystem_path(Path(target))
        if target.exists():
            raise ValueError('restore target must not exist; existing archives are never overwritten')
        manifest = json.loads((snapshot/'snapshot.json').read_text('utf-8'))
        if manifest.get('format') != 'assembly.snapshot.v1' or not isinstance(manifest.get('files'),dict):
            raise ValueError('invalid snapshot')
        target.parent.mkdir(parents=True,exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix='.restore-',dir=target.parent))
        try:
            for relative,digest in manifest['files'].items():
                source = safe_target(snapshot,relative)
                output = safe_target(temporary,relative)
                output.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(source,output)
                if file_sha256(output)!=digest:
                    raise ValueError('snapshot content mismatch')
            for required in ('index.sqlite','.assembly-format.json'):
                if not (temporary/required).is_file():
                    raise ValueError('snapshot is incomplete')
            if target.exists():
                raise ValueError('restore target appeared during copy')
            os.rename(temporary,target)
            return {'status':'restored','path':str(target)}
        finally:
            if temporary.exists():
                _remove_work_tree(temporary,target.parent)

