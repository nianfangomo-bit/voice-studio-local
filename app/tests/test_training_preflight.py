"""§B 训练前检查：依赖、脚本、配置、基础模型缺失时清楚阻止入队；完整环境可通过。"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import training as training_module  # noqa: E402
from studio import Studio, digest, uid  # noqa: E402


class TrainingPreflightTests(unittest.TestCase):
    # 中文维护：每个用例使用独立临时数据目录和伪造引擎树，不读用户模型或作品。
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.engine = self.root / 'engine'
        self.runtime = self.root / 'python.exe'
        self.runtime.write_bytes(b'fake runtime for dependency probe')
        self.ffmpeg = self.root / 'ffmpeg.exe'
        self.ffmpeg.write_bytes(b'fake ffmpeg')
        self.write_engine()
        self.s = self.open_studio()
        self.ds = {'id': uid(), 'name': '试训', 'revision': 1,
                   'params': {'gpt_epochs': 5, 'sovits_epochs': 8, 'batch_size': 2},
                   'rows': [{'id': uid(), 'excluded': False, 'reviewed': True, 'seconds': 4,
                             'text': '第一段', 'audio': 'a.wav'},
                            {'id': uid(), 'excluded': False, 'reviewed': True, 'seconds': 6,
                             'text': '第二段', 'audio': 'b.wav'}]}
        self.s.save('datasets', self.ds)

    def tearDown(self):
        self.temp.cleanup()

    # 中文维护：按固定清单创建伪造引擎文件，便于逐项制造缺失。
    def write_engine(self, skip=()):
        for rel in training_module.REQUIRED_TRAINING_FILES:
            if rel in skip:
                continue
            path = self.engine / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'fixture ' + rel.encode('ascii'))

    # 中文维护：用当前伪造引擎指纹保存一份可用环境设置。
    def save_settings(self, **overrides):
        core = self.engine / 'GPT_SoVITS/TTS_infer_pack/TTS.py'
        settings = {'id': 'local', 'engine': str(self.engine), 'runtime': str(self.runtime),
                    'ffmpeg': str(self.ffmpeg), 'engine_hash': digest(core)}
        settings.update(overrides)
        return self.s.save('settings', settings)

    # 中文维护：打开独立 Studio，不启动消费线程，避免测试触碰真实队列。
    def open_studio(self, data=None):
        studio = Studio(data or (self.root / 'data'))
        return studio

    # 中文维护：完整依赖探测通过时，计划就绪并恰好入队一个训练任务。
    def test_complete_environment_passes_and_enqueues_once(self):
        self.save_settings()
        healthy = {'ok': True, 'python': '3.9.13', 'modules': {'torch': '2.0.0'}, 'missing': [],
                   'errors': {}, 'problems': [], 'cuda_available': True}
        with mock.patch.object(training_module, 'probe_training_environment', return_value=healthy) as probe:
            report = training_module.plan(self.s, self.ds['id'])
            self.assertTrue(report['ready'])
            self.assertEqual(report['missing'], [])
            self.assertEqual(report['problems'], [])
            job = training_module.enqueue(self.s, self.ds['id'], {'confirmed': True, 'revision': 1})
            self.assertEqual(job['kind'], 'training')
            self.assertEqual(job['state'], 'queued')
            with self.assertRaisesRegex(ValueError, '已有训练任务'):
                training_module.enqueue(self.s, self.ds['id'], {'confirmed': True, 'revision': 1})
            self.assertEqual(probe.call_count, 2)
        self.assertEqual(len([j for j in self.s.all('jobs') if j['kind'] == 'training']), 1)

    # 中文维护：缺脚本/配置/模型时列出具体路径，不启动依赖探测，也不入队。
    def test_missing_script_config_or_model_is_reported(self):
        for rel in ('GPT_SoVITS/s2_train.py', 'GPT_SoVITS/configs/s2v2Pro.json',
                    'GPT_SoVITS/pretrained_models/v2Pro/s2Gv2Pro.pth'):
            (self.engine / rel).unlink()
        self.save_settings()
        with mock.patch.object(training_module, 'probe_training_environment') as probe:
            report = training_module.plan(self.s, self.ds['id'])
            probe.assert_not_called()
        self.assertFalse(report['ready'])
        text = '；'.join(report['missing'])
        self.assertIn('s2_train.py', text)
        self.assertIn('s2v2Pro.json', text)
        self.assertIn('s2Gv2Pro.pth', text)
        with self.assertRaises(ValueError) as caught:
            training_module.enqueue(self.s, self.ds['id'], {'confirmed': True, 'revision': 1})
        message = str(caught.exception)
        self.assertIn('训练前检查未通过，已阻止入队', message)
        self.assertIn('s2_train.py', message)
        self.assertIn('缺少文件或配置', message)
        self.assertEqual(self.s.all('jobs'), [])

    # 中文维护：运行环境或 FFmpeg 路径缺失时给出可读名称，而不是抛异常。
    def test_missing_runtime_path_blocks_with_readable_reason(self):
        self.save_settings(runtime=str(self.root / 'not-there' / 'python.exe'))
        report = training_module.plan(self.s, self.ds['id'])
        self.assertFalse(report['ready'])
        self.assertTrue(any('运行环境 python.exe' in item for item in report['missing']))
        with self.assertRaisesRegex(ValueError, '运行环境'):
            training_module.enqueue(self.s, self.ds['id'], {'confirmed': True, 'revision': 1})
        self.assertEqual(self.s.all('jobs'), [])

    # 中文维护：依赖探测失败时把缺少的模块和底层首行错误写进入队阻断原因。
    def test_dependency_probe_failure_blocks_with_module_names(self):
        self.save_settings()
        failed = {'ok': False, 'modules': {'numpy': '1.23.4'}, 'missing': ['torch'],
                  'errors': {'torch': 'OSError: DLL load failed while importing torch'},
                  'problems': ['训练需要可用的 CUDA；当前运行环境未检测到可用显卡。']}
        with mock.patch.object(training_module, 'probe_training_environment', return_value=failed):
            report = training_module.plan(self.s, self.ds['id'])
            self.assertFalse(report['ready'])
            joined = '；'.join(report['problems'])
            self.assertIn('torch', joined)
            self.assertIn('CUDA', joined)
            self.assertTrue(any('训练前检查' in item for item in report['warnings']))
            with self.assertRaises(ValueError) as caught:
                training_module.enqueue(self.s, self.ds['id'], {'confirmed': True, 'revision': 1})
        message = str(caught.exception)
        self.assertIn('已阻止入队', message)
        self.assertIn('torch', message)
        self.assertIn('CUDA', message)
        self.assertEqual(self.s.all('jobs'), [])

    # 中文维护：引擎核心文件被替换但设置未更新时阻止入队，避免训练中途才失败。
    def test_changed_engine_file_blocks_before_enqueue(self):
        self.save_settings(engine_hash='0' * 64)
        with mock.patch.object(training_module, 'probe_training_environment') as probe:
            report = training_module.plan(self.s, self.ds['id'])
            probe.assert_not_called()
        self.assertFalse(report['ready'])
        self.assertTrue(any('引擎核心文件已变化' in item for item in report['problems']))
        with self.assertRaisesRegex(ValueError, '引擎核心文件已变化'):
            training_module.enqueue(self.s, self.ds['id'], {'confirmed': True, 'revision': 1})

    # 中文维护：完全没有环境设置时提示先去环境设置，不崩溃也不排队。
    def test_unconfigured_environment_explains_next_step(self):
        fresh = self.open_studio(self.root / 'fresh-data')
        fresh.save('datasets', self.ds)
        with mock.patch.object(training_module, 'probe_training_environment') as probe:
            report = training_module.plan(fresh, self.ds['id'])
            probe.assert_not_called()
        self.assertFalse(report['ready'])
        self.assertTrue(any('尚未完成环境设置' in item for item in report['problems']))
        with self.assertRaisesRegex(ValueError, '环境设置'):
            training_module.enqueue(fresh, self.ds['id'], {'confirmed': True, 'revision': 1})
        self.assertEqual(fresh.all('jobs'), [])

    # 中文维护：依赖探测子进程异常退出或没有输出时按失败处理，并保留退出码。
    def test_probe_handles_bad_subprocess_output(self):
        completed = subprocess.CompletedProcess(args=['python'], returncode=2, stdout='', stderr='boom')
        with mock.patch.object(training_module.subprocess, 'run', return_value=completed):
            report = training_module.probe_training_environment(str(self.runtime))
        self.assertFalse(report['ok'])
        self.assertTrue(any('退出码 2' in item for item in report['problems']))

    # 中文维护：正常探测结果可用于计划；缓存只复用同一运行环境路径。
    def test_probe_parses_json_and_caches_success(self):
        training_module._PROBE_CACHE.clear()
        payload = ('VOICE_TRAINING_PROBE={"ok": true, "python": "3.9.13", "modules": '
                   '{"torch": "2.0.0"}, "missing": [], "errors": {}, "problems": [], '
                   '"cuda_available": true}\n')
        completed = subprocess.CompletedProcess(args=['python'], returncode=0, stdout=payload, stderr='')
        with mock.patch.object(training_module.subprocess, 'run', return_value=completed) as run:
            first = training_module.probe_training_environment(str(self.runtime))
            second = training_module.probe_training_environment(str(self.runtime))
        self.assertTrue(first['ok'])
        self.assertEqual(second['modules']['torch'], '2.0.0')
        self.assertEqual(run.call_count, 1)
        training_module._PROBE_CACHE.clear()

    # 中文维护：即使 cuda.is_available() 为 True，矩阵/FFT 架构不兼容也必须在预检拦截。
    def test_training_probe_blocks_cuda_arch_mismatch(self):
        import io
        import json
        import types
        import runtime_guard
        import training_probe
        fake_torch = types.SimpleNamespace(
            __version__='2.0.0+cu118',
            version=types.SimpleNamespace(cuda='11.8'),
            cuda=types.SimpleNamespace(is_available=lambda: True, get_device_name=lambda idx: 'RTX 5060 Laptop'),
        )
        fake_modules = {name: (fake_torch if name == 'torch' else types.SimpleNamespace(__version__='1.0'))
                        for name in training_probe.MODULES}
        bad_cuda = {'ok': False, 'detail': '当前运行环境与显卡架构不兼容，请使用经过验证的兼容环境；不要重新录音或原地升级依赖。',
                    'error': 'RuntimeError: nvrtc: error: invalid value for --gpu-architecture'}
        buf = io.StringIO()
        with mock.patch.object(training_probe.importlib, 'import_module', side_effect=lambda n: fake_modules[n]), \
             mock.patch.dict(sys.modules, {'torch': fake_torch}), \
             mock.patch.object(runtime_guard, 'probe_cuda', return_value=bad_cuda), \
             mock.patch('sys.stdout', buf):
            training_probe.main()
        line = [l for l in buf.getvalue().splitlines() if l.startswith('VOICE_TRAINING_PROBE=')][-1]
        report = json.loads(line.split('=', 1)[1])
        self.assertFalse(report['ok'])
        self.assertTrue(report['cuda_available'])
        self.assertTrue(any('架构不兼容' in item for item in report['problems']))


if __name__ == '__main__':
    unittest.main()