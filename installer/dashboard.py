"""Attach explicit update controls to the product dashboard across replacements."""
import argparse
import importlib.util
import json
from pathlib import Path
import secrets
import sys
from urllib.parse import urlsplit

sys.path.insert(0,str(Path(__file__).resolve().parent))
from transaction import Installer, atomic
from updates import check, launch, status

PANEL = r'''<script>
(()=>{
const token=__TOKEN__, section=document.createElement('section');
section.className='panel';section.id='product-update';section.style.marginTop='12px';
section.innerHTML='<h2>软件更新</h2><p data-update-status role="status" aria-live="polite"></p><button type="button" data-update-check>检查更新</button> <button type="button" data-update-install disabled>升级</button><p class="label" data-update-note></p>';
document.querySelector('#system-overview').after(section);
const text=section.querySelector('[data-update-status]'),checkButton=section.querySelector('[data-update-check]'),installButton=section.querySelector('[data-update-install]');
let current=null, waiting=false, timer=null, failures=0;
const copy=()=>({zh:{title:'软件更新',check:'检查更新',install:'升级到 ',current:'当前版本：',local:'本机自定义版本',latest:'最新发布：',none:'尚未检查',note:'仅点击时检查或升级。升级期间状态台会短暂重启，配置和记忆保留。',busy:'正在下载或安装，请稍候…',done:'升级完成',restart:'状态台正在重启…',offline:'此平台请停机后通过命令行升级'},en:{title:'Software updates',check:'Check for updates',install:'Upgrade to ',current:'Installed: ',local:'Local custom build',latest:'Latest release: ',none:'Not checked',note:'Checks and updates run only when clicked. The dashboard briefly restarts; settings and memories are preserved.',busy:'Downloading or installing…',done:'Update complete',restart:'Dashboard restarting…',offline:'Stop services and update using the CLI on this platform'},ja:{title:'ソフトウェア更新',check:'更新を確認',install:'更新先：',current:'現在：',local:'ローカル変更版',latest:'最新：',none:'未確認',note:'クリック時のみ確認・更新します。更新中は画面が再起動します。設定と記憶は保持されます。',busy:'ダウンロード・更新中…',done:'更新完了',restart:'再起動中…',offline:'この環境では停止後に CLI で更新してください'}})[typeof language==='string'?language:'zh'];
function render(data){current=data;const t=copy(),job=data.job||{},busy=['queued','downloading','applying'].includes(job.phase);section.querySelector('h2').textContent=t.title;section.querySelector('[data-update-note]').textContent=t.note;checkButton.textContent=t.check;installButton.textContent=data.candidate&&!data.candidate.available?({zh:'已是最新版本',en:'Up to date',ja:'最新バージョンです'})[typeof language==='string'?language:'zh']:t.install+(data.candidate?.version||'—');checkButton.disabled=busy;installButton.disabled=busy||!data.candidate?.available||!data.live_supported;text.textContent=t.current+(data.local_baseline?t.local:(data.current||'—'))+' · '+t.latest+(data.candidate?.version||t.none)+(busy?' · '+t.busy:job.phase==='error'?' · '+job.error:job.phase==='done'?' · '+t.done:'')+(!data.live_supported?' · '+t.offline:'');if(waiting&&job.phase==='done'){location.reload();return}waiting=busy;if(busy)poll();}
function poll(){clearTimeout(timer);timer=setTimeout(readStatus,3000)}
async function readStatus(){try{const r=await fetch('/api/update',{cache:'no-store'});if(!r.ok)throw Error(r.status);failures=0;render(await r.json())}catch(e){if(waiting&&failures++<120){text.textContent=copy().restart;poll()}else{text.textContent=String(e);checkButton.disabled=false}}}
async function action(action){checkButton.disabled=true;installButton.disabled=true;try{const r=await fetch('/api/update',{method:'POST',headers:{'Content-Type':'application/json','X-Update-Token':token},body:JSON.stringify({action,...(action==='start'?{version:current.candidate.version}:{})})});const d=await r.json();if(!r.ok)throw Error(d.error||r.status);render(d)}catch(e){text.textContent=String(e);checkButton.disabled=false}}
checkButton.onclick=()=>action('check');installButton.onclick=()=>action('start');
document.addEventListener('click',e=>{if(e.target.closest('[data-language]')&&current)render(current)});
readStatus();
})();</script>'''


def integrated_server(root, archive, config, *, port=8765):
    root=Path(root).resolve()
    sys.path.insert(0,str(root/'core'))
    spec=importlib.util.spec_from_file_location('memory_product_dashboard',root/'core/dashboard.py')
    product=importlib.util.module_from_spec(spec);spec.loader.exec_module(product)
    token=secrets.token_urlsafe(32)
    page=Installer(root).control/'dashboard-update.html'
    html=(root/'core/dashboard.html').read_text('utf-8')
    atomic(page,html.replace('</body>',PANEL.replace('__TOKEN__',json.dumps(token))+'</body>').encode('utf-8'))
    server=product.make_server(product.MemoryRuntime(archive),port=port,html_path=page,config=config)
    base=server.RequestHandlerClass
    class Handler(base):
        def do_GET(self):
            if urlsplit(self.path).path!='/api/update': return super().do_GET()
            if not self._host_ok(): return self._json(403,{'error':'invalid host'})
            try: self._json(200,status(root))
            except (ValueError,OSError) as exc: self._json(400,{'error':str(exc)})

        def do_POST(self):
            if urlsplit(self.path).path!='/api/update': return super().do_POST()
            if (not self._host_ok() or self.headers.get('Origin')!='http://'+self.headers.get('Host','')
                or self.headers.get('Content-Type','').split(';')[0]!='application/json'
                or not secrets.compare_digest(self.headers.get('X-Update-Token',''),token)):
                return self._json(403,{'error':'same-origin update action required; reload the page'})
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=1024: raise ValueError('invalid request size')
                body=json.loads(self.rfile.read(length))
                if body=={'action':'check'}: result=check(root)
                elif isinstance(body,dict) and set(body)=={'action','version'} and body['action']=='start':
                    result=launch(root,body['version'])
                else: raise ValueError('unknown update action')
                self._json(200,result)
            except (ValueError,RuntimeError,OSError) as exc: self._json(400,{'error':str(exc)})
    server.RequestHandlerClass=Handler
    return server


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True,help='archive root, as in the product dashboard')
    parser.add_argument('--config',required=True)
    parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args()
    product_root=Path(__file__).resolve().parent.parent
    config=json.loads(Path(args.config).read_text('utf-8-sig'))
    with integrated_server(product_root,args.root,config,port=args.port) as server:
        server.serve_forever()
