import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import launcher

class LauncherTests(unittest.TestCase):
    def test_ready_before_exit(self):
        process=Mock();process.poll.return_value=0
        with patch.object(launcher,'existing_session',return_value='http://127.0.0.1:8791/#test'):
            url,error=launcher.wait_for_service(process)
        self.assertIsNone(error);self.assertTrue(url)
    def test_exited_is_not_success(self):
        process=Mock();process.poll.return_value=1
        with patch.object(launcher,'existing_session',return_value=None):
            url,error=launcher.wait_for_service(process)
        self.assertIsNone(url);self.assertIn('已有',error)
    def test_timeout_does_not_kill_service(self):
        process=Mock()
        url,error=launcher.wait_for_service(process,timeout=0)
        self.assertIsNone(url);self.assertIn('尚未就绪',error)
        process.terminate.assert_not_called()
    def test_bad_session_never_contacts_network(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'data').mkdir()
            for url in ('https://example.com/#secret','http://127.0.0.1:8791/','http://127.0.0.1:bad/#secret'):
                (root/'data/session.json').write_text(json.dumps({'url':url}),encoding='utf-8')
                with patch.object(launcher,'ROOT',root),patch('urllib.request.build_opener') as opener:
                    self.assertIsNone(launcher.existing_session());opener.assert_not_called()
    def test_redirect_is_rejected(self):
        self.assertIsNone(launcher.NoRedirect().redirect_request(None,None,302,'',{},'https://example.com'))

if __name__=='__main__':unittest.main()
