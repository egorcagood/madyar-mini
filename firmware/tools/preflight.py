"""Pure deployment gate. This module has no upload or network operation."""
import json
from pathlib import Path
import sys


def _common_preflight(evidence):
    e = evidence if isinstance(evidence, dict) else {}
    blockers = []
    if e.get('board_reports_conflict') is not False:
        blockers.append('conflicting_hardware_evidence')
    if e.get('rollback_tested') is not True:
        blockers.append('rollback_unverified')
    identity=e.get('actual_device_id')
    if not isinstance(identity,str) or not identity or any(e.get(k)!=identity for k in ('backup_device_id','recovery_device_id','rollback_device_id')):
        blockers.append('evidence_device_mismatch')
    if e.get('model') != 'SmallTV-Ultra' or e.get('chip') != 'ESP8266' or e.get('flash_bytes') != 4194304:
        blockers.append('target_profile_mismatch')
    for key, reason in [('board_verified', 'board_unverified'), ('recovery_verified', 'physical_recovery_unverified'),
                        ('assets_verified', 'assets_unverified'), ('image_verified', 'image_unverified'),
                        ('stable_power_verified', 'power_unverified')]:
        if e.get(key) is not True:
            blockers.append(reason)
    if e.get('full_backup_verified') is not True or e.get('backup_bytes') != 4194304:
        blockers.append('full_flash_backup_missing')
    age = e.get('evidence_age_seconds')
    if type(age) not in (int, float) or not 0 <= age <= 300:
        blockers.append('preflight_evidence_stale')
    return blockers


def evaluate_preflight(evidence):
    e = evidence if isinstance(evidence, dict) else {}
    blockers = _common_preflight(e)
    capacity, size = e.get('ota_capacity_bytes'), e.get('image_bytes')
    if type(capacity) is not int or capacity <= 0:
        blockers.append('ota_capacity_unverified')
    elif type(size) is not int or size <= 0 or size + 4096 > capacity:
        blockers.append('image_does_not_fit_with_margin')
    return blockers


def evaluate_serial_preflight(evidence):
    """First serial hardware test; does not assert that TFT pins were measured."""
    e = evidence if isinstance(evidence, dict) else {}
    blockers = _common_preflight(e)
    if e.get('route') != 'uart':
        blockers.append('serial_route_required')
    if type(e.get('image_offset')) is not int or e.get('image_offset') != 0:
        blockers.append('serial_image_offset_invalid')
    size = e.get('image_bytes')
    if type(size) is not int or not 0 < size <= 1044464:
        blockers.append('serial_image_out_of_range')
    if type(e.get('fs_offset')) is not int or e.get('fs_offset') != 0x200000 or type(e.get('fs_bytes')) is not int or e.get('fs_bytes') != 0x1FA000:
        blockers.append('serial_filesystem_layout_invalid')
    if e.get('preserve_sdk_tail') is not True:
        blockers.append('sdk_tail_preservation_missing')
    if e.get('stock_restored_boot_verified') is not True:
        blockers.append('restored_stock_boot_unverified')
    if e.get('settings_migrated') is not True:
        blockers.append('settings_migration_missing')
    return blockers


if __name__ == '__main__':
    result = evaluate_preflight(json.loads(Path(sys.argv[1]).read_text(encoding='utf-8-sig')))
    print(json.dumps({'deployment_allowed': not result, 'blockers': result}, indent=2))
    raise SystemExit(2 if result else 0)
