"""环境选择不覆盖原目录，启动器与任务配置使用同一事实来源。"""
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from environment_selection import selected_runtime
from studio import Studio


class EnvironmentSelectionTests(unittest.TestCase):
    def setUp(self):
        """创建隔离假环境，不运行其中任何程序。"""
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.studio = Studio(self.root / 'data')
        for rel in ('engine/runtime/python.exe','engine/GPT_SoVITS/TTS_infer_pack/TTS.py','legacy/voice_tool/main.py','engine/runtime/ffmpeg.exe','new-runtime/python.exe'):
            path=self.root/rel; path.parent.mkdir(parents=True,exist_ok=True);path.write_text('fixture')
        self.config={'trusted':True,'engine':str(self.root/'engine'),'legacy':str(self.root/'legacy'),'ffmpeg':str(self.root/'engine/runtime/ffmpeg.exe')}

    def tearDown(self):
        """只清理本测试拥有的临时目录。"""
        self.temp.cleanup()

    def test_selected_overrides_bundled_and_can_restore(self):
        """已选环境优先于随包环境，历史恢复走同一个校验入口。"""
        original=self.studio.configure(self.config)
        changed=self.studio.configure(dict(self.config,runtime=str(self.root/'new-runtime/python.exe')))
        self.assertEqual(selected_runtime(self.root),Path(changed['runtime']))
        history=self.studio.all('environment_history')
        self.assertEqual(history[0]['settings']['runtime'],original['runtime'])
        self.studio.configure(dict(history[0]['settings'],trusted=True))
        self.assertEqual(selected_runtime(self.root),Path(original['runtime']))

    def test_missing_selected_does_not_fallback(self):
        """已选环境丢失时不能静默改用随包环境。"""
        self.studio.configure(dict(self.config,runtime=str(self.root/'new-runtime/python.exe')))
        (self.root/'new-runtime/python.exe').unlink()
        with self.assertRaisesRegex(ValueError,'不存在'):
            selected_runtime(self.root)

    def test_busy_cannot_switch(self):
        """有任务时拒绝切换且不生成历史记录。"""
        self.studio.configure(self.config)
        self.studio.save('jobs',{'id':'busy','state':'running'})
        with self.assertRaisesRegex(ValueError,'任务运行'):
            self.studio.configure(dict(self.config,runtime=str(self.root/'new-runtime/python.exe')))
        self.assertEqual(self.studio.all('environment_history'),[])

    def test_first_start_bundled(self):
        """首次没有配置仍能找到随包解释器。"""
        self.assertEqual(selected_runtime(self.root),self.root/'engine/runtime/python.exe')

    def test_requires_explicit_trust(self):
        """外部环境必须由用户显式确认。"""
        with self.assertRaises(ValueError):
            self.studio.configure(dict(self.config,trusted=False))

    def test_failed_probe_preserves_previous(self):
        """新环境验证失败不能写入设置或新增历史。"""
        original = self.studio.configure(self.config)
        with patch.object(self.studio, 'probe_environment', return_value={'ok': False, 'detail': 'fixture failure'}):
            with self.assertRaisesRegex(ValueError, '原配置保留'):
                self.studio.configure(dict(self.config, runtime=str(self.root/'new-runtime/python.exe')), verify_runtime=True)
        self.assertEqual(self.studio.settings(), original)
        self.assertEqual(self.studio.all('environment_history'), [])

    def test_verified_configuration_records_scope(self):
        """记录基础检查结果，不标记完整训练通过。"""
        with patch.object(self.studio, 'probe_environment', return_value={'ok': True, 'torch': 'fixture'}):
            result = self.studio.configure(self.config, verify_runtime=True)
        self.assertEqual(result['validation'], 'dependencies_and_cuda')


if __name__=='__main__':
    unittest.main()
