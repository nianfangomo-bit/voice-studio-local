"""Human-reviewed datasets and version-pinned training plans."""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import time
import wave
from pathlib import Path
from studio import HIDDEN, ROOT, uid, digest, require_file


# 中文维护：与 training_worker.py 六阶段真实入口一一对应的脚本、配置和基础模型。
REQUIRED_TRAINING_FILES = (
    'config.py',
    'GPT_SoVITS/TTS_infer_pack/TTS.py',
    'GPT_SoVITS/configs/s2v2Pro.json',
    'GPT_SoVITS/configs/s1longer-v2.yaml',
    'GPT_SoVITS/prepare_datasets/1-get-text.py',
    'GPT_SoVITS/prepare_datasets/2-get-hubert-wav32k.py',
    'GPT_SoVITS/prepare_datasets/2-get-sv.py',
    'GPT_SoVITS/prepare_datasets/3-get-semantic.py',
    'GPT_SoVITS/s2_train.py',
    'GPT_SoVITS/s1_train.py',
    'GPT_SoVITS/pretrained_models/v2Pro/s2Gv2Pro.pth',
    'GPT_SoVITS/pretrained_models/v2Pro/s2Dv2Pro.pth',
    'GPT_SoVITS/pretrained_models/s1v3.ckpt',
    'GPT_SoVITS/pretrained_models/sv/pretrained_eres2netv2w24s4ep4.ckpt',
    'GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large/pytorch_model.bin',
    'GPT_SoVITS/pretrained_models/chinese-hubert-base/pytorch_model.bin',
    'GPT_SoVITS/text/G2PWModel/g2pW.onnx',
)

# 中文维护：依赖检查超时；首次加载 torch 等模块在机械硬盘上可能较慢。
TRAINING_PROBE_TIMEOUT = 180
# 中文维护：通过结果短时复用，避免每次打开/刷新计划都重复加载大依赖；失败结果很快重查。
_PROBE_CACHE = {}
_PROBE_OK_TTL = 300.0
_PROBE_FAIL_TTL = 30.0


# 中文维护：在真实训练运行环境中加载必需模块，失败时返回可读原因；不训练、不安装。
def probe_training_environment(runtime, timeout=TRAINING_PROBE_TIMEOUT):
    runtime = str(runtime or '')
    if not runtime:
        return {'ok': False, 'modules': {}, 'missing': [],
                'problems': ['环境设置缺少运行环境 python.exe，请先在“环境设置”完成配置。']}
    try:
        stamp = os.path.getmtime(runtime)
    except OSError:
        stamp = 0
    cache_key = (runtime, stamp)
    cached = _PROBE_CACHE.get(cache_key)
    if cached and time.monotonic() - cached[0] < (_PROBE_OK_TTL if cached[1].get('ok') else _PROBE_FAIL_TTL):
        return dict(cached[1])
    try:
        proc = subprocess.run([runtime, '-I', '-X', 'utf8', '-B', str(ROOT / 'training_probe.py')],
                              capture_output=True, text=True, encoding='utf-8', errors='replace',
                              timeout=timeout, creationflags=HIDDEN)
    except subprocess.TimeoutExpired:
        result = {'ok': False, 'modules': {}, 'missing': [],
                  'problems': ['运行环境依赖检查超过 %s 秒；请检查环境是否完整，或稍后重试。' % timeout]}
        _PROBE_CACHE[cache_key] = (time.monotonic(), result)
        return dict(result)
    except OSError as exc:
        result = {'ok': False, 'modules': {}, 'missing': [],
                  'problems': ['无法启动运行环境依赖检查：%s。请重新选择可信的完整环境。' % exc]}
        _PROBE_CACHE[cache_key] = (time.monotonic(), result)
        return dict(result)
    lines = [line for line in proc.stdout.splitlines() if line.startswith('VOICE_TRAINING_PROBE=')]
    if not lines:
        result = {'ok': False, 'modules': {}, 'missing': [],
                  'problems': ['运行环境依赖检查没有返回结果（退出码 %s）；请检查完整安装。' % proc.returncode]}
        _PROBE_CACHE[cache_key] = (time.monotonic(), result)
        return dict(result)
    try:
        report = json.loads(lines[-1].split('=', 1)[1])
    except (ValueError, IndexError):
        result = {'ok': False, 'modules': {}, 'missing': [],
                  'problems': ['运行环境依赖检查结果无法解析；请检查完整安装。']}
        _PROBE_CACHE[cache_key] = (time.monotonic(), result)
        return dict(result)
    if not isinstance(report, dict):
        result = {'ok': False, 'modules': {}, 'missing': [],
                  'problems': ['运行环境依赖检查结果格式不正确；请检查完整安装。']}
        _PROBE_CACHE[cache_key] = (time.monotonic(), result)
        return dict(result)
    if proc.returncode != 0:
        report['ok'] = False
        report.setdefault('problems', []).append('运行环境依赖检查进程退出码 %s。' % proc.returncode)
    _PROBE_CACHE[cache_key] = (time.monotonic(), dict(report))
    return report


