import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
import mcp_server as mcp
import environment_sync as env


class Tests(unittest.TestCase):
    def call(self, params):
        runtime = SimpleNamespace(status=lambda: {'healthy':True})
        return mcp.dispatch(runtime, {'jsonrpc':'2.0','id':1,'method':'tools/call','params':params}, {'initialized':True})

    def test_metadata(self):
        for meta in ({}, {'progressToken':1}, {'client/example': {'value':True}}):
            self.assertIn('result',self.call({'name':'memory.status','arguments':{},'_meta':meta}))

    def test_legacy(self):
        self.assertIn('result',self.call({'name':'memory.status'}))

    def test_invalid_metadata(self):
        for meta in (None, 'bad', [], 1):
            self.assertIn('error', self.call({'name':'memory.status','_meta':meta}))

    def test_unknown_parameter(self):
        self.assertIn('error',self.call({'name':'memory.status','unexpected':True}))

    def test_business_arguments_still_checked(self):
        self.assertIn('error',self.call({'name':'memory.status','arguments':{'_meta':{}}}))

    def test_waiting_and_other_package(self):
        files = {'AGENTS.md':b'Use {{MW_PATH:orf1-project-agents}}',
                 'path-keys.json':b'{"format":"memory-wuxian-path-refs-v1","keys":["orf1-project-agents"]}'}
        first = {'verified':{'artifact_id':'global-rule:codex-agents','revision_id':env.revision_id('global-codex-agents',files),'files':files}}
        second = {'verified':{'artifact_id':'global-skill:memory-wuxian','revision_id':'test-revision','files':{'SKILL.md':b'entry'}}}
        applied, acks = [], []
        service = SimpleNamespace(_read=lambda:{'bindings':{'global-codex-agents':{'strategy':'whole-file'},'global-memory-wuxian-skill':{'strategy':'skill-tree'}}},
            apply=lambda name, data, **kw: applied.append(name) or {'status':'applied'})
        transport = SimpleNamespace(receive_files=lambda **kw:[first,second],acknowledge_files=lambda item:acks.append(item))
        original_validate, original_revision = env.validate_files,env.revision_id
        with tempfile.TemporaryDirectory() as root, \
                patch.object(env,'validate_files',side_effect=lambda sid,f: f if sid=='memory-wuxian-core' else original_validate(sid,f)), \
                patch.object(env,'revision_id',side_effect=lambda sid,f: 'test-revision' if sid=='memory-wuxian-core' else original_revision(sid,f)):
            config={'transport':transport,'environment':{}}
            result=env.run_once(SimpleNamespace(root=Path(root)),service,config)
            self.assertEqual(result['state'],'completed')
            self.assertEqual(result['received'][0]['state'],'waiting-source')
            self.assertEqual(result['waiting_sources'],['orf1-project-agents'])
            self.assertEqual(applied,['global-memory-wuxian-skill'])
            self.assertEqual(acks,[second])
            config['environment']['path_bindings']={'orf1-project-agents':'D:/project/AGENTS.md'}
            result=env.run_once(SimpleNamespace(root=Path(root)),service,config)
            self.assertEqual(result['waiting_sources'],[])
            self.assertEqual(result['received'][0]['state'],'applied')

    def test_invalid_mapping_not_waiting(self):
        with self.assertRaises(ValueError) as error:
            env.render_agents({'AGENTS.md':b'{{MW_PATH:x}}','path-keys.json':b'{}'}, {'path_bindings':[]})
        self.assertNotIsInstance(error.exception,env.WaitingForSource)


if __name__=='__main__':
    unittest.main()
