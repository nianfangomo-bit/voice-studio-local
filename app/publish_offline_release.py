"""Upload the offline package as a reviewable GitHub release draft.

Uses Git's configured credential helper for the same GitHub account/repository.
Credentials stay in memory, are never printed or saved, and are sent only to
api.github.com / uploads.github.com. Run with the user's configured HTTPS proxy.
"""
import argparse
import hashlib
import json
import re
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO='nianfangomo-bit/voice-studio-local'


# 中文维护：核对所有分卷、版本和合并摘要，防止漏卷或混用不同版本。
def validate_package(directory, manifest):
    """Validate the full set before any authenticated network write."""
    version=manifest.get('version','')
    if not re.fullmatch(r'[0-9][A-Za-z0-9.\-]*',version):
        raise ValueError('Invalid package version')
    folder='VoiceStudio-'+version+'-win64-offline'
    if manifest.get('folder')!=folder or not 1<=len(manifest.get('parts',[]))<=200:
        raise ValueError('Invalid package manifest')
    whole=hashlib.sha256()
    for index,part in enumerate(manifest['parts'],1):
        if part.get('name')!=folder+'.zip.part%03d'%index:
            raise ValueError('Mixed versions or unordered parts')
        path=directory/part['name']
        if not path.is_file() or path.stat().st_size!=part['bytes'] or not 0<part['bytes']<=2*1024**3:
            raise ValueError('Missing or incomplete part: '+part['name'])
        digest=hashlib.sha256()
        with path.open('rb') as source:
            for chunk in iter(lambda:source.read(4*1024**2),b''):
                whole.update(chunk);digest.update(chunk)
        if digest.hexdigest()!=part.get('sha256'):
            raise ValueError('Part checksum mismatch: '+part['name'])
    if whole.hexdigest()!=manifest.get('archive_sha256'):
        raise ValueError('Archive checksum mismatch')
    for name in ('VoiceStudio-Offline-Setup.exe','SHA256SUMS.txt','INSTALL.txt'):
        if not (directory/name).is_file():raise ValueError('Missing release file: '+name)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    # 中文维护：拒绝自动重定向，避免启动令牌或发布凭据被转发。
    def redirect_request(self,*args,**kwargs):
        raise RuntimeError('Unexpected GitHub redirect; refusing to forward authentication')


# 中文维护：依据大小、上传状态和内容摘要判断是否可安全复用远端附件。
def validate_existing_asset(path, asset):
    if asset.get('state')!='uploaded' or asset.get('size')!=path.stat().st_size:
        raise RuntimeError('Existing asset is incomplete or has a different size: '+path.name)
    digest=hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda:source.read(4*1024**2),b''):digest.update(chunk)
    if asset.get('digest')!='sha256:'+digest.hexdigest():
        raise RuntimeError('Existing asset content is different or unverified; refusing to mix release builds: '+path.name)


class ProgressFile:
    # 中文维护：打开待上传文件并初始化限频进度记录。
    def __init__(self,path):
        self.path=path;self.stream=path.open('rb');self.size=path.stat().st_size;self.done=0;self.last=0
    # 中文维护：分块读取上传数据并报告实际字节进度。
    def read(self,size=-1):
        data=self.stream.read(max(size,1024**2));self.done+=len(data)
        now=time.time()
        if now-self.last>20 or not data:
            print(json.dumps({'asset':self.path.name,'uploaded_bytes':self.done,'total_bytes':self.size}),flush=True)
            self.last=now
        return data
    # 中文维护：释放上传文件句柄。
    def close(self):self.stream.close()


