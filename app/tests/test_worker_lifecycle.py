"""B 批（审核补充）P0 回归：工作进程生命周期，旧进程未确认退出时队列不得启动新任务。

修复前这些用例必然失败：
- 不存在 ``confirm_process_exit``：收尾只调用一次 ``stop_process``，失败后仍把任务标 failed
  并继续消费，旧子进程可能仍在跑（GPU 冲突）。
- 不存在隔离状态：无法阻止下一个任务启动。

本文件包含一组**真实子进程**用例（不用 Mock 返回值代替进程生命周期）。
"""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import studio as studio_module  # noqa: E402
from studio import Studio, uid  # noqa: E402

PYTHON = sys.executable
CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
WINDOWS = os.name == 'nt'

# 中文维护：真实“卡死工作进程”脚本；可选持有 gpu.lock，模拟推理/训练进程占锁。
WORKER_SCRIPT = '''
import sys, time
lock_path, seconds = sys.argv[1], float(sys.argv[2])
if lock_path and lock_path != '-':
    handle = open(lock_path, 'a+b')
    handle.seek(0)
    if sys.platform == 'win32':
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
time.sleep(seconds)
'''


# 中文维护：登记并回收真实子进程，避免测试结束后留下孤儿进程。
def spawn(script, lock_path, seconds=120):
    return subprocess.Popen([PYTHON, str(script), str(lock_path or '-'), str(seconds)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=CREATE_NO_WINDOW)


# 中文维护：不使用 subprocess.run/taskkill 结束子进程——测试会把 studio 的 Popen 替换为替身，
# 直接对真实 Popen 句柄 kill，避免“终止命令”本身也被替身劫持。
def kill_tree(proc):
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if WINDOWS:
            os.system('taskkill /PID %d /T /F >NUL 2>&1' % proc.pid)


def wait_until(predicate, timeout=10, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class ProcessExitConfirmationTests(unittest.TestCase):
    """进程退出判断：已退出、正常停止、第一次失败后成功、始终无法退出。"""

    # 中文维护：每个用例独立临时目录，不触碰用户数据。
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.script = self.root / 'holder.py'
        self.script.write_text(WORKER_SCRIPT, encoding='utf-8')
        self.s = Studio(self.root / 'data')
        self.s.STOP_TIMEOUT = 0.5
        self.children = []

    # 中文维护：回收所有子进程并清理临时目录。
    def tearDown(self):
        for child in self.children:
            kill_tree(child)
        self.temp.cleanup()

    def start(self, lock_path='-', seconds=120):
        child = spawn(self.script, lock_path, seconds)
        self.children.append(child)
        return child

    # A. 进程已退出时直接确认，不再发送终止命令。
    def test_a_already_exited_process_is_confirmed(self):
        child = subprocess.Popen([PYTHON, '-c', 'pass'], creationflags=CREATE_NO_WINDOW)
        child.wait(timeout=60)
        attempts = []
        self.assertTrue(self.s.confirm_process_exit(child, timeout=1, stop=lambda p: attempts.append(1)))
        self.assertEqual(attempts, [])
        self.assertIsNotNone(child.poll())

    # 正常停止：真实进程被终止且确认退出。
    def test_normal_stop_kills_real_process(self):
        child = self.start()
        self.assertIsNone(child.poll())
        self.assertTrue(self.s.confirm_process_exit(child, timeout=10))
        self.assertIsNotNone(child.poll())

    # B. 第一次停止失败（异常/无效），随后成功退出。
    def test_b_first_stop_fails_then_exits(self):
        child = self.start()
        attempts = []

        def flaky_stop(proc):
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError('模拟第一次停止失败')
            Studio.stop_process(proc)

        self.assertTrue(self.s.confirm_process_exit(child, timeout=10, stop=flaky_stop))
        self.assertGreaterEqual(len(attempts), 2)
        self.assertIsNotNone(child.poll())

    # C1. 停止动作“返回成功”但进程仍在运行 → 不得认定已退出。
    def test_c_stop_returning_without_exit_is_not_confirmed(self):
        child = self.start()
        self.assertFalse(self.s.confirm_process_exit(child, timeout=0.4, stop=lambda p: None))
        self.assertIsNone(child.poll())
        self.assertTrue(getattr(self.s, 'blocked', None) is None)  # 由调用方决定隔离，本方法只判定

    # C2. 停止动作始终抛异常且进程不退 → 返回 False。
    def test_c2_stop_always_failing_is_not_confirmed(self):
        child = self.start()
        attempts = []

        def failing_stop(proc):
            attempts.append(1)
            raise RuntimeError('模拟终止失败')

        self.assertFalse(self.s.confirm_process_exit(child, timeout=0.6, stop=failing_stop))
        self.assertIsNone(child.poll())
        self.assertGreaterEqual(len(attempts), 2)  # 超时前有合理重试，而不是一次就放弃


@unittest.skipUnless(WINDOWS, 'GPU 文件锁与 taskkill 用例仅在 Windows 运行')
class QueueQuarantineTests(unittest.TestCase):
    """真实子进程未退出时：队列隔离、不启动下一任务、解锁后恢复。"""

    # 中文维护：真实工作进程替身 + 两个排队任务；第二个任务只记录调用，不真正执行。
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.script = self.root / 'holder.py'
        self.script.write_text(WORKER_SCRIPT, encoding='utf-8')
        self.data = self.root / 'data'
        self.s = Studio(self.data)
        self.s.STOP_TIMEOUT = 0.4
        self.s.QUARANTINE_POLL = 0.05
        self.s.QUEUE_MIN_BACKOFF = 0.05
        self.s.QUEUE_MAX_BACKOFF = 0.2
        self.runtime = self.root / 'runtime.bin'
        self.runtime.write_bytes(b'fake runtime for require_file')
        self.child = None
        self.calls = []
        self.real_popen = subprocess.Popen
        self.first = self.add_job()
        self.second = self.add_job()

        real_run_job = self.s.run_job

        # 第一个任务走真实 run_job（会启动真实子进程）；第二个只记录，证明队列是否继续。
        def dispatch(job):
            if job['id'] == self.first['id']:
                real_run_job(job)
            else:
                self.calls.append(job['id'])
                self.finish(job)

        # 让 run_job 走到取消分支：工作表里立即变成 cancelling。
        def cancelling_ingest(job, folder):
            latest = self.s.get('jobs', job['id'])
            if latest['state'] == 'running':
                latest.update(state='cancelling')
                self.s.save('jobs', latest)

        self.s.run_job = dispatch
        self.s.ingest = cancelling_ingest
        self.s.stop_process = self.failing_stop

    # 中文维护：停掉线程、杀残留子进程、清理目录。
    def tearDown(self):
        self.s.stopping.set()
        self.s.wake.set()
        if self.s.thread:
            self.s.thread.join(10)
        kill_tree(self.child)
        self.temp.cleanup()

    def add_job(self):
        job = {'id': uid(), 'kind': 'dub', 'name': '配音', 'project_id': 'p', 'state': 'queued',
               'mode': 'all', 'segment_id': None, 'completed': 0, 'total': 1,
               'message': '等待', 'created': time.time(),
               'request': {'segments': [{'id': 's1'}], 'settings': {'runtime': str(self.runtime)}}}
        self.s.save('jobs', job)
        return job

    def finish(self, job):
        latest = self.s.get('jobs', job['id'])
        latest.update(state='done', message='生成完成')
        self.s.save('jobs', latest)

    # 中文维护：终止动作始终失败，模拟“杀不掉”的旧进程。
    def failing_stop(self, proc):
        raise RuntimeError('模拟终止失败')

    # 中文维护：用真实子进程替换 Popen，但保留真实句柄与生命周期。
    def fake_popen(self, args, **kwargs):
        self.child = self.real_popen([PYTHON, str(self.script), str(self.s.root / 'gpu.lock'), '120'],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     creationflags=CREATE_NO_WINDOW)
        return self.child

    # 中文维护：尝试获取 GPU 锁，返回 (是否成功, 句柄)。
    def try_lock(self):
        handle = open(self.s.root / 'gpu.lock', 'a+b')
        handle.seek(0)
        import msvcrt
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True, handle
        except OSError:
            return False, handle

    # 中文维护：释放测试自己持有的锁。
    @staticmethod
    def unlock(handle):
        import msvcrt
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        handle.close()

    # C/D/F. 旧进程杀不掉时：队列隔离，第二个任务绝不启动，GPU 锁仍被旧进程持有，任务标记失败。
    def test_cd_quarantine_blocks_next_job_and_keeps_lock(self):
        with mock.patch('studio.subprocess.Popen', self.fake_popen):
            self.s.start()
            self.assertTrue(wait_until(lambda: self.child is not None, timeout=10))
            self.assertTrue(wait_until(lambda: getattr(self.s, 'blocked', None) is not None, timeout=10))
            # 给队列留出时间：若实现错误，第二个任务会在这段时间被启动。
            time.sleep(0.6)
            self.assertEqual(self.calls, [])
            self.assertEqual(self.s.get('jobs', self.second['id'])['state'], 'queued')
            self.assertIsNone(self.child.poll())

            first = self.s.get('jobs', self.first['id'])
            self.assertEqual(first['state'], 'failed')
            self.assertIn('队列', first['message'])

            acquired, handle = self.try_lock()
            try:
                self.assertFalse(acquired, '旧进程未退出时 GPU 锁必须仍被占用')
            finally:
                self.unlock(handle)

    # C/D/E. 旧进程最终退出后：队列恢复，第二个任务执行，GPU 锁可重新获取。
    def test_e_queue_recovers_after_worker_exits(self):
        with mock.patch('studio.subprocess.Popen', self.fake_popen):
            self.s.start()
            self.assertTrue(wait_until(lambda: getattr(self.s, 'blocked', None) is not None, timeout=10))
            kill_tree(self.child)
            self.assertTrue(wait_until(lambda: self.s.get('jobs', self.second['id'])['state'] == 'done', timeout=10))
            self.assertEqual(self.calls, [self.second['id']])
            self.assertIsNone(self.s.blocked)

            acquired, handle = self.try_lock()
            try:
                self.assertTrue(acquired, '旧进程退出后 GPU 锁应可重新获取')
            finally:
                self.unlock(handle)

    # F. 隔离期间 busy() 为真：界面不会允许编辑或再排新任务，并且直接说明暂停原因。
    def test_f_busy_during_quarantine(self):
        with mock.patch('studio.subprocess.Popen', self.fake_popen):
            self.s.start()
            self.assertTrue(wait_until(lambda: getattr(self.s, 'blocked', None) is not None, timeout=10))
            self.assertTrue(self.s.busy())
            with self.assertRaises(ValueError) as ctx:
                self.s.enqueue('p', 'all', None)
            self.assertIn('队列已暂停', str(ctx.exception))
            self.assertIn('重新打开软件', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()