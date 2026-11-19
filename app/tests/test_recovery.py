"""A 批 P0 回归：启动恢复容错与队列自恢复。

修复前这两组用例必然失败：
- 进度记录里的片段标识与项目不一致时，``ingest`` 的 ``next()`` 抛 ``StopIteration``，
  ``Studio.__init__`` 只兜 OSError/ValueError/KeyError，于是整个服务启动失败。
- 队列消费线程没有兜底，``run_job`` 内部异常逃出即杀死唯一消费线程，后续任务永久卡住。
"""
import gc
import json
import math
import struct
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio import Studio, uid  # noqa: E402


class FakeProc:
    """假工作进程：默认 poll 返回未结束；把 returncode 置为 0 表示“旧进程已退出”。"""

    instances = []

    def __init__(self, *args, **kwargs):
        self.pid = 0
        self.returncode = None
        FakeProc.instances.append(self)

    def poll(self):
        return self.returncode


# 中文维护：回收引用后清理临时目录；Windows 文件句柄短暂占用时最多重试五次。
def cleanup_temp(temp):
    for _ in range(5):
        gc.collect()
        try:
            temp.cleanup()
            return
        except OSError:
            time.sleep(0.1)
    temp.cleanup()


# 中文维护：写最小合法 PCM16 单声道 WAV，供片段与导出保留断言使用。
def write_wav(path, frames=3200):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as w:
        w.setparams((1, 2, 32000, 0, 'NONE', 'not compressed'))
        w.writeframes(b''.join(struct.pack('<h', int(2000 * math.sin(j / 10))) for j in range(frames)))
    return path


