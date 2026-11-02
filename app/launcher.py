"""Prefer the colocated runtime; external-engine chooser remains a fallback."""
import json
import subprocess
import sys
import webbrowser
import urllib.request
import urllib.parse
import socket
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def main():
    root = tk.Tk()
    root.withdraw()
    # Reopen the existing authenticated session instead of starting a second service.
    try:
        session = json.loads((ROOT / 'data/session.json').read_text(encoding='utf-8'))
        url = urllib.parse.urlparse(session['url'])
        if url.scheme == 'http' and url.hostname == '127.0.0.1':
            origin = f'http://127.0.0.1:{url.port}'
            req = urllib.request.Request(origin + '/connect', data=b'',
                headers={'Origin': origin, 'x-start-token': url.fragment}, method='POST')
            with urllib.request.urlopen(req, timeout=2) as response:
                if response.status == 200:
                    webbrowser.open(session['url'])
                    return
    except (OSError, ValueError, KeyError):
        pass
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
    with log.open('a', encoding='utf-8') as stream:
        subprocess.Popen([str(runtime), '-B', str(ROOT / 'server.py'), '--port', str(port)], cwd=ROOT,
                         stdout=stream, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
    messagebox.showinfo('声线工作室', '正在打开本机工作台。\n如果页面没有打开，请检查 data/server.log。\n关闭浏览器不会停止正在运行的任务。')


if __name__ == '__main__':
    main()
