"""审核补充三：工作进程身份查询失败、启动两步之间的中断、标记损坏时的队列安全。

用户场景：任务进行中主服务被关闭/崩溃，工作进程仍在运行。修复要求：
1. 查不到进程身份（权限、查询失败）时必须保留隔离，不能当作“已退出”；
2. 进程启动与身份记录之间的中断（服务退出、写标记失败、标记损坏）必须可恢复；
3. 故障解除且旧进程确认退出后，新任务才能完成，旧任务仍需用户显式重试。

所有用例都在临时目录、同一测试数据库上重启 Studio，并使用真实子进程。
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
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import studio as studio_module  # noqa: E402
from studio import Studio, atomic_json  # noqa: E402

APP_DIR = Path(__file__).resolve().parents[1]
WORKER_MARKER_NAME = getattr(studio_module, 'WORKER_MARKER_NAME', 'worker-current.json')
WORKER_MARKER_BACKUP = getattr(studio_module, 'WORKER_MARKER_BACKUP', WORKER_MARKER_NAME + '.bak')
WORKER_JOB_RECORD = getattr(studio_module, 'WORKER_JOB_RECORD', 'worker.json')
WORKER_LEASE_NAME = getattr(studio_module, 'WORKER_LEASE_NAME', 'worker.lock')

PYTHON = sys.executable
CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

# 中文维护：普通“旧进程”，不持租约也不写身份记录（模拟第三方/旧版本遗留进程）。
PLAIN_HOLDER = "import time\nprint('worker-ready', flush=True)\ntime.sleep(180)\n"
# 中文维护：与正式工作进程一致的持有者：先持运行租约并写自己的身份记录，再开始工作。
LEASE_HOLDER = """
import sys, time
sys.path.insert(0, r'%s')
from worker_lease import hold
hold(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else None)
print('worker-ready', flush=True)
time.sleep(180)
""" % str(APP_DIR)
# 中文维护：延迟补写身份记录的“慢启动”进程：先活过等待期，之后才写出可用身份。
LATE_IDENTITY_HOLDER = """
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, r'@APP@')
from studio import atomic_json, process_identity
print('worker-ready', flush=True)
time.sleep(@DELAY@)
job = Path(sys.argv[1])
identity = process_identity(os.getpid()) or {}
atomic_json(job / 'worker.json', {'schema': 2, 'state': 'running', 'job_id': job.name,
    'pid': os.getpid(), 'created': identity.get('created'), 'exe': identity.get('exe') or '',
    'since': time.time()})
