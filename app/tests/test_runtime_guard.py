"""覆盖零退出码掩盖失败、空产物和 CUDA 探测错误；不冒充真实显卡验收。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_guard import check_stage, validate_features, validate_rows, probe_cuda


class RuntimeGuardTests(unittest.TestCase):
    def test_zero_exit_missing_features_preserves_cause(self):
        """上游吞异常后退出零，仍应通过缺失产物识别失败。"""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            log = root / 'stage-3.log'
            log.write_text('nvrtc: error: invalid value for --gpu-architecture (-arch)\n' * 28)
            with self.assertRaisesRegex(RuntimeError, '显卡架构不兼容'):
                check_stage(0, log, lambda: validate_features(root, [{'id': 'a', 'audio': 'a.wav'}], [('7-sv_cn', '.pt')]))

    def test_empty_feature_rejected(self):
        """空文件不能被当作成功产物。"""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / '7-sv_cn').mkdir()
            (root / '7-sv_cn/a.wav.pt').touch()
            with self.assertRaises(RuntimeError):
                validate_features(root, [{'id': 'a', 'audio': 'a.wav'}], [('7-sv_cn', '.pt')])

    def test_valid_and_wrong_row_counts(self):
        """逐阶段清单数量必须与参与训练的片段一致。"""
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'rows.txt'
            path.write_text('one\ntwo\n')
            validate_rows(path, 2)
            with self.assertRaises(RuntimeError):
                validate_rows(path, 3)

    def test_nonzero_exit_fails_without_validator(self):
        """无额外产物验证器时也不能忽略非零退出码。"""
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / 'stage-5.log'
            log.write_text('RuntimeError: CUDA out of memory')
            with self.assertRaisesRegex(RuntimeError, '显存不足'):
                check_stage(1, log)

    def test_matmul_pass_fft_fail(self):
        """复现可识别 CUDA 且矩阵运算通过，但 FFT 失败的组合。"""
        torch = MagicMock()
        torch.__version__ = 'test'
        torch.version.cuda = '11.8'
        torch.cuda.is_available.return_value = True
        torch.cuda.get_device_capability.return_value = (12, 0)
        torch.cuda.get_arch_list.return_value = ['sm_90']
        torch.fft.rfft.side_effect = RuntimeError('nvrtc: error: invalid value for --gpu-architecture (-arch)')
        report = probe_cuda(torch)
        self.assertFalse(report['ok'])
        self.assertTrue(report['matmul'])
        self.assertIn('显卡架构不兼容', report['detail'])

    def test_no_cuda(self):
        """无 CUDA 时给出明确说明。"""
        torch = MagicMock()
        torch.__version__ = 'test'
        torch.cuda.is_available.return_value = False
        self.assertFalse(probe_cuda(torch)['ok'])


if __name__ == '__main__':
    unittest.main()
