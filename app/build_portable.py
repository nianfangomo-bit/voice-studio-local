"""Build LOCAL acceptance candidate, not a licensed public release.

Explicitly omits user weights, recordings, history and personalized inference config.
Use --copy only after inspecting --engine inventory; never merges into existing output.
"""
import argparse
import hashlib
import json
import shutil
from pathlib import Path

APP_FILES = ['studio.py','server.py','launcher.py','training.py','training_worker.py',
             'inference_worker.py','engine_entry.py','preparation.py','preparation_worker.py',
             '启动.cmd','static/index.html','static/app.js','static/style.css','legacy/voice_tool/main.py']
MODEL_PATHS = ['chinese-hubert-base','chinese-roberta-wwm-ext-large','fast_langdetect',
               'sv/pretrained_eres2netv2w24s4ep4.ckpt','v2Pro/s2Gv2Pro.pth',
               'v2Pro/s2Dv2Pro.pth','s1v3.ckpt']
SKIP_PARTS = {'__pycache__','.git','.cache','._____temp','.pytest_cache'}


def inventory(engine):
    roots = [engine/'runtime', engine/'config.py', engine/'LICENSE', engine/'GPT_SoVITS',
             engine/'tools/__init__.py', engine/'tools/my_utils.py', engine/'tools/i18n',
             engine/'tools/slicer2.py',engine/'tools/assets.py',engine/'tools/audio_sr.py',
             engine/'tools/AP_BWE_main',
             engine/'tools/asr/models/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch']
    files = set()
    for root in roots:
        if not root.exists(): raise ValueError('Missing required engine component: '+str(root))
        for p in ([root] if root.is_file() else root.rglob('*')):
            if not p.is_file() or p.is_symlink(): continue
            rel = p.relative_to(engine)
            if SKIP_PARTS.intersection(rel.parts) or p.suffix in {'.pyc','.log'}: continue
            if 'pretrained_models' in rel.parts:
                model_rel = p.relative_to(engine/'GPT_SoVITS/pretrained_models').as_posix()
                if not any(model_rel == a or model_rel.startswith(a+'/') for a in MODEL_PATHS): continue
            if rel.as_posix().startswith('GPT_SoVITS/configs/'):
                if rel.name not in {'s2v2Pro.json','s1longer-v2.yaml'}: continue
            if p.name.startswith('.env') or p.name in {'weight.json', 'session.json', 'bootstrap.json'}: continue
            files.add(p)
    return sorted(files)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--engine',type=Path,required=True)
    parser.add_argument('--destination',type=Path,required=True)
    parser.add_argument('--copy',action='store_true')
    args=parser.parse_args()
    app=Path(__file__).resolve().parent
    files=inventory(args.engine.resolve())
    size=sum(p.stat().st_size for p in files)
    print(json.dumps({'engine_files':len(files),'engine_gb':round(size/1024**3,2),
                      'mode':'copy-local-candidate' if args.copy else 'inventory-only'}),flush=True)
    if not args.copy:return
    dest=args.destination.resolve()
    if dest.exists():raise ValueError('Destination must be new. Existing packages are never overwritten.')
    if shutil.disk_usage(dest.parent).free < size+5*1024**3:raise ValueError('Insufficient free space')
    for rel in APP_FILES:
        if not (app/rel).is_file():raise ValueError('Missing app component: '+rel)
    dest.mkdir()
    manifest=[]
    for i,p in enumerate(files):
        target=dest/'engine'/p.relative_to(args.engine.resolve())
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(p,target)
        manifest.append({'path':target.relative_to(dest).as_posix(),'bytes':target.stat().st_size})
        if i%3000==0:print('Copied %d / %d engine files'%(i,len(files)),flush=True)
    for rel in APP_FILES:
        target=dest/rel
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(app/rel,target)
    config={'custom':{'device':'cuda','is_half':True,'version':'v2Pro',
        't2s_weights_path':'GPT_SoVITS/pretrained_models/s1v3.ckpt',
        'vits_weights_path':'GPT_SoVITS/pretrained_models/v2Pro/s2Gv2Pro.pth',
        'bert_base_path':'GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large',
        'cnhuhbert_base_path':'GPT_SoVITS/pretrained_models/chinese-hubert-base'}}
    (dest/'engine/GPT_SoVITS/configs/tts_infer.yaml').write_text(json.dumps(config),encoding='utf-8')
    (dest/'LOCAL-ACCEPTANCE-ONLY.txt').write_text('本机验收候选包，不是公开发行包。包含现有运行环境与基础模型；依赖再分发许可、干净 Windows 安装和完整训练验收未完成。不得直接上传。\n启动：双击 启动.cmd，无需系统 Python。\n',encoding='utf-8')
    (dest/'inventory.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Local candidate created: '+str(dest),flush=True)


if __name__=='__main__':main()
