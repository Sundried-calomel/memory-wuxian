"""Configure the portable runtime; keep paths and identities on this device."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from archive import ArchiveStore
from storage import atomic_write_json


def register_bundled_installation(root):
    """Enroll an untouched extracted complete package once, without network access."""
    root=Path(root).resolve()
    if not (root/'BUNDLE.json').is_file(): return
    sys.path.insert(0,str(root/'installer'))
    from package import CONTRACT, ENGINE_PROTOCOL, digest, host_platform, parse
    from transaction import Installer, lock, target_path, write_json
    installer=Installer(root)
    with lock(installer.control/'lock'):
        if installer.state.exists(): return
        if installer.journal.exists(): raise ValueError('recover interrupted installation before enrollment')
        manifest=parse((root/'MANIFEST.json').read_bytes())
        descriptor=parse((root/'INSTALL.json').read_bytes())
        version=(root/'VERSION').read_text('utf-8').strip()
        if descriptor!={'installer_protocol':ENGINE_PROTOCOL,'version':version,'platform':host_platform(),'compatibility':CONTRACT}:
            raise ValueError('complete package is not compatible with this device')
        owned={}
        for name,expected in manifest.items():
            path=root/name if name=='INSTALL.json' else target_path(root,name)
            if digest(path.read_bytes())!=expected: raise ValueError('extracted program differs: '+name)
            if name!='INSTALL.json': owned[name]={'sha256':expected,'mode':0o755 if name.startswith('bin/') else 0o644}
        bundle=parse((root/'BUNDLE.json').read_bytes())
        if bundle['product_version']!=version: raise ValueError('bundle version mismatch')
        write_json(installer.state,dict(root=str(root),version=version,compatibility=CONTRACT,
            platform=host_platform(),package_sha256=bundle['payload_sha256'],files=owned))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True)
    parser.add_argument('--sessions',required=True)
    parser.add_argument('--codex',required=True)
    parser.add_argument('--backup',required=True)
    parser.add_argument('--config',default=str(Path(__file__).with_name('live-config.json')))
    parser.add_argument('--model',default='gpt-6-luna')
    parser.add_argument('--interval-seconds',type=int,default=60,
                        help='display the interval used by the operating-system scheduler')
    parser.add_argument('--node-id',required=True)
    parser.add_argument('--identity',required=True)
    parser.add_argument('--exchange')
    parser.add_argument('--peer',help='Explicitly trusted peer public identity JSON')
    args=parser.parse_args()
    config_path=Path(args.config).resolve()
    if config_path.exists():raise ValueError('config exists; edit selected settings explicitly')
    sessions=Path(args.sessions).resolve(strict=True)
    codex=Path(args.codex).resolve(strict=True)
    if not sessions.is_dir() or not codex.is_file():raise ValueError('invalid sessions or Codex path')
    binary=Path(__file__).resolve().parent.parent/'bin'/('memory-wuxian-envelope.exe' if os.name=='nt' else 'memory-wuxian-envelope')
    identity=Path(args.identity).resolve()
    command='show-identity' if identity.exists() else 'init-identity'
    cmd=[str(binary),command,'--path',str(identity)]
    if command=='init-identity':cmd+=['--node-id',args.node_id]
    local=json.loads(subprocess.check_output(cmd,text=True,encoding='utf-8'))
    if local['node_id']!=args.node_id:raise ValueError('identity belongs to another node')
    config={'root':str(Path(args.root).resolve()),'sessions_root':str(sessions),
            'codex':str(codex),'model':args.model,'auto_summary':True,'summary_rounds':5,
            'backup':str(Path(args.backup).resolve()),'retention':1,'backup_interval_seconds':900,
            'interval_seconds':args.interval_seconds}
    if bool(args.peer)!=bool(args.exchange):raise ValueError('peer and exchange must be supplied together')
    if args.peer:
        peer=json.loads(Path(args.peer).read_text('utf-8'))
        config['sync']={'binary':str(binary),'identity':str(identity),'local_node_id':args.node_id,
                        'peer_id':peer['node_id'],'peer_encryption_public_key':peer['encryption_public_key'],
                        'peer_signing_public_key':peer['signing_public_key'],'exchange_root':str(Path(args.exchange).resolve())}
    register_bundled_installation(Path(__file__).resolve().parent.parent)
    ArchiveStore(config['root'])
    atomic_write_json(config_path,config)
    if os.name!='nt':config_path.chmod(0o600)
    print(json.dumps({'config':str(config_path),'public_identity':local,'sync_configured':bool(args.peer)}))

if __name__=='__main__':main()