class StartupRecoveryTests(unittest.TestCase):
    """异常或不匹配的任务进度记录不能阻止启动，已有录音/片段/导出必须保留。"""

    # 中文维护：每个用例独立临时目录，不读用户真实录音与作品。
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / 'data'
        self.s = Studio(self.data)
        wav = write_wav(self.root / 'recordings' / 'first.wav')
        seg = {'id': uid(), 'original': '原文', 'spoken': '朗读文本', 'display': '字幕文本',
               'paragraph': False, 'history': [],
               'audio': {'path': str(wav), 'fingerprint': 'fingerprint-0', 'created': 1}}
        export = self.root / 'exports' / 'e1'
        export.mkdir(parents=True)
        (export / 'narration.wav').write_bytes(b'RIFF-export-kept')
        self.p = {'id': uid(), 'name': '保留测试', 'original': seg['spoken'], 'speed': 1.,
                  'gap_ms': 180, 'paragraph_gap_ms': 420, 'dirty': False,
                  'voice': {'id': uid()}, 'segments': [seg],
                  'exports': [{'id': 'e1', 'folder': str(export)}]}
        self.s.save('projects', self.p)

    # 中文维护：释放临时目录；Windows 上 SQLite 可能短暂占用文件句柄，清理失败不算测试失败。
    def tearDown(self):
        cleanup_temp(self.temp)

    # 中文维护：构造一条“上次运行中断”的配音任务。
    def job(self, **extra):
        j = {'id': uid(), 'kind': 'dub', 'name': '配音', 'project_id': self.p['id'],
             'state': 'running', 'mode': 'all', 'segment_id': None, 'completed': 0, 'total': 1,
             'message': '生成中', 'created': time.time(), 'request': {}}
        j.update(extra)
        return j

    # 中文维护：在任务目录写进度记录，模拟工作进程留下的真实文件。
    def write_progress(self, jid, payload):
        folder = self.data / 'jobs' / jid
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'progress.json').write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')

    # 中文维护：断言项目、片段音频与旧导出原样保留。
    def assert_user_data_preserved(self, studio):
        kept = studio.get('projects', self.p['id'])
        self.assertEqual(kept['segments'][0]['audio'], self.p['segments'][0]['audio'])
        self.assertEqual(kept['segments'][0]['history'], [])
        self.assertEqual(kept['exports'], self.p['exports'])
        self.assertTrue((self.root / 'exports' / 'e1' / 'narration.wav').is_file())

    # 核心复现：进度里的片段标识在项目中不存在（StopIteration）。
    def test_unknown_segment_in_progress_does_not_block_startup(self):
        j = self.job()
        self.s.save('jobs', j)
        self.write_progress(j['id'], {'message': '生成中', 'completed': 1, 'results': [
            {'id': 'segment-does-not-exist', 'audio': {'path': 'x.wav', 'fingerprint': 'f', 'created': 2}}]})

        restarted = Studio(self.data)

        self.assertEqual(restarted.get('jobs', j['id'])['state'], 'interrupted')
        self.assert_user_data_preserved(restarted)

    # 核心复现：进度文件本身不是对象（AttributeError）。
    def test_non_object_progress_does_not_block_startup(self):
        j = self.job()
        self.s.save('jobs', j)
        self.write_progress(j['id'], ['不是对象', 'results'])

        restarted = Studio(self.data)

        self.assertEqual(restarted.get('jobs', j['id'])['state'], 'interrupted')
        self.assert_user_data_preserved(restarted)

    # 进度条目残缺（缺 audio）时跳过该项，不崩溃、不删片段。
    def test_incomplete_progress_item_is_skipped(self):
        j = self.job()
        self.s.save('jobs', j)
        self.write_progress(j['id'], {'message': '生成中', 'results': [{'id': self.p['segments'][0]['id']}]})

        restarted = Studio(self.data)

        self.assertEqual(restarted.get('jobs', j['id'])['state'], 'interrupted')
        self.assert_user_data_preserved(restarted)

    # 坏记录不能连坐：两条任务都保持 interrupted，数据不变。
    def test_one_bad_job_does_not_block_other_job_recovery(self):
        good, bad = self.job(), self.job()
        self.s.save('jobs', good)
        self.s.save('jobs', bad)
        self.write_progress(good['id'], {'message': '生成中', 'results': []})
        self.write_progress(bad['id'], {'message': '生成中', 'results': [{'id': 'missing'}]})

        restarted = Studio(self.data)

        self.assertEqual(restarted.get('jobs', good['id'])['state'], 'interrupted')
        self.assertEqual(restarted.get('jobs', bad['id'])['state'], 'interrupted')
        self.assert_user_data_preserved(restarted)

    # 回归：合法进度仍要恢复出片段音频，不能因为容错把恢复逻辑关掉。
    def test_valid_progress_is_still_restored(self):
        j = self.job()
        self.s.save('jobs', j)
        new_audio = {'path': str(write_wav(self.root / 'recordings' / 'second.wav')),
                     'fingerprint': 'fingerprint-1', 'created': 2}
        self.write_progress(j['id'], {'message': '生成完成', 'results': [
            {'id': self.p['segments'][0]['id'], 'audio': new_audio}]})

        restarted = Studio(self.data)

        kept = restarted.get('projects', self.p['id'])
        self.assertEqual(kept['segments'][0]['audio'], new_audio)
        self.assertEqual(kept['segments'][0]['history'], [self.p['segments'][0]['audio']])
        self.assertEqual(restarted.get('jobs', j['id'])['state'], 'interrupted')


