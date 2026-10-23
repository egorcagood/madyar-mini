"""Explicit first-run Wi-Fi setup. Prompts hide credentials; nothing is discovered."""
import argparse
import getpass
import json
from pathlib import Path
import warnings

from device_client import DeviceClient, validate_host


def configure_wifi(config, gateway, ssid, password, *, transport=None):
    # ESP8266 String.length() measures UTF-8 bytes, not Python characters.
    for value, minimum, maximum in ((ssid, 1, 32), (password, 8, 63)):
        if (not isinstance(value, str) or any(ord(char) < 32 or ord(char) == 127 for char in value)
                or not minimum <= len(value.encode('utf-8')) <= maximum):
            raise ValueError('Invalid SSID/password length or control character')
    client = DeviceClient(gateway, config['key'], transport=transport)
    health = client.transport('/api/v1/health', None)
    if (not isinstance(health, dict) or not str(health.get('firmware', '')).startswith('MadyarMini-')
            or health.get('protocol') != 1):
        raise ValueError('Gateway is not supported Madyar Mini firmware')
    result = client.transport('/api/v1/wifi', {'ssid': ssid, 'password': password})
    if not isinstance(result, dict) or result.get('ok') is not True:
        raise ValueError('Wi-Fi configuration was not acknowledged')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gateway', required=True, help='Your device AP gateway private IPv4; no URL or port')
    parser.add_argument('--config', type=Path, default=Path(__file__).resolve().parents[1]/'private/device.json')
    args = parser.parse_args()
    try:
        gateway = validate_host(args.gateway)
        config = json.loads(args.config.read_text(encoding='utf-8'))
        with warnings.catch_warnings():
            # Abort instead of falling back to echoed input on an unsupported terminal.
            warnings.simplefilter('error', getpass.GetPassWarning)
            ssid = getpass.getpass('Your 2.4 GHz Wi-Fi SSID (hidden): ')
            password = getpass.getpass('Your Wi-Fi password (hidden): ')
        configure_wifi(config, gateway, ssid, password)
    except (OSError, ValueError, KeyError, EOFError, KeyboardInterrupt, getpass.GetPassWarning):
        raise SystemExit('Wi-Fi setup was not completed. Check the gateway, local config, hidden-input terminal and credentials. No credential was printed.')
    print('Wi-Fi settings acknowledged. Reconnect to your home network, find the device LAN address, then rerun provision.py --host with that address to preserve the existing key.')


if __name__ == '__main__':
    main()
