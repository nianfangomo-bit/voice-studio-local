"""Create Release-sized parts and installer metadata for the complete ZIP."""
import argparse
import hashlib
import json
import zipfile
from pathlib import Path

parser=argparse.ArgumentParser()
parser.add_argument('archive',type=Path)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
archive=args.archive.resolve()
output=args.output.resolve()
if output.exists():raise ValueError('Use a new output directory')
output.mkdir(parents=True)
parts=[]
whole=hashlib.sha256()
with archive.open('rb') as source:
    index=1
    while True:
        first=source.read(4*1024**2)
        if not first:break
        name=archive.name+'.part%03d'%index
        path=output/name
        digest=hashlib.sha256();size=0
        with path.open('wb') as target:
            chunk=first
            while chunk:
                target.write(chunk);digest.update(chunk);whole.update(chunk);size+=len(chunk)
                if size>=1900*1024**2:break
                chunk=source.read(min(4*1024**2,1900*1024**2-size))
        parts.append({'name':name,'bytes':size,'sha256':digest.hexdigest()})
        print(json.dumps(parts[-1]),flush=True)
        index+=1
with zipfile.ZipFile(archive) as z:
    root=z.namelist()[0].split('/')[0]
    total=sum(info.file_size for info in z.infolist())
metadata={'version':'0.2.0-alpha.2','folder':root,'archive_sha256':whole.hexdigest(),'unpacked_bytes':total,'parts':parts}
(output/'offline-package.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
(output/'SHA256SUMS.txt').write_text('\n'.join(p['sha256']+'  '+p['name'] for p in parts)+'\n',encoding='utf-8')
print(json.dumps({'parts':len(parts),'packed_bytes':archive.stat().st_size,'unpacked_bytes':total}),flush=True)