class QueueResilienceTests(unittest.TestCase):
    """单个任务处理异常不能让唯一的消费线程永久停止，后续任务仍要执行。"""

    # 中文维护：临时数据根 + 两条排队任务；用替身替换 run_job，不启动真实子进程。
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name) / 'data'
        self.s = Studio(self.data)
        self.jobs = [self.queue_job(), self.queue_job()]
        FakeProc.instances.clear()

    # 中文维护：释放临时目录；执行中的线程必须已停止。
    def tearDown(self):
        self.s.stopping.set()
        self.s.wake.set()
        if self.s.thread:
            self.s.thread.join(5)
        cleanup_temp(self.temp)

    # 中文维护：直接落库一条排队任务（不经过 HTTP 层）。
    def queue_job(self):
        j = {'id': uid(), 'kind': 'dub', 'name': '配音', 'project_id': 'p', 'state': 'queued',
             'mode': 'all', 'segment_id': None, 'completed': 0, 'total': 1,
             'message': '等待', 'created': time.time(), 'request': {}}
        self.s.save('jobs', j)
        return j

    # 中文维护：等待条件成立，超时即失败，避免用例挂死。
    def wait_for(self, predicate, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.05)
        return False

    # 中文维护：让任务直接完成，模拟正常业务路径。
    def finish(self, job):
        latest = self.s.get('jobs', job['id'])
        latest.update(state='done', message='生成完成')
        self.s.save('jobs', latest)

    # 核心复现：第一个任务抛异常后，第二个任务仍被执行；第一个标失败而不是假完成。
    def test_exception_in_one_job_does_not_stop_queue(self):
        first, second = self.jobs
        calls = []

        def fake_run_job(job):
            calls.append(job['id'])
            if job['id'] == first['id']:
                raise RuntimeError('模拟单任务内部异常')
            self.finish(job)

        self.s.run_job = fake_run_job
        self.s.start()
        self.assertTrue(self.wait_for(lambda: self.s.get('jobs', second['id'])['state'] == 'done'))

        failed = self.s.get('jobs', first['id'])
        self.assertEqual(failed['state'], 'failed')
        self.assertIn('内部异常', failed['message'])
        # 不产生无限重试：每个任务只执行一次。
        self.assertEqual(calls, [first['id'], second['id']])
        self.assertTrue(self.s.thread.is_alive())

    # 连续多个任务失败后，后续任务仍可成功（线程未死、无假完成）。
    def test_two_failures_then_success_keeps_queue_running(self):
        third = self.queue_job()
        broken = {self.jobs[0]['id'], self.jobs[1]['id']}
        calls = []

        def fake_run_job(job):
            calls.append(job['id'])
            if job['id'] in broken:
                raise ValueError('模拟处理失败')
            self.finish(job)

        self.s.run_job = fake_run_job
        self.s.start()
        self.assertTrue(self.wait_for(lambda: self.s.get('jobs', third['id'])['state'] == 'done'))

        self.assertEqual(self.s.get('jobs', self.jobs[0]['id'])['state'], 'failed')
        self.assertEqual(self.s.get('jobs', self.jobs[1]['id'])['state'], 'failed')
        self.assertEqual(len(calls), 3)
        self.assertTrue(self.s.thread.is_alive())

    # 队列取任务时遇到瞬时数据库/读取错误也不能杀死线程。
    def test_transient_error_while_reading_queue_does_not_stop_worker(self):
        job = self.jobs[0]
        original_all = self.s.all
        raised = []

        def flaky_all(kind):
            if kind == 'jobs' and not raised:
                raised.append(True)
                raise RuntimeError('模拟数据库瞬时错误')
            return original_all(kind)

        self.s.all = flaky_all
        self.s.run_job = self.finish
        self.s.start()
        self.assertTrue(self.wait_for(lambda: self.s.get('jobs', job['id'])['state'] == 'done'))
        self.assertTrue(raised)
        self.assertTrue(self.s.thread.is_alive())

    # 核心复现：run_job 收尾时进程仍未退出 → 队列隔离，第二个任务不得启动，旧进程退出后恢复。
    def test_error_cleanup_raising_quarantines_queue(self):
        first, second = self.jobs
        self.s.STOP_TIMEOUT = 0.3
        self.s.QUARANTINE_POLL = 0.05
        runtime = write_wav(Path(self.temp.name) / 'runtime.bin', frames=100)
        for job, segment in ((first, 's1'), (second, 's2')):
            record = self.s.get('jobs', job['id'])
            record['request'] = {'segments': [{'id': segment}], 'settings': {'runtime': str(runtime)}}
            self.s.save('jobs', record)

        # 工作进程一直未退出；主进程收到取消后调用收尾，而收尾本身抛异常。
        def cancelling_ingest(job, folder):
            latest = self.s.get('jobs', job['id'])
            if latest['state'] == 'running':
                latest.update(state='cancelling')
                self.s.save('jobs', latest)

        def failing_stop(proc):
            raise RuntimeError('模拟收尾失败')

        self.s.ingest = cancelling_ingest
        self.s.stop_process = failing_stop
        with mock.patch('studio.subprocess.Popen', FakeProc):
            self.s.start()
            self.assertTrue(self.wait_for(lambda: self.s.blocked is not None, timeout=10))
            self.assertTrue(self.wait_for(lambda: self.s.get('jobs', first['id'])['state'] == 'failed', timeout=10))
            self.assertTrue(self.s.thread.is_alive())
            self.assertEqual(len(FakeProc.instances), 1)
            self.assertEqual(self.s.get('jobs', second['id'])['state'], 'queued')
            # 旧进程最终退出后，隔离解除并允许继续消费。
            FakeProc.instances[0].returncode = 0
            self.assertTrue(self.wait_for(lambda: self.s.blocked is None, timeout=10))


if __name__ == '__main__':
    unittest.main()