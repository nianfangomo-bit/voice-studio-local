"""Six real stages, immutable training snapshot, explicit output directories."""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from studio import ROOT, HIDDEN, atomic_json, digest
from training import initialize_checkpoint_directory, compatible_loader_workers


def main(request_path):
    req = json.loads(Path(request_path).read_text(encoding='utf-8'))
    folder = Path(req['job_dir'])
    s, ds = req['settings'], req['dataset']
    engine = Path(s['engine'])
    exp = folder / 'experiment'
    exp.mkdir(exist_ok=True)
    completed = 0

    def progress(message):
        atomic_json(folder / 'progress.json', {'message': message, 'completed': completed})

    try:
        lockfile = open(req['lock_path'], 'a+b')
        lockfile.seek(0)
        import msvcrt
        msvcrt.locking(lockfile.fileno(), msvcrt.LK_NBLCK, 1)
        # Parent watchdog kills this entire owned process tree on service death.
        import ctypes, threading
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        handle = kernel.OpenProcess(0x100000, False, req['parent_pid'])
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        def watch():
            if handle:
                kernel.WaitForSingleObject(handle, 0xFFFFFFFF)
                subprocess.run(['taskkill', '/PID', str(os.getpid()), '/T', '/F'], creationflags=HIDDEN)
        threading.Thread(target=watch, daemon=True).start()
        if digest(engine / 'GPT_SoVITS/TTS_infer_pack/TTS.py') != s['engine_hash']:
            raise RuntimeError('引擎已改变，请重新检查环境。')
        import torch
        import yaml
        if not torch.cuda.is_available():
            raise RuntimeError('训练需要可用的 CUDA 显卡。')
        if shutil.disk_usage(folder).free < 12 * 1024**3:
            raise RuntimeError('可用磁盘不足 12 GB，未开始训练。')
        selected = [r for r in ds['rows'] if not r['excluded']]
        for row in selected:
            if not row['reviewed'] or digest(row['audio']) != row['hash']:
                raise RuntimeError('片段未校对或音频发生变更，请重新校对。')
        labels = folder / 'reviewed.list'
        labels.write_text('\n'.join(f'{r["audio"]}|speaker|ZH|{r["text"]}' for r in selected), encoding='utf-8')
        # Vendor text code expects relative asset paths. A directory junction makes
        # existing assets readable without copying/downloading or editing originals.
        sandbox = folder / 'work'
        sandbox.mkdir(exist_ok=True)
        for source_name in ('GPT_SoVITS', 'tools'):
            junction = sandbox / source_name
            if not junction.exists():
                subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(engine / source_name)],
                               check=True, capture_output=True, creationflags=HIDDEN)
        shutil.copy2(engine / 'config.py', sandbox / 'config.py')
        env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
            version='v2Pro', is_half='True', i_part='0', all_parts='1', _CUDA_VISIBLE_DEVICES='0', hz='25hz',
            inp_text=str(labels), inp_wav_dir='', exp_name='voice_' + ds['id'][:8], opt_dir=str(exp),
            bert_pretrained_dir=str(engine / 'GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large'),
            bert_path=str(engine / 'GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large'),
            cnhubert_base_dir=str(engine / 'GPT_SoVITS/pretrained_models/chinese-hubert-base'),
            sv_path=str(engine / 'GPT_SoVITS/pretrained_models/sv/pretrained_eres2netv2w24s4ep4.ckpt'),
            pretrained_s2G=str(engine / 'GPT_SoVITS/pretrained_models/v2Pro/s2Gv2Pro.pth'),
            s2config_path=str(engine / 'GPT_SoVITS/configs/s2v2Pro.json'))
        env['PATH'] = str(Path(s['ffmpeg']).parent) + os.pathsep + env['PATH']

        def run(stage, script, args=()):
            nonlocal completed
            progress(stage)
            print('\nSTAGE: ' + stage, flush=True)
            with (folder / ('stage-' + str(completed + 1) + '.log')).open('w', encoding='utf-8') as stage_log:
                child = subprocess.run([s['runtime'], '-I', '-X', 'utf8', '-B', str(ROOT / 'engine_entry.py'), str(engine), script, *args],
                           cwd=str(sandbox), env=env, stdout=stage_log, stderr=subprocess.STDOUT, creationflags=HIDDEN)
            if child.returncode != 0:
                detail = (folder / ('stage-' + str(completed + 1) + '.log')).read_text(encoding='utf-8', errors='replace')
                print(detail[-12000:], flush=True)
                raise RuntimeError(stage + ' 失败：' + detail[-1500:])
            completed += 1
            progress(stage + ' 完成')

        run('1/6 重新计算校对文字与文本特征', 'prepare_datasets/1-get-text.py')
        text_part = exp / '2-name2text-0.txt'
        if not text_part.exists() or len(text_part.read_text(encoding='utf-8').strip().splitlines()) != len(selected):
            raise RuntimeError('文字特征数量与校对片段不一致，已停止，不能带缺失样本继续训练。')
        shutil.copy2(text_part, exp / '2-name2text.txt')
        run('2/6 提取音频特征', 'prepare_datasets/2-get-hubert-wav32k.py')
        run('3/6 提取声纹特征', 'prepare_datasets/2-get-sv.py')
        run('4/6 提取语义特征', 'prepare_datasets/3-get-semantic.py')
        semantic = exp / '6-name2semantic-0.tsv'
        lines = semantic.read_text(encoding='utf-8').strip().splitlines()
        if len(lines) != len(selected):
            raise RuntimeError('语义特征数量不完整，已停止。')
        (exp / '6-name2semantic.tsv').write_text('item_name\tsemantic_audio\n'+'\n'.join(lines)+'\n', encoding='utf-8')
        for row in selected:
            for sub, suffix in [('4-cnhubert','.pt'),('5-wav32k',''),('7-sv_cn','.pt')]:
                if not (exp / sub / (Path(row['audio']).name + suffix)).is_file():
                    raise RuntimeError('音频特征缺失：' + row['id'])
        params = ds['params']
        weights = folder / 'weights'
        weights.mkdir()
        config2 = json.loads((engine / 'GPT_SoVITS/configs/s2v2Pro.json').read_text(encoding='utf-8'))
        config2['train'].update(batch_size=params['batch_size'], epochs=params['sovits_epochs'],
            pretrained_s2G=env['pretrained_s2G'], pretrained_s2D=str(engine / 'GPT_SoVITS/pretrained_models/v2Pro/s2Dv2Pro.pth'),
            if_save_latest=True, if_save_every_weights=True, save_every_epoch=params['sovits_epochs'],
            gpu_numbers='0', text_low_lr_rate=.4, fp16_run=True, grad_ckpt=False)
        config2['data']['exp_dir'] = str(exp)
        config2.update(s2_ckpt_dir=str(exp), save_weight_dir=str(weights), name=env['exp_name'], version='v2Pro')
        config2['model']['version'] = 'v2Pro'
        # The upstream WebUI creates this directory before invoking s2_train;
        # the standalone script assumes it already exists when saving checkpoints.
        initialize_checkpoint_directory(exp)
        atomic_json(folder / 's2.json', config2)
        run('5/6 SoVITS 声音模型训练（查看日志了解实际轮数）', 's2_train.py', ['--config', str(folder / 's2.json')])
        config1 = yaml.safe_load((engine / 'GPT_SoVITS/configs/s1longer-v2.yaml').read_text(encoding='utf-8'))
        config1['train'].update(batch_size=params['batch_size'], epochs=params['gpt_epochs'],
            save_every_n_epoch=params['gpt_epochs'], if_save_every_weights=True, if_save_latest=True, if_dpo=False,
            half_weights_save_dir=str(weights), exp_name=env['exp_name'])
        # Upstream uses persistent_workers=True; zero workers is invalid.
        config1['data']['num_workers'] = compatible_loader_workers(1)
        config1.update(pretrained_s1=str(engine / 'GPT_SoVITS/pretrained_models/s1v3.ckpt'),
            train_semantic_path=str(exp / '6-name2semantic.tsv'), train_phoneme_path=str(exp / '2-name2text.txt'),
            output_dir=str(exp / 'logs_s1_v2Pro'))
        (folder / 's1.yaml').write_text(yaml.safe_dump(config1), encoding='utf-8')
        run('6/6 GPT 语义模型训练（查看日志了解实际轮数）', 's1_train.py', ['--config_file', str(folder / 's1.yaml')])
        gpt, sovits = list(weights.glob('*.ckpt')), list(weights.glob('*.pth'))
        if not gpt or not sovits:
            raise RuntimeError('训练结束但未找到可用的模型组合。请检查日志。')
        reference = next((r for r in selected if 3 <= r['seconds'] <= 10), None)
        if not reference:
            raise RuntimeError('训练产物已保留，但缺少 3～10 秒参考录音；请准备后手动导入模型。')
        atomic_json(folder / 'candidate.json', {'gpt':str(gpt[-1]),'sovits':str(sovits[-1]),
            'reference':reference['audio'],'prompt_text':reference['text']})
        progress('候选模型已保存，等待新文案试听验收')
        return 0
    except Exception as e:
        traceback.print_exc()
        atomic_json(folder / 'error.json', {'error':str(e)})
        return 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1]))
