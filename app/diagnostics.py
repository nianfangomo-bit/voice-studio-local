"""按白名单生成可分享的诊断摘要；不收集文案、路径、音频或原始日志。"""
from collections import Counter
import json
from pathlib import Path
import platform


def read_app_version(base_dir=None):
    """兼容源码仓库（上级 VERSION）与离线包扁平布局（同级 package.json）。"""
    here = Path(base_dir) if base_dir is not None else Path(__file__).resolve().parent
    version_path = here.parent / 'VERSION'
    if version_path.is_file():
        text = version_path.read_text(encoding='utf-8').strip()
        if text:
            return text
    package_path = here / 'package.json'
    if package_path.is_file():
        try:
            value = str(json.loads(package_path.read_text(encoding='utf-8')).get('version') or '').strip()
            if value:
                return value
        except (OSError, ValueError):
            pass
    return 'unknown'


def summary(studio):
    """只导出状态及计数，用户仍可另行审阅原始任务日志。"""
    version = read_app_version()
    jobs = studio.all('jobs')
    allowed = {'queued', 'running', 'cancelling', 'cancelled', 'done', 'failed', 'interrupted'}
    counts = Counter(job.get('state') if job.get('state') in allowed else 'unknown' for job in jobs)
    settings = studio.settings()
    return {'schema': 'voice-safe-diagnostics-v1', 'app_version': version,
            'system': platform.system(), 'system_release': platform.release(),
            'configured_files_exist': {key: bool(settings.get(key) and Path(settings[key]).exists())
                                       for key in ('engine', 'runtime', 'legacy', 'ffmpeg')},
            'job_states': dict(counts), 'voice_count': len(studio.all('voices')),
            'project_count': len(studio.all('projects')),
            'privacy': '不含用户名、本机路径、任务 ID、文案、录音、权重和原始日志。此摘要不能代替完整故障日志。'}
