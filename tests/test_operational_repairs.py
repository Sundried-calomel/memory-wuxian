import json
from contextlib import closing
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
from token_usage import collect_usage
from environment import EnvironmentService
from core_sync import CoreSyncService
from summary import resolve_codex
from dashboard_data import DashboardData
from peer_bridge import PeerIndex
from archive import ArchiveStore

class OperationalRepairs(unittest.TestCase):
    def test_activity_transitions(self):
        from live import activity_mode
        self.assertEqual(activity_mode(1000,1005),('active',5))
        self.assertEqual(activity_mode(1000,1060),('idle',60))
        self.assertEqual(activity_mode(1000,1600),('deep-idle',300))

    def test_usage_resume_duplicate_and_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source.jsonl'
            def event(n): return dict(type='event_msg',timestamp='2026-10-01T00:00:00Z',payload=dict(type='token_count',info=dict(total_token_usage={'total_tokens':n})))
            source.write_text(''.join(json.dumps(event(n))+'\n' for n in [10,10,25,3]),encoding='utf-8')
            collect_usage(root,source,'a',2);collect_usage(root,source,'a',2);collect_usage(root,source,'a',2)
            ledger=json.loads(next((root/'token-usage').glob('*.json')).read_text())
            self.assertEqual(ledger['reported_usage']['total_tokens'],28)
            self.assertEqual(ledger['counter_reset_count'],1)
            self.assertEqual(ledger['model_request_count'],3)

    def test_matching_files_are_not_conflicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'files').mkdir();f=root/'files/a';f.write_bytes(b'old')
            env=EnvironmentService(root);env.bind('files','files','small-files')
            f.write_bytes(b'new');env.apply('files',{'a':b'new'})
            f.write_bytes(b'local change')
            with self.assertRaises(RuntimeError):env.apply('files',{'a':b'other'})

    def test_missing_receive_page_does_not_block_send(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);service=CoreSyncService.__new__(CoreSyncService)
            service.outbox=root/'out';service.inbox=root/'in';service.inbox.mkdir()
            (service.inbox/'page.mwe').write_bytes(b'x')
            service.ack_outbox=root/'acks';service.state_lock=root/'lock'
            service.max_batches=1;service.peer_id='peer';service.peer_index=Mock()
            service.peer_index.state.return_value={'last_wire_sequence':500}
            service._state=Mock(return_value={});service._state_save=Mock()
            service._process_acks=Mock(return_value=0);service._process_file_acks=Mock(return_value=0)
            service._accept_one=Mock(side_effect=ValueError('gap'))
            service._export_one=Mock(return_value={'queued':True});service.status=Mock(return_value={})
            result=service.sync_once()
            self.assertEqual(result['receive_error']['expected_sequence'],501)
            self.assertEqual(result['last_run']['sent_batches'],1)

    def test_peer_daily_incremental(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store=ArchiveStore(root/'source')
            store.append_message('user','peer text',conversation_id='peer:c',message_id='p1')
            peer=PeerIndex(root/'target');peer.ingest('peer',store.records('peer:c'))
            data=DashboardData();first=sum(v['messages'] for v in data.peer_daily(root/'target').values())
            with closing(peer._connect(write=True)) as db, db:
                db.execute("INSERT INTO records SELECT origin,id,'another-digest',kind,conversation,source_sequence,wire_sequence,normalized,payload FROM records LIMIT 1")
            second=sum(v['messages'] for v in data.peer_daily(root/'target').values())
            self.assertEqual((first,second),(1,1))

    def test_rotated_codex_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'bin';p=root/'current/codex.exe';p.parent.mkdir(parents=True);p.write_bytes(b'x')
            self.assertEqual(resolve_codex(root/'removed/codex.exe'),str(p))

if __name__=='__main__':unittest.main()
