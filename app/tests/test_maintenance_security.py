"""维护清理的回归检查：路由、文件边界和已有功能入口必须保持明确。"""
import tempfile
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from server import make_app
from studio import Studio, uid


class MaintenanceSecurityTests(unittest.TestCase):
    # 创建隔离服务和数据库，不启动训练线程或读取真实用户数据。
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.studio = Studio(self.base / 'data')
        self.app = make_app(self.studio, 'fixture-secret', 8791)
        self.client = TestClient(self.app, base_url='http://127.0.0.1:8791')
        self.client.headers['Origin'] = 'http://127.0.0.1:8791'
        self.client.cookies.set('voice_session', 'fixture-secret')

    # 关闭测试客户端，只清理本次测试独占的临时目录。
    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    # 确认未暴露默认 API 文档和常见开发接口；不存在不等于曾删除过。
    def test_debug_routes_absent(self):
        for path in ('/docs', '/redoc', '/openapi.json', '/api/debug', '/api/test', '/api/reset', '/api/exec'):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    # 对每条实际注册业务路由检查无会话请求均被拦截，避免清理时漏掉认证。
    def test_all_api_routes_require_session(self):
        self.client.cookies.clear()
        routes = [r for r in self.app.routes if r.path.startswith('/api/')]
        self.assertEqual(len(routes), 21)
        for route in routes:
            method = sorted(route.methods - {'HEAD', 'OPTIONS'})[0]
            path = route.path
            for parameter in ('vid', 'did', 'rid', 'pid', 'sid', 'jid', 'action', 'eid', 'filename'):
                path = path.replace('{'+parameter+'}', 'fixture')
            with self.subTest(path=path):
                self.assertEqual(self.client.request(method, path).status_code, 401)

    # 模拟记录被外部篡改，素材试听不能读取数据目录外的文件。
    def test_dataset_audio_rejects_external_path(self):
        outside = self.base / 'private.wav'
        outside.write_bytes(b'private')
        self.studio.save('datasets', {'id':'ds', 'rows':[{'id':'row', 'audio':str(outside)}]})
        response = self.client.get('/api/datasets/ds/audio/row')
        self.assertEqual(response.status_code, 400)
        self.assertNotIn(b'private', response.content)

    # 合法素材试听保持可用，路径安全检查不能误伤本地文件。
    def test_dataset_audio_inside_store_still_available(self):
        path = self.studio.root / 'clip.wav'
        path.write_bytes(b'fixture audio')
        self.studio.save('datasets', {'id':'ds', 'rows':[{'id':'row', 'audio':str(path)}]})
        self.assertEqual(self.client.get('/api/datasets/ds/audio/row').content, b'fixture audio')

    # 下载记录不能通过替换导出目录读取库外同名文件。
    def test_export_rejects_external_folder(self):
        self.studio.save('projects', {'id':'project', 'exports':[{'id':'version','folder':str(self.base)}]})
        with self.assertRaises(ValueError):
            self.studio.file('project', 'version', 'narration.wav')

    # 同一数据库中的其他项目也不是当前项目的合法导出目录。
    def test_export_rejects_another_project_folder(self):
        folder = self.studio.root / 'projects' / uid() / 'exports' / 'version'
        self.studio.save('projects', {'id':'project', 'exports':[{'id':'version','folder':str(folder)}]})
        with self.assertRaises(ValueError):
            self.studio.file('project', 'version', 'subtitles.srt')

    # 静态文件必须使用固定允许列表，不能通过静态接口取得 Python 源码。
    def test_static_source_files_are_not_served(self):
        self.assertEqual(self.client.get('/static/server.py').status_code, 404)
        self.assertEqual(self.client.get('/static/style.css').status_code, 200)


if __name__ == '__main__':
    unittest.main()
