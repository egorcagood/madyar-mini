"""Allowlisted metadata projections from documented Codex hooks and notify."""
import uuid
import time

from .status_controller import ControllerError, validate_event


HOOK_KINDS = {
    'UserPromptSubmit': 'turn_started',
    'PermissionRequest': 'permission_requested',
    'PreToolUse': 'activity',
    'PostToolUse': 'activity',
    'Stop': 'turn_stopped',
    'Interrupt': 'interrupted',
    'SessionEnd': 'session_ended',
}


def _project(session, turn, kind, source):
    try:
        return validate_event(dict(event_id=uuid.uuid4().hex, session_id=session,
                                   turn_id=turn, kind=kind, source=source, reason='codex_event',
                                   emitted_at=time.time()))
    except ControllerError:
        return None


def hook_event(payload):
    if not isinstance(payload, dict):
        return None
    name = payload.get('hook_event_name')
    if not isinstance(name, str) or name not in HOOK_KINDS:
        return None
    kind = HOOK_KINDS[name]
    if name == 'Stop' and payload.get('stop_hook_active') is True:
        kind = 'activity'
    return _project(payload.get('session_id'), payload.get('turn_id'), kind, 'hook')


def notify_event(payload):
    if not isinstance(payload, dict) or payload.get('type') != 'agent-turn-complete':
        return None
    return _project(payload.get('thread-id'), payload.get('turn-id'), 'turn_stopped', 'notify')
