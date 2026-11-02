import copy
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from publish_offline_release import validate_package

class ReleaseValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        folder='VoiceStudio-1.0.1-win64-offline'
        name=folder+'.zip.part001';raw=b'fixture';digest=hashlib.sha256(raw).hexdigest()
        (self.root/name).write_bytes(raw)
        self.manifest={'version':'1.0.1','folder':folder,'archive_sha256':digest,
                       'parts':[{'name':name,'bytes':len(raw),'sha256':digest}]}
        for name in ('VoiceStudio-Offline-Setup.exe','SHA256SUMS.txt','安装说明.txt'):
            (self.root/name).write_text('fixture',encoding='utf-8')
    def tearDown(self):self.temp.cleanup()
    def test_complete(self):validate_package(self.root,self.manifest)
    def test_corruption(self):
        (self.root/self.manifest['parts'][0]['name']).write_bytes(b'changed')
        with self.assertRaises(ValueError):validate_package(self.root,self.manifest)
    def test_mixed_version(self):
        m=copy.deepcopy(self.manifest);m['version']='2.0.0'
        with self.assertRaises(ValueError):validate_package(self.root,m)
    def test_bad_combined_digest(self):
        m=copy.deepcopy(self.manifest);m['archive_sha256']='0'*64
        with self.assertRaises(ValueError):validate_package(self.root,m)
    def test_missing_instructions(self):
        (self.root/'安装说明.txt').unlink()
        with self.assertRaises(ValueError):validate_package(self.root,self.manifest)

if __name__=='__main__':unittest.main()
