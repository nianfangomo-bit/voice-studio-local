"""在临时应用副本启动真实服务，验证认证和检测接口，不访问用户数据。"""
import http.cookiejar
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


def main():
    """只管理本测试启动的服务，退出后清理自己的临时目录。"""
    source = Path(__file__).resolve().parents[1] / 'app'
    with tempfile.TemporaryDirectory(prefix='voice-app-smoke-') as temp:
        root = Path(temp)
        for item in source.iterdir():
            if item.is_file() and item.suffix in ('.py', '.exe', '.json'):
                shutil.copy2(item, root / item.name)
        shutil.copytree(source / 'static', root / 'static')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        origin = 'http://127.0.0.1:' + str(port)
        with (root / 'smoke.log').open('wb') as log:
            proc = subprocess.Popen([sys.executable, '-I', '-X', 'utf8', '-B', str(root / 'server.py'), '--port', str(port), '--no-open'],
                                    cwd=root, stdout=log, stderr=subprocess.STDOUT,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    if proc.poll() is not None:
                        raise RuntimeError('隔离服务提前退出')
                    try:
                        session = json.loads((root / 'data/session.json').read_text(encoding='utf-8'))
                        token = session['url'].split('#', 1)[1]
                        with opener.open(urllib.request.Request(origin+'/connect',data=b'',headers={'Origin':origin,'x-start-token':token}),timeout=2) as response:
                            assert response.status == 200
                        break
                    except (OSError, ValueError, KeyError):
                        time.sleep(.2)
                else:
                    raise RuntimeError('隔离服务启动超时')
                with opener.open(origin+'/api/state', timeout=5) as response:
                    state=json.load(response)
                    assert state['settings']=={} and state['projects']==[] and state['environment_history']==[]
                with opener.open(origin+'/api/hardware-advice', timeout=25) as response:
                    report=json.load(response)
                    assert report['schema']=='voice-hardware-report-v1'
                with opener.open(origin+'/static/app.js', timeout=5) as response:
                    assert len(response.read())>1000
                print(json.dumps({'service':'passed','authenticated_state':'passed','hardware_endpoint':'passed','static_bundle':'passed','user_data_used':False}))
            finally:
                proc.terminate()
                try: proc.wait(timeout=10)
                except subprocess.TimeoutExpired: proc.kill();proc.wait(timeout=5)


if __name__ == '__main__':
    main()
