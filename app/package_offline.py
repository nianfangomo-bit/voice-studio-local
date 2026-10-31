"""Produce a clean, complete offline ZIP without copying personal app data."""
import argparse
import hashlib
import json
import time
import zipfile
from pathlib import Path
from build_portable import inventory, APP_FILES

VERSION = json.loads((Path(__file__).resolve().parent/'package.json').read_text(encoding='utf-8'))['version']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--engine',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    root=Path(__file__).resolve().parent
    engine=args.engine.resolve()
    dest=args.output.resolve()
    dest.parent.mkdir(parents=True,exist_ok=True)
    if dest.exists():raise ValueError('Do not overwrite a previous package')
    prefix='VoiceStudio-'+VERSION+'-win64-offline/'
    files=[(p,'engine/'+p.relative_to(engine).as_posix()) for p in inventory(engine)]
    files += [(root/p,p) for p in APP_FILES]
    files += [(root/p,p) for p in ['offline_check.py','环境检查.cmd','Start.cmd']]
    files += [(root/'OFFLINE-README.md','使用说明.md')]
    # App and frontend notices are retained in addition to vendor runtime licenses.
    release=root.parent if (root.parent/'LICENSE').is_file() else root.parent/'voice-studio-release'
    files += [(release/'LICENSE','LICENSE')]
    files += [(p,'licenses/'+p.name) for p in sorted((release/'licenses').glob('*.txt'))]
    notices = root/'OFFLINE-THIRD-PARTY.md'
    if notices.is_file(): files.append((notices,'第三方组件说明.md'))
    files = [(p,n) for p,n in files if n!='engine/GPT_SoVITS/configs/tts_infer.yaml']
    manifest=[]
    started=time.time()
    temp=dest.with_suffix(dest.suffix+'.building')
    with zipfile.ZipFile(temp,'w',zipfile.ZIP_DEFLATED,compresslevel=1,allowZip64=True) as archive:
        for index,(p,name) in enumerate(files):
            hash_=hashlib.sha256()
            with p.open('rb') as source, archive.open(prefix+name,'w',force_zip64=True) as target:
                for chunk in iter(lambda:source.read(4*1024**2),b''):
                    hash_.update(chunk); target.write(chunk)
            manifest.append({'path':name,'bytes':p.stat().st_size,'sha256':hash_.hexdigest()})
            if index%3000==0:print(json.dumps({'files_done':index,'files_total':len(files),'elapsed_seconds':round(time.time()-started)},ensure_ascii=False),flush=True)
        config={'custom':{'device':'cuda','is_half':True,'version':'v2Pro',
            't2s_weights_path':'GPT_SoVITS/pretrained_models/s1v3.ckpt',
            'vits_weights_path':'GPT_SoVITS/pretrained_models/v2Pro/s2Gv2Pro.pth',
            'bert_base_path':'GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large',
            'cnhuhbert_base_path':'GPT_SoVITS/pretrained_models/chinese-hubert-base'}}
        data=json.dumps(config).encode()
        name='engine/GPT_SoVITS/configs/tts_infer.yaml'
        archive.writestr(prefix+name,data)
        manifest.append({'path':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
        archive.writestr(prefix+'package-manifest.json',json.dumps({'version':VERSION,'files':manifest},ensure_ascii=False,indent=2))
    # Reject any accidental personal-data or trained-weight root before delivery.
    with zipfile.ZipFile(temp) as archive:
        for entry in archive.namelist():
            rel=entry[len(prefix):]
            if rel.startswith(('data/','engine/GPT_weights','engine/SoVITS_weights','engine/output/','engine/logs/')):
                raise RuntimeError('Private data entered archive: '+rel)
    temp.replace(dest)
    hash_=hashlib.sha256()
    with dest.open('rb') as source:
        for chunk in iter(lambda:source.read(4*1024**2),b''):hash_.update(chunk)
    dest.with_suffix(dest.suffix+'.sha256').write_text(hash_.hexdigest()+'  '+dest.name+'\n',encoding='utf-8')
    print(json.dumps({'archive':str(dest),'bytes':dest.stat().st_size,'files':len(manifest)+1,'sha256':hash_.hexdigest()},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
