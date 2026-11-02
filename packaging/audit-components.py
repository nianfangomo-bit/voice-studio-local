"""Produce a metadata inventory, not an automatic legal-compliance verdict."""
import argparse,email,json,hashlib,csv
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('runtime',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
packages=[]
for metadata in sorted((a.runtime/'Lib/site-packages').glob('*.dist-info/METADATA')):
    m=email.message_from_string(metadata.read_text(encoding='utf-8',errors='replace'))
    marker=lambda f:any(x in f.name.lower() for x in ('license','licence','copying','notice'))
    licenses=[f.relative_to(a.runtime).as_posix() for f in metadata.parent.rglob('*') if f.is_file() and marker(f)]
    record=metadata.parent/'RECORD'
    if record.is_file():
        for row in csv.reader(record.read_text(encoding='utf-8').splitlines()):
            f=(metadata.parent.parent/row[0]).resolve()
            if marker(f) and a.runtime.resolve() in f.parents and f.is_file():
                licenses.append(f.relative_to(a.runtime.resolve()).as_posix())
    licenses=sorted(set(licenses))
    classifiers=[x for x in m.get_all('Classifier',[]) if x.startswith('License ::')]
    packages.append({'name':m.get('Name'),'version':m.get('Version'),'declared_license':m.get('License'),'license_expression':m.get('License-Expression'),'classifiers':classifiers,'license_files':licenses,'home_page':m.get('Home-page'),'metadata':metadata.relative_to(a.runtime).as_posix()})
report={'scope':'Runtime wheel metadata; requires review of embedded dependencies and model terms separately','packages':packages,'missing_license_files':[p['name'] for p in packages if not p['license_files']]}
a.output.parent.mkdir(parents=True,exist_ok=True)
a.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'packages':len(packages),'missing_license_files':report['missing_license_files']},ensure_ascii=False))
