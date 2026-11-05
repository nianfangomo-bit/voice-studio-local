"""Private subprocess: no network service, no changes to external model configuration."""
from __future__ import annotations
import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from studio import atomic_json, digest, load_legacy, HIDDEN


# 中文维护：核验引擎、权重和参考音频指纹后持锁生成片段；验证通过才写入恢复记录。
def main(request_path):
    request = json.loads(Path(request_path).read_text(encoding='utf-8'))
    folder = Path(request['job_dir'])
    results = []

    # 中文维护：原子写入实际阶段进度，供主服务恢复结果与刷新界面。
    def progress(message):
        atomic_json(folder / 'progress.json', {'message': message, 'results': results})

    # Exit with the owning service to avoid an orphan model keeping GPU memory.
    if os.name == 'nt':
        import ctypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        handle = kernel.OpenProcess(0x100000, False, request['parent_pid'])
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        # 中文维护：监控所属主进程退出，结束本任务以避免遗留显卡或子进程占用。
        def watch():
            if handle:
                kernel.WaitForSingleObject(handle, 0xFFFFFFFF)
                os._exit(3)
        threading.Thread(target=watch, daemon=True).start()
    try:
        lockfile = open(request['lock_path'], 'a+b')
        lockfile.seek(0)
        if os.name == 'nt':
            import msvcrt
            try:
                msvcrt.locking(lockfile.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise RuntimeError('另一个本软件推理进程仍占用显卡锁，请等待其结束后重试。')
        else:
            import fcntl
            fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
        s = request['settings']
        p = request['project']
        v = p['voice']
        progress('核验模型和参考录音指纹')
        engine = Path(s['engine'])
        if digest(engine / 'GPT_SoVITS/TTS_infer_pack/TTS.py') != s['engine_hash'] or v['engine_hash'] != s['engine_hash']:
            raise RuntimeError('引擎版本发生变化，请重新确认环境并导入声线。')
        for name in ('gpt', 'sovits', 'reference'):
            if digest(v[name]) != v[name + '_hash']:
                raise RuntimeError('声线文件已改变，请重新导入该声线：' + name)
        core = load_legacy(s)
        os.environ['PATH'] = str(Path(s['ffmpeg']).parent) + os.pathsep + os.environ['PATH']
        os.environ['HF_HUB_OFFLINE'] = '1'
        os.environ['TRANSFORMERS_OFFLINE'] = '1'
        os.chdir(str(engine))
        sys.path.insert(0, str(engine))
        sys.path.insert(0, str(engine / 'GPT_SoVITS'))
        import yaml
        import torch
        import soundfile as sf
        if not torch.cuda.is_available():
            raise RuntimeError('当前引擎无法使用 CUDA。请检查 NVIDIA 驱动与引擎环境。')
        config = yaml.safe_load((engine / 'GPT_SoVITS/configs/tts_infer.yaml').read_text(encoding='utf-8'))
        config['custom'] = {'device': 'cuda', 'is_half': True, 'version': 'v2Pro',
            't2s_weights_path': v['gpt'], 'vits_weights_path': v['sovits'],
            'bert_base_path': str(engine / 'GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large'),
            'cnhuhbert_base_path': str(engine / 'GPT_SoVITS/pretrained_models/chinese-hubert-base')}
        config_path = folder / 'private_infer.yaml'
        config_path.write_text(yaml.safe_dump(config), encoding='utf-8')
        progress('加载声线模型（首次需要等待，无虚拟进度）')
        from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config
        tts = TTS(TTS_Config(str(config_path)))
        for index, seg in enumerate(request['segments']):
            target = folder / (seg['id'] + '.wav')
            last_error = None
            for attempt in range(3):
                progress(f'生成第 {index+1}/{len(request["segments"])} 段；尝试 {attempt+1}/3')
                try:
                    payload = {'text': seg['spoken'], 'text_lang': 'zh', 'prompt_lang': 'zh',
                        'ref_audio_path': v['reference'], 'prompt_text': v['prompt_text'], 'aux_ref_audio_paths': [],
                        'text_split_method': 'cut0', 'batch_size': 1, 'streaming_mode': False,
                        'return_fragment': False, 'speed_factor': p['speed'], 'seed': attempt+1,
                        'parallel_infer': True, 'top_k': 15, 'top_p': 1.0, 'temperature': 1.0,
                        'repetition_penalty': 1.35, 'sample_steps': 32, 'super_sampling': False}
                    sr, audio = next(tts.run(payload))
                    sf.write(str(target), audio, sr, subtype='PCM_16')
                    core.validate_segment_wav(target, seg['spoken'])
                    # Only a new task-local file is normalized, never a user's original.
                    import subprocess
                    normalized = folder / (seg['id'] + '.normalized.wav')
                    subprocess.run([s['ffmpeg'], '-y', '-loglevel', 'error', '-i', str(target), '-ac', '1', '-ar', '32000',
                                    '-c:a', 'pcm_s16le', str(normalized)], check=True, creationflags=HIDDEN)
                    normalized.replace(target)
                    stats = core.validate_segment_wav(target, seg['spoken'])
                    results.append({'id': seg['id'], 'audio': {'path': str(target), 'fingerprint': seg['fingerprint'],
                        'sha256': digest(target), 'seed': attempt+1, 'stats': stats, 'created': time.time()}})
                    progress(f'已验证 {len(results)}/{len(request["segments"])} 段')
                    last_error = None
                    break
                except Exception as e:
                    last_error = e
                    traceback.print_exc()
            if last_error:
                raise RuntimeError(f'片段 {index+1} 连续三次未通过检查：{last_error}')
        progress('全部片段生成完成')
        return 0
    except Exception as e:
        traceback.print_exc()
        atomic_json(folder / 'error.json', {'error': str(e)})
        return 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1]))
