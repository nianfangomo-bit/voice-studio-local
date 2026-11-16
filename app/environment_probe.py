"""在用户选择的独立 Python 中做小型依赖与 CUDA 验证，不下载模型。"""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))


def main():
    """结构化输出版本和失败原因；不把基础检查称为训练验收。"""
    result = {'ok': False, 'python': sys.version.split()[0]}
    try:
        import torch
        import torchaudio
        import numpy
        import soundfile
        from runtime_guard import probe_cuda
        result.update(probe_cuda(torch))
        result.update(torchaudio=str(torchaudio.__version__), numpy=str(numpy.__version__))
    except Exception as exc:
        result.update(detail='运行环境依赖无法加载，请恢复已验证的完整环境，不要单独升级某个库。', error=str(exc)[-1500:])
    print('VOICE_PROBE_JSON=' + json.dumps(result, ensure_ascii=True))


if __name__ == '__main__':
    main()
