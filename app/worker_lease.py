"""工作进程运行租约：进程存活期间持锁，供主服务跨重启判断是否仍有遗留进程。"""
from __future__ import annotations

import os
import time
import traceback
from pathlib import Path

_HANDLES = []


# 中文维护：工作进程启动后立刻持有租约；拿不到说明已有另一个工作进程在跑，直接拒绝启动。
def hold(lease_path, job_dir=None):
    path = Path(lease_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open('a+b')
    handle.seek(0)
    if os.name == 'nt':
        import msvcrt
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise RuntimeError('另一个本软件工作进程仍持有运行租约，请等待它结束后重试。')
    else:
        import fcntl
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError('另一个本软件工作进程仍持有运行租约，请等待它结束后重试。')
    _HANDLES.append(handle)
    if job_dir:
        _write_identity(Path(job_dir))
    return handle


# 中文维护：工作进程自己再写一份身份记录，覆盖“主服务写标记失败/来不及写”的情况。
def _write_identity(job_dir):
    try:
        from studio import atomic_json, process_identity
        identity = process_identity(os.getpid()) or {}
        atomic_json(job_dir / 'worker.json', {
            'schema': 2, 'state': 'running', 'job_id': job_dir.name,
            'pid': os.getpid(), 'created': identity.get('created'), 'exe': identity.get('exe') or '',
            'since': time.time()})
    except Exception:
        traceback.print_exc()