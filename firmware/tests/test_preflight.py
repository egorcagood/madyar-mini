import copy
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
try:
    from preflight import evaluate_preflight
except ImportError:
    evaluate_preflight = None


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(evaluate_preflight, 'Missing firmware deployment preflight')
        self.good = dict(model='SmallTV-Ultra', chip='ESP8266', flash_bytes=4194304,
                         board_verified=True, recovery_verified=True, full_backup_verified=True,
                         backup_bytes=4194304, assets_verified=True, ota_capacity_bytes=900000,
                         image_bytes=500000, image_verified=True, stable_power_verified=True,
                         evidence_age_seconds=10, current_stock='Ultra-V9.0.43',
                         board_reports_conflict=False,rollback_tested=True,
                         actual_device_id='fixture-device',backup_device_id='fixture-device',
                         recovery_device_id='fixture-device',rollback_device_id='fixture-device')

    def test_complete_simulated_evidence(self):
        self.assertEqual(evaluate_preflight(self.good), [])

    def test_no_backup_blocks_even_with_permission(self):
        self.good.update(full_backup_verified=False, backup_bytes=0, user_approved=True)
        self.assertIn('full_flash_backup_missing', evaluate_preflight(self.good))

    def test_com1_is_not_verified_recovery(self):
        self.good['recovery_verified'] = False
        self.assertIn('physical_recovery_unverified', evaluate_preflight(self.good))

    def test_filesystem_space_does_not_prove_ota_capacity(self):
        self.good.pop('ota_capacity_bytes')
        self.good['filesystem_free_bytes'] = 3000000
        self.assertIn('ota_capacity_unverified', evaluate_preflight(self.good))

    def test_wrong_models_or_chip(self):
        for key, value in [('model', 'SmallTV-Pro'), ('chip', 'ESP32'), ('flash_bytes', 2097152)]:
            bad = dict(self.good, **{key: value})
            self.assertIn('target_profile_mismatch', evaluate_preflight(bad))

    def test_oversize_and_margin(self):
        self.good.update(image_bytes=900000, ota_capacity_bytes=900000)
        self.assertIn('image_does_not_fit_with_margin', evaluate_preflight(self.good))

    def test_stale_evidence(self):
        self.good['evidence_age_seconds'] = 301
        self.assertIn('preflight_evidence_stale', evaluate_preflight(self.good))

    def test_invalid_types_fail_closed(self):
        for val in (None, '900000', True, -1):
            self.good['ota_capacity_bytes'] = val
            self.assertIn('ota_capacity_unverified', evaluate_preflight(self.good))

    def test_changed_assets_or_image(self):
        for key, reason in [('assets_verified', 'assets_unverified'), ('image_verified', 'image_unverified'),
                            ('board_verified', 'board_unverified'), ('stable_power_verified', 'power_unverified')]:
            bad = dict(self.good, **{key: False})
            self.assertIn(reason, evaluate_preflight(bad))

    def test_conflicting_hardware_or_untested_rollback_blocks(self):
        self.assertIn('conflicting_hardware_evidence',evaluate_preflight(dict(self.good,board_reports_conflict=True)))
        for v in (None,False,'yes'):
            self.assertIn('rollback_unverified',evaluate_preflight(dict(self.good,rollback_tested=v)))

    def test_evidence_belongs_to_actual_device(self):
        for k in ('backup_device_id','recovery_device_id','rollback_device_id'):
            self.assertIn('evidence_device_mismatch',evaluate_preflight(dict(self.good,**{k:'other-device'})))


if __name__ == '__main__':
    unittest.main()