# 中文维护：把探测结果变成用户能照着处理的一句话，不隐藏底层错误首行。
def describe_environment_problems(report):
    parts = [str(item) for item in (report.get('problems') or []) if item]
    missing = [str(item) for item in (report.get('missing') or []) if item]
    if missing:
        parts.append('缺少模块：' + '、'.join(missing))
    errors = report.get('errors') or {}
    for name in missing[:3]:
        first = str(errors.get(name) or '').strip().splitlines()
        if first:
            parts.append('%s：%s' % (name, first[0][:240]))
    return '；'.join(parts) or '运行环境依赖检查未通过，请恢复已验证的完整环境。'


# 中文维护：补建上游训练脚本假设已存在的检查点目录，重复调用保持幂等。
def initialize_checkpoint_directory(experiment):
    path = Path(experiment) / 'logs_s2_v2Pro'
    path.mkdir(parents=True, exist_ok=True)
    return path


# 中文维护：上游固定启用持久 worker，将数量限制为至少一以避免 DataLoader 报错。
def compatible_loader_workers(value):
    # This engine always enables persistent_workers, so zero is not valid.
    return max(1, int(value))


# 中文维护：先验证全份标注，再复制录音到独立素材库，不修改原文件。
def import_dataset(studio, data):
    if data.get('authorized') is not True:
        raise ValueError('请确认拥有素材使用授权。')
    source = require_file(data['list_path'], {'.list'})
    ident = uid()
    folder = studio.root / 'datasets' / ident
    rows = []
    lines = source.read_text(encoding='utf-8-sig').splitlines()
    if not 2 <= len(lines) <= 2000:
        raise ValueError('当前向导支持 2～2000 条已切分标注。')
    # Validate the entire manifest before copying anything.
    for line in lines:
        parts = line.split('|', 3)
        if len(parts) != 4 or not parts[3].strip():
            raise ValueError('标注格式需为 音频路径|说话人|ZH|文字，且文字不能为空。')
        p = Path(parts[0])
        p = require_file(p if p.is_absolute() else source.parent / p, {'.wav'})
        if parts[2].upper() != 'ZH':
            raise ValueError('当前训练向导仅支持中文标注。')
        with wave.open(str(p), 'rb') as w:
            seconds = w.getnframes() / w.getframerate()
            if not 1 <= seconds <= 20 or w.getcomptype() != 'NONE':
                raise ValueError(f'{p.name} 不在 1～20 秒 PCM WAV 范围，请先重新切分。')
        rows.append({'id': uid(), 'source': str(p), 'original': parts[3], 'text': parts[3],
                     'seconds': round(seconds, 3), 'reviewed': False, 'excluded': False, 'hash': digest(p)})
    folder.mkdir(parents=True)
    for row in rows:
        target = folder / (row['id'] + '.wav')
        shutil.copy2(row['source'], target)
        row['audio'] = str(target)
    ds = {'id': ident, 'name': str(data.get('name') or source.parent.name), 'source_manifest': str(source),
          'authorization': str(data.get('authorization') or '用户确认使用授权'), 'rows': rows, 'created': time.time(),
          'params': {'gpt_epochs': 5, 'sovits_epochs': 8, 'batch_size': 2}, 'revision': 1}
    return studio.save('datasets', ds)


