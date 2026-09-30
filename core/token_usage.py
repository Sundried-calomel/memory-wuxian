"""Incremental model-reported usage ledger, separate from conversation text."""
import datetime as dt
import json
from pathlib import Path
from storage import atomic_write_json, bytes_sha256

FIELDS = ('input_tokens', 'cached_input_tokens', 'output_tokens', 'total_tokens')

def collect_usage(root, source, session, max_lines=2000):
    source = Path(source)
    key = bytes_sha256(str(source.resolve()).encode())
    path = Path(root) / 'token-usage' / (key + '.json')
    state = json.loads(path.read_text('utf-8')) if path.exists() else dict(
        measurement='codex-reported-model-usage', session_id=session, segment_id=key,
        conversation_id='codex:' + session, offset=0, reported_usage={}, daily_usage={},
        model_request_count=0, counter_reset_count=0)
    with source.open('rb') as handle:
        offset = state['offset']
        if source.stat().st_size < offset:
            raise ValueError('token ledger source truncated; explicit recovery required')
        if offset:
            handle.seek(max(0, offset-256))
            if bytes_sha256(handle.read(min(offset,256))) != state['anchor']:
                raise ValueError('token ledger source changed at cursor')
        handle.seek(offset)
        for _ in range(max_lines):
            start=handle.tell(); raw=handle.readline(16*1024*1024+1)
            if len(raw)>16*1024*1024: raise ValueError('oversized usage source line')
            if not raw or not raw.endswith(b'\n'):
                handle.seek(start); break
            event=json.loads(raw); payload=event.get('payload') or {}
            if event.get('type')=='event_msg' and payload.get('type')=='token_count':
                info=payload.get('info') or {}; total=info.get('total_token_usage')
                if isinstance(total,dict) and all(type(total.get(k,0)) is int and total.get(k,0)>=0 for k in FIELDS):
                    previous=state.get('last_counter',{})
                    reset=bool(previous) and total.get('total_tokens',0)<previous.get('total_tokens',0)
                    delta={k:max(0,total.get(k,0)-(0 if reset else previous.get(k,0))) for k in FIELDS}
                    if any(delta.values()):
                        stamp=event['timestamp'];day=dt.datetime.fromisoformat(stamp.replace('Z','+00:00')).astimezone().date().isoformat()
                        daily=state['daily_usage'].setdefault(day,{})
                        for k,v in delta.items():
                            state['reported_usage'][k]=state['reported_usage'].get(k,0)+v
                            daily[k]=daily.get(k,0)+v
                        state['model_request_count']+=1
                        state['counter_reset_count']+=int(reset)
                        state['updated_at']=stamp
                        state['latest_request_usage']=info.get('last_token_usage') or {}
                        state['model_context_window']=info.get('model_context_window')
                    state['last_counter']={k:total.get(k,0) for k in FIELDS}
            state['offset']=handle.tell()
        offset=state['offset'];handle.seek(max(0,offset-256))
        state['anchor']=bytes_sha256(handle.read(min(offset,256)))
    state['remaining_bytes']=max(0,source.stat().st_size-offset)
    if not path.exists() or json.loads(path.read_text('utf-8'))!=state:
        atomic_write_json(path,state)
    return state['remaining_bytes']
