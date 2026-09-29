"""One scheduled operational tick. No second permanent collection loop."""
import argparse
import datetime as dt
import json
import time
import os
from pathlib import Path
from runtime import MemoryRuntime
from collector import DirectCollector
from storage import atomic_write_json, exclusive_lock
from summary import CodexCLIModel

def sync_environment(runtime, transport, config):
    settings=config.get('environment')
    if not settings:
        return {'state':'not-configured'}
    from environment import EnvironmentService
    from environment_sync import run_once
    return run_once(runtime.store,EnvironmentService(settings['root']),
                    {'transport':transport,'environment':settings,'receive_limit':8})

def run_tick(config_path):
    config=json.loads(Path(config_path).read_text('utf-8-sig'))
    root=Path(config['root'])
    model=CodexCLIModel(config['codex'],config['model']) if config.get('auto_summary') else None
    runtime=MemoryRuntime(root,model=model)
    with exclusive_lock(root/'.live-tick.lock'):
        state_path=root/'live-status.json'
        state=json.loads(state_path.read_text('utf-8')) if state_path.exists() else {}
        now = dt.datetime.now(dt.timezone.utc)
        attempts = [stamp for stamp in state.get('attempts_last_hour', [])
                    if (now - dt.datetime.fromisoformat(stamp)).total_seconds() < 3600]
        attempts.append(now.isoformat())
        state.update(attempt_at=now.isoformat(),status='running',pid=os.getpid(),
                     phase='collection',attempts_last_hour=attempts)
        atomic_write_json(state_path,state)
        try:
            state['collection']=DirectCollector(runtime.store,config['sessions_root']).run_once()
            state['collection_completed_at']=dt.datetime.now(dt.timezone.utc).isoformat()
            state['summary_errors']={}
            state['auto_summary']=bool(model)
            state['phase']='summaries'
            state['summary_progress']={'checked':0,'remaining':0}
            atomic_write_json(state_path,state)
            if model:
                with runtime.store.connection() as db:
                    conversations=[row[0] for row in db.execute('SELECT DISTINCT conversation FROM messages WHERE sequence>?',
                        (state.get('summary_cursor',config.get('summary_start_sequence',0)),))]
                excluded=runtime.store.excluded_conversations()
                conversations=[conversation for conversation in conversations if conversation not in excluded]
                state['summary_progress']['remaining']=len(conversations)
                atomic_write_json(state_path,state)
                for conversation in conversations:
                    try:
                        runtime.summarize_due(conversation,config.get('summary_rounds',5),config.get('summary_start_sequence',0))
                        runtime.summarize_parents_due(conversation)
                    except Exception as exc:
                        state['summary_errors'][conversation]=str(exc)
                    state['summary_progress']['checked']+=1
                    state['summary_progress']['remaining']-=1
                    atomic_write_json(state_path,state)
                if not state['summary_errors']:
                    state['summary_cursor']=runtime.store.status()['last_sequence']
            state['phase']='sync'
            atomic_write_json(state_path,state)
            if config.get('sync'):
                from core_sync import CoreSyncService
                service=CoreSyncService(runtime.store,**config['sync'])
                try:
                    state['sync']=service.sync_once()
                except (ValueError,RuntimeError,OSError) as exc:
                    state['sync']={'status':'error','error':str(exc)}
                try:
                    state['environment']=sync_environment(runtime,service,config)
                except (ValueError,RuntimeError,OSError) as exc:
                    state['environment']={'state':'error','error':str(exc)}
            else:
                state['sync']={'status':'not-configured'}
            sequence=runtime.store.status()['last_sequence']
            summary_count=runtime.status()['summary_count']
            generation=[sequence,summary_count,state['sync'].get('received_batches',0)]
            state['backup_pending']=bool(config.get('backup') and state.get('backed_up_generation')!=generation)
            state['phase']='backup'
            atomic_write_json(state_path,state)
            if state['backup_pending'] and time.time()-state.get('backup_at',0)>=config.get('backup_interval_seconds',900):
                state['backup']=runtime.backup.create(config['backup'],config.get('retention',1))
                state['backed_up_generation']=generation
                state['backup_at']=time.time()
                state['backup_pending']=False
            state.update(status='completed-with-errors' if state['collection']['errors'] or state['summary_errors'] or state['sync'].get('status')=='error' or state.get('environment',{}).get('state') in {'error','partial'} else 'completed',
                         completed_at=dt.datetime.now(dt.timezone.utc).isoformat(),phase='idle')
            state.pop('error',None)
        except Exception as exc:
            state.update(status='error',error=str(exc))
            atomic_write_json(state_path,state)
            raise
        atomic_write_json(state_path,state)
        return state

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True)
    args=parser.parse_args()
    print(json.dumps(run_tick(args.config),ensure_ascii=False))

if __name__=='__main__': main()
