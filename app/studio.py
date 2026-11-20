from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
import uuid
import wave
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HIDDEN = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
# 中文维护：跨重启识别“同一工作进程”的持久化标记文件。
WORKER_MARKER_NAME = 'worker-current.json'
WORKER_MARKER_BACKUP = 'worker-current.json.bak'
WORKER_JOB_RECORD = 'worker.json'
WORKER_LEASE_NAME = 'worker.lock'


# 中文维护：探测进程状态，必须区分三态：alive / dead / unknown。
# unknown（权限不足、查询失败、句柄异常）绝不能当成“已退出”，否则会放行队列。
def process_probe(pid):
    if not isinstance(pid, int) or pid <= 0:
        return {'state': 'dead', 'identity': None, 'detail': '没有有效的 PID'}
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
                                          ctypes.POINTER(wintypes.FILETIME),
                                          ctypes.POINTER(wintypes.FILETIME),
                                          ctypes.POINTER(wintypes.FILETIME)]
        kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                      wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        # PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE：即可读身份，也可判断是否已退出。
        handle = kernel.OpenProcess(0x1000 | 0x00100000, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            if error == 87:  # ERROR_INVALID_PARAMETER：PID 不存在
                return {'state': 'dead', 'identity': None, 'detail': '进程不存在（PID %s）' % pid}
            if error == 5:  # ERROR_ACCESS_DENIED：进程可能存在，但不能查询
                return {'state': 'unknown', 'identity': None, 'detail': '拒绝访问进程（错误 5）'}
            return {'state': 'unknown', 'identity': None, 'detail': '无法打开进程（错误 %s）' % error}
        try:
            created, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
            if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                          ctypes.byref(kernel_time), ctypes.byref(user_time)):
                return {'state': 'unknown', 'identity': None, 'detail': '无法读取进程创建时间'}
            stamp = (created.dwHighDateTime << 32) | created.dwLowDateTime
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(32768)
            exe = buffer.value if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)) else ''
            waited = kernel.WaitForSingleObject(handle, 0)
            if waited == 0:  # WAIT_OBJECT_0：已退出
                return {'state': 'dead', 'identity': None, 'detail': '进程已退出'}
            if waited != 0x102:  # 非 WAIT_TIMEOUT：无法确认运行状态
                return {'state': 'unknown', 'identity': None, 'detail': '无法确认进程运行状态（%s）' % waited}
            identity = {'pid': pid, 'created': int(stamp), 'exe': exe}
            return {'state': 'alive', 'identity': identity, 'detail': ''}
        finally:
            kernel.CloseHandle(handle)
    try:
        stat = Path('/proc/%d/stat' % pid).read_text(encoding='utf-8')
    except FileNotFoundError:
        return {'state': 'dead', 'identity': None, 'detail': '进程不存在（PID %s）' % pid}
    except PermissionError:
        return {'state': 'unknown', 'identity': None, 'detail': '拒绝访问进程'}
    except OSError as exc:
        return {'state': 'unknown', 'identity': None, 'detail': '无法读取进程状态：%s' % exc}
    try:
        fields = stat.rsplit(')', 1)[1].split()
        state = fields[0]
        stamp = int(fields[19])
        exe = os.readlink('/proc/%d/exe' % pid)
    except (OSError, ValueError, IndexError) as exc:
        return {'state': 'unknown', 'identity': None, 'detail': '无法解析进程状态：%s' % exc}
    if state in ('Z', 'X'):
        return {'state': 'dead', 'identity': None, 'detail': '进程已退出'}
    return {'state': 'alive', 'identity': {'pid': pid, 'created': stamp, 'exe': exe}, 'detail': ''}


# 中文维护：取进程身份；查询失败返回 None（调用方需按 unknown 处理，不能当作已退出）。
def process_identity(pid):
    probe = process_probe(pid)
    return probe['identity'] if probe['state'] == 'alive' else None


# 中文维护：按身份确认进程是否仍是“同一个”进程；返回 (状态, 原因)，状态可为 alive/dead/unknown。
def process_state(identity):
    if not isinstance(identity, dict) or not isinstance(identity.get('pid'), int):
        return 'unknown', '缺少可核对的进程身份'
    probe = process_probe(identity['pid'])
    if probe['state'] != 'alive':
        return probe['state'], probe['detail']
    current = probe['identity']
    if identity.get('created') is not None and current.get('created') != identity['created']:
        return 'dead', 'PID 已被复用（创建时间不同）'
    if identity.get('exe') and current.get('exe') and identity['exe'].casefold() != current['exe'].casefold():
        return 'dead', 'PID 已被复用（可执行路径不同）'
    return 'alive', ''


# 中文维护：兼容旧调用；只有确认存活才返回 True，unknown 不再等同于退出。
def process_alive(identity):
    return process_state(identity)[0] == 'alive'


# 中文维护：工作进程运行租约。进程存活期间持锁，主服务重启后即使没有 PID 记录也能判断是否仍有进程。
def lease_state(path):
    path = Path(path)
    try:
        handle = path.open('a+b')
    except OSError as exc:
        return 'unknown', '无法打开租约文件：%s' % exc
    try:
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return 'locked', '运行租约被占用'
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return 'free', ''
        import fcntl
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return 'locked', '运行租约被占用'
        fcntl.flock(handle, fcntl.LOCK_UN)
        return 'free', ''
    except OSError as exc:
        return 'unknown', '无法检查运行租约：%s' % exc
    finally:
        handle.close()




# 中文维护：生成独立记录标识，避免文件名和不同任务冲突。
def uid():
    return uuid.uuid4().hex


# 中文维护：分块计算文件 SHA-256，用于模型、缓存和成品一致性核验。
def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


