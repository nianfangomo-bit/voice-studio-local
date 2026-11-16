"""正式启动入口的回归测试，不用后端启动成功代替启动器测试。"""
import subprocess
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_portable import APP_FILES


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
        """离线打包清单必须含新版启动、环境检查与数据迁移所需文件。"""
        root = Path(__file__).resolve().parents[1]
        required = {'bootstrap.ps1', 'diagnostics.py', 'environment_probe.py',
                    'migrate_data.py', 'MigrateData.cmd'}
        self.assertTrue(required.issubset(APP_FILES), required - set(APP_FILES))
        for name in required:
            self.assertTrue((root / name).is_file(), name)
