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
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.s = Studio(Path(self.temp.name))
        self.ds = create(self.s, {'name':'测试声线', 'authorized':True})

    def tearDown(self):
        self.temp.cleanup()

    def test_requires_authorization(self):
        with self.assertRaises(ValueError): create(self.s, {'name':'test'})

    def test_upload_has_server_generated_path(self):
        target, name = upload_target(self.s, self.ds['id'], '../../private.wav')
        self.assertEqual(name, 'private.wav')
        self.assertIn(self.s.root, target.parents)
        self.assertNotEqual(target.name, name)
        target.write_bytes(b'audio fixture')
        ds = register_upload(self.s, self.ds['id'], target, name)
        self.assertEqual(len(ds['recordings']), 1)
        self.assertTrue(target.exists())

    def test_reject_non_audio(self):
        with self.assertRaises(ValueError): upload_target(self.s, self.ds['id'], 'model.pth')

    def test_missing_model_does_not_enqueue(self):
        target, name = upload_target(self.s, self.ds['id'], 'test.wav')
        target.write_bytes(b'fixture')
        register_upload(self.s, self.ds['id'], target, name)
        with self.assertRaises(ValueError): enqueue(self.s, self.ds['id'])
        self.assertFalse(self.s.all('jobs'))

    def test_exclude_empty_transcription(self):
        self.ds['rows']=[{'id':'row', 'text':'', 'reviewed':False, 'excluded':False}]
        self.s.save('datasets', self.ds)
        ds = update_dataset(self.s, self.ds['id'], {'row_id':'row','text':'','excluded':True})
        self.assertTrue(ds['rows'][0]['excluded'])

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

    def test_silence_and_clipping_are_measurements_not_score(self):
        self.assertLess(audio_quality([0]*100)['rms_dbfs'], -60)
        self.assertEqual(audio_quality([1]*100)['clipped_ratio'], 1)
        self.assertNotIn('score', audio_quality([0]*100))

    def test_short_clip_does_not_call_vendor_bug(self):
        chunks = list(bounded_chunks([.1]*64000, None))
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0][1], 0)


if __name__ == '__main__': unittest.main()
