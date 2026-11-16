"""把已分卷的离线 ZIP 变成可验收的本地安装附件；不上传网络。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from publish_offline_release import validate_package


def sha256(path):
    """流式核对大分卷，避免把完整模型包读入内存。"""
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def main():
    """编译安装器、生成同版本说明与哈希；拒绝覆盖任何已有附件。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    directory = args.directory.resolve()
    package = json.loads((directory / 'offline-package.json').read_text(encoding='utf-8'))
    version = json.loads((root / 'package.json').read_text(encoding='utf-8'))['version']
    if package.get('version') != version or package.get('folder') != f'VoiceStudio-{version}-win64-offline':
        raise ValueError('分卷与应用源码版本不同，停止组装。')
    for index, part in enumerate(package['parts'], 1):
        if part['name'] != f'VoiceStudio-{version}-win64-offline.zip.part{index:03d}':
            raise ValueError('分卷名称或顺序不一致。')
        path = directory / part['name']
        if not path.is_file() or path.stat().st_size != part['bytes'] or sha256(path) != part['sha256']:
            raise ValueError('分卷缺失或校验失败：' + part['name'])
    names = ('VoiceStudio-Offline-Setup.exe', 'INSTALL.txt', 'ACCEPTANCE.json',
             'DownloadOffline.ps1')
    if any((directory / name).exists() for name in names):
        raise ValueError('目标已有安装附件；请在新的目录组装，不能覆盖旧版本。')
    windir = os.environ.get('WINDIR')
    compiler = Path(windir) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe' if windir else None
    if not compiler or not compiler.is_file():
        raise ValueError('缺少 Windows .NET Framework x64 编译器，不能生成安装器。')
    installer = directory / names[0]
    result = subprocess.run([str(compiler), '/nologo', '/target:winexe', '/platform:x64',
                             '/out:' + str(installer), '/reference:System.Web.Extensions.dll',
                             '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll',
                             '/reference:System.IO.Compression.dll',
                             '/reference:System.IO.Compression.FileSystem.dll',
                             str(root / 'OfflineSetup.cs')], capture_output=True, text=True)
    if result.returncode or not installer.is_file():
        raise RuntimeError('安装器编译失败：' + (result.stdout + result.stderr)[-2000:])
    (directory / 'INSTALL.txt').write_text(
        f'声线工作室 {version} · Windows 完整离线候选包\n\n'
        '下载同一发布版本的安装器、offline-package.json、SHA256SUMS.txt 和全部 part 分卷，放在同一文件夹。\n'
        '双击 VoiceStudio-Offline-Setup.exe，选择空间足够的新安装目录。不要覆盖已有安装或删除旧 data。\n'
        '安装完成后先运行 环境检查.cmd，再运行 Start.cmd；使用有授权的录音并人工校对转写。\n'
        '此包只使用随包的 Python、引擎和基础模型，不承诺适配所有显卡；显卡驱动仍由用户负责。\n'
        '候选状态和真实测试范围请看 ACCEPTANCE.json；没有通过异机测试前不得称为稳定版。\n',
        encoding='utf-8')
    (directory / 'ACCEPTANCE.json').write_text(json.dumps({
        'version': version, 'package_sha256': package['archive_sha256'],
        'status': 'local_candidate_pending_final_install_test',
        'package_files': len(package['parts']),
        'completed': ['分卷大小及 SHA-256 已核对', '安装器由同仓库源码编译'],
        'not_run': ['最终安装包实装', '异机训练与配音', '断网核心流程'],
        'note': '此文件是打包时的初始状态；完成实装后须另附真实验收记录，不可把初始状态当作通过。'
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    shutil.copy2(root / 'DownloadOffline.ps1', directory / 'DownloadOffline.ps1')
    files = [directory / part['name'] for part in package['parts']]
    files += [directory / name for name in names]
    files.append(directory / 'offline-package.json')
    (directory / 'SHA256SUMS.txt').write_text(
        ''.join(f'{sha256(path)}  {path.name}\n' for path in files), encoding='utf-8')
    validate_package(directory, package)
    print(json.dumps({'version': version, 'assets': len(files) + 1,
                      'status': 'local_candidate_pending_final_install_test'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
