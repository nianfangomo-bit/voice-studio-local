"""B 批（审核补充二）：服务重启后旧工作进程仍存活时的队列安全与恢复提示。

用户场景：任务卡住 → 用户关闭软件再打开 → 旧工作进程仍然存活。
修复前（b099d8d）行为：新服务把旧任务标为中断后照常启动下一项任务；没有任何持久化
进程身份，也无法识别 PID 是否被复用，更不会说明“实际恢复 0 段”。

本文件全部使用真实子进程，并在隔离临时目录里用同一份测试数据库完成“重启”。
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import studio as studio_module  # noqa: E402
from studio import Studio, atomic_json  # noqa: E402

# 中文维护：旧版本没有标记文件名，用默认值便于在修复前代码上跑出红灯。
WORKER_MARKER_NAME = getattr(studio_module, 'WORKER_MARKER_NAME', 'worker-current.json')

PYTHON = sys.executable
CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
# 中文维护：真实“旧工作进程”，跨重启继续存活。
HOLDER = "import time\nprint('worker-ready', flush=True)\ntime.sleep(180)\n"


# 中文维护：轮询等待条件成立。
def wait_until(predicate, timeout=10, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


# 中文维护：只结束时测试自己创建的真实子进程句柄，避免遗留孤儿。
def stop_proc(proc):
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.kill()
        proc.wait(10)
    except OSError:
        pass


# 中文维护：写入与引擎一致的 32 kHz 单声道 16 bit WAV。
def write_wav(path, frames=3200):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as handle:
        handle.setparams((1, 2, 32000, 0, 'NONE', 'not compressed'))
        handle.writeframes(b'\x01\x00' * frames)
    return path


class RestartOrphanTests(unittest.TestCase):
    """跨重启的孤儿工作进程、PID 复用与真实恢复提示。"""

    # 中文维护：每个用例独立临时目录，不触碰用户数据。
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / 'data'
        self.procs = []
        self.studios = []

    def tearDown(self):
        # 中文维护：先停消费线程，再结束真实子进程，最后删临时目录。
        for s in self.studios:
            s.stopping.set()
            s.wake.set()
        for s in self.studios:
            if s.thread:
                s.thread.join(5)
        for proc in self.procs:
            stop_proc(proc)
        try:
            self.temp.cleanup()
        except OSError:
            pass

    # 中文维护：创建 Studio 并登记，tearDown 统一停止消费线程。
    def open_studio(self):
        s = Studio(root=self.data)
        self.studios.append(s)
        return s

    # 中文维护：写一条任务记录并返回最新副本。
    def job(self, s, job_id, state='queued', created=1, kind='dub', project_id='p1'):
        s.save('jobs', {'id': job_id, 'kind': kind, 'state': state, 'project_id': project_id,
                        'created': created, 'request': {'segments': []}, 'message': '等待处理'})
        return s.get('jobs', job_id)

    # 中文维护：启动真实子进程并等它就绪。
    def spawn_worker(self):
        script = self.root / ('holder-%s.py' % uuid.uuid4().hex)
        script.write_text(HOLDER, encoding='utf-8')
        proc = subprocess.Popen([PYTHON, '-B', str(script)], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, creationflags=CREATE_NO_WINDOW)
        self.procs.append(proc)
        self.assertEqual(proc.stdout.readline().strip(), 'worker-ready')
        proc.stdout.close()
        return proc

    # 中文维护：用记录器代替真实训练/推理；只记录队列启动了哪些任务。
    def recorder(self, s, started):
        def fake_run(job):
            started.append(job['id'])
            latest = s.get('jobs', job['id'])
            latest.update(state='done', message='完成')
            s.save('jobs', latest)
        return fake_run

    # 中文维护：模拟“上次服务崩溃”：真实子进程仍活，数据目录里留下生产同款进程标记。
    def crash_with_live_worker(self):
        first = self.open_studio()
        job = self.job(first, 'job-old', state='running')
        child = self.spawn_worker()
        first.note_worker_started(job, child)
        self.assertIsNone(child.poll())
        return first, job, child

    # 1. 旧进程存活时重启：队列必须继续暂停，下一项任务不得启动。
    def test_restart_with_live_worker_pauses_queue(self):
        first, job, child = self.crash_with_live_worker()
        second = self.open_studio()
        self.assertIsNotNone(second.blocked)
        self.assertIn('仍在运行', second.blocked['reason'])
        old = second.get('jobs', 'job-old')
        self.assertEqual(old['state'], 'interrupted')
        self.assertIn('仍在运行', old['message'])
        self.assertIn('队列已暂停', old['message'])

        self.job(second, 'job-next', created=2)
        started = []
        second.run_job = self.recorder(second, started)
        second.start()
        time.sleep(1.0)
        self.assertEqual(started, [])
        self.assertEqual(second.get('jobs', 'job-next')['state'], 'queued')
        self.assertTrue(second.busy())
        self.assertTrue((self.data / WORKER_MARKER_NAME).is_file())
        self.assertIsNotNone(second.blocked)

    # 2. 旧进程退出后：清除持久化标记，新任务正常完成；旧任务不被重复执行。
    def test_queue_resumes_after_worker_exit_and_old_job_not_repeated(self):
        first, job, child = self.crash_with_live_worker()
        second = self.open_studio()
        self.assertIsNotNone(second.blocked)
        second.QUARANTINE_POLL = 0.1
        self.job(second, 'job-next', created=2)
        started = []
        second.run_job = self.recorder(second, started)
        second.start()
        time.sleep(0.4)
        self.assertEqual(started, [])

        stop_proc(child)
        self.assertTrue(wait_until(lambda: second.blocked is None, 15))
        self.assertFalse((self.data / WORKER_MARKER_NAME).exists())
        self.assertTrue(wait_until(lambda: second.get('jobs', 'job-next')['state'] == 'done', 15))
        self.assertEqual(started, ['job-next'])
        old = second.get('jobs', 'job-old')
        self.assertEqual(old['state'], 'interrupted')
        self.assertIn('队列已恢复', old['message'])
        self.assertIn('上次运行中断', old['message'])
        self.assertNotIn('job-old', started)

    # 3. PID 被复用：同 PID 但身份不符不得当作旧进程，也不得按 PID 结束进程。
    def test_pid_reuse_is_not_mistaken_for_orphan(self):
        first = self.open_studio()
        job = self.job(first, 'job-old', state='running')
        # 测试进程当前确实存活，但创建时间/可执行路径都不同于记录，必然是复用后的另一个进程。
        atomic_json(self.data / WORKER_MARKER_NAME,
                    {'job_id': job['id'], 'pid': os.getpid(), 'created': 1,
                     'exe': 'C:\\Windows\\System32\\not-our-worker.exe', 'since': time.time()})
        second = self.open_studio()
        self.assertIsNone(second.blocked)
        self.assertFalse((self.data / WORKER_MARKER_NAME).exists())
        self.job(second, 'job-next', created=2)
        started = []
        second.run_job = self.recorder(second, started)
        second.start()
        self.assertTrue(wait_until(lambda: second.get('jobs', 'job-next')['state'] == 'done', 10))
        self.assertEqual(started, ['job-next'])
        # 测试进程仍存活，说明没有“只凭 PID 就结束进程”。
        self.assertIsNone(second.blocked)
        log = (self.data / 'queue-errors.log').read_text(encoding='utf-8')
        self.assertIn('复用', log)

    # 4. 重启前旧进程就已退出：残留标记按已退出清理，队列直接恢复。
    def test_stale_marker_of_exited_worker_is_cleared(self):
        first = self.open_studio()
        job = self.job(first, 'job-old', state='running')
        child = self.spawn_worker()
        first.note_worker_started(job, child)
        stop_proc(child)
        second = self.open_studio()
        self.assertIsNone(second.blocked)
        self.assertFalse((self.data / WORKER_MARKER_NAME).exists())
        self.job(second, 'job-next', created=2)
        started = []
        second.run_job = self.recorder(second, started)
        second.start()
        self.assertTrue(wait_until(lambda: second.get('jobs', 'job-next')['state'] == 'done', 10))
        self.assertEqual(started, ['job-next'])

    # 5. 进度损坏：提示必须说明“未恢复任何片段”，不能谎称已恢复。
    def test_recovery_message_for_corrupt_progress(self):
        first = self.open_studio()
        self.job(first, 'job-old', state='running')
        folder = self.data / 'jobs' / 'job-old'
        folder.mkdir(parents=True)
        (folder / 'progress.json').write_text('{"results": [', encoding='utf-8')
        second = self.open_studio()
        message = second.get('jobs', 'job-old')['message']
        self.assertEqual(second.get('jobs', 'job-old')['state'], 'interrupted')
        self.assertIn('进度记录损坏', message)
        self.assertIn('未恢复任何片段', message)

    # 6. 没有任何进度：同样如实说明“没有可恢复的进度”。
    def test_recovery_message_when_nothing_to_recover(self):
        first = self.open_studio()
        self.job(first, 'job-old', state='running')
        second = self.open_studio()
        message = second.get('jobs', 'job-old')['message']
        self.assertEqual(second.get('jobs', 'job-old')['state'], 'interrupted')
        self.assertIn('没有可恢复的进度', message)
        self.assertIn('未恢复任何片段', message)

    # 7. 部分恢复：说明已恢复数量与被跳过的数量，并保留有效音频、跳过无效音频。
    def test_recovery_message_counts_partial_recovery(self):
        first = self.open_studio()
        first.save('projects', {'id': 'p1', 'segments': [
            {'id': 's1', 'audio': None, 'history': []},
            {'id': 's2', 'audio': None, 'history': []}]})
        self.job(first, 'job-old', state='running')
        folder = self.data / 'jobs' / 'job-old'
        folder.mkdir(parents=True)
        good = write_wav(self.root / 'good.wav')
        (folder / 'progress.json').write_text(json.dumps({'results': [
            {'id': 's1', 'audio': {'path': str(good), 'fingerprint': 'fp-1'}},
            {'id': 's2', 'audio': {'path': str(self.root / 'missing.wav'), 'fingerprint': 'fp-2'}}]}),
            encoding='utf-8')
        second = self.open_studio()
        message = second.get('jobs', 'job-old')['message']
        self.assertIn('已恢复 1 个已完成片段', message)
        self.assertIn('另有 1 条进度被跳过', message)
        project = second.get('projects', 'p1')
        self.assertEqual(project['segments'][0]['audio']['path'], str(good))
        self.assertIsNone(project['segments'][1]['audio'])

    # 8. 整理任务恢复：说明实际恢复的整理段数。
    def test_recovery_message_for_preparation_rows(self):
        first = self.open_studio()
        first.save('datasets', {'id': 'p1', 'rows': [], 'quality_reports': [], 'revision': 0})
        self.job(first, 'job-old', state='running', kind='preparation')
        folder = self.data / 'jobs' / 'job-old'
        folder.mkdir(parents=True)
        audio = write_wav(self.root / 'row.wav')
        (folder / 'progress.json').write_text(json.dumps({'completed': 1, 'rows': [
            {'id': 'r1', 'audio': str(audio), 'text': '你好', 'seconds': 1.0}]}), encoding='utf-8')
        second = self.open_studio()
        message = second.get('jobs', 'job-old')['message']
        self.assertIn('已恢复 1 段整理结果', message)
        self.assertEqual(len(second.get('datasets', 'p1')['rows']), 1)

    # 9. 重启后旧任务保持中断态，不会自动重新排队（需用户确认后重试）。
    def test_interrupted_job_is_not_auto_requeued(self):
        first, job, child = self.crash_with_live_worker()
        second = self.open_studio()
        self.assertEqual(second.get('jobs', 'job-old')['state'], 'interrupted')
        queued = [j['id'] for j in second.all('jobs') if j.get('state') == 'queued']
        self.assertEqual(queued, [])
        self.assertIsNotNone(second.blocked)


if __name__ == '__main__':
    unittest.main()