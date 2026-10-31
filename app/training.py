"""Human-reviewed datasets and version-pinned training plans."""
from __future__ import annotations
import json
import shutil
import time
import wave
from pathlib import Path
from studio import uid, digest, require_file, atomic_json


def initialize_checkpoint_directory(experiment):
    path = Path(experiment) / 'logs_s2_v2Pro'
    path.mkdir(parents=True, exist_ok=True)
    return path


def compatible_loader_workers(value):
    # This engine always enables persistent_workers, so zero is not valid.
    return max(1, int(value))


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


def plan(studio, did):
    ds = studio.get('datasets', did)
    s = studio.settings()
    engine = Path(s['engine'])
    rows = [r for r in ds['rows'] if not r['excluded']]
    required = ['GPT_SoVITS/configs/s2v2Pro.json', 'GPT_SoVITS/configs/s1longer-v2.yaml',
                'GPT_SoVITS/pretrained_models/v2Pro/s2Gv2Pro.pth', 'GPT_SoVITS/pretrained_models/v2Pro/s2Dv2Pro.pth',
                'GPT_SoVITS/pretrained_models/s1v3.ckpt',
                'GPT_SoVITS/pretrained_models/sv/pretrained_eres2netv2w24s4ep4.ckpt',
                'GPT_SoVITS/text/G2PWModel']
    missing = [str(engine / p) for p in required if not (engine / p).exists()]
    seconds = round(sum(r['seconds'] for r in rows), 2)
    pending = sum(not r['reviewed'] for r in rows)
    invalid = sum(not r.get('text', '').strip() or not 1 <= r['seconds'] <= 20 for r in rows)
    reference_available = any(3 <= r['seconds'] <= 10 for r in rows)
    free = shutil.disk_usage(studio.root).free / 1024**3
    warnings = []
    if seconds < 120: warnings.append('素材不足两分钟：适合链路试训，不据此保证声线质量。')
    if invalid: warnings.append('有空标注或时长不合规的片段，请补写文字或排除。')
    if not reference_available: warnings.append('至少保留一段 3～10 秒的校对录音，用作训练后的参考音频。')
    warnings.append('默认参数是保守起点，不是自动找到的最优参数；显存不足时需降低批大小。')
    if any(r.get('review_method') == 'secondary_asr_agreement' for r in rows):
        warnings.append('本试验包含机器交叉核验的标注，并非人工试听确认；结果仅用于功能验收。')
    return {'dataset_id': did, 'revision': ds['revision'], 'count': len(rows), 'seconds': seconds,
            'pending_review': pending, 'params': ds['params'], 'free_gb': round(free, 1), 'missing': missing,
            'ready': len(rows) >= 2 and pending == 0 and invalid == 0 and reference_available and not missing and free >= 12,
            'warnings': warnings, 'disk_note': '预留至少 12 GB（包含双模型检查点、特征和候选权重；不是精确占用预测）。',
            'time_note': '训练耗时取决于显卡、录音长度与轮数；按实际阶段和日志展示，不预测固定完成时间。'}


def enqueue(studio, did, data):
    with studio.lock:
        report = plan(studio, did)
        if data.get('confirmed') is not True:
            raise ValueError('请确认训练会占用显卡和磁盘，再启动。')
        if data.get('revision') != report['revision']:
            raise ValueError('素材或参数已变化，请重新检查训练计划。')
        if not report['ready']:
            raise ValueError('尚有未校对片段、缺少文件、素材不足或磁盘空间不足；请检查计划。')
        if studio.busy(did):
            raise ValueError('此数据集已有训练任务。')
        ds = studio.get('datasets', did)
        j = {'id': uid(), 'kind': 'training', 'name': '训练 · ' + ds['name'], 'project_id': did, 'state': 'queued',
             'mode': 'training', 'segment_id': None, 'completed': 0, 'total': 6, 'message': '等待训练', 'created': time.time(),
             'request': {'dataset': ds, 'settings': studio.settings()}}
        studio.save('jobs', j)
        studio.wake.set()
        return j


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
