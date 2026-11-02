"""Build a source-only release using an explicit privacy allowlist."""
from pathlib import Path
import hashlib
import json
import zipfile

root = Path(__file__).resolve().parent
version = (root / 'VERSION').read_text(encoding='utf-8').strip()
assert json.loads((root/'app/package.json').read_text(encoding='utf-8'))['version'] == version, '版本号不一致'
out = root.parent / 'voice-studio-release-artifacts'
out.mkdir(exist_ok=True)
top = {'README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'CHANGELOG.md',
       '.gitignore', 'tested-environment.json', 'build-release.py', 'VERSION'}
app_files = {'studio.py', 'server.py', 'launcher.py', 'training.py', 'training_worker.py',
             'inference_worker.py', 'engine_entry.py', '启动.cmd', 'build.cjs', 'package.json',
             'ui/app.jsx', 'ui/Training.jsx', 'ui/voice-selection.mjs', 'static/index.html',
             'static/style.css', 'static/app.js', 'tests/test_studio.py',
             'tests/voice-selection.test.mjs', 'legacy/voice_tool/main.py'}
app_files.update({'preparation.py','preparation_worker.py','build_portable.py',
                  'tests/test_preparation.py','tests/test_preparation_api.py'})
app_files.update({'offline_check.py','环境检查.cmd','Start.cmd','OFFLINE-README.md',
                  'OFFLINE-THIRD-PARTY.md','package_offline.py','split_offline.py',
                  'OfflineSetup.cs','publish_offline_release.py','DownloadOffline.ps1'})
files = [root / p for p in sorted(top)] + [root / 'app' / p for p in sorted(app_files)]
files += sorted((root / 'docs').glob('*.md')) + sorted((root / 'licenses').glob('*.txt'))
files.append(root/'docs/cover.png')
manifest = []
for p in files:
    data = p.read_bytes()
    manifest.append({'path': p.relative_to(root).as_posix(), 'bytes': len(data),
                     'sha256': hashlib.sha256(data).hexdigest()})
dest = out / ('voice-studio-local-v' + version + '-source.zip')
if dest.exists():
    raise RuntimeError('版本文件已存在，请增加版本号，不能覆盖既有发行资产。')
with zipfile.ZipFile(dest, 'w', zipfile.ZIP_DEFLATED) as z:
    for p in files:
        z.write(p, p.relative_to(root).as_posix())
    z.writestr('MANIFEST.json', json.dumps(manifest, ensure_ascii=False, indent=2))
with zipfile.ZipFile(dest) as z:
    assert z.testzip() is None
digest = hashlib.sha256(dest.read_bytes()).hexdigest()
(out / (dest.name + '.sha256')).write_text(f'{digest}  {dest.name}\n', encoding='utf-8')
print(json.dumps({'zip': str(dest), 'files': len(files)+1, 'bytes': dest.stat().st_size,
                  'sha256': digest}, ensure_ascii=False))
