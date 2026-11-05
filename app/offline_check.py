"""Human-readable offline checks using the runtime included in the package."""
import argparse
import hashlib
import importlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


# 中文维护：检查随包依赖、基础模型、CUDA 和空间；深度模式逐文件核验清单。
def inspect(deep=False):
    checks = []
    # 中文维护：将单项检查结果归一化保存，供中文终端报告使用。
    def record(name, ok, detail):
        checks.append({'name': name, 'ok': bool(ok), 'detail': str(detail)})
    record('随包 Python', ROOT in Path(sys.executable).resolve().parents, sys.version.split()[0])
    required = ['runtime/ffmpeg.exe','runtime/python.exe','GPT_SoVITS/configs/s2v2Pro.json',
        'GPT_SoVITS/configs/s1longer-v2.yaml','GPT_SoVITS/TTS_infer_pack/TTS.py',
        'GPT_SoVITS/pretrained_models/s1v3.ckpt','GPT_SoVITS/pretrained_models/v2Pro/s2Gv2Pro.pth',
        'GPT_SoVITS/pretrained_models/v2Pro/s2Dv2Pro.pth',
        'GPT_SoVITS/pretrained_models/sv/pretrained_eres2netv2w24s4ep4.ckpt',
        'GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large/pytorch_model.bin',
        'GPT_SoVITS/pretrained_models/chinese-hubert-base/pytorch_model.bin',
        'GPT_SoVITS/text/G2PWModel/g2pW.onnx',
        'tools/asr/models/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch/model.pt']
    missing = [p for p in required if not (ROOT/'engine'/p).is_file()]
    record('训练、配音与中文转写文件', not missing, '完整' if not missing else '缺少：'+'；'.join(missing))
    # Engine utilities import Gradio even though the product uses its own UI.
    # Check training/inference imports, not merely the HTTP server dependencies.
    for module in ('tkinter','fastapi','uvicorn','numpy','soundfile','funasr','yaml','torch',
                   'torchaudio','gradio','pandas','transformers','peft','pytorch_lightning',
                   'onnxruntime','jieba_fast','split_lang'):
        try:
            loaded = importlib.import_module(module)
            record(module, True, getattr(loaded,'__version__','可加载'))
        except Exception as exc:
            record(module, False, str(exc)+'。请完整解压；如提示 DLL 缺失，检查系统运行库及显卡驱动。')
    for resource in ('corpora/cmudict.zip','taggers/averaged_perceptron_tagger.zip'):
        record('离线语言资源 '+resource,
               (ROOT/'engine/runtime/nltk_data'/resource).is_file(),
               '必须随包提供，不能依赖首次启动联网下载')
    torch = sys.modules.get('torch')
    if torch:
        try:
            available = torch.cuda.is_available()
            detail = torch.cuda.get_device_name(0) if available else '未检测到可用 CUDA；可整理录音，训练与当前配音引擎需 NVIDIA 显卡和兼容驱动。'
            record('CUDA 训练与配音', available, detail)
        except Exception as exc:
            record('CUDA 训练与配音', False, exc)
    free = shutil.disk_usage(ROOT).free / 1024**3
    record('训练剩余空间', free >= 12, f'{free:.1f} GB 可用，训练至少预留 12 GB')
    if deep:
        manifest = json.loads((ROOT/'package-manifest.json').read_text(encoding='utf-8'))
        invalid = []
        for i, item in enumerate(manifest['files']):
            p = ROOT/item['path']
            if not p.is_file() or p.stat().st_size != item['bytes']:
                invalid.append(item['path']); continue
            digest = hashlib.sha256()
            with p.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024**2), b''): digest.update(chunk)
            if digest.hexdigest() != item['sha256']: invalid.append(item['path'])
            if i % 5000 == 0: print(f'已校验 {i} / {len(manifest["files"])} 个文件', flush=True)
        record('全包文件校验', not invalid, '完整一致' if not invalid else '缺失或损坏：'+'；'.join(invalid[:20]))
    return checks


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--deep', action='store_true')
    args = parser.parse_args()
    checks = inspect(args.deep)
    for item in checks: print(('通过' if item['ok'] else '需处理')+' | '+item['name']+' | '+item['detail'])
    sys.exit(0 if all(c['ok'] for c in checks) else 1)