# 中文维护：校验本地产物并认证目标仓库，在明确参数控制下上传发行附件。
def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('directory',type=Path)
    parser.add_argument('--commit',required=True)
    parser.add_argument('--release-tag',help='Optional prerelease tag for this exact version; avoids mixing old drafts')
    parser.add_argument('--asset',action='append',help='Upload only named package assets; repeat for selected files')
    parser.add_argument('--retry-incomplete',action='store_true',help='Replace only selected unfinished assets from a failed upload')
    args=parser.parse_args()
    manifest=json.loads((args.directory/'offline-package.json').read_text(encoding='utf-8'))
    validate_package(args.directory,manifest)
    TAG=args.release_tag or 'v'+manifest['version']
    if not re.fullmatch('v'+re.escape(manifest['version'])+r'(?:-[0-9A-Za-z][0-9A-Za-z.\-]*)?',TAG):
        raise ValueError('Release tag must match the package version')
    env=dict(os.environ,GIT_TERMINAL_PROMPT='0',GCM_INTERACTIVE='Never')
    credential=subprocess.run(['git','-c','credential.interactive=never','credential','fill'],
        input='protocol=https\nhost=github.com\n\n',text=True,capture_output=True,env=env,check=True)
    auth=dict(line.split('=',1) for line in credential.stdout.splitlines() if '=' in line)
    token=auth.get('password')
    if not token:raise RuntimeError('GitHub authentication is not available')
    opener=urllib.request.build_opener(NoRedirect)

    # 中文维护：构造认证 API 请求，区分 JSON 与二进制载荷并限制重定向。
    def request(path,method='GET',data=None,content_type='application/json',size=None):
        url=path if path.startswith('https://') else 'https://api.github.com'+path
        if urllib.parse.urlparse(url).hostname not in {'api.github.com','uploads.github.com'}:
            raise ValueError('Unexpected destination')
        headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
                 'X-GitHub-Api-Version':'2022-11-28','User-Agent':'VoiceStudio-Offline-Release',
                 'Content-Type':content_type}
        if isinstance(data,(dict,list)):data=json.dumps(data).encode()
        if size is not None:headers['Content-Length']=str(size)
        req=urllib.request.Request(url,data=data,headers=headers,method=method)
        with opener.open(req,timeout=900) as response:
            return {} if response.status==204 else json.load(response)

    user=request('/user')
    if user['login'].lower()!=REPO.split('/')[0]:raise RuntimeError('Authenticated account differs from the repository owner')
    releases=request('/repos/'+REPO+'/releases?per_page=100')
    release=next((r for r in releases if r['tag_name']==TAG),None)
    if release and not release['draft']:raise RuntimeError('This version is already published; do not overwrite assets')
    if not release:
        release=request('/repos/'+REPO+'/releases','POST',{
            'tag_name':TAG,'target_commitish':args.commit,'name':'声线工作室 '+TAG+' · 完整离线安装包（预发布）',
            'draft':True,'prerelease':True,
            'body':'包含 Python、GPT-SoVITS v2Pro、训练和配音基础模型、本地中文转写模型、FFmpeg 及中文工作台。\n\n下载安装程序、offline-package.json 和全部 '+str(len(manifest['parts']))+' 个 part 分卷，放在同一目录，双击 VoiceStudio-Offline-Setup.exe。无需手动安装 Python 或模型。\n\n这是供仓库所有者验收的草稿；以此包对应的验收记录为准。干净 Windows 验收及第三方分发材料归档未全部完成前，不公开发布。'})
    (args.directory/'github-release-record.json').write_text(json.dumps({
        'id':release['id'],'url':release['html_url'],'draft':release['draft'],'tag':TAG},ensure_ascii=False,indent=2),encoding='utf-8')
    manifest=json.loads((args.directory/'offline-package.json').read_text(encoding='utf-8'))
    names=['VoiceStudio-Offline-Setup.exe','offline-package.json','SHA256SUMS.txt','INSTALL.txt']+[p['name'] for p in manifest['parts']]
    for optional in ('DownloadOffline.ps1','ACCEPTANCE.json'):
        if (args.directory/optional).is_file():names.append(optional)
    if args.asset:
        if any(name not in names for name in args.asset): raise ValueError('Unknown package asset')
        names=args.asset
    assets=request('/repos/'+REPO+'/releases/'+str(release['id'])+'/assets?per_page=100')
    existing={a['name']:a for a in assets}
    uploaded=[]
    for name in names:
        path=args.directory/name
        if not path.is_file():raise ValueError('Missing asset: '+name)
        if name in existing:
            if existing[name].get('state')!='uploaded' and args.retry_incomplete and args.asset:
                request('/repos/'+REPO+'/releases/assets/'+str(existing[name]['id']),'DELETE')
                del existing[name]
        if name in existing:
            validate_existing_asset(path,existing[name])
            print('Already uploaded: '+name,flush=True);uploaded.append(existing[name]);continue
        source=ProgressFile(path)
        try:
            url=release['upload_url'].split('{',1)[0]+'?name='+urllib.parse.quote(name)
            asset=request(url,'POST',source,'application/octet-stream',path.stat().st_size)
            validate_existing_asset(path,asset)
        finally:source.close()
        uploaded.append(asset)
        print(json.dumps({'asset_uploaded':name,'bytes':asset['size']}),flush=True)
    record_name='github-release-assets.json' if not args.asset else 'github-release-assets-'+args.asset[0]+'.json'
    (args.directory/record_name).write_text(json.dumps([
        {'name':a['name'],'bytes':a['size'],'url':a['browser_download_url']} for a in uploaded],ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'draft_release':release['html_url'],'assets':len(uploaded)}),flush=True)


if __name__=='__main__':main()
