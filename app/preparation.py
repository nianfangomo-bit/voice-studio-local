"""Local recording intake and persistent, human-reviewed preparation jobs."""
import shutil
import time
from pathlib import Path
from studio import uid, digest

ASR_MODEL = 'tools/asr/models/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch'
MAX_UPLOAD = 512 * 1024**2


def create(studio, data):
    if data.get('authorized') is not True:
        raise ValueError('请先确认录音使用授权。')
    name = str(data.get('name', '')).strip()
    if not name or len(name) > 100:
        raise ValueError('请填写 1～100 字的声线名称。')
    return studio.save('datasets', {'id': uid(), 'name': name, 'rows': [], 'recordings': [],
        'authorization': str(data.get('authorization') or '用户确认拥有录音使用授权'),
        'created': time.time(), 'revision': 1, 'params': {'gpt_epochs': 5, 'sovits_epochs': 8, 'batch_size': 1}})


def upload_target(studio, did, filename):
    ds = studio.get('datasets', did)
    if 'recordings' not in ds or studio.busy(did):
        raise ValueError('此素材库不能追加录音，或已有任务正在处理。')
    name = str(filename).replace('\\', '/').split('/')[-1]
    suffix = Path(name).suffix.lower()
    if suffix not in {'.wav', '.mp3', '.m4a', '.flac', '.ogg'}:
        raise ValueError('请选择 WAV、MP3、M4A、FLAC 或 OGG 录音。')
    if len(ds['recordings']) >= 30:
        raise ValueError('每个素材库最多导入 30 个录音文件。')
    if shutil.disk_usage(studio.root).free < 2 * 1024**3:
        raise ValueError('剩余磁盘不足 2 GB，请先释放空间。')
    folder = studio.root / 'datasets' / did / 'originals'
    folder.mkdir(parents=True, exist_ok=True)
    return folder / (uid() + suffix), name


def register_upload(studio, did, target, name):
    with studio.lock:
        ds = studio.get('datasets', did)
        if studio.busy(did):
            raise ValueError('已有任务开始处理，本次上传文件保留，但未加入素材库。')
        ds['recordings'].append({'id': target.stem, 'name': name, 'path': str(target),
                                 'hash': digest(target), 'bytes': target.stat().st_size})
        ds['revision'] += 1
        return studio.save('datasets', ds)


def enqueue(studio, did):
    with studio.lock:
        ds = studio.get('datasets', did)
        if studio.busy(did):
            raise ValueError('此素材已有任务。请等待或取消后再试。')
        if not ds.get('recordings'):
            raise ValueError('请先选择并导入录音。')
        settings = studio.settings()
        engine = Path(settings.get('engine', ''))
        for rel in ['tools/slicer2.py', ASR_MODEL + '/model.pt', ASR_MODEL + '/config.yaml']:
            if not (engine / rel).is_file():
                raise ValueError('缺少本地切分或中文转写组件：' + rel + '。未启动下载，请检查完整环境包。')
        if not Path(settings.get('ffmpeg', '')).is_file():
            raise ValueError('缺少音频转换工具，请检查环境。')
        job = {'id': uid(), 'kind': 'preparation', 'name': '整理录音 · ' + ds['name'],
            'project_id': did, 'state': 'queued', 'mode': 'preparation', 'segment_id': None,
            'completed': 0, 'total': len(ds['recordings']), 'message': '等待切分与转写（本机 CPU）',
            'created': time.time(), 'request': {'dataset': ds, 'settings': settings}}
        studio.save('jobs', job)
        studio.wake.set()
        return job