# 中文维护：空闲时保存人工校对、排除状态或受限参数，每次变更递增修订号。
def update_dataset(studio, did, data):
    with studio.lock:
        if studio.busy(did):
            raise ValueError('训练任务进行中，不能修改此数据集。')
        ds = studio.get('datasets', did)
        if 'row_id' in data:
            row = next((r for r in ds['rows'] if r['id'] == data['row_id']), None)
            if not row:
                raise ValueError('录音片段不存在')
            text = str(data.get('text', row['text'])).strip()
            if (not text and data.get('excluded') is not True) or len(text) > 1000 or any(x in text for x in ('|', '\n', '\r')):
                raise ValueError('标注需为单行非空文字，不能包含竖线，最长 1000 字。')
            row.update(text=text, reviewed=data.get('reviewed') is True, excluded=data.get('excluded') is True)
            row['review_method'] = 'user_confirmed' if row['reviewed'] else 'pending'
        if 'params' in data:
            params = {k: int(data['params'][k]) for k in ('gpt_epochs', 'sovits_epochs', 'batch_size')}
            if not (1 <= params['gpt_epochs'] <= 20 and 1 <= params['sovits_epochs'] <= 20 and 1 <= params['batch_size'] <= 4):
                raise ValueError('本版限制训练轮数 1～20，批大小 1～4。')
            ds['params'] = params
        ds['revision'] += 1
        return studio.save('datasets', ds)


# 中文维护：汇总素材、校对状态、运行环境依赖、脚本/配置/基础模型和磁盘空间；推荐参数不是质量保证。
def plan(studio, did):
    ds = studio.get('datasets', did)
    s = studio.settings()
    engine_value = str(s.get('engine') or '')
    engine = Path(engine_value) if engine_value else None
    missing = []
    problems = []

    # 中文维护：环境未配置时给出明确入口，而不是让后续检查抛 KeyError。
    if not s:
        problems.append('尚未完成环境设置：请先在“环境设置”选择完整引擎与运行环境。')
    else:
        for key, label in (('runtime', '运行环境 python.exe'), ('ffmpeg', 'FFmpeg')):
            value = str(s.get(key) or '')
            if not value or not Path(value).is_file():
                missing.append('%s（%s）' % (label, value or '未配置'))
        if engine is None or not engine.is_dir():
            missing.append('引擎目录（%s）' % (engine_value or '未配置'))
        else:
            missing.extend(str(engine / rel) for rel in REQUIRED_TRAINING_FILES
                           if not (engine / rel).is_file())
        expected_hash = s.get('engine_hash')
        if not expected_hash:
            problems.append('环境设置缺少引擎指纹，请重新检查并保存环境后再训练。')
        elif engine is not None and engine.is_dir():
            core = engine / 'GPT_SoVITS/TTS_infer_pack/TTS.py'
            if core.is_file() and digest(core) != expected_hash:
                problems.append('引擎核心文件已变化，请重新检查并保存环境后再训练。')

    # 中文维护：静态文件齐全后才在真实运行环境中检查依赖，避免无意义地启动大模块。
    dependencies = {}
    if s and not missing and not problems:
        dependencies = probe_training_environment(s.get('runtime'))
        if dependencies.get('ok') is not True:
            problems.append('训练运行环境依赖未通过：' + describe_environment_problems(dependencies))

    rows = [r for r in ds['rows'] if not r['excluded']]
    seconds = round(sum(float(r.get('seconds') or 0) for r in rows), 2)
    pending = sum(not r['reviewed'] for r in rows)
    invalid = sum(not str(r.get('text') or '').strip() or not 1 <= float(r.get('seconds') or 0) <= 20
                  for r in rows)
    reference_available = any(3 <= float(r.get('seconds') or 0) <= 10 for r in rows)
    free = shutil.disk_usage(studio.root).free / 1024**3
    if len(rows) < 2:
        problems.append('至少需要 2 段未排除的录音才能训练。')
    if pending:
        problems.append('还有 %d 段未完成人工校对。' % pending)
    if invalid:
        problems.append('有 %d 段缺少文字或时长不在 1～20 秒范围。' % invalid)
    if rows and not reference_available:
        problems.append('至少保留一段 3～10 秒的校对录音，用作训练后的参考音频。')
    if free < 12:
        problems.append('磁盘可用 %.1f GB，训练至少需要 12 GB。' % free)

    warnings = []
    if seconds < 120: warnings.append('素材不足两分钟：适合链路试训，不据此保证声线质量。')
    warnings.append('默认参数是保守起点，不是自动找到的最优参数；显存不足时需降低批大小。')
    if any(r.get('review_method') == 'secondary_asr_agreement' for r in rows):
        warnings.append('本试验包含机器交叉核验的标注，并非人工试听确认；结果仅用于功能验收。')
    warnings.extend('训练前检查：' + item for item in problems)
    return {'dataset_id': did, 'revision': ds['revision'], 'count': len(rows), 'seconds': seconds,
            'pending_review': pending, 'params': ds['params'], 'free_gb': round(free, 1),
            'missing': missing, 'problems': problems, 'dependencies': dependencies,
            'ready': not missing and not problems and len(rows) >= 2 and pending == 0 and invalid == 0
            and reference_available and free >= 12,
            'warnings': warnings, 'disk_note': '预留至少 12 GB（包含双模型检查点、特征和候选权重；不是精确占用预测）。',
            'time_note': '训练耗时取决于显卡、录音长度与轮数；按实际阶段和日志展示，不预测固定完成时间。'}


