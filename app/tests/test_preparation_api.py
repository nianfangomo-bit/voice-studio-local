import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from server import make_app
from studio import Studio


class RecordingApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.s = Studio(Path(self.temp.name))
        self.client = TestClient(make_app(self.s, 'test-token', 8799), base_url='http://127.0.0.1:8799')
        self.client.headers['Origin'] = 'http://127.0.0.1:8799'
        self.client.cookies.set('voice_session', 'test-token')

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def create(self):
        response = self.client.post('/api/recording-datasets', json={'name':'测试','authorized':True})
        self.assertEqual(response.status_code, 200)
        return response.json()['id']

    def test_upload_and_no_original_overwrite(self):
        did = self.create()
        response = self.client.post(f'/api/datasets/{did}/recordings?filename=sample.wav', content=b'fixture')
        self.assertEqual(response.status_code, 200)
        first = response.json()['recordings'][0]['path']
        response = self.client.post(f'/api/datasets/{did}/recordings?filename=sample.wav', content=b'another')
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(first, response.json()['recordings'][1]['path'])
        self.assertEqual(Path(first).read_bytes(), b'fixture')

    def test_empty_upload_and_unsupported_extension(self):
        did = self.create()
        self.assertEqual(self.client.post(f'/api/datasets/{did}/recordings?filename=a.wav', content=b'').status_code,400)
        self.assertEqual(self.client.post(f'/api/datasets/{did}/recordings?filename=a.exe', content=b'x').status_code,400)
        self.assertFalse(self.s.get('datasets',did)['recordings'])

    def test_origin_and_cookie_required(self):
        did = self.create()
        self.client.headers['Origin']='https://example.com'
        self.assertEqual(self.client.post(f'/api/datasets/{did}/recordings?filename=a.wav',content=b'x').status_code,403)
        self.client.headers['Origin']='http://127.0.0.1:8799'
        self.client.cookies.clear()
        self.assertEqual(self.client.post(f'/api/datasets/{did}/recordings?filename=a.wav',content=b'x').status_code,401)


if __name__=='__main__':unittest.main()
