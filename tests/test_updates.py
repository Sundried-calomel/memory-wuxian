import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'installer'))
import updates
from package import CONTRACT
from transaction import Installer, write_json


class UpdatesTests(unittest.TestCase):
    def test_status_is_local_and_check_is_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve()/'product'; installer=Installer(root)
            write_json(installer.state,dict(root=str(root),version='2.20.1',compatibility=CONTRACT,files={}))
            with patch.object(updates,'release',return_value={'version':'2.20.3'}) as remote:
                self.assertEqual(updates.status(root)['current'],'2.20.1');remote.assert_not_called()
                self.assertTrue(updates.check(root)['candidate']['available']);remote.assert_called_once_with(None)
                write_json(installer.state,dict(root=str(root),version='2.20.3',compatibility=CONTRACT,files={}))
                self.assertFalse(updates.status(root)['candidate']['available'])

    def test_release_rejects_other_repositories_and_prereleases(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self,*a): pass
            def read(self,*a): return json.dumps(payload).encode()
        payload={'tag_name':'v2.20.3','prerelease':True}
        with patch.object(updates,'urlopen',return_value=Response()):
            with self.assertRaisesRegex(ValueError,'formal'): updates.release()
            payload={'tag_name':'v2.20.3','assets':[{'name':f'memory-wuxian-2.20.3-{updates.host_platform()}.zip',
                     'browser_download_url':'https://example.com/a.zip','digest':'sha256:'+'0'*64,'size':123}]}
            with self.assertRaisesRegex(ValueError,'trusted'): updates.release()

    def test_unchecked_launch_is_refused(self):
        if updates.os.name!='nt': self.skipTest('Windows manual launch')
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError,'check and select'): updates.launch(Path(folder).resolve()/'product','2.20.3')


if __name__=='__main__': unittest.main()
