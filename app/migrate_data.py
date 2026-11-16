"""离线复制迁移：不改源数据、不合并目标，不跟随目录联接。"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import uuid


def sha256(path):
    """流式核对复制结果，避免大模型占满内存。"""
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def inventory(source):
    """拒绝符号链接及 Windows reparse point，绝不递归进入引擎联接。"""
    files = []
    for directory, dirs, names in os.walk(source, followlinks=False):
        for name in list(dirs) + names:
            path = Path(directory) / name
            info = path.lstat()
            if path.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
                relative = path.relative_to(source).parts
                # 训练沙箱只读映射上游代码；重试会重建，不复制整个引擎。
                if name in dirs and len(relative) == 4 and relative[0] == 'jobs' and relative[2] == 'work' and relative[3] in ('GPT_SoVITS', 'tools'):
                    dirs.remove(name)
                    continue
                raise ValueError('发现目录联接或链接，请先确认真实数据位置：' + str(path))
            if stat.S_ISREG(info.st_mode):
                files.append(path)
    return files


def remap(value, source, destination):
    """仅重写指向源数据目录的完整路径；不做文案子串替换。"""
    if isinstance(value, dict):
        return {key: remap(item, source, destination) for key, item in value.items()}
    if isinstance(value, list):
        return [remap(item, source, destination) for item in value]
    if isinstance(value, str):
        try:
            path = Path(value)
            if path.is_absolute():
                relative = path.relative_to(source)
                if '..' not in relative.parts:
                    return str(destination / relative)
        except (ValueError, OSError):
            pass
    return value


def migrate(source, destination, confirmed_stopped=False):
    """停止软件后复制到新目录，校验成功才改名提交，失败保留暂存目录。"""
    if not confirmed_stopped:
        raise ValueError('请关闭源工作台及训练进程，再确认迁移。')
    raw_source = Path(source).absolute()
    if raw_source.is_symlink() or getattr(raw_source.lstat(), 'st_file_attributes', 0) & 0x400:
        raise ValueError('源数据目录不能是目录联接。')
    source, destination = raw_source.resolve(), Path(destination).resolve()
    if destination.exists() or source == destination or source in destination.parents or destination in source.parents:
        raise ValueError('目标必须是不存在的独立新目录，不能覆盖或嵌套源数据。')
    database = source / 'studio.sqlite'
    if not database.is_file():
        raise ValueError('源目录中没有 studio.sqlite。请选择旧软件的 data 目录。')
    lock = (source / 'service.lock').open('a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise ValueError('源工作台仍在运行，请先退出；没有迁移任何数据。') from exc
        files = inventory(source)
        required = sum(path.stat().st_size for path in files)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(destination.parent).free < required + 64 * 1024 * 1024:
            raise ValueError('目标磁盘空间不足；源数据保持不变。')
        stage = destination.with_name(destination.name + '.migrating-' + uuid.uuid4().hex)
        stage.mkdir()
        ignored = {'studio.sqlite', 'studio.sqlite-wal', 'studio.sqlite-shm', 'service.lock', 'session.json'}
        copied = 0
        for path in files:
            relative = path.relative_to(source)
            if len(relative.parts) == 1 and relative.name in ignored:
                continue
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            before = sha256(path)
            shutil.copy2(path, target)
            if before != sha256(target) or before != sha256(path):
                raise ValueError('复制校验失败；源数据保留，暂存目录：' + str(stage))
            if target.suffix.lower() == '.json':
                try:
                    original = json.loads(target.read_text(encoding='utf-8'))
                    updated = remap(original, source, destination)
                    if updated != original:
                        target.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding='utf-8')
                except (UnicodeError, json.JSONDecodeError):
                    pass
            elif target.suffix.lower() == '.list':
                lines = target.read_text(encoding='utf-8').splitlines()
                target.write_text('\n'.join('|'.join([remap(parts[0], source, destination)] + parts[1:])
                    for parts in (line.split('|') for line in lines)) + '\n', encoding='utf-8')
            copied += 1
        with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as old:
            with closing(sqlite3.connect(stage / 'studio.sqlite')) as new:
                old.backup(new)
                for kind, ident, body in new.execute('SELECT kind,id,body FROM records').fetchall():
                    value = remap(json.loads(body), source, destination)
                    if kind == 'jobs' and value.get('state') in ('running', 'queued', 'cancelling'):
                        value.update(state='interrupted', message='迁移前任务未完成；已有成果保留，请检查后重试。')
                    new.execute('UPDATE records SET body=? WHERE kind=? AND id=?',
                                (json.dumps(value, ensure_ascii=False), kind, ident))
                if new.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('数据库检查失败；源数据保留。')
                new.commit()
        report = {'schema': 'voice-data-migration-v1', 'copied_files': copied,
                  'source_preserved': True, 'source': str(source), 'destination': str(destination),
                  'limitations': ['已知训练沙箱 jobs/*/work/GPT_SoVITS 和 tools 联接不复制，重试时重建。',
                                  '外部引擎、外部导入模型及其他目录的文件不搬迁；删除旧目录前仍需复用验收。',
                                  '迁移报告含本机路径，仅供本地使用，不要公开上传。']}
        (stage / 'migration-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        stage.rename(destination)
        return report
    finally:
        lock.close()


def main():
    """供维护人员或安装助手调用，不执行任意命令、不下载环境。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source')
    parser.add_argument('--destination')
    parser.add_argument('--wizard', action='store_true')
    parser.add_argument('--confirmed-stopped', action='store_true')
    args = parser.parse_args()
    if args.wizard:
        wizard()
        return
    if not args.source or not args.destination:
        parser.error('需要 --source 和 --destination，或使用 --wizard')
    print(json.dumps(migrate(args.source, args.destination, args.confirmed_stopped), ensure_ascii=False, indent=2))