time.sleep(180)
"""


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


class WorkerIdentityRecoveryTests(unittest.TestCase):
    """身份未知与启动中断下的隔离、恢复和“实际启动次数为 0”。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / 'data'
        self.procs = []
        self.studios = []

    def tearDown(self):
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
    def job(self, s, job_id, state='queued', created=1, kind='dub', project_id='p1', request=None):
        s.save('jobs', {'id': job_id, 'kind': kind, 'state': state, 'project_id': project_id,
                        'created': created, 'request': request or {'segments': []}, 'message': '等待处理'})
        return s.get('jobs', job_id)

    # 中文维护：写临时脚本并等真实子进程就绪。
    def _spawn(self, source, args):
        script = self.root / ('holder-%s.py' % uuid.uuid4().hex)
        script.write_text(source, encoding='utf-8')
        proc = subprocess.Popen([PYTHON, '-B', str(script)] + args, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, creationflags=CREATE_NO_WINDOW)
        self.procs.append(proc)
        self.assertEqual(proc.stdout.readline().strip(), 'worker-ready')
        proc.stdout.close()
        return proc

    # 中文维护：不带租约、不写身份记录的遗留进程。
    def spawn_plain(self):
        return self._spawn(PLAIN_HOLDER, [])

    # 中文维护：与正式工作进程一致的持有者，可选写入某个任务目录的身份记录。
    def spawn_lease(self, job_id=None):
        args = [str(self.data / WORKER_LEASE_NAME)]
        if job_id:
            args.append(str(self.data / 'jobs' / job_id))
        return self._spawn(LEASE_HOLDER, args)

    # 中文维护：先经历一段“有进程、无身份、无租约”的窗口，之后才补写身份记录。
    def spawn_late_identity(self, job_id, delay=1.5):
        source = LATE_IDENTITY_HOLDER.replace('@APP@', str(APP_DIR)).replace('@DELAY@', repr(float(delay)))
        return self._spawn(source, [str(self.data / 'jobs' / job_id)])

    # 中文维护：直接写入可用于正常入队的最小项目与声线快照（不调用真实引擎）。
    def make_project(self, s, project_id='p1'):
        s.save('projects', {'id': project_id, 'name': '测试项目', 'original': '你好。',
                            'voice': {'id': 'v1', 'name': '测试声线', 'gpt_hash': 'g', 'sovits_hash': 's',
                                      'reference_hash': 'r', 'prompt_text': '你好。', 'engine_hash': 'e'},
                            'segments': [{'id': 's1', 'original': '你好。', 'spoken': '你好。', 'display': '你好。',
                                          'audio': None, 'history': []}],
                            'speed': 1.0, 'gap_ms': 180, 'paragraph_gap_ms': 420, 'exports': [],
                            'created': 1, 'dirty': True})
        return project_id

    # 中文维护：用记录器代替真实训练/推理；只记录队列实际启动了哪些任务。
    def recorder(self, s, started):
        def fake_run(job):
            started.append(job['id'])
            latest = s.get('jobs', job['id'])
            latest.update(state='done', message='完成')
            s.save('jobs', latest)
        return fake_run

    # 中文维护：断言“旧进程存活期间新任务实际启动次数为 0”，并返回启动记录。
    def assert_no_new_start(self, s, sleep=0.7):
        started = []
        self.job(s, 'job-next', created=2)
        s.QUARANTINE_POLL = 0.1
        s.run_job = self.recorder(s, started)
        s.start()
        time.sleep(sleep)
        self.assertEqual(started, [])
        self.assertEqual(s.get('jobs', 'job-next')['state'], 'queued')
        return started

    # 中文维护：旧进程退出后队列恢复，新任务完成，旧任务仍为中断态且未被重复执行。
    def assert_recovers(self, s, started):
        self.assertTrue(wait_until(lambda: s.blocked is None, 15))
        self.assertTrue(wait_until(lambda: s.get('jobs', 'job-next')['state'] == 'done', 15))
        self.assertEqual(started, ['job-next'])
        old = s.get('jobs', 'job-old')
        self.assertEqual(old['state'], 'interrupted')
        self.assertIn('队列已恢复', old['message'])
        self.assertNotIn('job-old', started)
        self.assertFalse((self.data / WORKER_MARKER_NAME).exists())

    # 1. 身份查询失败：重启时必须保留隔离与记录，不能删除标记、不能放行队列。
    def test_unknown_identity_keeps_quarantine_and_marker(self):
        first = self.open_studio()
        job = self.job(first, 'job-old', state='running')
        child = self.spawn_plain()
        first.note_worker_started(job, child)
        original = studio_module.process_state
        studio_module.process_state = lambda identity: ('unknown', '模拟身份查询失败')
        try:
            second = self.open_studio()
        finally:
            studio_module.process_state = original
        self.assertIsNotNone(second.blocked)
        self.assertTrue(second.blocked.get('unknown'))
        self.assertIn('队列已暂停', second.blocked['reason'] + second.get('jobs', 'job-old')['message'])
        self.assertTrue((self.data / WORKER_MARKER_NAME).is_file())
        self.assertTrue((self.data / WORKER_MARKER_BACKUP).is_file())
        self.assertTrue((self.data / 'jobs' / 'job-old' / WORKER_JOB_RECORD).is_file())
        started = self.assert_no_new_start(second)
        # 查询恢复后真实状态仍是存活，继续暂停。
        time.sleep(0.5)
        self.assertIsNotNone(second.blocked)
        self.assertEqual(started, [])
        # 旧进程真正退出后才恢复。
        stop_proc(child)
        self.assert_recovers(second, started)

    # 2. 一直查不到身份：即使进程已退出也不能自动放行（unknown 不等于 dead）。
    def test_unknown_never_releases_even_after_process_exit(self):
        first = self.open_studio()
        job = self.job(first, 'job-old', state='running')
        child = self.spawn_plain()
        first.note_worker_started(job, child)
        original = studio_module.process_state
        studio_module.process_state = lambda identity: ('unknown', '模拟身份查询失败')
        try:
            second = self.open_studio()
            started = self.assert_no_new_start(second)
            stop_proc(child)
            time.sleep(0.6)
            # 进程已退出，但身份仍无法确认：必须继续隔离，且记录不得删除。
            self.assertIsNotNone(second.blocked)
            self.assertEqual(started, [])
            self.assertTrue((self.data / WORKER_MARKER_NAME).is_file())
        finally:
            studio_module.process_state = original
        self.assert_recovers(second, started)

    # 3. 服务在“启动进程”与“写身份”之间退出：租约 + 工作进程自写记录让重启仍能确认存活。
    def test_crash_between_spawn_and_identity_write_is_recoverable(self):
        first = self.open_studio()
        job = self.job(first, 'job-old', state='running')
        first.begin_worker_start(job)
        # 真实进程启动并持有运行租约、自己写身份记录；随后“服务崩溃”，没有 note_worker_started。
        child = self.spawn_lease('job-old')
        second = self.open_studio()
        self.assertIsNotNone(second.blocked)
        self.assertIn('仍在运行', second.blocked['reason'])
        started = self.assert_no_new_start(second)
        stop_proc(child)
        self.assert_recovers(second, started)

    # 4. 等待期到期只触发复查：旧进程未持锁、未写身份时活过整个等待期也不得放行。
    def test_grace_expiry_only_rechecks_and_never_releases(self):
        first = self.open_studio()
        job = self.job(first, 'job-old', state='running')
        first.begin_worker_start(job)
        child = self.spawn_late_identity('job-old', delay=1.5)
        original_grace = Studio.STARTUP_GRACE
        Studio.STARTUP_GRACE = 0.3
        try:
            second = self.open_studio()
            self.assertIsNotNone(second.blocked)
            self.assertIsNone(second.blocked.get('identity'))
            started = self.assert_no_new_start(second, sleep=0.05)
            # 等待期（0.3 秒）已过并经过多次复查：旧进程仍存活、仍未持锁，新任务实际启动次数必须为 0。
            time.sleep(0.6)
            self.assertIsNone(child.poll())
            self.assertEqual(started, [])
            self.assertIsNotNone(second.blocked)
            self.assertTrue((self.data / WORKER_MARKER_NAME).is_file())
            self.assertTrue((self.data / WORKER_MARKER_BACKUP).is_file())
            # 队列确实执行过“等待期到期后”的复查，但仍保持隔离。
            self.assertTrue(wait_until(lambda: second.blocked and second.blocked.get('grace_noted'), 5))
            log = (self.data / 'queue-errors.log').read_text(encoding='utf-8')
            self.assertIn('不会仅凭等待期放行', log)
            # 旧进程稍后补写有效身份：复查读到它，仍然不启动新任务。
            self.assertTrue(wait_until(
                lambda: second.blocked and second.blocked.get('identity') and second.blocked['identity'].get('pid') == child.pid, 10))
            self.assertIsNone(child.poll())
            self.assertEqual(started, [])
            # 取得可靠退出证据（身份确认死亡）后才恢复：新任务恰好执行一次。
            stop_proc(child)
            self.assert_recovers(second, started)
        finally:
            Studio.STARTUP_GRACE = original_grace

    # 5. 文件锁查询持续 unknown：不能当作“锁空闲”放行，必须保留记录与隔离。
    def test_lease_query_unknown_keeps_quarantine(self):
        first = self.open_studio()
        job = self.job(first, 'job-old', state='running')
        first.begin_worker_start(job)
        child = self.spawn_late_identity('job-old', delay=1.5)
        original_lease = studio_module.lease_state
        original_grace = Studio.STARTUP_GRACE
        Studio.STARTUP_GRACE = 0.3
        studio_module.lease_state = lambda path: ('unknown', '模拟租约查询失败')
        try:
            second = self.open_studio()
            self.assertIsNotNone(second.blocked)
            started = self.assert_no_new_start(second, sleep=0.05)
            # 等待期已过、锁查询持续失败：仍不得放行（旧实现会在这里启动新任务）。
            time.sleep(0.6)
            self.assertIsNone(child.poll())
            self.assertEqual(started, [])
            self.assertIsNotNone(second.blocked)
            self.assertTrue((self.data / WORKER_MARKER_NAME).is_file())
            self.assertTrue(wait_until(lambda: second.blocked and second.blocked.get('grace_noted'), 5))
            self.assertIn('无法读取运行租约', (self.data / 'queue-errors.log').read_text(encoding='utf-8'))
        finally:
            studio_module.lease_state = original_lease
        # 锁恢复可读但身份仍未补写：继续隔离，不启动新任务。
        time.sleep(0.3)
        self.assertIsNotNone(second.blocked)
        self.assertEqual(started, [])
        # 旧进程补写身份后退出：取得可靠退出证据才恢复。
        self.assertTrue(wait_until(
            lambda: second.blocked and second.blocked.get('identity'), 10))
        stop_proc(child)
        self.assert_recovers(second, started)
        Studio.STARTUP_GRACE = original_grace

    # 7. 进程启动后写身份失败：意图记录 + 任务目录记录仍可恢复，队列不会放行。
    def test_identity_write_failure_after_spawn_is_recoverable(self):
        first = self.open_studio()
        job = self.job(first, 'job-old', state='running')
        first.begin_worker_start(job)
        child = self.spawn_lease('job-old')
        original = studio_module.atomic_json

        def failing(path, data):
            if 'worker-current' in Path(path).name:
                raise OSError('模拟磁盘写入失败')
            return original(path, data)

        with mock.patch.object(studio_module, 'atomic_json', failing):
            first.note_worker_started(job, child)
        self.assertIsNone(child.poll())
        marker = json.loads((self.data / WORKER_MARKER_NAME).read_text(encoding='utf-8'))
        self.assertEqual(marker.get('state'), 'starting')
        record = json.loads((self.data / 'jobs' / 'job-old' / WORKER_JOB_RECORD).read_text(encoding='utf-8'))
        self.assertEqual(record.get('pid'), child.pid)
        log = (self.data / 'queue-errors.log').read_text(encoding='utf-8')
        self.assertIn('写入失败', log)
        second = self.open_studio()
        self.assertIsNotNone(second.blocked)
        started = self.assert_no_new_start(second)
        stop_proc(child)
        self.assert_recovers(second, started)

    # 6. 启动前写身份记录失败：必须拒绝启动进程（实际启动次数为 0），并把任务标为失败。
    def test_identity_write_failure_before_spawn_does_not_start_process(self):
        first = self.open_studio()
        runtime = write_wav(self.root / 'fake-runtime.exe', frames=1)
        request = {'segments': [{'id': 's1'}], 'settings': {'runtime': str(runtime)}}
        job = self.job(first, 'job-old', state='running', request=request)
        calls = []
        original = studio_module.atomic_json

        def failing(path, data):
            if 'worker-current' in Path(path).name:
                raise OSError('模拟磁盘写入失败')
            return original(path, data)

        def forbidden_popen(*args, **kwargs):
            calls.append(args)
            raise AssertionError('写身份记录失败时不允许启动子进程')

        with mock.patch.object(studio_module, 'atomic_json', failing):
            with mock.patch.object(studio_module.subprocess, 'Popen', forbidden_popen):
                first.run_job(job)
        self.assertEqual(calls, [])
        failed = first.get('jobs', 'job-old')
        self.assertEqual(failed['state'], 'failed')
        self.assertIn('未启动', failed['message'])
        self.assertFalse((self.data / 'jobs' / 'job-old' / WORKER_JOB_RECORD).exists())

    # 7. 主标记损坏：用备份记录确认身份，旧进程存活期间继续暂停，退出后恢复。
    def test_corrupt_primary_marker_uses_backup(self):
        first = self.open_studio()
        job = self.job(first, 'job-old', state='running')
        child = self.spawn_plain()
        first.note_worker_started(job, child)
        (self.data / WORKER_MARKER_NAME).write_text('{"job_id": ', encoding='utf-8')
        (self.data / 'jobs' / 'job-old' / WORKER_JOB_RECORD).write_text('损坏', encoding='utf-8')
        second = self.open_studio()
        self.assertIsNotNone(second.blocked)
        self.assertIn('仍在运行', second.blocked['reason'])
        started = self.assert_no_new_start(second)
        stop_proc(child)
        self.assert_recovers(second, started)

    # 8. 全部记录损坏：无法确认时保持暂停；用户确认无遗留进程并清理记录后重启，队列恢复。
    def test_all_records_corrupt_stays_paused_until_records_cleaned(self):
        first = self.open_studio()
        job = self.job(first, 'job-old', state='running')
        child = self.spawn_plain()
        first.note_worker_started(job, child)
        for path in (self.data / WORKER_MARKER_NAME, self.data / WORKER_MARKER_BACKUP,
                     self.data / 'jobs' / 'job-old' / WORKER_JOB_RECORD):
            path.write_text('损坏', encoding='utf-8')
        second = self.open_studio()
        self.assertIsNotNone(second.blocked)
        self.assertTrue(second.blocked.get('unknown'))
        message = second.blocked['reason']
        self.assertIn('损坏', message)
        started = self.assert_no_new_start(second)
        stop_proc(child)
        time.sleep(0.6)
        # 记录损坏且无法核对时不会自动放行。
        self.assertIsNotNone(second.blocked)
        self.assertEqual(started, [])
        self.assertTrue((self.data / WORKER_MARKER_NAME).is_file())
        # 用户按提示确认无遗留进程后清理记录并重启。
        for path in (self.data / WORKER_MARKER_NAME, self.data / WORKER_MARKER_BACKUP,
                     self.data / 'jobs' / 'job-old' / WORKER_JOB_RECORD):
            path.unlink()
        third = self.open_studio()
        self.assertIsNone(third.blocked)
        third.QUARANTINE_POLL = 0.1
        fresh = []
        self.job(third, 'job-next2', created=3)
        third.run_job = self.recorder(third, fresh)
        third.start()
        self.assertTrue(wait_until(lambda: third.get('jobs', 'job-next2')['state'] == 'done', 10))
        self.assertEqual(fresh, ['job-next2'])
        self.assertEqual(third.get('jobs', 'job-old')['state'], 'interrupted')

    # 10. 没有主标记、但运行租约被占用：暂停队列；租约释放且任务记录确认退出后恢复。
    def test_lease_held_without_marker_pauses_queue(self):
        first = self.open_studio()
        self.job(first, 'job-old', state='running')
        child = self.spawn_lease('job-old')  # 工作进程自写身份记录并持租约
        second = self.open_studio()
        self.assertIsNotNone(second.blocked)
        self.assertIn('租约', second.blocked['reason'])
        started = self.assert_no_new_start(second)
        stop_proc(child)
        self.assert_recovers(second, started)
        self.assertTrue((self.data / WORKER_LEASE_NAME).is_file())

    # 10. 暂停提示必须说明原因与恢复方式（界面直接展示 job.message）。
    def test_paused_message_explains_reason_and_recovery(self):
        first = self.open_studio()
        job = self.job(first, 'job-old', state='running')
        child = self.spawn_plain()
        first.note_worker_started(job, child)
        second = self.open_studio()
        message = second.get('jobs', 'job-old')['message']
        self.assertIn('队列已暂停', message)
        self.assertIn('仍在运行', message)
        self.assertIn('重新打开软件', message)
        self.assertIn('上次运行中断', message)
        # 11. 启动检查没有记录、锁又读不到：必须暂停；没有旧任务记录时也要让用户看到原因与恢复步骤。
    def test_startup_without_records_and_lease_unknown_pauses_queue(self):
        first = self.open_studio()          # 干净目录：没有任何工作进程记录或旧任务
        child = self.spawn_plain()          # 真实子进程存活，但未持锁、未写身份
        original_lease = studio_module.lease_state
        studio_module.lease_state = lambda path: ('unknown', '模拟锁查询失败')
        try:
            second = self.open_studio()
            self.assertIsNotNone(second.blocked)
            self.assertTrue(second.blocked.get('unknown'))
            self.assertIn('运行租约', second.blocked['reason'])
            started = self.assert_no_new_start(second, sleep=0.8)
            self.assertEqual(started, [])
            # 用户可见：没有旧任务可写消息，暂停说明被补到新排队任务上。
            message = second.get('jobs', 'job-next')['message']
            self.assertIn('队列已暂停', message)
            self.assertIn('无法读取运行租约', message)
            self.assertIn('关闭软件', message)
            self.assertIn('worker-current.json', message)
            self.assertIn('重新打开软件', message)
            # 暂停期间再提交任务，同样直接收到原因与恢复步骤，而不是笼统的“已有任务”。
            with self.assertRaises(ValueError) as raised:
                second.enqueue('missing-project')
            self.assertIn('队列已暂停', str(raised.exception))
            self.assertIn('关闭软件', str(raised.exception))
            # 界面/诊断读取的快照也带暂停状态。
            pause = second.snapshot().get('queue_pause')
            self.assertIsNotNone(pause)
            self.assertIn('worker-current.json', pause['message'])
            # 队列确实执行过锁状态的复查。
            self.assertIn('无法读取运行租约', (self.data / 'queue-errors.log').read_text(encoding='utf-8'))
        finally:
            studio_module.lease_state = original_lease
        # 锁恢复可读且空闲，但旧进程身份仍未知：不能凭“锁空闲”放行。
        time.sleep(0.8)
        self.assertIsNone(child.poll())
        self.assertIsNotNone(second.blocked)
        self.assertEqual(started, [])
        # 旧进程退出后仍没有任何身份记录：无法证明，继续暂停并给出说明。
        stop_proc(child)
        time.sleep(0.6)
        self.assertIsNotNone(second.blocked)
        self.assertEqual(started, [])
        # 用户按提示确认没有遗留进程并重启后，队列才正常消费新任务（用户确认是恢复依据）。
        second.stopping.set()
        second.wake.set()
        second.thread.join(5)
        third = self.open_studio()
        self.assertIsNone(third.blocked)
        fresh = []
        third.QUARANTINE_POLL = 0.1
        third.run_job = self.recorder(third, fresh)
        self.job(third, 'job-after-restart', created=3)
        third.start()
        self.assertTrue(wait_until(lambda: third.get('jobs', 'job-after-restart')['state'] == 'done', 10))
        time.sleep(0.2)
        self.assertEqual(fresh, ['job-after-restart'])

    # 12. 启动检查锁 unknown + 无记录；旧进程稍后补写身份：确认退出后自动恢复且新任务只执行一次。
    def test_startup_without_records_recovers_after_late_identity(self):
        first = self.open_studio()
        child = self.spawn_late_identity('job-orphan', delay=1.2)
        original_lease = studio_module.lease_state
        studio_module.lease_state = lambda path: ('unknown', '模拟锁查询失败')
        try:
            second = self.open_studio()
        finally:
            studio_module.lease_state = original_lease
        self.assertIsNotNone(second.blocked)
        started = self.assert_no_new_start(second, sleep=0.3)
        self.assertEqual(started, [])
        # 旧进程补写有效身份：复查读到后确认仍存活，继续暂停。
        self.assertTrue(wait_until(lambda: second.blocked and second.blocked.get('identity')
                                   and second.blocked['identity'].get('pid') == child.pid, 10))
        self.assertIsNone(child.poll())
        self.assertIsNotNone(second.blocked)
        self.assertEqual(started, [])
        # 取得可靠退出证据（身份核对为 dead）后才恢复，新任务恰好执行一次。
        stop_proc(child)
        self.assertTrue(wait_until(lambda: second.blocked is None, 15))
        self.assertTrue(wait_until(lambda: second.get('jobs', 'job-next')['state'] == 'done', 15))
        time.sleep(0.2)
        self.assertEqual(started, ['job-next'])

    # 13. 干净首次安装：没有记录、锁可读空闲；首个任务正常入队并恰好执行一次。
    def test_clean_first_install_runs_first_job_once(self):
        s = self.open_studio()
        self.assertIsNone(s.blocked)
        self.assertIsNone(s.snapshot().get('queue_pause'))
        self.make_project(s)
        job = s.enqueue('p1')
        self.assertEqual(job['state'], 'queued')
        self.assertNotIn('队列已暂停', job['message'])
        started = []
        s.QUARANTINE_POLL = 0.1
        s.run_job = self.recorder(s, started)
        s.start()
        self.assertTrue(wait_until(lambda: s.get('jobs', job['id'])['state'] == 'done', 10))
        time.sleep(0.3)
        self.assertEqual(started, [job['id']])
        self.assertIsNone(s.blocked)
        self.assertIsNone(s.snapshot().get('queue_pause'))
        self.assertFalse((self.data / WORKER_MARKER_NAME).exists())
        self.assertFalse((self.data / WORKER_MARKER_BACKUP).exists())


if __name__ == '__main__':
    unittest.main()