"""Small stdio MCP server: explicit task signal, no model/API dependency."""
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bridge import IDENTIFIER, LIMIT, post_local

TOOL = {
    'name': 'madyar_task_status',
    'description': (
        'Update the local Madyar Mini status for this root Codex turn using the exact '
        'session_id and turn_id supplied by its UserPromptSubmit hook. working means '
        'active work; warning means user input, permission, or attention is needed. '
        'done is an explicit completion signal only after the requested task or explicitly '
        'requested stage is actually complete and required checks have passed. '
        'Do not send done merely because a turn ends, progress is reported, a goal budget '
        'expires, or you ask a question. A warning cannot be finished before resolved. '
        'Do not invent identities or report child-agent completion as root completion. '
        'Only identifiers and state are transmitted to a local loopback service.'),
    'inputSchema': {'type': 'object', 'properties': {
        'session_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_.:-]{1,128}$'},
        'turn_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_.:-]{1,128}$'},
        'state': {'type': 'string', 'enum': ['working', 'warning', 'done']},
    }, 'required': ['session_id', 'turn_id', 'state'], 'additionalProperties': False},
    'annotations': {'readOnlyHint': False, 'destructiveHint': False,
                    'idempotentHint': True, 'openWorldHint': False},
}
PROTOCOLS = ('2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05')


def result_content(value, *, error=False):
    return {'content': [{'type': 'text', 'text': json.dumps(value, separators=(',', ':'))}],
            'isError': error}


def safe_status(value):
    # Never relay free text, error details, user messages, or unrecognized fields.
    result = {}
    status = value.get('status')
    if status in ('CLOCK', 'WORKING', 'WARNING', 'DONE', 'working', 'warning', 'done', 'ok'):
        result['status'] = status
    counters = value.get('counters')
    if isinstance(counters, dict):
        result['counters'] = {key: number for key, number in counters.items()
                              if key in ('working', 'waiting', 'completed', 'diagnostic')
                              and type(number) is int and 0 <= number <= 2**53}
    return result


def call_tool(params, post):
    if not isinstance(params, dict) or params.get('name') != TOOL['name']:
        return result_content({'status': 'invalid_tool'}, error=True)
    args = params.get('arguments')
    if (not isinstance(args, dict) or set(args) != {'session_id', 'turn_id', 'state'}
            or any(not isinstance(args[key], str) or not IDENTIFIER.fullmatch(args[key])
                   for key in ('session_id', 'turn_id'))
            or args['state'] not in ('working', 'warning', 'done')):
        return result_content({'status': 'invalid_arguments'}, error=True)
    try:
        response = post('/api/task', {key: args[key] for key in ('session_id', 'turn_id', 'state')}, 'mcp')
        return result_content(safe_status(response))
    except Exception:
        return result_content({'status': 'unavailable_or_rejected'}, error=True)


def dispatch(request, *, post=post_local):
    if not isinstance(request, dict):
        return {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32600, 'message': 'Invalid Request'}}
    if 'id' not in request:
        return None
    identity = request['id']
    if request.get('jsonrpc') != '2.0' or not isinstance(request.get('method'), str):
        return {'jsonrpc': '2.0', 'id': identity, 'error': {'code': -32600, 'message': 'Invalid Request'}}
    method, params = request['method'], request.get('params', {})
    if method == 'initialize':
        version = params.get('protocolVersion') if isinstance(params, dict) else None
        value = {'protocolVersion': version if version in PROTOCOLS else PROTOCOLS[0],
                 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'madyar-mini', 'version': '1.0.0'}}
    elif method == 'ping':
        value = {}
    elif method == 'tools/list':
        value = {'tools': [TOOL]}
    elif method == 'tools/call':
        value = call_tool(params, post)
    else:
        return {'jsonrpc': '2.0', 'id': identity, 'error': {'code': -32601, 'message': 'Method not found'}}
    return {'jsonrpc': '2.0', 'id': identity, 'result': value}


def run(source, target):
    while True:
        raw = source.readline(LIMIT + 1)
        if not raw:
            return 0
        if len(raw) > LIMIT:
            # A malicious oversized line must not be split into separate JSON messages.
            while raw and not raw.endswith(b'\n'):
                raw = source.readline(LIMIT + 1)
            response = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Parse error'}}
        else:
            try:
                response = dispatch(json.loads(raw))
            except Exception:
                response = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Parse error'}}
        if response is not None:
            target.write(json.dumps(response, ensure_ascii=True, separators=(',', ':')) + '\n')
            target.flush()


if __name__ == '__main__':
    raise SystemExit(run(sys.stdin.buffer, sys.stdout))
