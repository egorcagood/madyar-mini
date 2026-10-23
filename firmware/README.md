# Madyar Mini firmware 0.3.0-public.1

Source-only firmware for the owner's tested SmallTV-Ultra profile: ESP8266 / ESP-12F, 4 MiB flash, ST7789 240×240. Other boards and similarly named products are unverified. The `esp12e` PlatformIO board ID is a toolchain profile, not a claim about every module revision.

The public variant keeps the original three status GIFs and M animation bytes, uses an openly licensed Noto Sans Bold bitmap font, and starts with empty city/country, UTC, 30% daytime brightness and disabled night mode. It does not contain the owner's configuration. New font metrics can differ from the owner's original local font.

## Build locally

Use Python 3.10 or newer. Run these commands from this `firmware` directory, replacing `<YOUR_DEVICE_LAN_IPV4>` with the private LAN IPv4 address of your device. The host is required; there is no discovery or built-in owner's address.

```text
python -m pip install -r requirements-build.txt
python tools/build_assets.py
python tools/provision.py --host <YOUR_DEVICE_LAN_IPV4>
python -m unittest discover -s tests
python -m platformio run -e smalltv_ultra
python -m platformio run -e smalltv_ultra -t buildfs
```

The font defaults to `../assets/fonts/NotoSans-Bold.ttf`, resolved relative to the repository rather than the current directory. To use another legally obtained compatible font, run `python tools/build_assets.py --font <FONT_TTF_PATH>` and rebuild both images. Retain its license and visually check Latin/Cyrillic text and clipping. The repository's Noto font license is adjacent to the TTF.

The asset helper validates the original GIF/M hashes, creates only `data/font.bin`, and generates `include/resource_manifest.h` plus `ASSET_MANIFEST.json` from exactly five resources. Both `FONT_BYTES` and `FONT_SHA256` are emitted for the font upload endpoint. Unrelated files in `data/` are excluded from the manifest, but PlatformIO packs every file in `data/`; keep that directory limited to the five documented resource files.

Provisioning creates a unique random key in ignored `private/device.json` and `include/private_config.h`. Repeating provisioning preserves an existing valid key while updating the explicit host; it never changes the on-device key. It does not flash or contact anything. The runtime client must use the same key/config as the local image you flash. Never publish either file or your compiled application: the key is embedded in it. Keep backups, keys and build artifacts outside public commits, CI artifacts and release downloads.

Expected local outputs are `.pio/build/smalltv_ultra/firmware.bin` and `.pio/build/smalltv_ultra/littlefs.bin`. The two build commands above do not upload. PlatformIO fetches pinned platform/libraries on first build. The first release pins ESP8266 platform 4.2.1, TFT_eSPI 2.5.43, ArduinoJson 6.21.5 and AnimatedGIF 2.2.3.

## Before installation

Verify the actual device is an ESP8266 with exactly 4,194,304 flash bytes and the listed TFT wiring: MOSI 13, SCLK 14, no CS, DC 0, reset 2, backlight 5 (active low), rotation 0, SPI 27 MHz. Confirm a physical UART recovery path and stable power. Read the entire flash twice, compare hashes/size, and prove a same-device restoration route before writing. Full flash backups can contain Wi-Fi credentials and calibration; keep them private. A USB power connector does not establish that UART is available.

