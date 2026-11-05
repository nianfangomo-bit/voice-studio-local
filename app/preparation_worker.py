"""CPU-only offline slicing/ASR; originals and earlier revisions are never overwritten."""
import json
import math
import os
import socket
import subprocess
import sys
import traceback
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from studio import atomic_json, digest, uid, HIDDEN
from preparation import ASR_MODEL


# 中文维护：计算采样峰值、RMS 与静音/削波比例，仅提供可复核告警，不生成主观分数。
def audio_quality(samples):
    import numpy as np
    a = np.asarray(samples, dtype=np.float64)
    if not a.size:
        raise ValueError('音频为空')
    rms = float(np.sqrt(np.mean(a*a)))
    report = {'peak': round(float(np.max(np.abs(a))), 5),
              'rms_dbfs': round(20 * math.log10(max(rms, 1e-10)), 1),
              'clipped_ratio': round(float(np.mean(np.abs(a) >= .999)), 5),
              'near_silent_sample_ratio': round(float(np.mean(np.abs(a) < .001)), 5)}
    warnings = []
    if report['clipped_ratio'] > .001: warnings.append('检测到接近满幅的采样点，可能削波，请试听。')
    if report['rms_dbfs'] < -35: warnings.append('整体音量偏低，请检查是否为有效人声。')
    if report['rms_dbfs'] > -8: warnings.append('整体音量偏高，请检查失真。')
    report['warnings'] = warnings
    return report


# 中文维护：兼容上游短录音返回格式，最长十秒硬切并标记需人工检查的边界。
def bounded_chunks(audio, slicer, sr=32000):
    """Vendor slicer has inconsistent return shape for short audio."""
    if len(audio) < sr * 5:
        groups = [(audio, 0, len(audio))]
    else:
        groups = slicer.slice(audio)
    for chunk, start, end in groups:
        # Long uninterrupted speech cannot always be split on silence. Mark every
        # hard-cut segment so the user knows to check clipped words.
        forced = len(chunk) > 10 * sr
        for offset in range(0, len(chunk), 10 * sr):
            part = chunk[offset:offset+10*sr]
            if len(part) < sr:
                # Keep short material visible instead of silently losing it.
                yield part, int(start)+offset, True
            else:
                yield part, int(start)+offset, forced


# 中文维护：在 CPU 上离线转换、切分、转写并保存可校对片段，原录音保持不变。
def main(request_path):
    req = json.loads(Path(request_path).read_text(encoding='utf-8'))
    folder = Path(req['job_dir'])
    ds, s = req['dataset'], req['settings']
    engine = Path(s['engine'])
    rows, reports = [], []
    complete = 0

    if os.name == 'nt':
        import ctypes, threading
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        handle = kernel.OpenProcess(0x100000, False, req['parent_pid'])
        # 中文维护：监控所属主进程退出，结束本任务以避免遗留显卡或子进程占用。
        def watch():
            if handle:
                kernel.WaitForSingleObject(handle, 0xFFFFFFFF)
                subprocess.run(['taskkill', '/PID', str(os.getpid()), '/T', '/F'], creationflags=HIDDEN)
        threading.Thread(target=watch, daemon=True).start()

    # 中文维护：原子写入实际阶段进度，供主服务恢复结果与刷新界面。
    def progress(message):
        atomic_json(folder / 'progress.json', {'message': message, 'completed': complete,
            'rows': rows, 'quality_reports': reports})

    try:
        # Explicitly deny network access inside this worker, including model SDK
        # metadata checks. Missing assets produce errors instead of downloads.
        # 中文维护：拒绝工作进程网络连接，缺失模型应明确失败而非隐式下载。
        def offline(*args, **kwargs):
            raise OSError('本地整理进程禁止联网；请检查离线模型是否完整。')
        socket.socket.connect = offline
        socket.create_connection = offline
        os.environ['CUDA_VISIBLE_DEVICES'] = ''
        os.environ['OMP_NUM_THREADS'] = '4'
        import soundfile as sf
        sys.path.insert(0, str(engine / 'tools'))
        from slicer2 import Slicer
        progress('加载本地中文转写模型（CPU，不下载）')
        from funasr import AutoModel
        model = AutoModel(model=str(engine / ASR_MODEL), device='cpu', ncpu=4, disable_update=True)
        slicer = Slicer(sr=32000, threshold=-40, min_length=5000, min_interval=300, hop_size=10, max_sil_kept=500)
        for recording in ds['recordings']:
            if digest(recording['path']) != recording['hash']:
                raise ValueError('原始录音已被修改，请重新导入。')
            progress('转换并检查录音：' + recording['name'])
            decoded = folder / (recording['id'] + '-decoded.wav')
            with (folder / 'decode.log').open('a', encoding='utf-8') as log:
                subprocess.run([s['ffmpeg'], '-nostdin', '-v', 'error', '-y', '-i', recording['path'],
                    '-t', '1801', '-vn', '-ac', '1', '-ar', '32000', '-c:a', 'pcm_s16le', str(decoded)],
                    stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180, creationflags=HIDDEN)
            audio, sr = sf.read(str(decoded), dtype='float32')
            if len(audio) > 1800 * sr:
                raise ValueError('单条录音超过 30 分钟，请拆成多个文件后导入。没有截断原始录音。')
            reports.append({'recording_id': recording['id'], 'name': recording['name'],
                            'seconds': round(len(audio)/sr, 3), **audio_quality(audio)})
            progress('按静音切分：' + recording['name'])
            for chunk, start, forced in bounded_chunks(audio, slicer, sr):
                if len(rows) >= 2000: raise ValueError('切分超过 2000 段，请拆成多个素材库。已处理片段保留。')
                rid = uid()
                target = folder / (rid + '.wav')
                sf.write(str(target), chunk, sr, subtype='PCM_16')
                quality = audio_quality(chunk)
                if forced: quality['warnings'].append('连续语音较长或片段过短，边界需要试听检查。')
                row = {'id': rid, 'source': recording['path'], 'recording_id': recording['id'],
                    'audio': str(target), 'seconds': round(len(chunk)/sr, 3), 'start_seconds': round(start/sr, 3),
                    'hash': digest(target), 'original': '', 'text': '', 'reviewed': False,
                    'excluded': len(chunk) < sr or quality['rms_dbfs'] < -60, 'quality': quality}
                rows.append(row)
                progress('转写第 %d 段（%s），请完成后人工校对' % (len(rows), recording['name']))
                try:
                    result = model.generate(input=str(target), batch_size_s=20)
                    text = str(result[0].get('text', '')).strip().replace('|', '，').replace('\n', ' ')
                    row.update(original=text, text=text)
                    if not text: row['asr_error'] = '未识别到文字，请试听补写或排除。'
                except Exception as exc:
                    row['asr_error'] = '转写失败，请试听补写或排除：' + str(exc)[:200]
                progress('已转写 %d 段；文字尚未人工确认' % len(rows))
            complete += 1
            progress('已整理 %d / %d 个录音' % (complete, len(ds['recordings'])))
        if not rows: raise ValueError('没有得到可用片段，请检查录音是否包含人声。')
        atomic_json(folder / 'prepared.json', {'rows': rows, 'quality_reports': reports})
    except Exception as exc:
        atomic_json(folder / 'error.json', {'error': str(exc)})
        traceback.print_exc()
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1]))
