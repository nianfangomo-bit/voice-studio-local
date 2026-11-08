"""启动器只读复用业务数据库中的环境，不创建第二份可分叉的环境配置。"""
import json
import sqlite3
from pathlib import Path


def selected_runtime(root):
    """优先使用已保存环境；已选环境丢失时明确停止，不悄悄退回旧环境。"""
    root = Path(root)
    db = root / 'data' / 'studio.sqlite'
    if db.is_file():
        try:
            with sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True) as conn:
                row = conn.execute('SELECT body FROM records WHERE kind=? AND id=?', ('settings', 'local')).fetchone()
            if row:
                value = json.loads(row[0]).get('runtime')
                if not value or not Path(value).is_file():
                    raise ValueError('已选择的 Python 环境不存在。请恢复原目录，或在仍可打开的软件中重新选择环境；不会自动替换环境。')
                return Path(value)
        except (sqlite3.Error, json.JSONDecodeError) as exc:
            raise ValueError('无法读取已保存的环境设置，请保留 data 目录并检查日志；不会自动覆盖配置。') from exc
    bootstrap = root / 'data' / 'bootstrap.json'
    if bootstrap.is_file():
        try:
            runtime = Path(json.loads(bootstrap.read_text(encoding='utf-8'))['runtime'])
            if runtime.is_file():
                return runtime
        except (OSError, ValueError, KeyError, TypeError):
            pass
    bundled = root / 'engine' / 'runtime' / 'python.exe'
    return bundled if bundled.is_file() else None
