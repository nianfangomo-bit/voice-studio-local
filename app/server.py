from __future__ import annotations
import argparse
import atexit
import json
import os
import secrets
import sys
import webbrowser
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
import uvicorn
from studio import Studio, ROOT, atomic_json


# 中文维护：构建仅供本机工作台使用的路由集合，不启动工作线程或公开调试文档。
def make_app(studio, token, port):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    origin = f'http://127.0.0.1:{port}'
    host = f'127.0.0.1:{port}'

    # 中文维护：统一校验 Host、Origin 和会话令牌，附加禁缓存与浏览器安全响应头。
    @app.middleware('http')
    async def security(request: Request, call_next):
        if request.headers.get('host') != host:
            return JSONResponse({'error': '主机名不允许'}, status_code=403)
        if request.headers.get('origin') not in (None, origin):
            return JSONResponse({'error': '仅允许本机软件页面访问'}, status_code=403)
        if request.method not in ('GET', 'HEAD') and request.headers.get('origin') != origin:
            return JSONResponse({'error': '请求来源校验失败'}, status_code=403)
        if request.url.path.startswith('/api/'):
            if not secrets.compare_digest(request.cookies.get('voice_session', ''), token):
                return JSONResponse({'error': '会话已失效，请使用启动器重新打开。'}, status_code=401)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'"
        return response

    # 中文维护：将业务校验失败转换为可理解的 400 响应。
    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse({'error': str(exc)}, status_code=400)

    # 中文维护：隐藏内部字段细节，提示用户补齐输入。
    @app.exception_handler(KeyError)
    async def key_error(request, exc):
        return JSONResponse({'error': '输入不完整，请检查表单。'}, status_code=400)

    # 中文维护：在本机日志保留异常细节，向界面返回不含堆栈的恢复提示。
    @app.exception_handler(Exception)
    async def general_error(request, exc):
        import traceback
        traceback.print_exc()
        return JSONResponse({'error': '操作失败；已有文件保留。请检查文件权限、格式和运行日志。'}, status_code=500)

    # 中文维护：返回随应用打包的工作台入口。
    @app.get('/')
    def index():
        return FileResponse(ROOT / 'static/index.html')

    # 中文维护：仅提供白名单脚本与样式，防止读取源码或私有文件。
    @app.get('/static/{name}')
    def static(name: str):
        if name not in ('app.js', 'style.css'):
            return Response(status_code=404)
        return FileResponse(ROOT / 'static' / name)

    # 中文维护：核验启动令牌并创建 HttpOnly 会话，不是无认证的调试登录。
    @app.post('/connect')
    def connect(request: Request):
        if not secrets.compare_digest(request.headers.get('x-start-token', ''), token):
            return JSONResponse({'error': '启动凭据无效，请重新启动软件。'}, status_code=401)
        r = JSONResponse({'ok': True})
        r.set_cookie('voice_session', token, httponly=True, samesite='strict', path='/')
        return r

    # 中文维护：读取工作台状态，供页面轮询任务与作品。
    @app.get('/api/state')
    def state():
        return studio.snapshot()

    # 中文维护：保存经过用户信任确认的本机引擎配置。
    @app.post('/api/settings')
    def settings(data: dict):
        return studio.configure(data)

    # 中文维护：提供用户点击触发的环境诊断，不是开发测试接口。
    @app.get('/api/check')
    def check():
        return studio.check()

    # 中文维护：导入用户明确授权且可信的模型组合与参考录音。
    @app.post('/api/voices')
    def voices(data: dict):
        return studio.import_voice(data)

    # 中文维护：要求用户确认试听，并由业务层核实已生成未见文本后发布候选声线。
    @app.post('/api/voices/{vid}/publish')
    def publish(vid: str, data: dict):
        if data.get('listened') is not True:
            raise ValueError('请试听新文案后确认发布。')
        from training import publish
        return publish(studio, vid)

    # 中文维护：导入既有标注清单，供高级素材入口使用。
    @app.post('/api/datasets')
    def dataset_import(data: dict):
        from training import import_dataset
        return import_dataset(studio, data)

    # 中文维护：创建保留原始录音的素材库。
    @app.post('/api/recording-datasets')
    def recording_dataset(data: dict):
        from preparation import create
        return create(studio, data)

    # 中文维护：按大小限制流式保存录音；失败只清理本请求的未完成临时文件。
    @app.post('/api/datasets/{did}/recordings')
    async def recording_upload(did: str, request: Request, filename: str):
        from preparation import upload_target, register_upload, MAX_UPLOAD
        target, name = upload_target(studio, did, filename)
        if int(request.headers.get('content-length', '0')) > MAX_UPLOAD:
            raise ValueError('单个录音不能超过 512 MB，请先拆分。')
        partial = target.with_suffix(target.suffix + '.partial')
        size = 0
        created = False
        try:
            with partial.open('xb') as stream:
                created = True
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_UPLOAD:
                        raise ValueError('单个录音超过 512 MB，未加入素材库。')
                    stream.write(chunk)
            if not size:
                raise ValueError('录音文件为空，请重新选择。')
            partial.replace(target)
        finally:
            # Only remove this request's unfinished temporary file, never recordings.
            if created and partial.exists():
                partial.unlink()
        return register_upload(studio, did, target, name)

    # 中文维护：将素材库的离线切分转写任务入队。
    @app.post('/api/datasets/{did}/prepare')
    def recording_prepare(did: str):
        from preparation import enqueue
        return enqueue(studio, did)

    # 中文维护：保存片段校对和训练参数，并更新素材修订号。
    @app.post('/api/datasets/{did}')
    def dataset_update(did: str, data: dict):
        from training import update_dataset
        return update_dataset(studio, did, data)

    # 中文维护：读取真实素材与依赖状态，生成训练前确认计划。
    @app.get('/api/datasets/{did}/plan')
    def dataset_plan(did: str):
        from training import plan
        return plan(studio, did)

    # 中文维护：校验已确认的计划修订号后排队真实训练。
    @app.post('/api/datasets/{did}/train')
    def dataset_train(did: str, data: dict):
        from training import enqueue
        return enqueue(studio, did, data)

    # 中文维护：按数据集和片段标识提供试听，只允许读取项目库内的音频。
    @app.get('/api/datasets/{did}/audio/{rid}')
    def dataset_audio(did: str, rid: str):
        ds = studio.get('datasets', did)
        row = next((r for r in ds['rows'] if r['id'] == rid), None)
        if not row:
            raise ValueError('录音片段不存在')
        path = Path(row['audio']).resolve()
        if studio.root not in path.parents:
            raise ValueError('音频路径不属于当前项目库')
        return FileResponse(path, media_type='audio/wav')

    # 中文维护：创建保留原文、声线快照和分段文本的配音项目。
    @app.post('/api/projects')
    def create(data: dict):
        return studio.create_project(data)

    # 中文维护：保存指定片段修改，不立即触发整篇配音。
    @app.post('/api/projects/{pid}/segments/{sid}')
    def edit(pid: str, sid: str, data: dict):
        return studio.edit_segment(pid, sid, data)

    # 中文维护：更新语速及停顿，由业务层检查任务状态和参数范围。
    @app.post('/api/projects/{pid}/options')
    def options(pid: str, data: dict):
        return studio.options(pid, data)

    # 中文维护：提交首句、全文或指定片段生成请求，不在 HTTP 请求中长时间占用显卡。
    @app.post('/api/projects/{pid}/generate')
    def generate(pid: str, data: dict):
        return studio.enqueue(pid, data.get('mode', 'all'), data.get('segment_id'))

    # 中文维护：仅分派取消或重试两种任务操作，拒绝其他动作。
    @app.post('/api/jobs/{jid}/{action}')
    def job(jid: str, action: str):
        if action == 'cancel':
            return studio.cancel(jid)
        if action == 'retry':
            return studio.retry(jid)
        raise ValueError('未知操作')

    # 中文维护：读取已登记任务的有限日志尾部，供错误诊断，不接受任意文件路径。
    @app.get('/api/jobs/{jid}/log')
    def log(jid: str):
        studio.get('jobs', jid)
        folder = studio.root / 'jobs' / jid
        paths = [folder / 'worker.log'] + sorted(folder.glob('stage-*.log'))[-1:]
        chunks = []
        for path in paths:
            if path.exists():
                with path.open('rb') as stream:
                    stream.seek(max(0, path.stat().st_size - 18000))
                    raw = stream.read()
                try:
                    text = raw.decode('utf-8')
                except UnicodeDecodeError:
                    text = raw.decode('gb18030', errors='replace')
                chunks.append(path.name + '\n' + text)
        return {'text': '\n\n'.join(chunks) if chunks else '尚未启动工作进程。'}

    # 中文维护：按已登记片段返回项目库内音频，拒绝库外路径。
    @app.get('/api/projects/{pid}/segments/{sid}/audio')
    def audio(pid: str, sid: str):
        p = studio.get('projects', pid)
        seg = next((s for s in p['segments'] if s['id'] == sid), None)
        if not seg or not seg.get('audio'):
            raise ValueError('片段尚未生成')
        path = Path(seg['audio']['path']).resolve()
        if studio.root not in path.parents:
            raise ValueError('音频路径不属于当前项目库')
        return FileResponse(path, media_type='audio/wav')

    # 中文维护：通过版本和文件白名单下载音频、字幕或交付包。
    @app.get('/api/projects/{pid}/exports/{eid}/{filename}')
    def download(pid: str, eid: str, filename: str):
        path = studio.file(pid, eid, filename)
        return FileResponse(path, filename=None if filename == 'narration.wav' else filename)

    return app


