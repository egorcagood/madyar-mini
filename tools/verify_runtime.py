"""Offline checks of the public copy; temporary state only, no hardware or Codex."""
import ast
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import runpy
import os
import subprocess

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from madyarmini.integration_data import default_settings, validate_settings
from madyarmini.integrated_service import IntegratedService
from firmware.tools.device_client import validate_host

for path in ROOT.rglob('*.py'):
    if any(p in {'.pio','.venv','var','private','__pycache__'} for p in path.relative_to(ROOT).parts):continue
    ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
for path in ROOT.rglob('*.json'):
    if any(p in {'.pio','.venv','var','private','__pycache__'} for p in path.relative_to(ROOT).parts):continue
    json.loads(path.read_text(encoding='utf-8-sig'))
with tempfile.TemporaryDirectory(prefix='plugin path кириллица ', dir=ROOT.parent) as folder:
    import shutil
    project = Path(folder)
    plugin = project / 'integrations/codex-plugin'
    shutil.copytree(ROOT / 'integrations/codex-plugin/template', plugin / 'template')
    executable = project / '.venv/Scripts/python.exe'
    executable.parent.mkdir(parents=True)
    subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(project / '.venv')], check=True, capture_output=True)
    shutil.copytree(ROOT / 'madyarmini', project / 'madyarmini')
    namespace = runpy.run_path(str(ROOT / 'tools/configure_plugin.py'))
    main = namespace['main']
    main.__globals__['ROOT'] = project
    main.__globals__['PLUGIN'] = plugin
    main()
    generated = plugin / 'generated/plugins/madyar-mini'
    mcp = json.loads((generated / 'mcp.json').read_text(encoding='utf-8'))
    assert mcp['mcpServers']['madyar-mini']['command'] == executable.as_posix()
    hooks = json.loads((generated / 'hooks/hooks.json').read_text(encoding='utf-8'))
    for groups in hooks['hooks'].values():
        for group in groups:
            for hook in group['hooks']:
                assert hook['command'] == hook['commandWindows']
                assert hook['command'].startswith('powershell.exe -NoLogo -NoProfile -NonInteractive -Command ')
                environment = os.environ.copy()
                environment['PLUGIN_ROOT'] = str(generated)
                result = subprocess.run(hook['command'], input=b'{}', capture_output=True, env=environment, timeout=10)
                assert result.returncode == 0, 'generated hook execution failed'
                assert result.stdout.strip() == b'{}', 'generated hook output must be empty JSON object'
    bridge = runpy.run_path(str(generated / 'scripts/bridge.py'))
    # Normalize mixed Windows separators before comparing resolved runtime paths.
    assert bridge['RUNTIME'].resolve() == (project / 'var/state/integrated/runtime.json').resolve()
assert validate_settings(default_settings())['device_host'] == ''
for host in ('127.0.0.1', '192.0.2.1', '8.8.8.8', 'https://example.com'):
    try:
        validate_host(host)
    except ValueError:
        pass
    else:
        raise AssertionError('invalid hardware host accepted')
with tempfile.TemporaryDirectory(prefix='madyar-public-check-', dir=ROOT.parent) as state:
    service = IntegratedService(ROOT, state, port=0, sources=False)
    assert service.server.server_address[0] == '127.0.0.1'
    worker = threading.Thread(target=service.run)
    worker.start()
    def request(path, token=None, host=None):
        connection = http.client.HTTPConnection('127.0.0.1', service.port, timeout=3)
        headers = {'Host': host or f'127.0.0.1:{service.port}'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        connection.request('GET', path, headers=headers)
        response = connection.getresponse()
        code = response.status
        body = response.read()
        connection.close()
        return code, body
    try:
        assert request('/api/state')[0] == 401
        assert request('/api/state', service.tokens['panel'], 'example.com')[0] == 403
        code, body = request('/api/state', service.tokens['panel'])
        assert code == 200 and json.loads(body)['sources']['device']['status'] == 'simulation'
        assert request('/')[0] == 200
        assert request('/renderer.js', service.tokens['panel'])[0] == 200
    finally:
        service.stop.set()
        worker.join(5)
        assert not worker.is_alive()
print('PASS: public Python/JSON syntax, blank host, RFC1918 boundary, loopback-only simulation, authentication, Host check, HTML/renderer; sources disabled, no hardware/Codex calls.')