# 中文维护：先完整写入临时文件再原子替换；短暂占用时有限重试，失败保留临时成果。
def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uid() + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    # Windows readers/antivirus can briefly hold the destination without delete
    # sharing. Keep the complete temporary file and retry the atomic rename.
    for attempt in range(20):
        try:
            temp.replace(path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(.05)


# 中文维护：核对已确认的旧核心指纹后动态导入，核心变更必须重新确认环境。
def load_legacy(settings):
    path = Path(settings['legacy']) / 'voice_tool' / 'main.py'
    if digest(path) != settings['legacy_hash']:
        raise ValueError('旧配音核心已改变，请在环境设置中重新确认并保存。')
    spec = importlib.util.spec_from_file_location('voice_studio_legacy', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# 中文维护：解析本机文件路径并检查存在性及允许后缀，不负责模型可信度判定。
def require_file(value, suffix=None):
    p = Path(str(value)).expanduser().resolve()
    if not p.is_file() or (suffix and p.suffix.lower() not in suffix):
        raise ValueError('找不到所需文件或格式不符：' + str(p))
    return p


class Studio:
    # 中文维护：进程收尾与队列异常退避参数；可在测试中覆盖，生产默认值保持保守。
    STOP_TIMEOUT = 20.0
    QUARANTINE_POLL = 1.0
    QUEUE_MIN_BACKOFF = 0.2
    QUEUE_MAX_BACKOFF = 5.0
    # 中文维护：启动意图记录没有 PID 时，等待多久后再提示“仍在复查”；等待期只触发复查，不作为放行依据。
    STARTUP_GRACE = 5.0
    # 中文维护：unknown 状态复查日志的节流间隔（秒），避免每秒刷屏。
    UNKNOWN_LOG_INTERVAL = 30.0

    # 中文维护：初始化独立存储和锁，将上次未结束任务标为中断并尝试恢复已完成片段。
    def __init__(self, root=None):
        self.root = Path(root or ROOT / 'data').resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.db = self.root / 'studio.sqlite'
        # 中文维护：blocked 表示有工作进程未能确认退出；此时队列不得启动任何新任务。
        self.blocked = None
        self.queue_backoff = 0.0
        self.pending_state = {}
        with self.conn() as c:
            c.execute('CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, body TEXT, PRIMARY KEY(kind,id))')
        for job in self.all('jobs'):
            if not isinstance(job, dict) or job.get('state') not in ('queued', 'running', 'cancelling'):
                continue
            # 中文维护：单个任务的恢复失败不得阻止服务启动；任务保持中断并保留录音、片段和导出。
            try:
                job.update(state='interrupted', message='上次运行中断；已有片段保留。请确认后重试。')
                self.save('jobs', job)
                summary = self.ingest(job, self.root / 'jobs' / job['id'])
                restored = self.get('jobs', job['id'])
                # 中文维护：按实际恢复结果说明（损坏、跳过、恢复 0 段时不再谎称“已恢复完成片段”）。
                restored['message'] = self.recovery_message(job.get('kind'), summary)
                self.save('jobs', restored)
            except Exception as exc:
                traceback.print_exc()
                self.mark_interrupted_after_recovery_error(job, exc)
        # 中文维护：上次运行留下的工作进程记录/租约；存活、未知或损坏都继续暂停队列，避免并发占卡。
        self.adopt_orphan_records()
        self.thread = None

    # 中文维护：打开带 WAL 的数据库连接，调用者用上下文管理事务。
    def conn(self):
        c = sqlite3.connect(str(self.db), timeout=20)
        c.execute('PRAGMA journal_mode=WAL')
        return c

    # 中文维护：以类型和标识持久化记录并返回原对象。
    def save(self, kind, obj):
        with self.conn() as c:
            c.execute('INSERT OR REPLACE INTO records VALUES (?,?,?)', (kind, obj['id'], json.dumps(obj, ensure_ascii=False)))
        return obj

    # 中文维护：参数化查询指定记录，不存在时返回可显示的业务错误。
    def get(self, kind, ident):
        with self.conn() as c:
            row = c.execute('SELECT body FROM records WHERE kind=? AND id=?', (kind, ident)).fetchone()
        if not row:
            raise ValueError('记录不存在')
        return json.loads(row[0])

    # 中文维护：读取某类型全部记录，按入库顺序倒序返回。
    def all(self, kind):
        with self.conn() as c:
            rows = c.execute('SELECT body FROM records WHERE kind=? ORDER BY rowid DESC', (kind,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    # 中文维护：读取本地环境设置；首次未配置时返回空字典。
    def settings(self):
        try:
            return self.get('settings', 'local')
        except ValueError:
            return {}

    # 中文维护：校验用户信任和必要文件，空闲时保存路径与引擎指纹，不安装依赖。
    def configure(self, data, verify_runtime=False):
        if data.get('trusted') is not True:
            raise ValueError('请确认这是可信的本地引擎与配音程序。')
        engine = Path(data['engine']).resolve()
        runtime = require_file(data.get('runtime') or engine / 'runtime' / 'python.exe')
        if runtime.name.lower() != 'python.exe':
            raise ValueError('请选择可信环境中的 python.exe，不接受其他程序。')
        require_file(engine / 'GPT_SoVITS' / 'TTS_infer_pack' / 'TTS.py')
        legacy = Path(data['legacy']).resolve()
        core = require_file(legacy / 'voice_tool' / 'main.py')
        ffmpeg = require_file(data.get('ffmpeg') or shutil.which('ffmpeg') or '')
        if ffmpeg.name.lower() not in ('ffmpeg.exe', 'ffmpeg'):
            raise ValueError('请选择 FFmpeg 可执行文件')
        with self.lock:
            if self.busy():
                raise ValueError('任务运行时不能修改环境。请等待或取消任务。')
            previous = self.settings()
            selected = {'id': 'local', 'engine': str(engine), 'runtime': str(runtime),
                'legacy': str(legacy), 'legacy_hash': digest(core), 'ffmpeg': str(ffmpeg),
                'engine_hash': digest(engine / 'GPT_SoVITS' / 'TTS_infer_pack' / 'TTS.py'), 'version': 'v2Pro',
                'environment_name': str(data.get('environment_name') or engine.name)[:100],
                'validation': 'paths_only'}
            if verify_runtime:
                report = self.probe_environment(runtime)
                if report.get('ok') is not True:
                    raise ValueError('新环境未通过检查，原配置保留。' + report.get('detail', '请使用经过验证的完整环境。'))
                selected.update(validation='dependencies_and_cuda', validation_report=report)
            if previous and any(previous.get(k) != selected.get(k) for k in ('engine', 'runtime', 'legacy', 'ffmpeg')):
                self.save('environment_history', {'id': uid(), 'created': time.time(), 'settings': previous})
            return self.save('settings', selected)

    def probe_environment(self, runtime):
        """在独立进程检查候选环境；调用方持有业务锁，避免与任务同时占用显卡。"""
        try:
            probe = subprocess.run([str(runtime), '-I', '-X', 'utf8', '-B', str(ROOT / 'environment_probe.py')],
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60, creationflags=HIDDEN)
            lines = [line for line in probe.stdout.splitlines() if line.startswith('VOICE_PROBE_JSON=')]
            report = json.loads(lines[-1].split('=', 1)[1]) if lines else {}
            if probe.returncode != 0:
                report['ok'] = False
            if not report:
                return {'ok': False, 'detail': '运行环境没有返回检查结果，请检查完整安装。'}
            return report
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return {'ok': False, 'detail': '环境检查失败或超时，请确认 Python 与依赖完整后重试。'}

    # 中文维护：检测磁盘、配置路径和显卡，返回可操作的检查结果而非音质评分。
    def check(self):
        s = self.settings()
        checks = [{'name': '项目磁盘可用空间', 'ok': shutil.disk_usage(self.root).free > 2 * 1024**3,
                   'detail': f'{shutil.disk_usage(self.root).free / 1024**3:.1f} GB'}]
        for key in ('runtime', 'engine', 'legacy', 'ffmpeg'):
            checks.append({'name': key, 'ok': bool(s.get(key) and Path(s[key]).exists()), 'detail': s.get(key, '尚未配置')})
        gpu = shutil.which('nvidia-smi')
        if gpu:
            try:
                p = subprocess.run([gpu, '--query-gpu=name,memory.free', '--format=csv,noheader'], capture_output=True,
                                   text=True, timeout=10, creationflags=HIDDEN)
                checks.append({'name': 'NVIDIA 显卡', 'ok': p.returncode == 0, 'detail': p.stdout.strip() or p.stderr.strip()})
            except Exception as e:
                checks.append({'name': '显卡检测', 'ok': False, 'detail': str(e)})
        else:
            checks.append({'name': 'NVIDIA 显卡', 'ok': False, 'detail': '未找到 nvidia-smi；当前版本需要 CUDA 环境。'})
        with self.lock:
            if self.busy():
                checks.append({'name': '实际运行环境检查', 'ok': False, 'detail': '当前有任务，暂不占用显卡。请在任务结束后再次检查。'})
            elif s.get('runtime') and Path(s['runtime']).is_file():
                report = self.probe_environment(s['runtime'])
                checks.append({'name': '依赖、矩阵与 FFT 实测', 'ok': report.get('ok') is True,
                               'detail': report.get('detail', '运行环境未返回检查结果，请检查完整安装与本机日志。'),
                               'versions': {k: report[k] for k in ('python', 'torch', 'cuda', 'torchaudio') if k in report}})
        return checks

    # 中文维护：验证模型来源确认与参考音频时长，复制参考录音并保存权重指纹。
    def import_voice(self, data):
        s = self.settings()
        if not s:
            raise ValueError('请先完成环境设置。')
        if data.get('authorized') is not True or data.get('trusted') is not True:
            raise ValueError('导入前需确认素材授权和模型来源可信。')
        name = str(data.get('name', '')).strip()
        prompt = str(data.get('prompt_text', '')).strip()
        if not name or not prompt:
            raise ValueError('声线名称和参考录音原文不能为空。')
        gpt = require_file(data['gpt'], {'.ckpt'})
        sovits = require_file(data['sovits'], {'.pth'})
        ref = require_file(data['reference'], {'.wav'})
        with wave.open(str(ref), 'rb') as w:
            duration = w.getnframes() / w.getframerate()
        if not 3 <= duration <= 10:
            raise ValueError('当前引擎参考录音需为 3～10 秒的 PCM WAV。请先整理录音。')
        ident = uid()
        dest = self.root / 'voices' / ident / 'reference.wav'
        dest.parent.mkdir(parents=True)
        shutil.copy2(ref, dest)
        voice = {'id': ident, 'name': name, 'version': 1, 'status': 'imported', 'gpt': str(gpt), 'sovits': str(sovits),
                 'gpt_hash': digest(gpt), 'sovits_hash': digest(sovits), 'reference': str(dest), 'reference_hash': digest(dest),
                 'prompt_text': prompt, 'authorization': str(data.get('authorization', '用户确认拥有使用授权')),
                 'engine_hash': s['engine_hash'], 'engine_version': s['version'], 'created': time.time()}
        return self.save('voices', voice)

    # 中文维护：检查全局或指定项目是否存在排队、运行、取消中的任务；隔离期间一律视为忙。
    def busy(self, project=None):
        if self.blocked:
            return True
        return any(j['state'] in ('queued', 'running', 'cancelling') and (not project or j['project_id'] == project)
                   for j in self.all('jobs'))

    # 中文维护：保留原文并按标点分段，固定所选声线快照及三种文本字段。
    def create_project(self, data):
        text = str(data.get('text', '')).strip()
        if not text or len(text) > 30000:
            raise ValueError('请输入 1～30000 字文案。')
        voice = self.get('voices', data['voice_id'])
        core = load_legacy(self.settings())
        segments = [{'id': uid(), 'original': a.text, 'spoken': a.text, 'display': a.text,
                     'paragraph': a.paragraph_break, 'audio': None, 'history': []} for a in core.split_script(text)]
        if any(len(seg['spoken']) > 3000 for seg in segments):
            raise ValueError('单段超过 3000 字，请增加句号或分号后再创建。')
        return self.save('projects', {'id': uid(), 'name': str(data.get('name') or text[:18]), 'original': text,
                         'voice': voice, 'segments': segments, 'gap_ms': 180, 'paragraph_gap_ms': 420,
                         'speed': 1.0, 'exports': [], 'created': time.time(), 'dirty': True})

    # 中文维护：仅空闲时修改指定片段的朗读和字幕文本，保留已有音频历史。
    def edit_segment(self, project_id, segment_id, data):
        with self.lock:
            if self.busy(project_id):
                raise ValueError('此项目正在生成，请先取消或等待完成后修改。')
            p = self.get('projects', project_id)
            seg = next((x for x in p['segments'] if x['id'] == segment_id), None)
            if not seg:
                raise ValueError('片段不存在')
            for field in ('spoken', 'display'):
                value = str(data.get(field, seg[field])).strip()
                if not value or len(value) > 3000:
                    raise ValueError('每段朗读和字幕需为 1～3000 字。')
                seg[field] = value
            p['dirty'] = True
            return self.save('projects', p)

    # 中文维护：在合法范围内保存语速和停顿，标记成品需要重新合成。
    def options(self, project_id, data):
        with self.lock:
            if self.busy(project_id):
                raise ValueError('生成期间不能修改参数')
            p = self.get('projects', project_id)
            speed, gap = float(data['speed']), int(data['gap_ms'])
            if not .6 <= speed <= 1.5 or not 0 <= gap <= 3000:
                raise ValueError('语速范围 0.6～1.5；句间停顿范围 0～3000 毫秒。')
            p.update(speed=speed, gap_ms=gap, dirty=True)
            return self.save('projects', p)

    # 中文维护：根据朗读文本、声线和生成参数计算缓存键；字幕与停顿不影响音频缓存。
    @staticmethod
    def fingerprint(p, seg):
        v = p['voice']
        value = [seg['spoken'], p['speed'], v['gpt_hash'], v['sovits_hash'], v['reference_hash'],
                 v['prompt_text'], v['engine_hash'], 'zh', 'cut0', 1.35, 15, 1.0, 32]
        return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()

    # 中文维护：检查片段音频存在且指纹匹配，不将旧音频当作当前文本的结果。
    def current(self, p, seg):
        audio = seg.get('audio')
        return bool(audio and audio['fingerprint'] == self.fingerprint(p, seg) and Path(audio['path']).is_file())

    # 中文维护：固定任务输入快照，仅将需要生成的片段排队；单句重做可主动绕过缓存。
    def enqueue(self, project_id, mode='all', segment_id=None):
        if mode not in ('all', 'preview', 'segment'):
            raise ValueError('未知任务类型')
        with self.lock:
            if self.blocked:
                # 中文维护：暂停时优先说明原因与恢复步骤，不用笼统的“已有任务”掩盖真实状态。
                raise ValueError(self.pause_message())
            if self.busy(project_id):
                raise ValueError('项目已有等待或运行中的任务。')
            p = self.get('projects', project_id)
            chosen = p['segments'] if mode == 'all' else p['segments'][:1] if mode == 'preview' else [s for s in p['segments'] if s['id'] == segment_id]
            if not chosen:
                raise ValueError('没有可生成的片段')
            todo = [dict(s, fingerprint=self.fingerprint(p, s)) for s in chosen if mode == 'segment' or not self.current(p, s)]
            j = {'id': uid(), 'project_id': project_id, 'mode': mode, 'segment_id': segment_id,
                 'state': 'queued', 'completed': 0, 'total': len(todo), 'message': '等待处理',
                 'created': time.time(), 'request': {'project': p, 'segments': todo, 'settings': self.settings()}}
            self.save('jobs', j)
            self.wake.set()
            return j

    # 中文维护：排队任务直接取消；运行任务标为取消中，交由工作线程终止。
    def cancel(self, ident):
        with self.lock:
            j = self.get('jobs', ident)
            if j['state'] == 'queued':
                j.update(state='cancelled', message='已取消，已有成果保留。')
            elif j['state'] == 'running':
                j.update(state='cancelling', message='正在停止处理进程，已完成片段会保留。')
            return self.save('jobs', j)

    # 中文维护：只重排可重试任务；训练须回向导重新确认，不伪装断点续训。
    def retry(self, ident):
        j = self.get('jobs', ident)
        if j.get('kind') == 'training':
            raise ValueError('请回训练工作台重新检查计划并确认重试。旧检查点保留；本版不会自动断点续训。')
        if j['state'] not in ('failed', 'cancelled', 'interrupted'):
            raise ValueError('仅失败、取消或中断任务可以重试')
        if j.get('kind') == 'preparation':
            from preparation import enqueue
            return enqueue(self, j['project_id'])
        return self.enqueue(j['project_id'], j['mode'], j['segment_id'])

    # 中文维护：判断整理结果是否可用；空结果只在原有素材也为空时接受，音频必须真实存在。
    def validate_preparation_rows(self, dataset, rows):
        existing = dataset.get('rows') if isinstance(dataset.get('rows'), list) else []
        if not rows:
            if existing:
                return False, '进度中的整理结果为空，但素材库已有 %d 个片段，保留原数据' % len(existing)
            return True, ''
        for index, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                return False, '第 %d 条整理结果不是对象' % index
            for field in ('id', 'audio'):
                if not isinstance(row.get(field), str) or not row.get(field):
                    return False, '第 %d 条整理结果缺少 %s' % (index, field)
            if not isinstance(row.get('text'), str):
                return False, '第 %d 条整理结果缺少文本' % index
            if isinstance(row.get('seconds'), bool) or not isinstance(row.get('seconds'), (int, float)):
                return False, '第 %d 条整理结果缺少时长' % index
            if not Path(row['audio']).is_file():
                return False, '第 %d 条整理结果音频不存在：%s' % (index, row['audio'])
        return True, ''

    # 中文维护：只有文件真实存在、可解析且参数符合引擎约定时才接受进度里的音频。
    def valid_segment_audio(self, audio):
        if not isinstance(audio, dict):
            return False
        path = audio.get('path')
        if not isinstance(path, str) or not path:
            return False
        if not isinstance(audio.get('fingerprint'), str) or not audio['fingerprint']:
            return False
        target = Path(path)
        if not target.is_file() or target.suffix.lower() != '.wav':
            return False
        try:
            with wave.open(str(target), 'rb') as handle:
                if (handle.getnchannels(), handle.getsampwidth(), handle.getframerate()) != (1, 2, 32000):
                    return False
                if handle.getnframes() <= 0:
                    return False
        except (OSError, EOFError, wave.Error):
            return False
        if isinstance(audio.get('sha256'), str) and audio['sha256']:
            return digest(target) == audio['sha256']
        return True

    # 中文维护：把恢复时跳过的原因写进任务目录与队列日志，便于排查且不影响主流程。
    def note_recovery_issue(self, job, folder, reason):
        try:
            folder = Path(folder)
            folder.mkdir(parents=True, exist_ok=True)
            with (folder / 'recovery.log').open('a', encoding='utf-8') as handle:
                handle.write('[%s] %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), reason))
        except OSError:
            traceback.print_exc()
        self.log_problem('recovery', '任务 %s：%s' % (job.get('id'), reason))

    # 中文维护：按恢复摘要生成真实提示；恢复 0 段、进度损坏或被跳过时如实说明而不是谎称已恢复。
    @staticmethod
    def recovery_message(kind, summary):
        summary = summary if isinstance(summary, dict) else {}
        corrupt = bool(summary.get('corrupt'))
        applied = summary.get('applied') or 0
        skipped = summary.get('skipped') or 0
        if kind == 'training':
            return '上次运行中断；训练需回训练工作台重新确认计划后重试，旧检查点保留。'
        if kind == 'preparation':
            if applied:
                return '上次运行中断；已恢复 %d 段整理结果，请逐段试听校对后重试。' % applied
            if corrupt:
                return '上次运行中断；进度记录损坏，未恢复整理结果，原有素材和录音保留。请确认后重试。'
            if skipped:
                return '上次运行中断；进度中的整理结果被跳过（与现有素材不一致或音频缺失），原有素材保留。请确认后重试。'
            return '上次运行中断；没有可恢复的整理结果，原有素材保留。请确认后重试。'
        if applied:
            extra = '；另有 %d 条进度被跳过' % skipped if skipped else ''
            return '上次运行中断；已恢复 %d 个已完成片段%s。请确认后重试。' % (applied, extra)
        if corrupt:
            return '上次运行中断；进度记录损坏，未恢复任何片段，已有录音和片段保留。请确认后重试。'
        if skipped:
            return '上次运行中断；%d 条进度结果无效已跳过，未恢复新片段。请确认后重试。' % skipped
        return '上次运行中断；没有可恢复的进度，未恢复任何片段。请确认后重试。'

    # 中文维护：标记文件位置（主文件 + 备份），任一可读即可恢复身份。
    def marker_paths(self):
        return [self.root / WORKER_MARKER_NAME, self.root / WORKER_MARKER_BACKUP]

    # 中文维护：任务目录里的冗余身份记录，标记文件全部损坏时仍可核对。
    def job_marker_path(self, job_id):
        return self.root / 'jobs' / str(job_id) / WORKER_JOB_RECORD

    # 中文维护：从任意记录中提取可核对的进程身份；只有“启动意图”等无 PID 记录返回 None。
    @staticmethod
    def marker_identity(marker):
        if not isinstance(marker, dict):
            return None
        pid = marker.get('pid')
        if not isinstance(pid, int) or pid <= 0:
            return None
        return {'pid': pid, 'created': marker.get('created'), 'exe': marker.get('exe') or ''}

    # 中文维护：先写“进程即将启动”的意图记录，再启动进程；写盘失败则拒绝启动，避免遗留不可追踪进程。
    def begin_worker_start(self, job):
        job_id = job.get('id') if isinstance(job, dict) else None
        lease_path = self.root / WORKER_LEASE_NAME
        try:
            lease_path.open('a+b').close()
        except OSError as exc:
            raise RuntimeError('无法创建运行租约（%s），本次未启动任务。' % exc)
        try:
            prior = self.get('jobs', job_id).get('message') or '' if job_id else ''
        except ValueError:
            prior = ''
        marker = {'schema': 2, 'state': 'starting', 'job_id': job_id,
                  'lease': str(lease_path), 'since': time.time(), 'prior_message': prior}
        try:
            self.write_worker_marker(marker, hard=True)
        except OSError as exc:
            self.log_problem('worker-start', traceback.format_exc())
            raise RuntimeError('无法记录工作进程身份（%s），为避免留下无法追踪的进程，本次未启动任务。' % exc)
        return marker

    # 中文维护：写入标记及冗余副本；hard=True 时主文件失败必须抛错（调用方据此拒绝启动进程）。
    def write_worker_marker(self, marker, hard=False):
        job_id = marker.get('job_id')
        targets = list(self.marker_paths())
        if job_id:
            targets.append(self.job_marker_path(job_id))
        failures = []
        for index, path in enumerate(targets):
            try:
                atomic_json(path, marker)
            except OSError as exc:
                failures.append('%s: %s' % (path.name, exc))
                if index == 0 and hard:
                    raise
                traceback.print_exc()
        if failures and not hard:
            self.log_problem('worker-marker', '工作进程标记部分写入失败：%s' % '; '.join(failures))
        return True

    # 中文维护：进程启动后写入真实身份；写失败只记录并保留意图记录与运行租约，不沿用旧代码“无记录运行”。
    def note_worker_started(self, job, proc):
        pid = getattr(proc, 'pid', None)
        identity = process_identity(pid) or {'pid': pid, 'created': None, 'exe': ''}
        job_id = job.get('id') if isinstance(job, dict) else None
        marker = {'schema': 2, 'state': 'running', 'job_id': job_id,
                  'pid': pid, 'created': identity.get('created'), 'exe': identity.get('exe'),
                  'lease': str(self.root / WORKER_LEASE_NAME), 'since': time.time()}
        try:
            self.write_worker_marker(marker)
        except Exception:
            self.log_problem('worker-identity', traceback.format_exc())

    # 中文维护：依次尝试主标记、备份标记、任务目录冗余记录；全部损坏时返回 corrupt 状态。
    # quiet=True 供隔离复查循环使用，避免每次复查重复写日志。
    def read_worker_marker(self, quiet=False):
        corrupt = False
        candidates = [('main', self.root / WORKER_MARKER_NAME),
                      ('backup', self.root / WORKER_MARKER_BACKUP)]
        job_root = self.root / 'jobs'
        if job_root.is_dir():
            try:
                records = sorted(job_root.glob('*/' + WORKER_JOB_RECORD),
                                 key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
            except OSError:
                records = []
            candidates += [('job', path) for path in records]
        found = None
        for source, path in candidates:
            if not path.is_file():
                continue
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
            except FileNotFoundError:
                continue
            except (OSError, ValueError) as exc:
                corrupt = True
                if not quiet:
                    self.log_problem('worker-marker', '%s 记录无法读取（%s）：%s' % (source, path, exc))
                continue
            if not isinstance(data, dict):
                corrupt = True
                continue
            if source != 'main' and not quiet:
                self.log_problem('worker-marker', '主标记不可用，已使用 %s 记录：%s' % (source, path))
            if found is None or (self.marker_identity(found) is None and self.marker_identity(data) is not None):
                found = data
        return found, ('corrupt' if corrupt and found is None else 'ok')

    # 中文维护：删除全部标记（含任务目录冗余记录）；文件不存在或暂被占用都不影响主流程。
    def clear_worker_marker(self):
        paths = list(self.marker_paths())
        job_root = self.root / 'jobs'
        if job_root.is_dir():
            try:
                paths += list(job_root.glob('*/' + WORKER_JOB_RECORD))
            except OSError:
                pass
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                traceback.print_exc()

    # 中文维护：启动时接续孤儿状态：确认存活则暂停；确认退出/复用则放行；无法判断则保留隔离并复查。
    # 临时读写故障不得让服务启动失败：记录日志并保留隔离，交给消费线程稍后复查。
    def adopt_orphan_records(self):
        try:
            return self._adopt_orphan_records()
        except Exception:
            traceback.print_exc()
            self.log_problem('worker-adopt', '启动检查遇到临时故障，保留隔离并稍后复查\n' + traceback.format_exc())
            try:
                self.block_orphan_quarantine(None, None, '启动检查遇到临时故障，暂时无法确认是否仍有遗留进程',
                                             unknown=True)
            except Exception:
                traceback.print_exc()
                self.log_problem('worker-adopt', '暂停说明写入失败\n' + traceback.format_exc())
                self._block_in_memory('启动检查遇到临时故障，暂时无法确认是否仍有遗留进程')
            return True

    # 中文维护：启动检查的实际判定；异常由 adopt_orphan_records 统一兜底，避免启动失败或误放行。
    def _adopt_orphan_records(self):
        marker, marker_state = self.read_worker_marker()
        lease, lease_detail = lease_state(self.root / WORKER_LEASE_NAME)
        identity = self.marker_identity(marker)
        if marker is None and marker_state != 'corrupt':
            if lease == 'locked':
                self.block_orphan_quarantine(None, None, '检测到运行租约仍被占用（%s），但没有找到进程记录'
                                             % lease_detail, unknown=True)
                return True
            if lease == 'unknown':
                # 中文维护：没有记录且锁读不到时同样无法排除遗留进程；与已暂停队列保持一致，先暂停并复查。
                self.block_orphan_quarantine(None, None, '没有找到工作进程记录，且暂时无法读取运行租约（%s）'
                                             % lease_detail, unknown=True)
                return True
            return False
        if lease == 'locked':
            self.block_orphan_quarantine(marker, identity, '旧工作进程仍在运行（仍持有运行租约）', unknown=False)
            return True
        if identity:
            state, detail = process_state(identity)
            if state == 'alive':
                self.block_orphan_quarantine(marker, identity, '上次任务的工作进程仍在运行', unknown=False)
                return True
            if state == 'dead':
                self.clear_worker_marker()
                self.log_problem('quarantine-cleared',
                                 '上次记录的 PID %s 已退出或已被复用（%s），按已退出处理' % (identity['pid'], detail))
                return False
            # 中文维护：查询失败属于“暂时无法判断”，保留记录与隔离，等待复查，绝不据此放行。
            self.block_orphan_quarantine(marker, identity, '暂时无法确认旧工作进程是否已退出（%s）' % detail,
                                         unknown=True)
            return True
        if marker is None or marker_state == 'corrupt':
            self.block_orphan_quarantine(None, None, '工作进程记录已损坏，无法确认是否仍有遗留进程', unknown=True)
            return True
        # 只有启动意图（进程可能还在启动中，也可能已退出），先暂停并观察运行租约。
        self.block_orphan_quarantine(marker, None, '上次服务在启动工作进程时中断，正在复查是否仍有遗留进程',
                                     unknown=True, grace=True)
        return True

    # 中文维护：进入启动隔离并说明原因与恢复方式；unknown 状态持续复查，不会自动当作已退出。
    def block_orphan_quarantine(self, marker, identity, reason, unknown=False, grace=False):
        marker = marker if isinstance(marker, dict) else {}
        job_id = marker.get('job_id')
        pid = identity.get('pid') if identity else marker.get('pid')
        prior = marker.get('prior_message') or ''
        if unknown:
            warning = ('队列已暂停，不会启动新任务：%s。软件会继续复查，请不要重复提交任务；'
                       '若确认已没有遗留的 Python 进程，请先关闭软件，再删除 data 目录下的 '
                       'worker-current.json、worker-current.json.bak 以及对应任务目录中的 worker.json，然后重新打开软件。'
                       % reason)
        else:
            warning = ('旧工作进程仍在运行（PID %s），为避免与它争用显卡，队列已暂停，不会启动新任务；'
                       '请等待它结束，或手动结束该进程后重新打开软件。' % pid)
        self.blocked = {'job_id': job_id, 'pid': pid, 'proc': None, 'identity': identity,
                        'marker': marker, 'unknown': unknown,
                        'grace': (time.time() + self.STARTUP_GRACE) if grace else None,
                        'since': time.time(), 'reason': reason, 'prior_message': prior,
                        'warning': warning, 'detail': '', 'last_log': 0.0}
        self.log_problem('quarantine-unknown' if unknown else 'quarantine-restored', warning)
        if job_id:
            try:
                latest = self.get('jobs', job_id)
                prior = prior or latest.get('message') or ''
                separator = '' if prior.endswith(('。', '；', ';', '！', '!')) else '；'
                latest['message'] = prior + separator + warning
                self.save('jobs', latest)
            except ValueError:
                pass
            except Exception:
                # 中文维护：写旧任务消息失败不影响内存中的隔离状态；消费线程稍后会重试标注。
                traceback.print_exc()
                self.log_problem('quarantine-annotate', traceback.format_exc())
        else:
            # 中文维护：没有旧任务可写消息时，把原因与恢复步骤补到队列中的任务上，界面才能直接展示。
            try:
                self.annotate_paused_jobs(warning)
            except Exception:
                # 中文维护：启动时说明写入失败不能阻止服务启动；隔离已生效，稍后由消费线程重试。
                traceback.print_exc()
                self.log_problem('quarantine-annotate', traceback.format_exc())
        self.blocked['prior_message'] = prior
        return True

    # 中文维护：启动检查异常且连说明都写不进数据库时的最小隔离状态；只放内存，仍不解封。
    def _block_in_memory(self, reason):
        warning = ('队列已暂停，不会启动新任务：%s。软件会继续复查；若确认已没有遗留的 Python 进程，'
                   '请先关闭软件并重新打开。' % reason)
        self.blocked = {'job_id': None, 'pid': None, 'proc': None, 'identity': None, 'marker': {},
                        'unknown': True, 'grace': None, 'since': time.time(), 'reason': reason,
                        'prior_message': '', 'warning': warning, 'detail': '', 'last_log': 0.0}

    # 中文维护：把暂停原因与恢复步骤追加到尚未开始的排队任务，重复调用不会重复追加。
    def annotate_paused_jobs(self, warning=None):
        warning = warning or (self.blocked or {}).get('warning')
        if not warning:
            return
        jobs = self.all('jobs')  # 读失败交给调用方退避重试，绝不因此解封队列。
        for job in jobs:
            if not isinstance(job, dict) or job.get('state') != 'queued':
                continue
            prior = job.get('message') or ''
            if warning in prior:
                continue
            separator = '' if prior.endswith(('。', '；', ';', '！', '!')) else '；'
            job['message'] = prior + separator + warning
            job['queue_paused'] = True
            try:
                self.save('jobs', job)
            except Exception:
                # 中文维护：单条任务写入失败只记录并留到下一轮复查，不影响消费线程。
                traceback.print_exc()
                self.log_problem('quarantine-annotate', traceback.format_exc())
                return

    # 中文维护：队列暂停期间给用户看的完整说明（原因 + 可执行的恢复步骤）。
    def pause_message(self):
        info = self.blocked or {}
        return info.get('warning') or ('队列已暂停，不会启动新任务：%s。'
                                       '请确认没有遗留的 Python 进程并重新打开软件。'
                                       % (info.get('reason') or '旧工作进程状态尚未确认'))

    # 中文维护：合并工作进程实际进度；保存片段历史或素材修订，支持异常后恢复。返回恢复摘要。
    def ingest(self, j, folder):
        progress_path = folder / 'progress.json'
        if not progress_path.exists():
            return None
        # 中文维护：损坏的进度文件不能阻止启动或队列；保留原文件、已生成片段和旧导出。
        try:
            progress = json.loads(progress_path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            self.note_recovery_issue(j, folder, 'progress.json 无法解析：%s' % exc)
            return {'kind': j.get('kind'), 'corrupt': True, 'applied': 0, 'skipped': 0}
        if not isinstance(progress, dict):
            self.note_recovery_issue(j, folder, 'progress.json 不是对象，已忽略')
            return {'kind': j.get('kind'), 'corrupt': True, 'applied': 0, 'skipped': 0}
        with self.lock:
            kind = j.get('kind')
            summary = {'kind': kind, 'corrupt': False, 'applied': 0, 'skipped': 0}
            if kind == 'preparation':
                try:
                    ds = self.get('datasets', j['project_id'])
                except ValueError:
                    ds = None
                if isinstance(ds, dict):
                    if ds.get('preparation_job') != j['id']:
                        if ds.get('rows'):
                            atomic_json(self.root / 'datasets' / ds['id'] / ('revision-' + str(ds['revision']) + '.json'), ds)
                        ds['preparation_job'] = j['id']
                        ds['revision'] += 1
                    rows = progress.get('rows')
                    if isinstance(rows, list):
                        accepted, reason = self.validate_preparation_rows(ds, rows)
                        if accepted:
                            ds['rows'] = rows
                            summary['applied'] = len(rows)
                        else:
                            # 中文维护：未通过校验的整理结果不得覆盖已有校对片段。
                            summary['skipped'] = len(rows)
                            summary['kept'] = bool(ds.get('rows'))
                            self.note_recovery_issue(j, folder, '未采用进度整理结果：' + reason)
                    reports = progress.get('quality_reports')
                    if isinstance(reports, list) and (reports or not ds.get('quality_reports')):
                        ds['quality_reports'] = reports
                    self.save('datasets', ds)
                else:
                    summary['missing'] = True
                    self.note_recovery_issue(j, folder, '素材库记录不存在，已跳过整理结果恢复')
            if kind in ('training', 'preparation'):
                try:
                    latest = self.get('jobs', j['id'])
                except ValueError:
                    self.note_recovery_issue(j, folder, '任务记录不存在，进度无法写回')
                    summary['missing'] = True
                    return summary
                latest.update(completed=progress.get('completed', 0), message=progress.get('message', '训练中'))
                self.save('jobs', latest)
                return summary
            try:
                p = self.get('projects', j['project_id'])
            except ValueError:
                self.note_recovery_issue(j, folder, '项目记录不存在，已跳过片段音频恢复')
                summary['missing'] = True
                return summary
            if not isinstance(p, dict):
                self.note_recovery_issue(j, folder, '项目记录格式异常，已跳过片段音频恢复')
                summary['missing'] = True
                return summary
            results = progress.get('results')
            if not isinstance(results, list):
                results = []
            segments = {s.get('id'): s for s in p.get('segments', []) if isinstance(s, dict)}
            for item in results:
                if not isinstance(item, dict) or not isinstance(item.get('audio'), dict):
                    summary['skipped'] += 1
                    continue
                seg = segments.get(item.get('id'))
                # 中文维护：进度与项目片段不一致时跳过该条，绝不改动已有片段或历史。
                if seg is None:
                    summary['skipped'] += 1
                    continue
                audio = item['audio']
                # 中文维护：音频不存在/不可读/格式不符时保留原有有效音频，并记录原因。
                if not self.valid_segment_audio(audio):
                    summary['skipped'] += 1
                    self.note_recovery_issue(j, folder, '片段 %s 的进度音频无效（不存在/不可读/格式不符），保留原音频'
                                             % item.get('id'))
                    continue
                summary['applied'] += 1
                if seg.get('audio') != audio:
                    if seg.get('audio'):
                        seg['history'].append(seg['audio'])
                    seg['audio'] = audio
                    p['dirty'] = True
            self.save('projects', p)
            try:
                latest = self.get('jobs', j['id'])
            except ValueError:
                self.note_recovery_issue(j, folder, '任务记录不存在，进度无法写回')
                return summary
            latest.update(completed=len(results), message=progress.get('message', '处理中'))
            self.save('jobs', latest)
            return summary

    # 中文维护：恢复失败时把任务留在中断态并记录原因，绝不向上抛出阻断启动。
    def mark_interrupted_after_recovery_error(self, job, reason=None):
        detail = '%s: %s' % (type(reason).__name__, reason) if reason is not None else '未知异常'
        self.log_problem('recovery', '任务 %s 恢复失败：%s' % (job.get('id'), detail))
        self.save_job_state(job.get('id'), state='interrupted',
                            message='上次运行中断；恢复进度时出现异常（%s），录音、片段和导出均已保留。请确认后重试。'
                                    % (type(reason).__name__ if reason is not None else '未知'))

    # 中文维护：队列层兜底；仅把仍在 running 的任务标失败，不产生自动重试或假完成。
    def fail_job_after_worker_error(self, job):
        if not isinstance(job, dict) or not job.get('id'):
            return
        try:
            latest = self.get('jobs', job['id'])
            if latest.get('state') == 'running':
                self.save_job_state(job['id'], state='failed',
                                    message='处理任务时出现内部异常；已完成内容和日志已保留，请重试。')
        except Exception:
            self.log_problem('queue-error', traceback.format_exc())

    # 中文维护：启动单一后台消费线程；由服务入口负责避免重复实例。
    def start(self):
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    # 中文维护：按创建时间串行消费队列，空闲时等待唤醒以避免忙轮询。
    def loop(self):
        while not self.stopping.is_set():
            if self.blocked:
                # 中文维护：旧工作进程未确认退出前，绝不启动下一个任务，也不再消费队列。
                # 暂停期间新排入的任务也要带上可见的暂停原因与恢复步骤（没有旧任务记录时尤其重要）。
                try:
                    self.annotate_paused_jobs()
                except Exception:
                    # 中文维护：临时读写故障只记录并退避，绝不能让唯一消费线程退出或提前解封。
                    self.pause_loop_error('quarantine-annotate', '暂停说明读取/写入失败，保留隔离并稍后重试')
                    continue
                try:
                    self.release_quarantine_when_exited()
                except Exception:
                    self.pause_loop_error('quarantine-error', '暂停复查遇到临时故障，保留隔离并稍后重试')
                    continue
                self.queue_backoff = 0.0
                self.retry_pending_state()
                continue
            job = None
            # 中文维护：单个任务或单次读取异常不得终止唯一消费线程，后续任务仍须执行。
            try:
                with self.lock:
                    jobs = sorted([j for j in self.all('jobs') if isinstance(j, dict) and j.get('state') == 'queued'],
                                  key=lambda j: j.get('created', 0))
                    if jobs:
                        job = jobs[0]
                        label = {'training': '训练', 'preparation': '切分转写'}.get(job.get('kind'), '配音')
                        job.update(state='running', message='准备独立' + label + '进程')
                        self.save('jobs', job)
                if job is None:
                    self.wake.wait(1)
                    self.wake.clear()
                    continue
                self.run_job(job)
                # 中文维护：正常失败的任务之间也退避，避免大量快速失败时形成高速循环。
                latest = self.get('jobs', job['id'])
                if isinstance(latest, dict) and latest.get('state') == 'failed':
                    self.queue_backoff = min(max(self.QUEUE_MIN_BACKOFF, self.queue_backoff * 2),
                                             self.QUEUE_MAX_BACKOFF)
                    self.stopping.wait(self.queue_backoff)
                else:
                    self.queue_backoff = 0.0
            except Exception:
                traceback.print_exc()
                self.log_problem('queue-error', traceback.format_exc())
                self.fail_job_after_worker_error(job)
                # 中文维护：持久性错误指数退避，避免每 0.2 秒无限重试；成功一轮后重置。
                self.queue_backoff = min(max(self.QUEUE_MIN_BACKOFF, self.queue_backoff * 2),
                                         self.QUEUE_MAX_BACKOFF)
                self.stopping.wait(self.queue_backoff)
            finally:
                self.retry_pending_state()

    # 中文维护：隔离期间遇到临时读写故障：只记录并按队列退避策略等待，绝不解封，也不终止消费线程。
    def pause_loop_error(self, kind, detail):
        traceback.print_exc()
        self.log_problem(kind, detail + '\n' + traceback.format_exc())
        self.queue_backoff = min(max(self.QUEUE_MIN_BACKOFF, self.queue_backoff * 2), self.QUEUE_MAX_BACKOFF)
        self.stopping.wait(self.queue_backoff)

    # 中文维护：确认进程确实退出才返回 True；停止动作失败或超时都不算退出。
    def confirm_process_exit(self, proc, timeout=None, stop=None):
        if proc is None:
            return True
        if proc.poll() is not None:
            return True
        stop = stop or self.stop_process
        timeout = self.STOP_TIMEOUT if timeout is None else timeout
        deadline = time.time() + timeout
        while True:
            try:
                stop(proc)
            except Exception:
                # 中文维护：终止命令失败不代表进程已退出，继续以 poll 结果为准。
                self.log_problem('worker-stop', traceback.format_exc())
            grace_end = min(deadline, time.time() + 0.5)
            while time.time() < grace_end:
                if proc.poll() is not None:
                    return True
                time.sleep(0.05)
            if proc.poll() is not None:
                return True
            if time.time() >= deadline:
                return False

    # 中文维护：进入隔离态；在旧进程确认退出前，队列不启动任何新任务。
    def enter_worker_quarantine(self, job, proc, reason):
        pid = getattr(proc, 'pid', None)
        job_id = job.get('id') if isinstance(job, dict) else None
        prior = ''
        if job_id:
            try:
                prior = self.get('jobs', job_id).get('message') or ''
            except ValueError:
                prior = ''
        message = ('工作进程未能确认退出（PID %s），队列已暂停，不会再启动新任务；'
                   '请等待该进程结束或手动结束后再重试。原因：%s' % (pid, reason))
        # 先落库失败原因，再对外暴露隔离标记，避免观察者看到“已隔离但状态仍取消中”。
        if job_id:
            self.save_job_state(job_id, state='failed', message=message, orphan_pid=pid, quarantine=True)
        # 中文维护：持久化身份（含冗余副本）；即使主服务重启，也能识别旧进程是否仍是同一个。
        identity = process_identity(pid) or {'pid': pid, 'created': None, 'exe': ''}
        marker = {'schema': 2, 'state': 'running', 'job_id': job_id, 'pid': pid,
                  'created': identity.get('created'), 'exe': identity.get('exe'),
                  'lease': str(self.root / WORKER_LEASE_NAME), 'since': time.time(),
                  'reason': str(reason), 'prior_message': prior}
        try:
            self.write_worker_marker(marker)
        except Exception:
            self.log_problem('worker-marker', traceback.format_exc())
        self.blocked = {'job_id': job_id, 'pid': pid, 'proc': proc, 'identity': identity,
                        'marker': marker, 'unknown': False, 'grace': None,
                        'since': time.time(), 'reason': str(reason), 'prior_message': prior,
                        'detail': '', 'last_log': 0.0}
        self.log_problem('worker-quarantine', message)

    # 中文维护：隔离期间复查：只有可靠退出证据才放行；等待期到期、锁读不到、记录损坏都不算证据。
    def release_quarantine_when_exited(self):
        info = self.blocked or {}
        proc = info.get('proc')
        if proc is not None:
            if proc.poll() is not None:
                return self.finish_quarantine(info, 'PID %s 已确认退出，队列恢复' % info.get('pid'))
            self.stopping.wait(self.QUARANTINE_POLL)
            return False
        # 中文维护：工作进程可能在主服务之后才补写身份记录；每次复查都重新读取（静默读取避免刷日志）。
        marker, marker_state = self.read_worker_marker(quiet=True)
        identity = self.marker_identity(marker)
        if identity:
            previous = info.get('identity')
            info['identity'] = identity
            info['marker'] = marker
            if previous != identity or info.get('pid') != identity.get('pid'):
                info['pid'] = identity.get('pid')
                self._note_quarantine_check(info, '已读取到工作进程补写的身份记录（PID %s）' % identity['pid'])
        else:
            identity = info.get('identity')
        if identity is None:
            # 中文维护：等待期到期只记录一次复查；它不是放行依据。
            grace = info.get('grace')
            if grace is not None and not info.get('grace_noted') and time.time() >= grace:
                info['grace_noted'] = True
                self.log_problem('quarantine-check',
                                  '启动观察窗口已过，仍无可核对的进程身份；继续隔离复查，不会仅凭等待期放行')
        lease, lease_detail = lease_state(self.root / WORKER_LEASE_NAME)
        if lease == 'locked':
            info['grace'] = None
            self._note_quarantine_check(info, '运行租约仍被占用（%s），队列保持暂停' % lease_detail)
            self.stopping.wait(self.QUARANTINE_POLL)
            return False
        if lease == 'unknown':
            # 中文维护：锁状态读不到不能当作“空闲”；保留记录与隔离，等待下一次复查。
            self._note_quarantine_check(info, '暂时无法读取运行租约（%s），继续隔离并复查' % lease_detail)
            self.stopping.wait(self.QUARANTINE_POLL)
            return False
        if identity:
            state, detail = process_state(identity)
            if state == 'alive':
                self._note_quarantine_check(info, '进程仍存活，队列保持暂停')
            elif state == 'dead':
                return self.finish_quarantine(info, 'PID %s 已确认退出（%s），队列恢复' % (identity['pid'], detail))
            else:
                # 中文维护：查询失败不等于退出；保留隔离与记录，等待下一次复查。
                self._note_quarantine_check(info, '暂时无法确认旧进程是否退出（%s），继续隔离并复查' % detail)
        else:
            # 中文维护：没有可核对的 PID 时继续隔离；等待旧进程补写身份后再核对，绝不凭等待期放行。
            if marker_state == 'corrupt':
                self._note_quarantine_check(info, '工作进程记录损坏且无法核对身份，继续隔离并复查')
            elif marker is not None:
                self._note_quarantine_check(info, '尚未取得可核对的进程身份，等待工作进程补写记录后复查')
            else:
                self._note_quarantine_check(info, '没有可核对的进程记录，无法确认是否仍有遗留进程，继续隔离并复查')
        self.stopping.wait(self.QUARANTINE_POLL)
        return False

    # 中文维护：限制隔离复查日志频率；状态变化时立即记录，避免每秒刷屏。
    def _note_quarantine_check(self, info, detail):
        now = time.time()
        if detail != info.get('detail') or now - info.get('last_log', 0) >= self.UNKNOWN_LOG_INTERVAL:
            info['detail'] = detail
            info['last_log'] = now
            self.log_problem('quarantine-check', detail)

    # 中文维护：确认退出后清除持久化记录、恢复队列；中断任务仍需用户显式重试。
    def finish_quarantine(self, info=None, detail='工作进程已确认退出'):
        info = info or {}
        self.blocked = None
        self.queue_backoff = 0.0
        self.clear_worker_marker()
        self.log_problem('quarantine-cleared', detail)
        if info.get('job_id'):
            prior = info.get('prior_message') or ''
            message = '旧工作进程已退出，队列已恢复。'
            message += prior if prior else '中断任务需确认后重试。'
            self.save_job_state(info['job_id'], message=message)
        return True

    # 中文维护：任务状态写回失败时有限重试，并登记待补偿，避免任务永久停留在 running。
    def save_job_state(self, job_id, **updates):
        if not job_id:
            return False
        for attempt in range(3):
            try:
                latest = self.get('jobs', job_id)
                latest.update(updates)
                self.save('jobs', latest)
                self.pending_state.pop(job_id, None)
                return True
            except Exception:
                self.log_problem('job-state', traceback.format_exc())
                if attempt < 2:
                    time.sleep(0.05 * (attempt + 1))
        self.pending_state[job_id] = updates
        return False

    # 中文维护：每轮队列消费后补偿一次未写回的状态；失败只记录，不在同一轮内反复重试。
    def retry_pending_state(self):
        for job_id, updates in list(self.pending_state.items()):
            try:
                latest = self.get('jobs', job_id)
                latest.update(updates)
                self.save('jobs', latest)
                self.pending_state.pop(job_id, None)
                self.log_problem('job-state-recovered', '任务状态已补偿写回：' + job_id)
            except Exception:
                self.log_problem('job-state-retry', traceback.format_exc())
                return

    # 中文维护：把队列/进程异常追加到本机日志（超出 2 MB 轮转），日志故障不影响主流程。
    def log_problem(self, kind, detail):
        line = '[%s] %s %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), kind, str(detail).replace('\n', '\n    '))
        try:
            path = self.root / 'queue-errors.log'
            with path.open('a', encoding='utf-8') as handle:
                handle.write(line)
            if path.stat().st_size > 2 * 1024 * 1024:
                path.replace(path.with_name('queue-errors.log.1'))
        except OSError:
            traceback.print_exc()

    # 中文维护：启动受管工作进程、接收进度和取消请求，验证结果后保存候选声线或成品。
    def run_job(self, j):
        folder = self.root / 'jobs' / j['id']
        folder.mkdir(parents=True, exist_ok=True)
        request = dict(j['request'], job_dir=str(folder), lock_path=str(self.root / 'gpu.lock'),
                       lease_path=str(self.root / WORKER_LEASE_NAME), parent_pid=os.getpid())
        atomic_json(folder / 'request.json', request)
        proc = None
        try:
            training_job = j.get('kind') == 'training'
            preparation_job = j.get('kind') == 'preparation'
            if training_job or preparation_job or request['segments']:
                runtime = require_file(request['settings']['runtime'])
                # 中文维护：先落“即将启动”记录，成功后才允许创建进程；写盘失败则拒绝启动。
                self.begin_worker_start(j)
                with (folder / 'worker.log').open('w', encoding='utf-8') as log:
                    env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1',
                               HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                               HF_HUB_DISABLE_TELEMETRY='1', GRADIO_ANALYTICS_ENABLED='False',
                               DO_NOT_TRACK='1')
                    worker_name = 'preparation_worker.py' if preparation_job else 'training_worker.py' if training_job else 'inference_worker.py'
                    proc = subprocess.Popen([str(runtime), '-I', '-X', 'utf8', '-B', str(ROOT / worker_name), str(folder / 'request.json')],
                        stdout=log, stderr=subprocess.STDOUT, env=env, creationflags=HIDDEN)
                    # 中文维护：先持久化进程身份再收进度；主服务意外退出后新服务据此识别残留进程。
                    self.note_worker_started(j, proc)
                    while proc.poll() is None:
                        self.ingest(j, folder)
                        if self.get('jobs', j['id'])['state'] == 'cancelling' or self.stopping.is_set():
                            # 中文维护：取消必须先确认退出，未确认则交由外层进入隔离。
                            if not self.confirm_process_exit(proc):
                                raise RuntimeError('取消时工作进程未能确认退出')
                            break
                        time.sleep(.4)
                self.ingest(j, folder)
                # 中文维护：工作进程已结束（含取消已确认退出）才清除持久化标记。
                if proc is not None:
                    self.clear_worker_marker()
                if self.get('jobs', j['id'])['state'] == 'cancelling' or self.stopping.is_set():
                    state, message = 'cancelled', '已停止；成功片段保留，可重试剩余内容。'
                elif proc.returncode != 0:
                    err = folder / 'error.json'
                    detail = json.loads(err.read_text(encoding='utf-8'))['error'] if err.exists() else '处理进程异常退出，请查看日志。'
                    raise RuntimeError(detail)
                else:
                    state, message = 'done', '生成完成'
            else:
                state, message = 'done', '音频无需重做，使用已验证片段'
            with self.lock:
                if state == 'done':
                    if preparation_job:
                        if not (folder / 'prepared.json').is_file():
                            raise ValueError('整理进程没有完成结果；已有片段保留。')
                        message = '切分转写完成。请回训练工作台逐段试听校对，再确认训练。'
                    elif training_job:
                        candidate = json.loads((folder / 'candidate.json').read_text(encoding='utf-8'))
                        ds = request['dataset']
                        voice = self.import_voice(dict(candidate, name=ds['name'] + ' · 候选', trusted=True, authorized=True,
                                                       authorization=ds['authorization']))
                        voice.update(status='candidate', dataset_id=ds['id'], dataset_revision=ds['revision'],
                                     training_job=j['id'], training_params=ds['params'])
                        self.save('voices', voice)
                        message = '训练完成，候选声线已保存。请用新文案试听后发布。'
                    else:
                        p = self.get('projects', j['project_id'])
                        if all(self.current(p, s) for s in p['segments']):
                            self.export(p)
                            message += '；已生成新导出版本。'
                latest = self.get('jobs', j['id'])
                latest.update(state=state, message=message)
                self.save('jobs', latest)
        except Exception as e:
            # 中文维护：先确认旧工作进程确实退出；未确认则隔离队列，绝不启动下一个任务。
            if self.confirm_process_exit(proc):
                self.clear_worker_marker()
                self.save_job_state(j['id'], state='failed',
                                    message=str(e) + ' 已完成内容保留，请检查环境/日志后重试。')
            else:
                self.enter_worker_quarantine(j, proc, str(e))

    # 中文维护：仅停止传入任务进程及其子树；调用方必须再用 poll 确认是否真的退出。
    @staticmethod
    def stop_process(proc, timeout=20):
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], capture_output=True, creationflags=HIDDEN)
        else:
            proc.terminate()
        proc.wait(timeout=timeout)

    # 中文维护：按真实采样数合并音频、字幕和时间线，创建不可变导出版本且保留旧成品。
    def export(self, p):
        if not all(self.current(p, s) for s in p['segments']):
            raise ValueError('部分朗读文本还没有对应音频，请先生成。')
        eid = uid()
        folder = self.root / 'projects' / p['id'] / 'exports' / eid
        folder.mkdir(parents=True)
        core = load_legacy(self.settings())
        position = 0
        cues, manifest_segments = [], []
        with wave.open(str(folder / 'narration.wav'), 'wb') as out:
            out.setparams((1, 2, 32000, 0, 'NONE', 'not compressed'))
            for i, seg in enumerate(p['segments']):
                if seg['audio'].get('sha256') and digest(seg['audio']['path']) != seg['audio']['sha256']:
                    raise ValueError(f'第 {i+1} 段音频已被外部修改或损坏，请单独重做该段。')
                gap = p['paragraph_gap_ms'] if seg['paragraph'] else p['gap_ms'] if i else 0
                gap_samples = round(gap * 32)
                out.writeframes(b'\x00\x00' * gap_samples)
                position += gap_samples
                start = round(position * 1000 / 32000)
                with wave.open(seg['audio']['path'], 'rb') as w:
                    if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, 32000):
                        raise ValueError('片段音频参数异常，请重新生成该片段。')
                    count = w.getnframes()
                    out.writeframes(w.readframes(count))
                position += count
                end = round(position * 1000 / 32000)
                cues.append(f'{i+1}\n{core.fmt_srt_time(start)} --> {core.fmt_srt_time(end)}\n{seg["display"]}\n')
                manifest_segments.append(dict(seg, start_ms=start, end_ms=end))
        (folder / 'subtitles.srt').write_text('\n'.join(cues), encoding='utf-8')
        (folder / 'script.txt').write_text(p['original'], encoding='utf-8')
        timeline = {'schema_version': '1.0', 'project_id': p['id'], 'duration_ms': round(position * 1000 / 32000),
                    'source': {'type': 'gpt_sovits', 'video': None, 'audio': 'narration.wav', 'script': 'script.txt', 'subtitle': 'subtitles.srt'},
                    'segments': [{'id': s['id'], 'start_ms': s['start_ms'], 'end_ms': s['end_ms'], 'text': s['display'],
                                  'speaker': p['voice']['name'], 'source': 'srt'} for s in manifest_segments]}
        atomic_json(folder / 'timeline.json', timeline)
        # A portable package uses relative segment paths, not private source-model paths.
        package_segments = []
        (folder / 'segments').mkdir()
        for seg in manifest_segments:
            rel = 'segments/' + seg['id'] + '.wav'
            shutil.copy2(seg['audio']['path'], folder / rel)
            package_segments.append({k: seg[k] for k in ('id', 'original', 'spoken', 'display', 'start_ms', 'end_ms')} |
                                    {'audio': rel, 'generation': {k: v for k, v in seg['audio'].items() if k != 'path'}})
        atomic_json(folder / 'manifest.json', {'schema_version': '1.0', 'project_id': p['id'], 'original': p['original'],
                    'voice_id': p['voice']['id'], 'voice_version': p['voice']['version'], 'voice_model_hashes': [p['voice']['gpt_hash'], p['voice']['sovits_hash']],
                    'engine_hash': p['voice']['engine_hash'], 'reference_hash': p['voice']['reference_hash'],
                    'subtitle_precision': 'sentence', 'speed': p['speed'], 'gap_ms': p['gap_ms'],
                    'paragraph_gap_ms': p['paragraph_gap_ms'], 'segments': package_segments})
        with zipfile.ZipFile(folder / 'delivery.zip', 'w', zipfile.ZIP_DEFLATED) as z:
            for f in folder.rglob('*'):
                if f.is_file() and f.suffix != '.zip':
                    z.write(f, f.relative_to(folder).as_posix())
        p['exports'].append({'id': eid, 'created': time.time(), 'folder': str(folder)})
        p['dirty'] = False
        self.save('projects', p)
        return p

    # 中文维护：限定导出文件白名单并验证导出版本归属，拒绝任意文件下载。
    def file(self, project_id, export_id, filename):
        p = self.get('projects', project_id)
        if filename not in ('narration.wav', 'subtitles.srt', 'manifest.json', 'timeline.json', 'delivery.zip', 'script.txt'):
            raise ValueError('不允许访问此文件')
        entry = next((e for e in p['exports'] if e['id'] == export_id), None)
        if not entry:
            raise ValueError('导出版本不存在')
        # 数据库记录也可能被外部修改；解析链接后的真实路径仍须属于当前项目的导出目录。
        expected = (self.root / 'projects' / project_id / 'exports').resolve()
        path = (Path(entry['folder']) / filename).resolve()
        if self.root not in expected.parents or expected not in path.parents:
            raise ValueError('导出路径不属于当前项目库')
        return path

    # 中文维护：组装前端状态并计算片段有效性，不返回后台任务请求快照。
    def snapshot(self):
        projects = self.all('projects')
        for p in projects:
            for s in p['segments']:
                s['current'] = self.current(p, s)
        jobs = [{k: v for k, v in j.items() if k != 'request'} for j in self.all('jobs')]
        return {'settings': self.settings(), 'voices': self.all('voices'), 'projects': projects, 'jobs': jobs,
                'datasets': self.all('datasets'), 'environment_history': self.all('environment_history')[:10],
                'queue_pause': self.pause_status()}

    # 中文维护：向界面和诊断摘要说明队列为何暂停，以及用户可执行的恢复方式。
    def pause_status(self):
        info = self.blocked
        if not info:
            return None
        return {'reason': info.get('reason') or '旧工作进程状态尚未确认',
                'message': self.pause_message(),
                'since': info.get('since'), 'unknown': bool(info.get('unknown')),
                'job_id': info.get('job_id')}
