"""训练前依赖探测：只在所选运行环境中加载训练入口必需的模块，不训练、不初始化显卡运算。

由 training.plan()/enqueue() 在静态脚本、配置、模型检查通过后调用。输出一行 JSON，
缺少模块或 CUDA 不可用时 ok=false，并保留可读原因；不安装、升级或修改任何依赖。
"""
import importlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

# 与 GPT_SoVITS 六阶段训练脚本及其直接依赖保持一致；只检查可加载性。
MODULES = (
    'torch', 'torchaudio', 'numpy', 'scipy', 'librosa', 'transformers', 'pytorch_lightning',
    'tensorboard', 'tqdm', 'yaml', 'gradio', 'pandas', 'ffmpeg', 'soundfile', 'onnxruntime',
    'jieba_fast', 'split_lang',
)


def main():
    result = {'ok': True, 'python': sys.version.split()[0], 'modules': {}, 'missing': [],
              'errors': {}, 'problems': []}
    for name in MODULES:
        try:
            module = importlib.import_module(name)
            result['modules'][name] = str(getattr(module, '__version__', '可加载'))
        except Exception as exc:
            result['ok'] = False
            result['missing'].append(name)
            result['errors'][name] = str(exc)[-600:]
    torch = sys.modules.get('torch')
    if torch is not None:
        try:
            result['cuda_available'] = bool(torch.cuda.is_available())
            if result['cuda_available']:
                result['cuda'] = str(torch.version.cuda)
                result['device'] = str(torch.cuda.get_device_name(0))
                # 中文维护：在可用 CUDA 上做小型矩阵与 FFT 运算自检，提前拦截 50 系/旧 CUDA 架构不兼容。
                from runtime_guard import probe_cuda
                cuda_report = probe_cuda(torch)
                result['cuda_probe'] = cuda_report
                if cuda_report.get('ok') is not True:
                    result['ok'] = False
                    detail = cuda_report.get('detail') or '当前运行环境与显卡架构不兼容。'
                    raw_error = str(cuda_report.get('error') or '').strip().splitlines()
                    if raw_error:
                        detail += '（' + raw_error[0][:240] + '）'
                    result['problems'].append(detail)
            else:
                result['ok'] = False
                result['problems'].append('训练需要可用的 CUDA；当前运行环境未检测到可用显卡。'
                                          '请检查 NVIDIA 驱动与已验证的完整环境。')
        except Exception as exc:
            result['ok'] = False
            result['problems'].append('CUDA 可用性检查失败：' + str(exc)[-600:])
    print('VOICE_TRAINING_PROBE=' + json.dumps(result, ensure_ascii=True))


if __name__ == '__main__':
    main()