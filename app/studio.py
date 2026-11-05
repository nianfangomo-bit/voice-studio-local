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
import uuid
import wave
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HIDDEN = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


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
    # 中文维护：初始化独立存储和锁，将上次未结束任务标为中断并尝试恢复已完成片段。
    def __init__(self, root=None):
        self.root = Path(root or ROOT / 'data').resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.db = self.root / 'studio.sqlite'
        with self.conn() as c:
            c.execute('CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, body TEXT, PRIMARY KEY(kind,id))')
        for job in self.all('jobs'):
            if job['state'] in ('queued', 'running', 'cancelling'):
                job.update(state='interrupted', message='上次运行中断；已有片段保留。请确认后重试。')
                self.save('jobs', job)
                try:
                    self.ingest(job, self.root / 'jobs' / job['id'])
                    restored = self.get('jobs', job['id'])
                    restored['message'] = '上次运行中断；已恢复完成片段。请确认后重试。'
                    self.save('jobs', restored)
                except (OSError, ValueError, KeyError):
                    pass
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
    def configure(self, data):
        if data.get('trusted') is not True:
            raise ValueError('请确认这是可信的本地引擎与配音程序。')
        engine = Path(data['engine']).resolve()
        runtime = require_file(engine / 'runtime' / 'python.exe')
        require_file(engine / 'GPT_SoVITS' / 'TTS_infer_pack' / 'TTS.py')
        legacy = Path(data['legacy']).resolve()
        core = require_file(legacy / 'voice_tool' / 'main.py')
        ffmpeg = require_file(data.get('ffmpeg') or shutil.which('ffmpeg') or '')
        if ffmpeg.name.lower() not in ('ffmpeg.exe', 'ffmpeg'):
            raise ValueError('请选择 FFmpeg 可执行文件')
        with self.lock:
            if self.busy():
                raise ValueError('任务运行时不能修改环境。请等待或取消任务。')
            return self.save('settings', {'id': 'local', 'engine': str(engine), 'runtime': str(runtime),
                'legacy': str(legacy), 'legacy_hash': digest(core), 'ffmpeg': str(ffmpeg),
                'engine_hash': digest(engine / 'GPT_SoVITS' / 'TTS_infer_pack' / 'TTS.py'), 'version': 'v2Pro'})

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

    # 中文维护：检查全局或指定项目是否存在排队、运行或取消中的任务。
    def busy(self, project=None):
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

    # 中文维护：合并工作进程实际进度；保存片段历史或素材修订，支持异常后恢复。
    def ingest(self, j, folder):
        progress_path = folder / 'progress.json'
        if not progress_path.exists():
            return
        progress = json.loads(progress_path.read_text(encoding='utf-8'))
        with self.lock:
            if j.get('kind') == 'preparation':
                ds = self.get('datasets', j['project_id'])
                if ds.get('preparation_job') != j['id']:
                    if ds['rows']:
                        atomic_json(self.root / 'datasets' / ds['id'] / ('revision-' + str(ds['revision']) + '.json'), ds)
                    ds['preparation_job'] = j['id']
                    ds['revision'] += 1
                ds['rows'] = progress.get('rows', [])
                ds['quality_reports'] = progress.get('quality_reports', [])
                self.save('datasets', ds)
            if j.get('kind') in ('training', 'preparation'):
                latest = self.get('jobs', j['id'])
                latest.update(completed=progress.get('completed', 0), message=progress.get('message', '训练中'))
                self.save('jobs', latest)
                return
            p = self.get('projects', j['project_id'])
            for item in progress.get('results', []):
                seg = next(s for s in p['segments'] if s['id'] == item['id'])
                audio = item['audio']
                if seg.get('audio') != audio:
                    if seg.get('audio'):
                        seg['history'].append(seg['audio'])
                    seg['audio'] = audio
                    p['dirty'] = True
            self.save('projects', p)
            latest = self.get('jobs', j['id'])
            latest.update(completed=len(progress.get('results', [])), message=progress.get('message', '处理中'))
            self.save('jobs', latest)

    # 中文维护：启动单一后台消费线程；由服务入口负责避免重复实例。
    def start(self):
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    # 中文维护：按创建时间串行消费队列，空闲时等待唤醒以避免忙轮询。
    def loop(self):
        while not self.stopping.is_set():
            with self.lock:
                jobs = sorted([j for j in self.all('jobs') if j['state'] == 'queued'], key=lambda j: j['created'])
                if jobs:
                    j = jobs[0]
                    label = {'training':'训练', 'preparation':'切分转写'}.get(j.get('kind'), '配音')
                    j.update(state='running', message='准备独立' + label + '进程')
                    self.save('jobs', j)
            if not jobs:
                self.wake.wait(1)
                self.wake.clear()
                continue
            self.run_job(j)

    # 中文维护：启动受管工作进程、接收进度和取消请求，验证结果后保存候选声线或成品。
    def run_job(self, j):
        folder = self.root / 'jobs' / j['id']
        folder.mkdir(parents=True, exist_ok=True)
        request = dict(j['request'], job_dir=str(folder), lock_path=str(self.root / 'gpu.lock'), parent_pid=os.getpid())
        atomic_json(folder / 'request.json', request)
        proc = None
        try:
            training_job = j.get('kind') == 'training'
            preparation_job = j.get('kind') == 'preparation'
            if training_job or preparation_job or request['segments']:
                runtime = require_file(request['settings']['runtime'])
                with (folder / 'worker.log').open('w', encoding='utf-8') as log:
                    env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1',
                               HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                               HF_HUB_DISABLE_TELEMETRY='1', GRADIO_ANALYTICS_ENABLED='False',
                               DO_NOT_TRACK='1')
                    worker_name = 'preparation_worker.py' if preparation_job else 'training_worker.py' if training_job else 'inference_worker.py'
                    proc = subprocess.Popen([str(runtime), '-I', '-X', 'utf8', '-B', str(ROOT / worker_name), str(folder / 'request.json')],
                        stdout=log, stderr=subprocess.STDOUT, env=env, creationflags=HIDDEN)
                    while proc.poll() is None:
                        self.ingest(j, folder)
                        if self.get('jobs', j['id'])['state'] == 'cancelling' or self.stopping.is_set():
                            self.stop_process(proc)
                            break
                        time.sleep(.4)
                self.ingest(j, folder)
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
            if proc and proc.poll() is None:
                self.stop_process(proc)
            latest = self.get('jobs', j['id'])
            latest.update(state='failed', message=str(e) + ' 已完成内容保留，请检查环境/日志后重试。')
            self.save('jobs', latest)

    # 中文维护：仅停止传入任务进程及其子树，并等待退出，不能用于清理未知进程。
    @staticmethod
    def stop_process(proc):
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], capture_output=True, creationflags=HIDDEN)
        else:
            proc.terminate()
        proc.wait(timeout=20)

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
                'datasets': self.all('datasets')}
