import ast
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class StartupSafetyTests(unittest.TestCase):
    def test_launcher_does_not_recover_database(self):
        tree=ast.parse((Path(server.__file__).parent/'launcher.py').read_text(encoding='utf-8'))
        calls=[n.func.id for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)]
        self.assertNotIn('Studio',calls)

    @unittest.skipUnless(os.name=='nt','Windows service lock')
    def test_busy_service_exits_before_opening_database(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(server,'ROOT',Path(temp)), patch.object(sys,'argv',['server.py','--no-open']), \
                 patch('msvcrt.locking',side_effect=OSError('locked')), patch.object(server,'Studio') as studio:
                self.assertEqual(server.main(),1)
                studio.assert_not_called()


if __name__=='__main__':unittest.main()
