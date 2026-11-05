import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from server import make_app
from studio import Studio


class RecordingApiTests(unittest.TestCase):
    # 中文维护：创建本测试独占的临时数据与夹具，不使用用户模型或作品。
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.s = Studio(Path(self.temp.name))
        self.client = TestClient(make_app(self.s, 'test-token', 8799), base_url='http://127.0.0.1:8799')
        self.client.headers['Origin'] = 'http://127.0.0.1:8799'
        self.client.cookies.set('voice_session', 'test-token')

    # 中文维护：释放测试资源并清理本测试创建的临时目录。
    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    # 中文维护：通过认证测试客户端创建隔离录音库。
    def create(self):
        response = self.client.post('/api/recording-datasets', json={'name':'测试','authorized':True})
        self.assertEqual(response.status_code, 200)
        return response.json()['id']

    # 中文维护：确认同名上传产生独立文件，不覆盖旧录音。
    def test_upload_and_no_original_overwrite(self):
        did = self.create()
        response = self.client.post(f'/api/datasets/{did}/recordings?filename=sample.wav', content=b'fixture')
        self.assertEqual(response.status_code, 200)
        first = response.json()['recordings'][0]['path']
        response = self.client.post(f'/api/datasets/{did}/recordings?filename=sample.wav', content=b'another')
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(first, response.json()['recordings'][1]['path'])
        self.assertEqual(Path(first).read_bytes(), b'fixture')

    # 中文维护：确认空文件和非法后缀被拒绝且不留下半成品。
    def test_empty_upload_and_unsupported_extension(self):
        did = self.create()
        self.assertEqual(self.client.post(f'/api/datasets/{did}/recordings?filename=a.wav', content=b'').status_code,400)
        self.assertEqual(self.client.post(f'/api/datasets/{did}/recordings?filename=a.exe', content=b'x').status_code,400)
        self.assertFalse(self.s.get('datasets',did)['recordings'])
        self.assertFalse(list(self.s.root.rglob('*.partial')))

    # 中文维护：确认流式超限仅删除当前临时文件，不删除历史成果。
    def test_oversize_stream_removes_only_own_partial(self):
        did = self.create()
        folder = self.s.root / 'datasets' / did / 'originals'
        folder.mkdir(parents=True)
        retained = folder / 'previous.wav'
        retained.write_bytes(b'keep')
        with patch('preparation.MAX_UPLOAD', 3):
            response = self.client.post(f'/api/datasets/{did}/recordings?filename=a.wav',
                                        content=b'too large', headers={'content-length':'0'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(retained.read_bytes(), b'keep')
        self.assertFalse(list(folder.glob('*.partial')))
        self.assertFalse(self.s.get('datasets',did)['recordings'])

    # 中文维护：确认恶意 Host 和缺失 Origin 的写操作被拒绝。
    def test_host_and_missing_origin_rejected(self):
        self.assertEqual(self.client.get('/api/state',headers={'host':'attacker.test'}).status_code,403)
        del self.client.headers['Origin']
        self.assertEqual(self.client.post('/api/recording-datasets',json={'name':'test','authorized':True}).status_code,403)

    # 中文维护：确认创建会话必须持有启动令牌并设置安全 Cookie。
    def test_connect_requires_start_token(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.post('/connect').status_code,401)
        response=self.client.post('/connect',headers={'x-start-token':'test-token'})
        self.assertEqual(response.status_code,200)
        self.assertIn('HttpOnly',response.headers['set-cookie'])
        self.assertIn('SameSite=strict',response.headers['set-cookie'])
        self.assertEqual(self.client.get('/api/state').status_code,200)

    # 中文维护：确认上传同时检查来源和认证会话。
    def test_origin_and_cookie_required(self):
        did = self.create()
        self.client.headers['Origin']='https://example.com'
        self.assertEqual(self.client.post(f'/api/datasets/{did}/recordings?filename=a.wav',content=b'x').status_code,403)
        self.client.headers['Origin']='http://127.0.0.1:8799'
        self.client.cookies.clear()
        self.assertEqual(self.client.post(f'/api/datasets/{did}/recordings?filename=a.wav',content=b'x').status_code,401)


if __name__=='__main__':unittest.main()
