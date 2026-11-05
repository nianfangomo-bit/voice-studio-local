"""生成可复核的函数、接口和随包字体清单；不自动删除文件或作法律合规保证。"""
import argparse
import ast
from collections import Counter
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile

FONT_SUFFIXES = {'.ttf', '.otf', '.woff', '.woff2', '.ttc', '.eot', '.pfb', '.afm'}


# 收集自有 Python 函数及相邻中文注释；第三方源码归原供应方维护。
def functions(root):
    result = []
    for path in sorted(root.rglob('*.py')):
        if any(part in {'runtime-notices', 'node_modules', '.git', 'engine', 'data'} for part in path.relative_to(root).parts):
            continue
        source = path.read_text(encoding='utf-8-sig')
        lines = source.splitlines()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                start = min([node.lineno] + [d.lineno for d in node.decorator_list]) - 1
                note = lines[start - 1].strip() if start else ''
                result.append({'file':path.relative_to(root).as_posix(), 'function':node.name,
                    'line':node.lineno, 'comment':note, 'chinese_comment':note.startswith('#') and bool(re.search('[\u4e00-\u9fff]', note))})
    return result


# 读取真实装饰器注册的路由；用途由报告人工复核，不用“前端没搜到”自动判死代码。
def routes(root):
    tree = ast.parse((root/'app/server.py').read_text(encoding='utf-8-sig'))
    result = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in node.decorator_list:
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in {'get', 'post', 'put', 'delete', 'patch'}:
                    result.append({'method':d.func.attr.upper(), 'path':ast.literal_eval(d.args[0]),
                        'handler':node.name, 'line':node.lineno, 'decision':'retain: reviewed product entry'})
    return result


# 从实际字体二进制读取授权字段，不能拿 JavaScript 库许可证冒充字体许可证。
def font_metadata(data):
    from fontTools.ttLib import TTFont
    with TTFont(io.BytesIO(data)) as font:
        return sorted({n.toUnicode() for n in font['name'].names if n.nameID in {0, 13, 14}})


# 对实际离线包逐文件记录哈希、授权证据和保留理由；不触碰已发布压缩包。
def fonts(archive):
    result = []
    with zipfile.ZipFile(archive) as z:
        entries = [i for i in z.infolist() if Path(i.filename).suffix.lower() in FONT_SUFFIXES]
        all_names = z.namelist()
        katex_metadata = {}
        for info in entries:
            name = Path(info.filename).name
            if name.startswith('KaTeX_') and name.endswith('.ttf'):
                family = re.match(r'KaTeX_[A-Za-z0-9]+-(?:BoldItalic|Regular|Bold|Italic)', name).group()
                katex_metadata[family] = font_metadata(z.read(info))
        for info in entries:
            name = Path(info.filename).name
            relative = info.filename.split('/', 1)[1]
            data = z.read(info)
            row = {'path':relative, 'bytes':len(data), 'sha256':hashlib.sha256(data).hexdigest(),
                'decision':'保留：依赖资源或待确认项，不因主界面没有引用就删除'}
            if name.startswith('KaTeX_'):
                row.update(group='KaTeX (Gradio 4.44.1)', license='SIL OFL 1.1',
                    commercial='有条件允许；保留声明和许可证，不可单卖字体',
                    evidence=katex_metadata[re.match(r'KaTeX_[A-Za-z0-9]+-(?:BoldItalic|Regular|Bold|Italic)', name).group()],
                    usage='Gradio frontend CSS 引用；本产品静态白名单不提供该第三方页面')
            elif name.startswith('DejaVu'):
                row.update(group='DejaVu', license='Bitstream Vera / Arev notices', commercial='有条件允许随软件分发，不可单卖字体', evidence='matplotlib/mpl-data/fonts/ttf/LICENSE_DEJAVU', usage='训练频谱图的字体查找与默认渲染')
            elif name.startswith('STIX'):
                row.update(group='STIX', license='SIL OFL 1.1', commercial='有条件允许；保留转换说明、版权及许可证', evidence='matplotlib/mpl-data/fonts/ttf/LICENSE_STIX', usage='Matplotlib 数学字体集与回退')
            elif name.startswith('cm') and name.endswith('.ttf'):
                row.update(group='Computer Modern TTF', license='BaKoMa', commercial='有条件允许；保留版权及许可声明', evidence='matplotlib-3.6.2.dist-info/LICENSE_BAKOMA', usage='Matplotlib 数学字体集')
            elif '/pdfcorefonts/' in relative:
                row.update(group='PDF core AFM metrics', license='Adobe AFM permission', commercial='有条件允许；必须伴随 readme.txt，不能推导获得对应完整字体许可', evidence='matplotlib/mpl-data/fonts/pdfcorefonts/readme.txt', usage='Matplotlib PDF 核心字体度量，不是字体轮廓文件')
            elif '/afm/' in relative:
                row.update(group='Legacy AFM metrics', license='需逐项确认', commercial='未作全部免费商用结论', evidence='文件内版权头；pdfcorefonts 的许可不能直接套用此目录', usage='Matplotlib PS/AFM 兼容后端；尚未证明删除后全部相关路径无回归')
            else:
                row.update(group='unclassified', license='待确认', commercial='不判定', evidence='', usage='待检查')
            result.append(row)
        assert any(n.endswith('/matplotlib/mpl-data/fonts/pdfcorefonts/readme.txt') for n in all_names)
    return result


# 从命令行指定源码与待审离线包，生成脱敏清单供审阅及后续差异比较。
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    own = [p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p.suffix.lower() in FONT_SUFFIXES]
    report = {'scope':'发布源码自有函数/路由；可选完整离线 ZIP 直属字体及度量文件，不含嵌套第三方源码压缩包',
        'functions':functions(root), 'routes':routes(root), 'repository_font_files':own,
        'packaged_fonts':fonts(args.archive) if args.archive else []}
    report['font_groups'] = dict(Counter(row['group'] for row in report['packaged_fonts']))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'python_functions':len(report['functions']), 'missing_chinese_comments':sum(not f['chinese_comment'] for f in report['functions']),
        'routes':len(report['routes']), 'repository_fonts':len(own), 'packaged_fonts':len(report['packaged_fonts']), 'font_groups':report['font_groups']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
