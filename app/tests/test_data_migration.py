"""迁移测试仅使用临时数据，不读取个人模型或录音。"""
import json
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from migrate_data import migrate, remap


class MigrationTests(unittest.TestCase):
    def setUp(self):
        """建立包含真实 SQLite 和样本文件的旧目录。"""
        self.temp = tempfile.TemporaryDirectory()
        self.source = Path(self.temp.name) / 'old data'
        self.target = Path(self.temp.name) / '新数据'
        self.source.mkdir()
        (self.source / 'audio.wav').write_bytes(b'sample-content')
        self.body = {'id': 'one', 'state': 'running', 'audio': str(self.source / 'audio.wav'),
                     'spoken': '原文不能更改', 'external': str(Path(self.temp.name) / 'external.pth')}
        with closing(sqlite3.connect(self.source / 'studio.sqlite')) as db:
            db.execute('CREATE TABLE records (kind TEXT,id TEXT,body TEXT,PRIMARY KEY(kind,id))')
            db.execute('INSERT INTO records VALUES (?,?,?)', ('jobs', 'one', json.dumps(self.body)))
            db.commit()
        (self.source / 'manifest.json').write_text(json.dumps(self.body), encoding='utf-8')
        (self.source / 'sample.list').write_text(str(self.source / 'audio.wav') + '|speaker|zh|原文', encoding='utf-8')

    def tearDown(self):
        """清理本测试拥有的目录。"""
        self.temp.cleanup()

    def test_copy_preserves_source_and_rewrites_references(self):
        """迁移不改源数据库，目标可脱离源目录读取内部音频。"""
        migrate(self.source, self.target, True)
        with closing(sqlite3.connect(self.source / 'studio.sqlite')) as db:
            self.assertEqual(json.loads(db.execute('SELECT body FROM records').fetchone()[0]), self.body)
        with closing(sqlite3.connect(self.target / 'studio.sqlite')) as db:
            value = json.loads(db.execute('SELECT body FROM records').fetchone()[0])
        self.assertEqual(value['state'], 'interrupted')
        self.assertEqual(value['external'], self.body['external'])
        self.source.rename(self.source.with_name('source-unavailable'))
        self.assertEqual(Path(value['audio']).read_bytes(), b'sample-content')
        self.assertTrue((self.target / 'sample.list').read_text(encoding='utf-8').startswith(str(self.target)))

    def test_reject_existing_or_nested_target(self):
        """拒绝覆盖及源内递归复制。"""
        for target in (self.source, self.source / 'nested'):
            with self.assertRaises(ValueError):
                migrate(self.source, target, True)

    def test_requires_stop_confirmation(self):
        """没有明确停止确认不能开始迁移。"""
        with self.assertRaises(ValueError):
            migrate(self.source, self.target)
        self.assertFalse(self.target.exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows service lock')
    def test_reject_running_service(self):
        """复用正式服务锁，避免边训练边复制。"""
        import msvcrt
        with (self.source / 'service.lock').open('a+b') as lock:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            with self.assertRaisesRegex(ValueError, '仍在运行'):
                migrate(self.source, self.target, True)

    def test_does_not_rewrite_prose_or_neighbor(self):
        """只处理完整目录边界，不误改文字及相邻目录。"""
        for value in ('说明：' + str(self.source), str(self.source) + '-other/file.wav'):
            self.assertEqual(remap(value, self.source, self.target), value)

    @unittest.skipUnless(os.name == 'nt', 'Windows junction')
    def test_training_junction_not_copied(self):
        """真实创建训练沙箱联接，迁移不能递归复制外部引擎。"""
        external = Path(self.temp.name) / 'external-engine'
        external.mkdir()
        (external / 'engine-marker').write_bytes(b'not-user-data')
        work = self.source / 'jobs' / 'fixture' / 'work'
        work.mkdir(parents=True)
        link = work / 'GPT_SoVITS'
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(external)], check=True, capture_output=True)
        try:
            migrate(self.source, self.target, True)
            self.assertFalse((self.target / 'jobs/fixture/work/GPT_SoVITS').exists())
            self.assertTrue((external / 'engine-marker').exists())
        finally:
            # 仅移除本测试创建的联接本身，不递归删除目标。
            link.rmdir()
