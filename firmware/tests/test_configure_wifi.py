"""Wi-Fi setup uses synthetic input and an injected transport; no real network."""
from pathlib import Path
import runpy
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))


class WifiConfigurationTests(unittest.TestCase):
    def setUp(self):
        script = ROOT/'tools/configure_wifi.py'
        self.assertTrue(script.is_file(), 'Explicit first-run Wi-Fi helper missing')
        self.configure = runpy.run_path(str(script))['configure_wifi']
        self.gateway = '.'.join(map(str, (10, 0, 0, 1)))
        self.config = {'key': 'a'*48}

    def test_supported_firmware_receives_explicit_credentials_once(self):
        calls = []
        def transport(path, payload):
            calls.append((path, payload))
            if path.endswith('/health'):
                return {'firmware': 'MadyarMini-0.3.0-public.1', 'protocol': 1}
            return {'ok': True, 'operation': 'explicit_wifi_configuration'}
        self.configure(self.config, self.gateway, 'synthetic-test-ssid', 'synthetic-password', transport=transport)
        self.assertEqual(calls, [('/api/v1/health', None), ('/api/v1/wifi', {'ssid': 'synthetic-test-ssid', 'password': 'synthetic-password'})])

    def test_invalid_credentials_never_reach_transport(self):
        def never(*args):
            self.fail('Invalid credentials reached transport')
        for ssid, password in [('', 'abcdefgh'), ('s'*33, 'abcdefgh'), ('s', 'short'),
                               ('s', 'p'*64), ('я'*17, 'abcdefgh'), ('s\0x', 'abcdefgh'), ('s', 'abcd\0efgh')]:
            with self.subTest(lengths=(len(ssid), len(password))):
                with self.assertRaises(ValueError):
                    self.configure(self.config, self.gateway, ssid, password, transport=never)

    def test_stock_device_and_failed_ack_are_rejected(self):
        calls = []
        def stock(path, payload):
            calls.append(path)
            return {'model': 'SmallTV-Ultra'}
        with self.assertRaises(ValueError):
            self.configure(self.config, self.gateway, 'synthetic-test', 'abcdefgh', transport=stock)
        self.assertEqual(calls, ['/api/v1/health'])
        def rejected(path, payload):
            if path.endswith('/health'):
                return {'firmware': 'MadyarMini-test', 'protocol': 1}
            return {'ok': False}
        with self.assertRaises(ValueError):
            self.configure(self.config, self.gateway, 'synthetic-test', 'abcdefgh', transport=rejected)

    def test_external_gateway_is_rejected_before_transport(self):
        with self.assertRaises(ValueError):
            self.configure(self.config, 'example.invalid', 'synthetic-test', 'abcdefgh', transport=lambda *_: self.fail('Unexpected network request'))


if __name__ == '__main__':
    unittest.main()
