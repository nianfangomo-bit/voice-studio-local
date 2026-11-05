import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_portable import excluded_legacy_afm
from studio import load_legacy
import tempfile


class FontPolicyTests(unittest.TestCase):
    # 只匹配指定供应方目录，不能误删同名用户文件或有许可的 PDF 度量。
    def test_exact_scope(self):
        base = 'runtime/Lib/site-packages/matplotlib/mpl-data/fonts/'
        self.assertTrue(excluded_legacy_afm(base + 'afm/ptmr8a.afm'))
        for name in ['pdfcorefonts/Times-Roman.afm', 'ttf/DejaVuSans.ttf', 'afm/readme.txt']:
            self.assertFalse(excluded_legacy_afm(base + name))
        self.assertFalse(excluded_legacy_afm('recordings/ptmr8a.afm'))

    # 升级后未知核心指纹必须继续阻止执行，不能为了省事自动信任新代码。
    def test_changed_legacy_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'voice_tool').mkdir()
            (root/'voice_tool/main.py').write_text('raise RuntimeError("must not execute")', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '重新确认'):
                load_legacy({'legacy':temp, 'legacy_hash':'0'*64})
