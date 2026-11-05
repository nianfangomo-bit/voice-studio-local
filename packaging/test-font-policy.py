"""在独立字体资源副本中验证裁剪；不修改原引擎、不训练、不下载。"""
import argparse
import gc
import ast
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import tempfile
import subprocess
from contextlib import nullcontext


# 复制小型绘图资源并实际运行引擎频谱绘图函数；结果不是完整训练或离线包验收。
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--engine', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--scratch', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('不能覆盖既有测试记录')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.scratch is None:
        # 绘图放入子进程，退出后由父进程回收副本，确保 Windows 字体句柄已关闭。
        with tempfile.TemporaryDirectory(prefix='font-policy-', dir=args.output.parent) as tmp:
            subprocess.run([sys.executable, '-I', '-B', '-X', 'utf8', __file__,
                            '--engine', str(args.engine), '--output', str(args.output),
                            '--scratch', tmp], check=True)
        return
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'app'))
    from build_portable import excluded_legacy_afm
    source = args.engine/'runtime/Lib/site-packages/matplotlib/mpl-data'
    omitted = []
    with nullcontext(str(args.scratch)) as tmp:
        copied = Path(tmp)/'mpl-data'
        for path in source.rglob('*'):
            if not path.is_file():
                continue
            if excluded_legacy_afm(path.relative_to(args.engine)):
                omitted.append({'path':path.relative_to(args.engine).as_posix(),
                                'bytes':path.stat().st_size, 'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
                continue
            dest = copied/path.relative_to(source)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
        os.environ['MPLCONFIGDIR'] = str(Path(tmp)/'config')
        import matplotlib
        # 用独立资源目录和全新缓存初始化 FontManager，不借用旧 AFM 或旧字体缓存。
        matplotlib.get_data_path = lambda: str(copied)
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import numpy as np
        import matplotlib.font_manager as fm
        assert not list((copied/'fonts/afm').glob('*.afm'))
        assert all('/fonts/afm/' not in f.fname.replace('\\', '/') for f in fm.fontManager.afmlist)
        raw = (args.engine/'GPT_SoVITS/utils.py').read_text(encoding='utf-8')
        node = next(n for n in ast.parse(raw).body if isinstance(n, ast.FunctionDef) and n.name == 'plot_spectrogram_to_numpy')
        scope = {'np':np, 'logging':logging, 'MATPLOTLIB_FLAG':False}
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'engine/utils.py', 'exec'), scope)
        pixels = scope['plot_spectrogram_to_numpy'](np.random.default_rng(7).random((80, 100)))
        assert pixels.shape == (200, 1000, 3) and pixels.std() > 1
        fig, ax = plt.subplots()
        ax.set_title(r'Text and math: $q^3=3/2$')
        ax.plot([0, 1], [0, 1])
        for ext in ['png', 'pdf', 'svg']:
            fig.savefig(Path(tmp)/('plot.'+ext))
            assert (Path(tmp)/('plot.'+ext)).stat().st_size > 100
        plt.close(fig)
        # Windows 上释放字体对象的文件句柄，避免临时副本清理失败。
        del fig, ax
        fm._get_font.cache_clear()
        gc.collect()
        result = {'version':'1.0.2-rc.1', 'matplotlib':matplotlib.__version__,
                  'excluded_files':omitted, 'excluded_count':len(omitted),
                  'excluded_bytes':sum(f['bytes'] for f in omitted),
                  'engine_spectrogram_function':'passed (isolated exact AST function, not training)',
                  'png_pdf_svg':'passed', 'fresh_font_cache':True,
                  'full_package_training':'not tested', 'other_computer':'not tested'}
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        print(json.dumps({k:v for k,v in result.items() if k != 'excluded_files'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
