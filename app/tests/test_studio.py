import json
import math
import struct
import tempfile
import unittest
import wave
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio import Studio, digest, uid


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.s = Studio(self.root / 'data')
        core = self.root / 'legacy/voice_tool/main.py'
        core.parent.mkdir(parents=True)
        core.write_text('def fmt_srt_time(ms):\n return f"{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}"\n', encoding='utf-8')
        self.s.save('settings', {'id':'local','legacy':str(core.parents[1]),'legacy_hash':digest(core)})
        voice = {'id':uid(),'name':'测试夹具','version':1,'gpt_hash':'a','sovits_hash':'b','reference_hash':'c','prompt_text':'参考','engine_hash':'d'}
        self.p = {'id':uid(),'name':'测试','original':'第一句。第二句。','voice':voice,'speed':1.,'gap_ms':180,'paragraph_gap_ms':420,'exports':[],'dirty':True,'segments':[]}
        for i in range(2):
            path = self.root / f'{i}.wav'
            with wave.open(str(path),'wb') as w:
                w.setparams((1,2,32000,0,'NONE','not compressed'))
                w.writeframes(b''.join(struct.pack('<h',int(3000*math.sin(j/10))) for j in range(32001+i)))
            seg = {'id':uid(),'original':f'原文{i}','spoken':f'朗读{i}','display':f'字幕{i}','paragraph':False,'history':[]}
            seg['audio']={'path':str(path),'fingerprint':self.s.fingerprint(self.p,seg),'created':1}
            self.p['segments'].append(seg)
        self.s.save('projects',self.p)

    def tearDown(self):
        self.temp.cleanup()

    def test_export_sample_timeline_and_portability(self):
        p=self.s.export(self.p)
        f=Path(p['exports'][-1]['folder'])
        t=json.loads((f/'timeline.json').read_text())
        with wave.open(str(f/'narration.wav')) as w:
            self.assertEqual(w.getnframes(),32001+32002+5760)
            self.assertEqual(t['duration_ms'],round(w.getnframes()/32))
        self.assertEqual(t['segments'][1]['start_ms'],1180)
        self.assertIn('字幕0',(f/'subtitles.srt').read_text(encoding='utf-8'))
        m=json.loads((f/'manifest.json').read_text(encoding='utf-8'))
        self.assertFalse(Path(m['segments'][0]['audio']).is_absolute())
        self.assertTrue((f/'delivery.zip').is_file())

    def test_display_edit_reuses_audio(self):
        p=self.s.edit_segment(self.p['id'],self.p['segments'][0]['id'],{'display':'q³ = 3/2'})
        self.assertTrue(self.s.current(p,p['segments'][0]))
        self.assertTrue(p['dirty'])

    def test_spoken_edit_invalidates_only_one(self):
        p=self.s.edit_segment(self.p['id'],self.p['segments'][0]['id'],{'spoken':'新朗读'})
        self.assertFalse(self.s.current(p,p['segments'][0]))
        self.assertTrue(self.s.current(p,p['segments'][1]))
        j=self.s.enqueue(p['id'])
        self.assertEqual(j['total'],1)

    def test_speed_invalidates_all(self):
        p=self.s.options(self.p['id'],{'speed':1.2,'gap_ms':180})
        self.assertFalse(any(self.s.current(p,s) for s in p['segments']))

    def test_gap_reuses_audio(self):
        p=self.s.options(self.p['id'],{'speed':1.,'gap_ms':500})
        self.assertTrue(all(self.s.current(p,s) for s in p['segments']))

    def test_busy_prevents_edit_and_duplicate_job(self):
        self.s.enqueue(self.p['id'])
        with self.assertRaises(ValueError): self.s.enqueue(self.p['id'])
        with self.assertRaises(ValueError): self.s.edit_segment(self.p['id'],self.p['segments'][0]['id'],{'spoken':'修改'})

    def test_cancel_and_retry(self):
        j=self.s.enqueue(self.p['id'])
        self.assertEqual(self.s.cancel(j['id'])['state'],'cancelled')
        self.assertEqual(self.s.retry(j['id'])['state'],'queued')

    def test_restart_marks_interrupted(self):
        j=self.s.enqueue(self.p['id'])
        s2=Studio(self.s.root)
        self.assertEqual(s2.get('jobs',j['id'])['state'],'interrupted')

    def test_export_does_not_overwrite(self):
        p=self.s.export(self.p)
        first=Path(p['exports'][0]['folder'])/'narration.wav'
        before=digest(first)
        p=self.s.export(p)
        self.assertEqual(len(p['exports']),2)
        self.assertEqual(before,digest(first))

    def test_path_traversal_rejected(self):
        p=self.s.export(self.p)
        with self.assertRaises(ValueError): self.s.file(p['id'],p['exports'][0]['id'],'../../studio.sqlite')

    def test_missing_audio_blocks_export(self):
        Path(self.p['segments'][0]['audio']['path']).unlink()
        with self.assertRaises(ValueError): self.s.export(self.p)

    def test_http_auth_origin_host(self):
        from fastapi.testclient import TestClient
        from server import make_app
        client=TestClient(make_app(self.s,'test-secret',8791),base_url='http://127.0.0.1:8791')
        self.assertEqual(client.get('/api/state').status_code,401)
        self.assertEqual(client.post('/connect',headers={'Origin':'http://evil.test','x-start-token':'test-secret'}).status_code,403)
        self.assertEqual(client.post('/connect',headers={'Origin':'http://127.0.0.1:8791','x-start-token':'test-secret'}).status_code,200)
        self.assertEqual(client.get('/api/state').status_code,200)
        self.assertEqual(client.get('/api/state',headers={'Host':'evil.test'}).status_code,403)
        self.assertEqual(client.post('/api/projects',json={}).status_code,403)

    def test_training_import_preserves_original_and_requires_review(self):
        from training import import_dataset, update_dataset
        labels = self.root / 'samples.list'
        labels.write_text('\n'.join(f'{self.root / (str(i)+".wav")}|speaker|ZH|测试文字{i}' for i in range(2)), encoding='utf-8')
        before = digest(labels)
        ds = import_dataset(self.s, {'list_path':str(labels), 'authorized':True, 'name':'试训'})
        self.assertFalse(any(r['reviewed'] for r in ds['rows']))
        self.assertTrue(all(Path(r['audio']).is_file() for r in ds['rows']))
        ds2 = update_dataset(self.s, ds['id'], {'row_id':ds['rows'][0]['id'], 'text':'已校对', 'reviewed':True})
        self.assertEqual(ds2['rows'][0]['text'],'已校对')
        self.assertEqual(digest(labels), before)
        self.assertEqual(ds2['revision'],2)

    def test_training_rejects_untrusted_material_and_bad_params(self):
        from training import import_dataset, update_dataset
        with self.assertRaises(ValueError): import_dataset(self.s, {'authorized':False})
        ds={'id':uid(),'params':{},'rows':[],'revision':1}
        self.s.save('datasets',ds)
        with self.assertRaises(ValueError): update_dataset(self.s,ds['id'],{'params':{'gpt_epochs':999,'sovits_epochs':8,'batch_size':2}})

    def test_training_plan_reports_missing_and_unreviewed(self):
        from training import plan, enqueue
        setting=self.s.settings()
        setting['engine']=str(self.root/'missing-engine')
        self.s.save('settings',setting)
        ds={'id':uid(),'name':'试训','revision':1,'params':{'gpt_epochs':5,'sovits_epochs':8,'batch_size':2},
            'rows':[{'excluded':False,'reviewed':False,'seconds':4}]}
        self.s.save('datasets',ds)
        report=plan(self.s,ds['id'])
        self.assertFalse(report['ready'])
        self.assertEqual(report['pending_review'],1)
        self.assertGreater(len(report['missing']),0)
        with self.assertRaises(ValueError): enqueue(self.s,ds['id'],{'confirmed':True,'revision':1})

    def test_training_checkpoint_directory_exists_before_save(self):
        from training import initialize_checkpoint_directory
        path=initialize_checkpoint_directory(self.root/'new-experiment')
        self.assertTrue(path.is_dir())
        self.assertEqual(initialize_checkpoint_directory(path.parent),path)

    def test_persistent_dataloader_has_nonzero_workers(self):
        from training import compatible_loader_workers
        self.assertEqual(compatible_loader_workers(0),1)
        self.assertEqual(compatible_loader_workers(2),2)


if __name__=='__main__': unittest.main()
