"""Neutral metadata-only Codex command hook; no permission decisions."""
import json
from pathlib import Path
import sys

# Explicit paths are required because command hooks run in the session's cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, r'__PROJECT_ROOT__')
from bridge import LIMIT, post_local


def process(payload, *, post=post_local):
    try:
        from madyarmini.integration_sources import project_hook
        projected = project_hook(payload)
        if projected is None:
            return {}
        post('/api/hook', projected, 'hook')
        if projected['hook'] == 'UserPromptSubmit':
            event = projected['event']
            identity = json.dumps({'session_id': event['session_id'], 'turn_id': event['turn_id']},
                                  ensure_ascii=True, separators=(',', ':'))
            fallback = ("& '__PROJECT_ROOT__/.venv/Scripts/python.exe' -I '__PROJECT_ROOT__/tools/task_status.py' "
                        f"--session-id '{event['session_id']}' --turn-id '{event['turn_id']}' --state done")
            return {'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit',
                    'additionalContext': (
                        'Madyar Mini local status identity for this root turn: ' + identity + '. '
                        'The madyar_task_status MCP tool accepts these exact identifiers. '
                        'Use state=done only when the user-requested task or explicitly requested '
                        'stage is genuinely complete and required verification is finished. '
                        'Ending a turn, reporting progress, asking for input or permission, '
                        'or waiting for a user is not completion. Use warning when user input '
                        'is required. Never invent identifiers or mark another agent task done. '
                        'On genuine verified completion explicitly send done; do not leave completion implicit. '
                        'If the MCP tool is absent, use the approved local fallback command: ' + fallback + '. '
                        'It queues a bounded sender after the current tool approval finishes. '
                        'Check its delivery receipt using the same command plus --receipt before claiming DONE. '
                        'A queued command alone is not delivery. This fallback must never convert every Stop into done.')}}
        return {}
    except Exception:
        return {}


def run(source, target):
    result = {}
    try:
        raw = source.read(LIMIT + 1)
        if len(raw) <= LIMIT:
            result = process(json.loads(raw))
    except Exception:
        pass
    target.write(json.dumps(result, ensure_ascii=True, separators=(',', ':')) + '\n')
    target.flush()
    return 0


if __name__ == '__main__':
    raise SystemExit(run(sys.stdin.buffer, sys.stdout))
