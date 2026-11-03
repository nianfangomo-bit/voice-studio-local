"""Prefer the colocated runtime; external-engine chooser remains a fallback."""
import json
import subprocess
import sys
import webbrowser
import urllib.request
import urllib.parse
import socket
import time
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def existing_session():
    """Only authenticate locally; never forward the startup secret to a proxy."""
    try:
        session = json.loads((ROOT / 'data/session.json').read_text(encoding='utf-8'))
        url = urllib.parse.urlparse(session['url'])
        if url.scheme != 'http' or url.hostname != '127.0.0.1' or not url.port or not url.fragment:
            return None
        origin = f'http://127.0.0.1:{url.port}'
        req = urllib.request.Request(origin + '/connect', data=b'',
            headers={'Origin': origin, 'x-start-token': url.fragment}, method='POST')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(req, timeout=1) as response:
            if response.status == 200:
                return session['url']
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def wait_for_service(process, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        url = existing_session()
        if url:
            return url, None
        if process.poll() is not None:
            return None, '启动进程已退出。请运行“环境检查”，确认文件完整、磁盘可写和运行库正常。已有录音、声线和作品保留。'
        time.sleep(.3)
    return None, '工作台尚未就绪，可能仍在加载。请稍后重新双击启动；不要重复安装。已有内容保留。'


def main():
    root = tk.Tk()
    root.withdraw()
    # Reopen the existing authenticated session instead of starting a second service.
    url = existing_session()
    if url:
        webbrowser.open(url)
        return
    bootstrap = ROOT / 'data' / 'bootstrap.json'
    bundled = ROOT / 'engine' / 'runtime' / 'python.exe'
    if bundled.is_file():
        # Initialization/recovery belongs to the service, after its exclusive lock.
        # Merely reopening the launcher must never mark active jobs interrupted.
        bootstrap.parent.mkdir(parents=True, exist_ok=True)
        bootstrap.write_text(json.dumps({'runtime': str(bundled)}), encoding='utf-8')
    try:
        runtime = Path(json.loads(bootstrap.read_text(encoding='utf-8'))['runtime'])
    except (OSError, KeyError, ValueError):
        folder = filedialog.askdirectory(title='选择可信 GPT-SoVITS 整合包目录（包含 runtime 文件夹）')
        if not folder:
            return
        runtime = Path(folder) / 'runtime/python.exe'
        if not runtime.is_file():
            messagebox.showerror('环境未找到', '目录中没有 runtime/python.exe。未安装或修改任何依赖。')
            return
        if not messagebox.askyesno('确认运行环境', '仅选择可信来源的 GPT-SoVITS 整合包。是否用这个环境启动本机软件？'):
            return
        bootstrap.parent.mkdir(parents=True, exist_ok=True)
        bootstrap.write_text(json.dumps({'runtime': str(runtime)}), encoding='utf-8')
    log = ROOT / 'data' / 'server.log'
    port = None
    for candidate in range(8791, 8821):
        with socket.socket() as probe:
            try:
                probe.bind(('127.0.0.1', candidate))
                port = candidate
                break
            except OSError:
                pass
    if port is None:
        messagebox.showerror('无法启动', '本机可用端口不足。请关闭其他工作台实例后重试，已有成果未改动。')
        return
    try:
        with log.open('a', encoding='utf-8') as stream:
            process = subprocess.Popen([str(runtime), '-B', str(ROOT / 'server.py'), '--port', str(port), '--no-open'], cwd=ROOT,
                             stdout=stream, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
    except OSError:
        messagebox.showerror('无法启动', '运行环境无法启动或目录不可写。请运行“环境检查”，确认完整解压。已有内容未删除。')
        return
    url, error = wait_for_service(process)
    if url:
        webbrowser.open(url)
    else:
        messagebox.showerror('工作台未就绪', error + '\n诊断日志：' + str(log))


if __name__ == '__main__':
    main()
