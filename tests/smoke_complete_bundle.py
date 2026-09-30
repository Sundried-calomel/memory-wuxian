"""Check complete delivery, standard dashboard entry and unchanged installer files."""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import zipfile

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/'installer'))
from package import digest, read_package
from transaction import Installer
from platform_runtime import PlatformRuntime

parser=argparse.ArgumentParser()
parser.add_argument('--platform',required=True)
args=parser.parse_args()
version=(REPO/'VERSION').read_text('utf-8').strip()
payload=REPO/'dist'/f'memory-wuxian-{version}-{args.platform}.zip'
complete=payload.with_name(payload.stem+'-complete.zip')
with tempfile.TemporaryDirectory() as temporary:
    root=Path(temporary).resolve()/'product'; root.mkdir()
    with zipfile.ZipFile(complete) as bundle:
        bundle.extractall(root)
        for info in bundle.infolist():
            if os.name!='nt': os.chmod(root/info.filename,0o755 if info.filename.startswith('bin/') else 0o644)
    metadata=json.loads((root/'BUNDLE.json').read_bytes())
    assert metadata['installer_version']=='1.1.0'
    for name,checksum in metadata['installer_files'].items():
        assert digest((root/name).read_bytes())==checksum
    engine={p:(p.read_bytes(),p.stat().st_mtime_ns) for p in (root/'installer').glob('*') if p.is_file()}
    config=root/'core/live-config.json';config.write_text('{}',encoding='utf-8')
    with socket.socket() as sock: sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    process=subprocess.Popen([sys.executable,'-B',str(root/'core/dashboard.py'),'--root',str(root/'archive'),
        '--config',str(config),'--port',str(port)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    def local_get(path):
        # Explicit loopback must not inherit a runner's system HTTP proxy.
        connection=http.client.HTTPConnection('127.0.0.1',port,timeout=2)
        try:
            connection.request('GET',path)
            response=connection.getresponse()
            if response.status!=200: raise RuntimeError(f'{path}: HTTP {response.status}')
            return response.read()
        finally: connection.close()
    try:
        deadline=time.monotonic()+20
        while True:
            try:
                status=json.loads(local_get('/api/update'))
                break
            except OSError:
                if process.poll() is not None or time.monotonic()>deadline:
                    print('Child pid:',process.pid,'port:',port,'alive:',process.poll() is None,flush=True)
                    raise RuntimeError('standard dashboard did not expose updates')
                time.sleep(.1)
        assert status['current']==version and 'candidate' not in status
        html=local_get('/').decode('utf-8')
        assert 'data-update-check' in html and 'data-update-install' in html
    finally:
        process.terminate();stdout,stderr=process.communicate(timeout=10)
        if stderr: print(stderr.decode('utf-8','replace'))
    installer=Installer(root)
    package=read_package(payload,digest(payload.read_bytes()))
    package['files']['README.md']+=b'\nSynthetic compatible update.\n'
    result=installer.apply(package,PlatformRuntime(offline=True))
    assert result['changed']==['README.md']
    assert all(p.read_bytes()==data and p.stat().st_mtime_ns==stamp for p,(data,stamp) in engine.items())
    assert config.read_text('utf-8')=='{}'
print('Complete package: bundled installer 1.1.0; default dashboard has updates; program update leaves installer bytes and mtimes untouched.')