# 中文维护：核实授权确认、修订号、训练前依赖/脚本/配置/模型检查后保存快照并排队。
def enqueue(studio, did, data):
    with studio.lock:
        if studio.blocked:
            raise ValueError(studio.pause_message())
        if studio.busy(did):
            raise ValueError('此数据集已有训练任务。')
        report = plan(studio, did)
        if data.get('confirmed') is not True:
            raise ValueError('请确认训练会占用显卡和磁盘，再启动。')
        if data.get('revision') != report['revision']:
            raise ValueError('素材或参数已变化，请重新检查训练计划。')
        if not report['ready']:
            reasons = [str(item) for item in report.get('problems') or []]
            if report.get('missing'):
                reasons.append('缺少文件或配置：' + '；'.join(report['missing']))
            detail = '；'.join(reasons) or '请重新检查训练计划。'
            raise ValueError('训练前检查未通过，已阻止入队：' + detail
                             + ' 请按提示修复后重新检查；不要单独升级依赖或重新安装模型。')
        ds = studio.get('datasets', did)
        j = {'id': uid(), 'kind': 'training', 'name': '训练 · ' + ds['name'], 'project_id': did, 'state': 'queued',
             'mode': 'training', 'segment_id': None, 'completed': 0, 'total': 6, 'message': '等待训练', 'created': time.time(),
             'request': {'dataset': ds, 'settings': studio.settings()}}
        studio.save('jobs', j)
        studio.wake.set()
        return j


# 中文维护：仅允许已用未见文本生成试听的候选声线发布，保留原模型版本。
def publish(studio, vid):
    with studio.lock:
        voice = studio.get('voices', vid)
        if voice.get('status') != 'candidate':
            raise ValueError('该声线不是待验收候选版本。')
        ds = studio.get('datasets', voice['dataset_id'])
        texts = {r['text'] for r in ds['rows'] if not r['excluded']}
        auditioned = any(p['voice']['id'] == vid and any(studio.current(p, seg) and seg['spoken'] not in texts for seg in p['segments'])
                        for p in studio.all('projects'))
        if not auditioned:
            raise ValueError('请先用候选声线生成一段不在训练标注里的新文案，并试听确认。')
        voice.update(status='published', published=time.time())
        return studio.save('voices', voice)
