"""B 批（审核补充）P2 回归：持久性异常不得造成忙循环，异常信息必须可追踪。

审核需要确认：
1. 持久性数据库错误不能每 0.2 秒无限高速重试（合理退避）；
2. 状态写回失败不能让任务永久停留在 running（补偿写回）；
3. Worker/队列异常不能形成异常循环；
4. 异常信息有可追踪的持久化记录，且异常不被静默吞掉。
"""

import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio import Studio, uid  # noqa: E402


def wait_until(predicate, timeout=10, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class QueueBackoffTests(unittest.TestCase):
    # 中文维护：每个用例独立临时目录。
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.s = Studio(self.root / 'data')

    # 中文维护：停止线程并清理；Windows 句柄延迟不致命。
    def tearDown(self):
        self.s.stopping.set()
        self.s.wake.set()
        if getattr(self.s, 'thread', None):
            self.s.thread.join(10)
        try:
            self.temp.cleanup()
        except OSError:
            pass

    def job(self, **extra):
        record = {'id': uid(), 'kind': 'dub', 'name': '任务', 'project_id': 'p', 'state': 'queued',
                  'mode': 'all', 'segment_id': None, 'completed': 0, 'total': 1,
                  'message': '等待', 'created': time.time()}
        record.update(extra)
        return self.s.save('jobs', record)

    def error_log(self):
        path = self.s.root / 'queue-errors.log'
        return path.read_text(encoding='utf-8') if path.is_file() else ''

    # A. 持久性数据库错误：指数退避且写日志，不静默吞掉异常。
    def test_a_persistent_db_error_backs_off_and_is_logged(self):
        attempts = []

        def failing_all(kind):
            attempts.append(time.time())
            raise sqlite3.OperationalError('database is locked')

        self.s.all = failing_all
        self.s.start()
        time.sleep(1.2)
        self.s.stopping.set()
        self.s.wake.set()
        self.s.thread.join(10)

        self.assertGreaterEqual(len(attempts), 2)
        self.assertLessEqual(len(attempts), 8)  # 1.2 秒内合理次数，而不是每 0.2 秒一次
        gaps = [round(b - a, 2) for a, b in zip(attempts, attempts[1:])]
        self.assertGreater(gaps[-1], gaps[0], '连续失败应退避：%r' % gaps)
        log = self.error_log()
        self.assertIn('database is locked', log)
        self.assertIn('Traceback', log)
        self.assertIn('queue-error', log)

    # B. 状态写回失败时不永久停留在 running：恢复后由补偿写回纠正。
    def test_b_state_write_failure_is_compensated(self):
        job = self.job()
        state = {'fail': False}
        real_save = self.s.save

        def flaky_save(kind, record):
            if kind == 'jobs' and state['fail']:
                raise sqlite3.OperationalError('disk I/O error')
            return real_save(kind, record)

        def failing_run(record):
            state['fail'] = True
            raise RuntimeError('模拟处理失败')

        self.s.save = flaky_save
        self.s.run_job = failing_run
        self.s.start()
        self.assertTrue(wait_until(lambda: self.s.get('jobs', job['id'])['state'] == 'running', timeout=5))
        time.sleep(0.4)
        self.assertEqual(self.s.get('jobs', job['id'])['state'], 'running')  # 写入被阻塞
        self.assertTrue(self.s.pending_state.get(job['id']), '失败状态应登记为待补偿')

        state['fail'] = False
        self.assertTrue(wait_until(lambda: self.s.get('jobs', job['id'])['state'] == 'failed', timeout=10))
        self.assertFalse(self.s.pending_state.get(job['id']))
        log = self.error_log()
        self.assertIn('disk I/O error', log)
        self.assertIn('job-state', log)
        self.assertIn('job-state-recovered', log)

    # C. 反复失败的任务不会形成高速异常循环：相邻失败间隔递增。
    def test_c_repeated_failures_do_not_hot_loop(self):
        first = self.job()
        events = []

        def failing_run(record):
            events.append(time.time())
            current = self.s.get('jobs', record['id'])
            current.update(state='failed', message='失败')
            self.s.save('jobs', current)
            # 重新排一个任务，模拟持续有待处理任务且每次都异常。
            self.s.save('jobs', {'id': uid(), 'kind': 'dub', 'project_id': 'p', 'state': 'queued',
                                 'mode': 'all', 'segment_id': None, 'completed': 0, 'total': 1,
                                 'message': '等待', 'created': time.time()})

        self.s.run_job = failing_run
        self.s.start()
        time.sleep(1.2)
        self.s.stopping.set()
        self.s.wake.set()
        self.s.thread.join(10)

        self.assertLessEqual(len(events), 6)
        self.assertGreaterEqual(len(events), 2)
        gaps = [round(b - a, 2) for a, b in zip(events, events[1:])]
        self.assertGreater(gaps[-1], gaps[0], '连续任务异常应退避：%r' % gaps)
        self.assertEqual(self.s.get('jobs', first['id'])['state'], 'failed')

    # D. 队列异常后仍能恢复消费（退避不是永久停止）。
    def test_d_queue_recovers_after_transient_error(self):
        calls = {'n': 0}
        real_all = self.s.all

        def flaky_all(kind):
            if kind == 'jobs':
                calls['n'] += 1
                if calls['n'] <= 3:
                    raise sqlite3.OperationalError('temporarily unavailable')
            return real_all(kind)

        def done_run(record):
            current = self.s.get('jobs', record['id'])
            current.update(state='done', message='完成')
            self.s.save('jobs', current)

        self.s.all = flaky_all
        self.s.run_job = done_run
        job = self.job()
        self.s.start()
        self.assertTrue(wait_until(lambda: self.s.get('jobs', job['id'])['state'] == 'done', timeout=10))
        self.s.stopping.set()
        self.s.wake.set()
        self.s.thread.join(10)
        self.assertIn('temporarily unavailable', self.error_log())


if __name__ == '__main__':
    unittest.main()