def wizard():
    """提供停止后复制迁移入口；耗时复制在后台执行，窗口不显示虚假百分比。"""
    import queue
    import threading
    import tkinter as tk
    from tkinter import filedialog, messagebox
    root = tk.Tk()
    root.withdraw()
    source = filedialog.askdirectory(title='选择旧软件的 data 目录（原数据会保留）')
    if not source:
        root.destroy()
        return
    folder = filedialog.askdirectory(title='选择尚未启动过的新版 app 目录（将创建 data 子目录）')
    if not folder:
        root.destroy()
        return
    destination = Path(folder) / 'data'
    if not messagebox.askyesno('迁移确认', '请关闭新旧工作台及训练进程。\n迁移保留旧数据，不覆盖现有目标目录。\n外部引擎和外部模型不会搬迁。\n\n目标：' + str(destination) + '\n\n已停止，开始复制？'):
        root.destroy()
        return
    result = queue.Queue()
    root.title('正在复制并校验数据，请勿关闭')
    tk.Label(root, text='正在复制、核对文件和迁移数据库。\n耗时取决于数据大小，完成后会提示。', padx=25, pady=25).pack()
    root.protocol('WM_DELETE_WINDOW', lambda: messagebox.showinfo('正在迁移', '请等待完成；强制中断会留下暂存目录，原数据不删除。'))
    root.deiconify()

    def work():
        """复制线程只返回结果，不跨线程调用界面。"""
        try:
            migrate(source, destination, True)
            result.put((True, '迁移完成。请启动新版并检查素材、声线和作品。\n旧目录暂时不要删除；外部模型仍需原位置。'))
        except Exception as exc:
            result.put((False, '迁移未完成，原数据保留。\n' + str(exc)))

    def poll():
        """主线程显示真实完成或失败状态。"""
        try:
            ok, message = result.get_nowait()
        except queue.Empty:
            root.after(200, poll)
            return
        (messagebox.showinfo if ok else messagebox.showerror)('迁移结果', message)
        root.destroy()

    threading.Thread(target=work, daemon=True).start()
    root.after(200, poll)
    root.mainloop()


if __name__ == '__main__':
    main()
