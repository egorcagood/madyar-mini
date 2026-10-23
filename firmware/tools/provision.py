"""Create recipient-local credentials offline. Does not contact or flash a device."""
import argparse
import json
from pathlib import Path
import secrets

from device_client import validate_host


def provision(root, host):
    host = validate_host(host)
    config = root/'private/device.json'
    if config.exists():
        data = json.loads(config.read_text(encoding='utf-8'))
        key = data['key']
        if not isinstance(key, str) or len(key) != 48 or any(c not in '0123456789abcdef' for c in key):
            raise ValueError('Existing key is invalid; refusing to replace it')
    else:
        key = secrets.token_hex(24)
    config.parent.mkdir(parents=True, exist_ok=True)
    (root/'include').mkdir(parents=True, exist_ok=True)
    # No credential is printed. Both files must remain private and ignored.
    config.write_text(json.dumps({'host': host, 'key': key}, indent=2)+'\n', encoding='utf-8')
    (root/'include/private_config.h').write_text('#pragma once\n#define MADYAR_KEY "'+key+'"\n', encoding='ascii')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True, help='Your device private LAN IPv4 address; no URL or port')
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1], help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        provision(args.root, args.host)
    except (OSError, ValueError, KeyError):
        raise SystemExit('Provisioning failed. Check the explicit host and existing local config; no key was printed.')
    print('Local credentials prepared. Keep private/device.json, private_config.h and all compiled images private.')


if __name__ == '__main__':
    main()
