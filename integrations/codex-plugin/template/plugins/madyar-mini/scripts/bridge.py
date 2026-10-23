"""Bounded local-only bridge. Never print payloads, tokens, or HTTP errors."""
import http.client
import json
from pathlib import Path
import re

RUNTIME = Path(r'__PROJECT_ROOT__\var\state\integrated\runtime.json')
IDENTIFIER = re.compile(r'[A-Za-z0-9_.:-]{1,128}\Z')
LIMIT = 262144
TIMEOUT = 1.5


def validate_runtime(value):
    if not isinstance(value, dict) or value.get('host') != '127.0.0.1':
        raise ValueError('runtime_host')
    if type(value.get('port')) is not int or not 1 <= value['port'] <= 65535:
        raise ValueError('runtime_port')
    for key in ('hook_token', 'mcp_token'):
        if not isinstance(value.get(key), str) or not re.fullmatch('[a-fA-F0-9]{64}', value[key]):
            raise ValueError('runtime_token')
    return {key: value[key] for key in ('host', 'port', 'hook_token', 'mcp_token')}


def read_runtime():
    with RUNTIME.open('rb') as stream:
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise ValueError('runtime_size')
    return validate_runtime(json.loads(raw))


def post_local(path, payload, credential):
    if (path, credential) not in (('/api/hook', 'hook'), ('/api/task', 'mcp')):
        raise ValueError('route')
    runtime = read_runtime()
    body = json.dumps(payload, ensure_ascii=True, separators=(',', ':')).encode('ascii')
    if len(body) > LIMIT:
        raise ValueError('body_size')
    # http.client makes a direct socket connection: no environment proxies and no redirects.
    connection = http.client.HTTPConnection(runtime['host'], runtime['port'], timeout=TIMEOUT)
    try:
        connection.request('POST', path, body, headers={
            'Host': f"127.0.0.1:{runtime['port']}",
            'Authorization': 'Bearer ' + runtime[credential + '_token'],
            'Content-Type': 'application/json', 'Accept': 'application/json',
            'Connection': 'close',
        })
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError('local_service_rejected')
        raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError('response_size')
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError('response_shape')
        return value
    finally:
        connection.close()
