import copy
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from publish_offline_release import validate_package,validate_existing_asset

class ReleaseValidationTests(unittest.TestCase):
    # 中文维护：创建本测试独占的临时数据与夹具，不使用用户模型或作品。
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        folder='VoiceStudio-1.0.1-win64-offline'
        name=folder+'.zip.part001';raw=b'fixture';digest=hashlib.sha256(raw).hexdigest()
        (self.root/name).write_bytes(raw)
        self.manifest={'version':'1.0.1','folder':folder,'archive_sha256':digest,
                       'parts':[{'name':name,'bytes':len(raw),'sha256':digest}]}
        for name in ('VoiceStudio-Offline-Setup.exe','SHA256SUMS.txt','INSTALL.txt'):
            (self.root/name).write_text('fixture',encoding='utf-8')
    # 中文维护：释放测试资源并清理本测试创建的临时目录。
    def tearDown(self):self.temp.cleanup()
    # 中文维护：确认完整、匹配的分卷清单通过发布前检查。
    def test_complete(self):validate_package(self.root,self.manifest)
    # 中文维护：确认损坏分卷不能通过校验。
    def test_corruption(self):
        (self.root/self.manifest['parts'][0]['name']).write_bytes(b'changed')
        with self.assertRaises(ValueError):validate_package(self.root,self.manifest)
    # 中文维护：确认混合版本清单被拒绝。
    def test_mixed_version(self):
        m=copy.deepcopy(self.manifest);m['version']='2.0.0'
        with self.assertRaises(ValueError):validate_package(self.root,m)
    # 中文维护：确认整包摘要不一致时停止发布。
    def test_bad_combined_digest(self):
        m=copy.deepcopy(self.manifest);m['archive_sha256']='0'*64
        with self.assertRaises(ValueError):validate_package(self.root,m)
    # 中文维护：确认缺少必要安装说明时不能发布。
    def test_missing_instructions(self):
        (self.root/'INSTALL.txt').unlink()
        with self.assertRaises(ValueError):validate_package(self.root,self.manifest)
    # 中文维护：确认同大小但不同内容的远端附件不能复用。
    def test_existing_same_size_wrong_content_rejected(self):
        part=self.manifest['parts'][0];path=self.root/part['name']
        with self.assertRaises(RuntimeError):
            validate_existing_asset(path,{'state':'uploaded','size':part['bytes'],'digest':'sha256:'+'0'*64})
    # 中文维护：确认缺少摘要的附件不视为校验通过。
    def test_existing_missing_digest_rejected(self):
        part=self.manifest['parts'][0]
        with self.assertRaises(RuntimeError):
            validate_existing_asset(self.root/part['name'],{'state':'uploaded','size':part['bytes']})
    # 中文维护：确认状态、大小与摘要均匹配的附件可复用。
    def test_existing_verified_content_accepted(self):
        part=self.manifest['parts'][0]
        validate_existing_asset(self.root/part['name'],{'state':'uploaded','size':part['bytes'],'digest':'sha256:'+part['sha256']})

if __name__=='__main__':unittest.main()
