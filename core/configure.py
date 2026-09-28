"""Configure the portable runtime; keep paths and identities on this device."""
import argparse
import json
import os
import subprocess
from pathlib import Path
from archive import ArchiveStore
from storage import atomic_write_json

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True)
    parser.add_argument('--sessions',required=True)
    parser.add_argument('--codex',required=True)
    parser.add_argument('--backup',required=True)
    parser.add_argument('--config',default=str(Path(__file__).with_name('live-config.json')))
    parser.add_argument('--model',default='gpt-6-luna')
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
            'backup':str(Path(args.backup).resolve()),'retention':1,'backup_interval_seconds':900}
    if bool(args.peer)!=bool(args.exchange):raise ValueError('peer and exchange must be supplied together')
    if args.peer:
        peer=json.loads(Path(args.peer).read_text('utf-8'))
        config['sync']={'binary':str(binary),'identity':str(identity),'local_node_id':args.node_id,
                        'peer_id':peer['node_id'],'peer_encryption_public_key':peer['encryption_public_key'],
                        'peer_signing_public_key':peer['signing_public_key'],'exchange_root':str(Path(args.exchange).resolve())}
    ArchiveStore(config['root'])
    atomic_write_json(config_path,config)
    if os.name!='nt':config_path.chmod(0o600)
    print(json.dumps({'config':str(config_path),'public_identity':local,'sync_configured':bool(args.peer)}))

if __name__=='__main__':main()
