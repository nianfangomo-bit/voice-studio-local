"""安装前助手的规则与接口回归；模拟结果不代表目标显卡实测。"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hardware_advisor import inspect_hardware
from server import make_app
from studio import Studio
from fastapi.testclient import TestClient


class HardwareAdvisorTests(unittest.TestCase):
    def test_compiled_rules(self):
        """执行发布助手内置六个规则案例，不查询目标显卡。"""
        helper = Path(__file__).resolve().parents[1] / 'HardwareAdvisor.exe'
        p = subprocess.run([str(helper), '--self-test'], timeout=20)
        self.assertEqual(p.returncode, 0)

    def test_timeout_is_not_unsupported(self):
        """检测超时不得伪装成显卡不支持。"""
        with patch('hardware_advisor.subprocess.run', side_effect=subprocess.TimeoutExpired('helper', 20)):
            with self.assertRaisesRegex(ValueError, '超时'):
                inspect_hardware()

    def test_bad_schema(self):
        """拒绝未知格式，不向用户展示伪造的兼容结论。"""
        with patch('hardware_advisor.subprocess.run', return_value=subprocess.CompletedProcess([], 0, b'{}')):
            with self.assertRaisesRegex(ValueError, '格式'):
                inspect_hardware()

    def test_authenticated_route(self):
        """新增接口沿用 Host、Origin、会话防护。"""
        with tempfile.TemporaryDirectory() as temp:
            app = make_app(Studio(Path(temp)), 'test-secret', 8791)
            with TestClient(app, base_url='http://127.0.0.1:8791') as client:
                self.assertEqual(client.get('/api/hardware-advice').status_code, 401)
                client.cookies.set('voice_session', 'test-secret')
                fixture = {'schema':'voice-hardware-report-v1', 'gpus':[], 'note':'fixture'}
                with patch('hardware_advisor.inspect_hardware', return_value=fixture):
                    self.assertEqual(client.get('/api/hardware-advice').json(), fixture)
                    self.assertEqual(client.get('/api/hardware-advice', headers={'Origin':'https://example.com'}).status_code, 403)


if __name__ == '__main__':
    unittest.main()
