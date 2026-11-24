"""正式启动入口的回归测试，不用后端启动成功代替启动器测试。"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_portable import APP_FILES
from diagnostics import read_app_version


class LauncherEntryTests(unittest.TestCase):
    def test_isolated_import(self):
        """隔离模式执行启动器模块，避免弹窗及真实服务启动。"""
        launcher = Path(__file__).resolve().parents[1] / 'launcher.py'
        result = subprocess.run(
            [sys.executable, '-I', '-B', '-c',
             'import runpy,sys; runpy.run_path(sys.argv[1],run_name="entry_test")', str(launcher)],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_entry_does_not_require_bundled_python(self):
        """正式入口不再直接调用固定引擎路径。"""
        root = Path(__file__).resolve().parents[1]
        self.assertIn('bootstrap.ps1', (root / 'Start.cmd').read_text())
        self.assertNotIn('engine\\runtime\\python.exe', (root / 'Start.cmd').read_text())

    def test_offline_package_contains_startup_dependencies(self):
        """离线打包清单必须含启动、恢复租约、训练预检、环境检查与数据迁移所需文件。"""
        root = Path(__file__).resolve().parents[1]
        required = {'bootstrap.ps1', 'diagnostics.py', 'environment_probe.py',
                    'migrate_data.py', 'MigrateData.cmd',
                    'worker_lease.py', 'training_probe.py', 'runtime_guard.py', 'engine_entry.py'}
        self.assertTrue(required.issubset(APP_FILES), required - set(APP_FILES))
        for name in required:
            self.assertTrue((root / name).is_file(), name)

    def test_source_release_script_includes_new_runtime_and_tests(self):
        """源码打包清单必须同步包含新增运行时模块、统一测试入口和全部测试文件。"""
        repo = Path(__file__).resolve().parents[2]
        text = (repo / 'build-release.py').read_text(encoding='utf-8')
        for item in ('worker_lease.py', 'training_probe.py', 'run-tests.ps1', 'run-tests.cmd',
                     'tests/test_training_preflight.py', 'tests/test_worker_identity_recovery.py'):
            self.assertIn(item, text)

    def test_diagnostics_version_falls_back_to_package_json(self):
        """离线包扁平安装没有上级 VERSION 时，诊断摘要应回退读取同级 package.json。"""
        with tempfile.TemporaryDirectory() as temp:
            install_root = Path(temp) / 'installed'
            install_root.mkdir()
            (install_root / 'package.json').write_text(json.dumps({'version': '1.0.3-rc.3'}), encoding='utf-8')
            self.assertEqual(read_app_version(install_root), '1.0.3-rc.3')
