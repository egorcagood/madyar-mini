"""Offline regression tests; never contact hardware or a network."""
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PublicBuildTests(unittest.TestCase):
    def test_provision_keeps_key_private_stable_and_unique(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            roots = [Path(folder) / name for name in ('one', 'two')]
            keys = []
            host = '.'.join(map(str, (10, 0, 0, 2)))  # synthetic test input
            for root in roots:
                command = [sys.executable, str(ROOT/'tools/provision.py'), '--root', str(root), '--host', host]
                result = subprocess.run(command, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                config = json.loads((root/'private/device.json').read_text())
                key = config['key']
                self.assertEqual(config['host'], host)
                self.assertRegex(key, r'^[0-9a-f]{48}$')
                self.assertNotIn(key, result.stdout + result.stderr)
                self.assertIn(key, (root/'include/private_config.h').read_text())
                again = subprocess.run(command, capture_output=True, text=True)
                self.assertEqual(again.returncode, 0, again.stderr)
                self.assertEqual(json.loads((root/'private/device.json').read_text())['key'], key)
                keys.append(key)
            self.assertNotEqual(*keys)

    def test_provision_requires_explicit_lan_host(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            root = Path(folder)/'unused'
            result = subprocess.run([sys.executable, str(ROOT/'tools/provision.py'), '--root', str(root)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(root.exists())

    def test_manifest_has_font_upload_contract_and_ignores_stray_files(self):
        script = ROOT/'tools/build_assets.py'
        self.assertTrue(script.is_file(), 'Public asset builder missing')
        functions = runpy.run_path(str(script))
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            root = Path(folder)
            (root/'data').mkdir()
            (root/'include').mkdir()
            for name in ('working.gif', 'warning.gif', 'done.gif', 'm.rle', 'font.bin'):
                (root/'data'/name).write_bytes(b'fixture')
            (root/'data/unrelated-private-file').write_bytes(b'never-package')
            functions['generate_manifest'](root)
            header = (root/'include/resource_manifest.h').read_text()
            self.assertIn('FONT_BYTES=7', header)
            self.assertIn(hashlib.sha256(b'fixture').hexdigest(), header)
            self.assertIn('FONT_SHA256', header)
            self.assertNotIn('unrelated-private-file', header)
            self.assertEqual(header.count('{"/'), 5)


if __name__ == '__main__':
    unittest.main()
