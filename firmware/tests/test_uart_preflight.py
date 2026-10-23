import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import preflight


class SerialPreflightTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(hasattr(preflight, 'evaluate_serial_preflight'), 'Missing UART deployment gate')
        self.good = dict(route='uart', model='SmallTV-Ultra', chip='ESP8266', flash_bytes=4194304,
            board_verified=True, recovery_verified=True, full_backup_verified=True, backup_bytes=4194304,
            assets_verified=True, image_verified=True, stable_power_verified=True, evidence_age_seconds=10,
            board_reports_conflict=False, rollback_tested=True, actual_device_id='fixture',
            backup_device_id='fixture', recovery_device_id='fixture', rollback_device_id='fixture',
            image_offset=0, image_bytes=400000, fs_offset=0x200000, fs_bytes=0x1FA000, preserve_sdk_tail=True,
            stock_restored_boot_verified=True, settings_migrated=True)

    def test_uart_does_not_need_ota_slot(self):
        self.assertEqual(preflight.evaluate_serial_preflight(self.good), [])
        self.assertIn('ota_capacity_unverified', preflight.evaluate_preflight(self.good))

    def test_common_identity_backup_and_recovery_still_required(self):
        for key, val, blocker in [('backup_device_id','other','evidence_device_mismatch'),
            ('full_backup_verified',False,'full_flash_backup_missing'),
            ('rollback_tested',False,'rollback_unverified'),
            ('evidence_age_seconds',301,'preflight_evidence_stale')]:
            self.assertIn(blocker, preflight.evaluate_serial_preflight(dict(self.good, **{key:val})))

    def test_ranges_and_tail_are_fixed(self):
        for key,val,reason in [('image_bytes',1044465,'serial_image_out_of_range'),
            ('image_bytes',True,'serial_image_out_of_range'),
            ('fs_offset',0x100000,'serial_filesystem_layout_invalid'),
            ('fs_bytes',0x200000,'serial_filesystem_layout_invalid'),
            ('preserve_sdk_tail',False,'sdk_tail_preservation_missing')]:
            self.assertIn(reason,preflight.evaluate_serial_preflight(dict(self.good,**{key:val})))

    def test_boot_settings_and_route(self):
        for key,val,reason in [('route','ota','serial_route_required'),
            ('stock_restored_boot_verified',False,'restored_stock_boot_unverified'),
            ('settings_migrated',False,'settings_migration_missing')]:
            self.assertIn(reason,preflight.evaluate_serial_preflight(dict(self.good,**{key:val})))

    def test_app_offset_must_be_explicit_zero(self):
        for value in (None,True,0x1000,0x3FC000):
            self.assertIn('serial_image_offset_invalid',preflight.evaluate_serial_preflight(dict(self.good,image_offset=value)))
        missing=dict(self.good);missing.pop('image_offset')
        self.assertIn('serial_image_offset_invalid',preflight.evaluate_serial_preflight(missing))


if __name__ == '__main__': unittest.main()