Manual ROM download requires GPIO0 low during reset with GPIO15 low and GPIO2 high/pulled up. GPIO0 and GPIO2 are shared with the TFT in this profile, so do not improvise wiring or connect 5 V serial logic. Release GPIO0 and reset for normal operation. See [Espressif boot selection](https://docs.espressif.com/projects/esptool/en/latest/esp8266/advanced-topics/boot-mode-selection.html).

The pinned `eagle.flash.4m2m.ld` layout uses application address `0x0` with maximum application size 1,044,464 bytes. LittleFS begins at `0x200000` and is exactly `0x1FA000` bytes (2,072,576). Preserve the tail starting `0x3FA000`, including EEPROM/RF calibration/SDK Wi-Fi data, during a custom image installation. Do not use an ESP32 partition-table recipe or a separate bootloader image. Verify the actual build's layout and image sizes before upload. [Pinned linker script](https://raw.githubusercontent.com/esp8266/Arduino/3.1.2/tools/sdk/ld/eagle.flash.4m2m.ld)

First flash uses a verified serial route with the local application at zero and matching filesystem image at `0x200000`. Run your selected esptool version's `--help` for read/write/verify syntax; v4 command names use underscores and v5 uses hyphens. Do not routinely erase the whole flash; targeted writes erase their own affected sectors. Restoring your same-device full backup at zero is a deliberate whole-flash restoration and differs from installing only custom application/resources. Never restore somebody else's dump. [Espressif commands](https://docs.espressif.com/projects/esptool/en/latest/esp8266/esptool/basic-commands.html)

After backup and layout checks, PlatformIO's explicit serial upload targets are:

```text
python -m platformio run -e smalltv_ultra -t upload --upload-port <YOUR_SERIAL_PORT>
python -m platformio run -e smalltv_ultra -t uploadfs --upload-port <YOUR_SERIAL_PORT>
```

These commands write hardware. They are not part of the build-only sequence. Review the generated upload command/addresses for the selected environment before execution. Use matching images and verify the device boots with resources verified afterward. An application-only upload does not replace resources. LittleFS and SPIFFS formats are incompatible; the firmware disables autoformat and displays diagnostics when resource hashes or mounting fail. [Core filesystem documentation](https://arduino-esp8266.readthedocs.io/en/3.1.2/filesystem.html)

The authenticated `/update` interface exists only after this firmware is running. It is an HTTP update server, not `espota`/ArduinoOTA or a documented OEM first-install route. It uses username `madyar` and your local management key. Keep it on a trusted local network: plain HTTP provides authentication without encrypted transport. Application and filesystem uploads are separate; require sufficient measured OTA staging space, stable power and physical recovery. The preflight helper enforces image size plus 4096 bytes within OTA capacity. Its example test evidence is synthetic and must never stand in for actual measurements.

No Wi-Fi credentials are compiled into this source. The firmware tries saved SDK Wi-Fi settings; if disconnected for about 20 seconds, it starts a fallback AP using a chip-suffixed name and the first 12 characters of the local key as its password. The default SDK AP address is not explicitly configured here; determine your device's AP gateway locally. Wi-Fi provisioning is an explicit authenticated API operation that writes SDK credentials.

For first-run setup when saved Wi-Fi does not connect:

1. Connect your computer once to the device's fallback AP. Use the first 12 characters of the key in your ignored local `private/device.json` as the AP password. Read it locally; never paste the key into a command, log, issue or chat. Keep the full key and all flash backups private.
2. Find that connection's gateway address in your operating system's network details. From the firmware directory run the command below, replacing the gateway placeholder with your own AP gateway IPv4.
3. Enter your own 2.4 GHz network SSID and Wi-Fi password at the two hidden prompts. The helper requires 1–32 UTF-8 bytes for the SSID and 8–63 for the password; open networks are not supported by this API. It refuses terminals that would echo input. It verifies Madyar firmware before the explicit settings POST and performs no scanning or automatic retry.
4. Reconnect your computer to your home network. Find the device's new LAN address locally, then rerun `python tools/provision.py --host <YOUR_NEW_DEVICE_LAN_IPV4>`. This updates the host while preserving the same key used by your flashed image. Restart the runtime/bridge with this updated local config.

```text
python tools/configure_wifi.py --gateway <YOUR_DEVICE_AP_GATEWAY_IPV4>
```

The helper loads the same ignored local key as provisioning and uses `DeviceClient` with proxies and redirects disabled. Wi-Fi credentials travel over the directly connected device AP using plain HTTP; run this step only on that trusted local connection. A timeout or rejected request is reported without credentials and is not proof that settings were applied. There are no SSID/password command-line arguments, key output, automatic SSID scan or public default gateway embedded in the helper.

Three quick boots trigger RTC recovery mode, as does detecting a flash size other than exactly 4 MiB. The boot counter clears after about 60 seconds of healthy uptime. This helps diagnosis and does not replace UART recovery and a verified backup.

## Local tools

`tools/send_status.py` performs explicit manual status tests using ignored `private/device.json`; it does not supply automatic Codex event delivery. `device_client.py` rejects external hosts, redirects, stock firmware and invalid ownership/state. `preflight.py` evaluates supplied evidence without flashing or networking. Optional `migrate_settings.py` accepts only the documented SmallTV-Ultra stock settings schema/version; migration is local and does not read a device automatically.

Run `python -m unittest discover -s tests` for offline Python tests. Successful source compilation is separate from device testing. The public font/default changes need their own hardware visual validation; the owner's earlier 0.3.0 device result does not prove every public build or another board works.
