"""B 批（审核补充）P1 回归：恢复进度不得覆盖用户已有成果。

审核发现：
- studio.py 旧实现接受任意列表作为整理结果，``rows: []`` 会清空已有校对片段；
- 配音结果只检查 audio 是字典，音频文件不存在/不可读/格式不对时仍会覆盖当前有效音频。

修复前这些用例失败或无法证明数据被保护。
"""
import hashlib
import json
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio import Studio, uid  # noqa: E402


# 中文维护：写入与引擎一致的 32 kHz 单声道 16 bit WAV。
def write_wav(path, frames=3200):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as handle:
        handle.setparams((1, 2, 32000, 0, 'NONE', 'not compressed'))
        handle.writeframes(b'\x01\x00' * frames)
    return path


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class RecoveryDataTests(unittest.TestCase):
    # 中文维护：每个用例独立临时目录。
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.s = Studio(self.root / 'data')

    # 中文维护：回收线程与目录（Windows 文件句柄释放有延迟，失败不致命）。
    def tearDown(self):
        self.s.stopping.set()
        self.s.wake.set()
        try:
            self.temp.cleanup()
        except OSError:
            pass

    def dataset(self, rows=None):
        record = {'id': uid(), 'name': '素材', 'rows': list(rows or []), 'recordings': [],
                  'quality_reports': [], 'authorization': '用户确认拥有使用授权',
                  'created': time.time(), 'revision': 1, 'preparation_job': None,
                  'params': {'gpt_epochs': 5, 'sovits_epochs': 8, 'batch_size': 1}}
        return self.s.save('datasets', record)

    def project(self, segments):
        record = {'id': uid(), 'name': '项目', 'voice': {'id': 'v'}, 'segments': segments,
                  'gap_ms': 180, 'paragraph_gap_ms': 420, 'speed': 1.0,
                  'exports': [], 'created': time.time(), 'dirty': False}
        return self.s.save('projects', record)

    def job(self, kind, project_id):
        record = {'id': uid(), 'kind': kind, 'name': '任务', 'project_id': project_id,
                  'state': 'interrupted', 'completed': 0, 'total': 1, 'message': '中断',
                  'created': time.time()}
        return self.s.save('jobs', record)

    @staticmethod
    def folder(job):
        return None

    # 中文维护：整理任务的进度目录。
    def progress(self, job, payload, folder=None):
        folder = folder or (self.s.root / 'jobs' / job['id'])
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'progress.json').write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        return folder

    # 中文维护：与 preparation_worker 输出一致的整理结果条目。
    def row(self, audio, text='你好'):
        return {'id': uid(), 'source': str(audio), 'recording_id': 'r1', 'audio': str(audio),
                'seconds': 0.1, 'start_seconds': 0.0, 'hash': digest(audio), 'original': text,
                'text': text, 'reviewed': False, 'excluded': False, 'quality': {'rms_dbfs': -20.0}}

    # 中文维护：读取恢复原因日志。
    def recovery_log(self, job):
        path = self.s.root / 'jobs' / job['id'] / 'recovery.log'
        return path.read_text(encoding='utf-8') if path.is_file() else ''

    # A. 合法非空整理结果正常恢复。
    def test_a_valid_rows_are_restored(self):
        ds = self.dataset([])
        job = self.job('preparation', ds['id'])
        wav = write_wav(self.root / 'seg-a.wav')
        row = self.row(wav)
        folder = self.progress(job, {'message': '已转写 1 段', 'completed': 0, 'rows': [row],
                                     'quality_reports': [{'recording_id': 'r1', 'name': 'r.wav', 'seconds': 1.0}]})
        self.s.ingest(job, folder)
        after = self.s.get('datasets', ds['id'])
        self.assertEqual([item['id'] for item in after['rows']], [row['id']])
        self.assertEqual(after['preparation_job'], job['id'])
        self.assertEqual(len(after['quality_reports']), 1)

    # B. 非法空列表不得覆盖已有校对成果。
    def test_b_empty_rows_do_not_wipe_reviewed_segments(self):
        wav = write_wav(self.root / 'seg-b.wav')
        old = self.row(wav, text='已校对文本')
        old['reviewed'] = True
        ds = self.dataset([old])
        job = self.job('preparation', ds['id'])
        folder = self.progress(job, {'message': '准备转写', 'completed': 0, 'rows': [], 'quality_reports': []})
        self.s.ingest(job, folder)
        after = self.s.get('datasets', ds['id'])
        self.assertEqual([item['id'] for item in after['rows']], [old['id']])
        self.assertTrue(after['rows'][0]['reviewed'])
        self.assertIn('空', self.recovery_log(job))

    # C. 合法空列表（原有素材也为空）仍能正常处理，不误报。
    def test_c_legitimate_empty_rows_are_accepted(self):
        ds = self.dataset([])
        job = self.job('preparation', ds['id'])
        folder = self.progress(job, {'message': '加载模型', 'completed': 0, 'rows': [], 'quality_reports': []})
        self.s.ingest(job, folder)
        after = self.s.get('datasets', ds['id'])
        self.assertEqual(after['rows'], [])
        self.assertNotIn('未采用', self.recovery_log(job))

    # D. 音频路径不存在时保留原有有效音频。
    def test_d_missing_audio_keeps_existing_audio(self):
        old_wav = write_wav(self.root / 'old.wav')
        old_audio = {'path': str(old_wav), 'fingerprint': 'fp-old', 'sha256': digest(old_wav),
                     'seed': 1, 'stats': {}, 'created': time.time()}
        segment = {'id': uid(), 'original': '你好', 'spoken': '你好', 'display': '你好',
                   'paragraph': False, 'audio': old_audio, 'history': []}
        project = self.project([segment])
        job = self.job('dub', project['id'])
        missing = self.root / 'missing-never-written.wav'
        folder = self.progress(job, {'message': '处理中', 'results': [
            {'id': segment['id'], 'audio': {'path': str(missing), 'fingerprint': 'fp-new',
                                            'sha256': '0' * 64, 'seed': 2}}]})
        self.s.ingest(job, folder)
        after = self.s.get('projects', project['id'])
        self.assertEqual(after['segments'][0]['audio'], old_audio)
        self.assertEqual(after['segments'][0]['history'], [])
        self.assertIn('音频', self.recovery_log(job))

    # E. 音频文件存在且格式正确时正常恢复，旧音频进入历史而不丢失。
    def test_e_valid_audio_is_restored(self):
        old_wav = write_wav(self.root / 'old-e.wav')
        old_audio = {'path': str(old_wav), 'fingerprint': 'fp-old', 'sha256': digest(old_wav),
                     'seed': 1, 'stats': {}, 'created': time.time()}
        segment = {'id': uid(), 'original': '你好', 'spoken': '你好', 'display': '你好',
                   'paragraph': False, 'audio': old_audio, 'history': []}
        project = self.project([segment])
        job = self.job('dub', project['id'])
        new_wav = write_wav(self.root / 'new-e.wav')
        new_audio = {'path': str(new_wav), 'fingerprint': 'fp-new', 'sha256': digest(new_wav),
                     'seed': 1, 'stats': {'rtf': 0.1}, 'created': time.time()}
        folder = self.progress(job, {'message': '处理中', 'results': [{'id': segment['id'], 'audio': new_audio}]})
        self.s.ingest(job, folder)
        after = self.s.get('projects', project['id'])
        self.assertEqual(after['segments'][0]['audio'], new_audio)
        self.assertEqual(after['segments'][0]['history'], [old_audio])
        self.assertTrue(Path(after['segments'][0]['audio']['path']).is_file())

    # F. 损坏进度不影响其他正常任务的恢复。
    def test_f_corrupt_progress_does_not_block_other_jobs(self):
        ds_bad = self.dataset([])
        bad = self.job('preparation', ds_bad['id'])
        bad_folder = self.s.root / 'jobs' / bad['id']
        bad_folder.mkdir(parents=True, exist_ok=True)
        (bad_folder / 'progress.json').write_text('{not json', encoding='utf-8')

        seg_wav = write_wav(self.root / 'good-f.wav')
        segment = {'id': uid(), 'original': '你好', 'spoken': '你好', 'display': '你好',
                   'paragraph': False, 'audio': None, 'history': []}
        project = self.project([segment])
        good = self.job('dub', project['id'])
        audio = {'path': str(seg_wav), 'fingerprint': 'fp-good', 'sha256': digest(seg_wav),
                 'seed': 1, 'stats': {}, 'created': time.time()}
        good_folder = self.progress(good, {'message': '处理中', 'results': [{'id': segment['id'], 'audio': audio}]})

        self.s.ingest(bad, bad_folder)   # 损坏进度：记录原因，不抛异常
        self.s.ingest(good, good_folder)  # 正常任务仍然恢复
        self.assertEqual(self.s.get('datasets', ds_bad['id'])['rows'], [])
        self.assertEqual(self.s.get('projects', project['id'])['segments'][0]['audio'], audio)
        self.assertIn('无法解析', self.recovery_log(bad))

    # G. 恢复失败不删除原始数据（文件、校对文本、历史和旧导出都保留）。
    def test_g_rejected_recovery_keeps_original_data(self):
        old_wav = write_wav(self.root / 'old-g.wav')
        old_audio = {'path': str(old_wav), 'fingerprint': 'fp-old', 'sha256': digest(old_wav),
                     'seed': 1, 'stats': {}, 'created': time.time()}
        segment = {'id': uid(), 'original': '原文', 'spoken': '已校对', 'display': '字幕',
                   'paragraph': False, 'audio': old_audio, 'history': []}
        project = self.project([segment])
        export_dir = self.s.root / 'projects' / project['id'] / 'exports' / 'e1'
        export_dir.mkdir(parents=True)
        (export_dir / 'narration.wav').write_bytes(b'old export')
        job = self.job('dub', project['id'])
        folder = self.progress(job, {'message': '处理中', 'results': [
            {'id': segment['id'], 'audio': {'path': str(self.root / 'nope.wav'), 'fingerprint': 'fp-x'}}]})

        before = self.s.get('projects', project['id'])
        self.s.ingest(job, folder)
        after = self.s.get('projects', project['id'])
        self.assertEqual(after['segments'], before['segments'])
        self.assertTrue(old_wav.is_file())
        self.assertTrue((export_dir / 'narration.wav').is_file())
        self.assertEqual(after['segments'][0]['display'], '字幕')
        self.assertIn('音频', self.recovery_log(job))

    # H. 记录缺失时同样留下恢复原因，并且不抛出异常阻断启动。
    def test_h_missing_records_are_logged(self):
        missing_dataset = self.job('preparation', 'no-such-dataset')
        folder = self.progress(missing_dataset, {'message': '加载模型', 'rows': []})
        self.s.ingest(missing_dataset, folder)
        self.assertIn('不存在', self.recovery_log(missing_dataset))

        missing_project = self.job('dub', 'no-such-project')
        folder = self.progress(missing_project, {'message': '处理中', 'results': []})
        self.s.ingest(missing_project, folder)
        self.assertIn('不存在', self.recovery_log(missing_project))


if __name__ == '__main__':
    unittest.main()