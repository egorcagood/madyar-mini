The service derives its project root from its installed source directory. A Windows folder containing spaces and Cyrillic characters is supported. Install Python 3.12 or newer, then run from this folder:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m madyarmini.integrated_service
```

The default is simulation, bound to `127.0.0.1:8772`. Open that address in your browser. This is a local HTTP service, not a Windows service registration. Startup creates private runtime/state files under `var/`; never publish that directory. The normal service obtains weather from Open-Meteo and quota metadata through the locally installed Codex runtime. It does not read conversation transcripts. Animation preview requires the separately prepared, redistributable `assets/` files.

For hardware mode, first follow the firmware build/provisioning guide. Provision your own explicit RFC1918 device IPv4 address and one newly generated management key. The matching key must be compiled into your own firmware and stored in `firmware/private/device.json` with fields `host` and `key` (48 lowercase hexadecimal characters). Keep both this file and `firmware/include/private_config.h` private. No existing binary is suitable for public redistribution because a build can contain its owner's key. The PC client uses direct HTTP to the configured LAN IP with this key; use a trusted local network. It rejects public addresses, DNS names, redirects and environment proxies. Do not expose the device or localhost service through router port forwarding.

```powershell
.\.venv\Scripts\python.exe -m madyarmini.integrated_service --live
```

On a clean installation the live service adopts the host from the private firmware configuration. If a saved panel setting names another host, startup rejects the mismatch; update it to the same host in simulation first.

For optional Codex integration, run:

```powershell
.\.venv\Scripts\python.exe .\tools\configure_plugin.py
```

This prepares a private, installation-specific marketplace under `integrations/codex-plugin/generated`; it does not install anything or modify Codex settings. The template stays portable; generated files contain this computer's project path. To install it, use the official executable from your own installed Codex app. These command forms were verified against the installed runtime's `--help`:

```powershell
$CodexExe = Read-Host 'Full path to your installed Codex app codex.exe'
& $CodexExe plugin marketplace add (Join-Path $PWD 'integrations/codex-plugin/generated') --json
& $CodexExe plugin add 'madyar-mini@madyar-mini-local' --json
```

After installation, restart Codex and review/enable hooks in Codex. Regenerate after moving the project, and update/reinstall the local plugin through Codex as needed. The path must not contain quote, dollar, backtick or newline characters. No account credential is required by the plugin; it reads freshly generated role tokens from this installation's local runtime file. Generated Windows hooks use the canonical PowerShell command and resolve `PLUGIN_ROOT` through `[Environment]::GetEnvironmentVariable`, without relying on shell interpolation.

Generated Windows hook commands were executed successfully with `PLUGIN_ROOT` set to a scratch installation containing Cyrillic characters and spaces; empty `{}` input returned exit code zero and `{}` output without submitting an event. Automatic integration has not been validated on a fresh second PC or every Codex release. A queued task-status command alone is not proof of device delivery; use the tool's receipt before claiming DONE.