# 中文维护：获取单实例锁后初始化存储与任务服务，仅监听回环地址并生成启动会话。
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8791)
    parser.add_argument('--no-open', action='store_true')
    args = parser.parse_args()
    # Single service per project store, including different ports.
    data_root = ROOT / 'data'
    data_root.mkdir(exist_ok=True)
    lock = open(data_root / 'service.lock', 'a+b')
    lock.seek(0)
    if os.name == 'nt':
        import msvcrt
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            lock.close()
            print('软件已在运行；请打开原窗口。')
            return 1
    atexit.register(lock.close)
    s = Studio()
    bundled = ROOT / 'engine' / 'runtime' / 'python.exe'
    if bundled.is_file():
        existing = s.settings()
        if not existing or not Path(existing.get('runtime', '')).is_file():
            engine = bundled.parents[1]
            s.configure({'trusted': True, 'engine': str(engine), 'legacy': str(ROOT / 'legacy'),
                         'ffmpeg': str(engine / 'runtime' / 'ffmpeg.exe')})
    token = secrets.token_urlsafe(32)
    url = f'http://127.0.0.1:{args.port}/#{token}'
    atomic_json(s.root / 'session.json', {'url': url, 'pid': os.getpid()})
    s.start()
    atexit.register(s.stopping.set)
    if not args.no_open:
        import threading
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(make_app(s, token, args.port), host='127.0.0.1', port=args.port, access_log=False)
    s.stopping.set()
    s.wake.set()
    if s.thread:
        s.thread.join(25)
    lock.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
