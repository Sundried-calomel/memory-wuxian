"""Explicit release checks and downloads; no timers or background discovery."""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen
from package import MAX_PACKAGE, host_platform, read_package
from transaction import Installer, atomic, lock, parse, write_json
from platform_runtime import PlatformRuntime

REPOSITORY = 'Sundried-calomel/memory-wuxian'
BUSY = {'queued','downloading','applying'}


def release(version=None):
    if version is not None and not re.fullmatch(r'\d+\.\d+\.\d+',version):
        raise ValueError('invalid release version')
    endpoint = 'tags/v'+version if version else 'latest'
    request = Request(f'https://api.github.com/repos/{REPOSITORY}/releases/{endpoint}',
                      headers={'Accept':'application/vnd.github+json','User-Agent':'MemoryWuxian-Updater'})
    with urlopen(request,timeout=30) as response:
        raw=response.read(2_000_001)
    if len(raw)>2_000_000: raise ValueError('release metadata too large')
    data=parse(raw)
    tag=data.get('tag_name','')
    if data.get('draft') or data.get('prerelease') or not re.fullmatch(r'v\d+\.\d+\.\d+',tag):
        raise ValueError('only formal product releases are supported')
    selected=tag[1:]
    if version and selected!=version: raise ValueError('release identity mismatch')
    name=f'memory-wuxian-{selected}-{host_platform()}.zip'
    asset=next((a for a in data['assets'] if a['name']==name),None)
    if not asset: raise ValueError('no release package for this device')
    url=f'https://github.com/{REPOSITORY}/releases/download/{tag}/{name}'
    checksum=asset.get('digest') or ''
    if asset.get('browser_download_url')!=url or not re.fullmatch(r'sha256:[0-9a-f]{64}',checksum):
        raise ValueError('release lacks a trusted asset URL or digest')
    if not 0<asset.get('size',0)<=MAX_PACKAGE: raise ValueError('invalid asset size')
    return dict(version=selected,name=name,url=url,sha256=checksum[7:],size=asset['size'])


def status(root):
    installer=Installer(root)
    state=installer.read_state() or {}
    result={'current':state.get('version'),'local_baseline':state.get('local_baseline',False),
            'live_supported':os.name=='nt','job':{'phase':'idle'}}
    for filename,key in [('update-selection.json','candidate'),('update-status.json','job')]:
        path=installer.control/filename
        if path.exists(): result[key]=parse(path.read_bytes())
    if result.get('candidate') and state.get('version'):
        result['candidate']['available']=tuple(map(int,result['candidate']['version'].split('.')))>tuple(map(int,state['version'].split('.')))
    return result


def check(root, version=None):
    installer=Installer(root)
    state=installer.read_state()
    if state is None: raise ValueError('adopt this installation before updating')
    selected=release(version)
    selected['available']=tuple(map(int,selected['version'].split('.')))>tuple(map(int,state['version'].split('.')))
    selected['checked_at']=time.time()
    write_json(installer.control/'update-selection.json',selected)
    return status(root)


def update(root, version, *, offline=False):
    installer=Installer(root)
    path=installer.control/'update-status.json'
    with lock(installer.control/'update-job.lock',timeout=0):
        def record(phase, **extra):
            write_json(path,dict(phase=phase,version=version,updated_at=time.time(),pid=os.getpid(),**extra))
        try:
            record('downloading')
            selected=release(version)
            request=Request(selected['url'],headers={'User-Agent':'MemoryWuxian-Updater'})
            with urlopen(request,timeout=60) as response:
                raw=response.read(MAX_PACKAGE+1)
            if len(raw)!=selected['size']: raise ValueError('incomplete or oversized download')
            package_path=installer.control/selected['name']
            atomic(package_path,raw)
            package=read_package(package_path,selected['sha256'])
            record('applying')
            result=installer.apply(package,PlatformRuntime(offline=offline))
            record('done',result=result)
            return result
        except BaseException as exc:
            record('error',error=str(exc)[:2000])
            raise


def launch(root, version):
    """Run outside the dashboard's OS task, which the transaction must stop."""
    if os.name!='nt': raise ValueError('stop services and use CLI update --offline on this platform')
    installer=Installer(root)
    with lock(installer.control/'update-launch.lock',timeout=0):
        current=status(root)
        selected=current.get('candidate') or {}
        if selected.get('version')!=version or not selected.get('available'):
            raise ValueError('check and select an available release first')
        job=current['job']
        if job.get('phase') in BUSY and time.time()-job.get('updated_at',0)<900:
            raise ValueError('an update is already in progress')
        cli=Path(__file__).with_name('cli.py')
        arguments=subprocess.list2cmdline(['-B','-X','utf8',str(cli),'update','--target',str(installer.root),'--version',version])
        write_json(installer.control/'update-status.json',dict(phase='queued',version=version,updated_at=time.time()))
        try:
            PlatformRuntime().powershell("""
            $action=New-ScheduledTaskAction -Execute $env:MW_PYTHON -Argument $env:MW_ARGUMENTS
            $principal=New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
            $settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
            Register-ScheduledTask -TaskName 'MemoryWuxianManualUpdate' -Action $action -Principal $principal -Settings $settings -Force | Out-Null
            Start-ScheduledTask -TaskName 'MemoryWuxianManualUpdate'
            """,PYTHON=sys.executable,ARGUMENTS=arguments)
        except Exception as exc:
            write_json(installer.control/'update-status.json',dict(phase='error',error=str(exc),updated_at=time.time()))
            raise RuntimeError('cannot start update task: '+str(exc)) from exc
    return status(root)
