import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio import Studio, atomic_json
from preparation import create, upload_target, register_upload, enqueue
from preparation_worker import audio_quality, bounded_chunks
from training import update_dataset


class PreparationTests(unittest.TestCase):
    # 中文维护：创建本测试独占的临时数据与夹具，不使用用户模型或作品。
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.s = Studio(Path(self.temp.name))
        self.ds = create(self.s, {'name':'测试声线', 'authorized':True})

    # 中文维护：释放测试资源并清理本测试创建的临时目录。
    def tearDown(self):
        self.temp.cleanup()

    # 中文维护：确认没有录音授权时拒绝创建素材。
    def test_requires_authorization(self):
        with self.assertRaises(ValueError): create(self.s, {'name':'test'})

    # 中文维护：确认上传路径由服务生成，不采信用户文件名中的路径。
    def test_upload_has_server_generated_path(self):
        target, name = upload_target(self.s, self.ds['id'], '../../private.wav')
        self.assertEqual(name, 'private.wav')
        self.assertIn(self.s.root, target.parents)
        self.assertNotEqual(target.name, name)
        target.write_bytes(b'audio fixture')
        ds = register_upload(self.s, self.ds['id'], target, name)
        self.assertEqual(len(ds['recordings']), 1)
        self.assertTrue(target.exists())

    # 中文维护：确认不支持的录音扩展名被拒绝。
    def test_reject_non_audio(self):
        with self.assertRaises(ValueError): upload_target(self.s, self.ds['id'], 'model.pth')

    # 中文维护：确认缺少模型时不启动任务或下载。
    def test_missing_model_does_not_enqueue(self):
        target, name = upload_target(self.s, self.ds['id'], 'test.wav')
        target.write_bytes(b'fixture')
        register_upload(self.s, self.ds['id'], target, name)
        with self.assertRaises(ValueError): enqueue(self.s, self.ds['id'])
        self.assertFalse(self.s.all('jobs'))

    # 中文维护：确认空转写可排除但不能作为有效标注。
    def test_exclude_empty_transcription(self):
        self.ds['rows']=[{'id':'row', 'text':'', 'reviewed':False, 'excluded':False}]
        self.s.save('datasets', self.ds)
        ds = update_dataset(self.s, self.ds['id'], {'row_id':'row','text':'','excluded':True})
        self.assertTrue(ds['rows'][0]['excluded'])

    # 中文维护：确认重新整理录音前保存旧校对版本。
    def test_progress_preserves_old_review_revision(self):
        self.ds['rows'] = [{'id':'old', 'text':'人工校对'}]
        self.s.save('datasets', self.ds)
        job = {'id':'job1','kind':'preparation','project_id':self.ds['id'], 'state':'running'}
        self.s.save('jobs', job)
        folder = self.s.root / 'jobs/job1'
        atomic_json(folder/'progress.json', {'rows':[{'id':'new'}], 'completed':1})
        self.s.ingest(job, folder)
        self.assertEqual(self.s.get('datasets', self.ds['id'])['rows'][0]['id'], 'new')
        old = json.loads((self.s.root/'datasets'/self.ds['id']/'revision-1.json').read_text(encoding='utf-8'))
        self.assertEqual(old['rows'][0]['text'], '人工校对')
        self.s.ingest(job, folder)
        self.assertEqual(self.s.get('datasets', self.ds['id'])['revision'],2)

    # 中文维护：确认静音和削波返回实测告警而非伪造音质评分。
    def test_silence_and_clipping_are_measurements_not_score(self):
        self.assertLess(audio_quality([0]*100)['rms_dbfs'], -60)
        self.assertEqual(audio_quality([1]*100)['clipped_ratio'], 1)
        self.assertNotIn('score', audio_quality([0]*100))

    # 中文维护：确认短录音绕开上游切分返回格式缺陷。
    def test_short_clip_does_not_call_vendor_bug(self):
        chunks = list(bounded_chunks([.1]*64000, None))
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0][1], 0)


if __name__ == '__main__': unittest.main()
