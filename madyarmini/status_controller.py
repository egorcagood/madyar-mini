"""Explicit status semantics; no Codex internals and no device/network discovery."""
import hashlib
import json
import math
import os
import re
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

from .notification import _atomic_json


class ControllerError(RuntimeError):
    pass


REASONS = frozenset({'manual', 'codex_event', 'user_confirmed', 'explicit_task_complete',
                     'permission', 'question', 'response', 'activity', 'turn_end',
                     'interrupted', 'error', 'heartbeat', 'shutdown'})
KINDS = frozenset({'turn_started', 'permission_requested', 'input_required',
                   'user_responded', 'activity', 'turn_stopped', 'task_completed',
                   'turn_failed', 'interrupted', 'session_ended', 'restore_clock'})
IDENTIFIER = re.compile(r'[A-Za-z0-9_.:-]{1,128}\Z')


def validate_event(event):
    required = {'event_id', 'session_id', 'turn_id', 'kind', 'source', 'reason'}
    if not isinstance(event, dict) or not required<=set(event) or set(event)-required-{'emitted_at'}:
        raise ControllerError('event_fields')
    if any(not isinstance(event[k], str) or not IDENTIFIER.fullmatch(event[k])
           for k in ('event_id', 'session_id', 'turn_id')):
        raise ControllerError('event_identifier')
    if (any(not isinstance(event[key], str) for key in required)
            or event['kind'] not in KINDS or event['source'] not in ('manual', 'hook', 'notify')
            or event['reason'] not in REASONS):
        raise ControllerError('event_values')
    if 'emitted_at' in event:
        value=event['emitted_at']
        if type(value) not in (int,float) or not math.isfinite(value) or value<=0:
            raise ControllerError('event_time')
    return dict(event)


def _hash(value):
    return hashlib.sha256(value.encode()).hexdigest()[:16] if value else None


class EventLog:
    """Bounded metadata only. No open handle left between calls (Windows friendly)."""
    def __init__(self, directory, max_bytes=1048576):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'events.jsonl'
        self.max_bytes = max_bytes

    def write(self, record):
        if self.path.exists() and self.path.stat().st_size >= self.max_bytes:
            oldest = self.directory/'events.2.jsonl'
            previous = self.directory/'events.1.jsonl'
            if previous.exists():
                os.replace(previous, oldest)
            os.replace(self.path, previous)
        with self.path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')
            stream.flush()


class StatusController:
    def __init__(self, display, state_dir, *, session_id='manual', done_seconds=8,
                 lease_seconds=600, clock=time.monotonic):
        if not isinstance(session_id, str) or not IDENTIFIER.fullmatch(session_id):
            raise ControllerError('session_id')
        for value, low, high in ((done_seconds, 1, 120), (lease_seconds, 10, 3600)):
            if (type(value) not in (int, float) or not math.isfinite(value)
                    or not low <= value <= high):
                raise ControllerError('timer_range')
        self.display, self.clock = display, clock
        self.session_id, self.done_seconds, self.lease_seconds = session_id, done_seconds, lease_seconds
        self.root = Path(state_dir)
        self.log = EventLog(self.root)
        self.deadline = None
        self.retry_at = None
        self.seen = OrderedDict()
        self.retired = set()
        self.closed = False
        self.closed_kind = None
        self._state = {'status': 'CLOCK', 'reason': 'startup', 'turn_id': None,
                       'recovery_pending': False, 'clock_restored': True}
        self._record('startup', 'startup')

    @property
    def state(self):
        return dict(self._state)

    def _record(self, outcome, reason, event=None):
        record = {'at': datetime.now(timezone.utc).isoformat(), 'outcome': outcome,
                  'reason': reason, 'status': self._state['status'],
                  'clock_restored': self._state['clock_restored'],
                  'recovery_pending': self._state['recovery_pending'],
                  'session_hash': _hash(self.session_id),
                  'turn_hash': _hash(self._state['turn_id'])}
        if event:
            record.update(event_hash=_hash(event['event_id']), kind=event['kind'], source=event['source'])
        self.log.write(record)
        _atomic_json(self.root/'state.json', record)

    def _transition(self, target, reason, event=None):
        if self._state['status'] == target:
            self._record('unchanged', reason, event)
            return self.state
        try:
            if target == 'CLOCK':
                self.display.restore_clock()
            else:
                self.display.show(target)
        except (OSError, RuntimeError):
            self._state.update(status='UNKNOWN', reason='display_error',
                               clock_restored=False, recovery_pending=True)
            self.deadline = None
            self.retry_at = self.clock() + 5
            self._record('restore_pending', 'display_error', event)
            return self.state
        self._state.update(status=target, reason=reason, recovery_pending=False,
                           clock_restored=target == 'CLOCK')
        self.retry_at = None
        self.deadline = (self.clock() + (self.done_seconds if target == 'DONE' else self.lease_seconds)
                         if target != 'CLOCK' else None)
        self._record('applied', reason, event)
        return self.state

    def handle(self, event):
        event = validate_event(event)
        if event['event_id'] in self.seen:
            self._record('duplicate', event['reason'], event)
            return self.state
        self.seen[event['event_id']] = None
        if len(self.seen) > 2048:
            self.seen.popitem(last=False)
        kind, turn = event['kind'], event['turn_id']
        if event['session_id'] != self.session_id:
            self._record('foreign_session', event['reason'], event)
            return self.state
        if self._state['recovery_pending']:
            self._record('blocked_recovery', event['reason'], event)
            return self.state
        if kind == 'restore_clock' and event['source'] == 'manual':
            self.closed = True
            self.closed_kind = kind
            return self._transition('CLOCK', event['reason'], event)
        if kind == 'turn_started':
            if turn in self.retired or (turn == self._state['turn_id'] and self.closed):
                self._record('stale_turn', event['reason'], event)
                return self.state
            if turn != self._state['turn_id']:
                if len(self.retired) >= 4096:
                    raise ControllerError('restart_required')
                if self._state['turn_id']:
                    self.retired.add(self._state['turn_id'])
                self._state['turn_id'], self.closed = turn, False
                self.closed_kind = None
            elif self._state['status'] in ('WARNING', 'DONE'):
                self._record('unchanged', event['reason'], event)
                return self.state
            if self._state['status'] == 'WORKING':
                self.deadline = self.clock() + self.lease_seconds
            return self._transition('WORKING', event['reason'], event)
        explicit_after_stop = (kind == 'task_completed' and event['source'] == 'manual'
                               and self.closed_kind == 'turn_stopped')
        if turn != self._state['turn_id'] or (self.closed and not explicit_after_stop):
            self._record('stale_turn', event['reason'], event)
            return self.state
        if kind == 'task_completed':
            if event['source'] != 'manual':
                self._record('unproven_completion', event['reason'], event)
                return self.state
            if self._state['status'] == 'WARNING':
                raise ControllerError('answer_required_before_done')
            return self._transition('DONE', event['reason'], event)
        if self._state['status'] == 'DONE':
            self._record('completed_turn', event['reason'], event)
            return self.state
        if kind in ('permission_requested', 'input_required', 'turn_failed'):
            return self._transition('WARNING', event['reason'], event)
        if kind == 'user_responded':
            return self._transition('WORKING', event['reason'], event)
        if kind == 'activity':
            if self._state['status'] in ('WORKING', 'WARNING'):
                self.deadline = self.clock() + self.lease_seconds
            self._record('heartbeat', event['reason'], event)
            return self.state
        if kind == 'turn_stopped' and self._state['status'] == 'WARNING':
            self._record('waiting_preserved', event['reason'], event)
            return self.state
        if kind in ('turn_stopped', 'interrupted', 'session_ended'):
            self.closed = True
            self.closed_kind = kind
            return self._transition('CLOCK', event['reason'], event)
        raise ControllerError('event_not_applicable')

    def _manual(self, kind, reason):
        turn = self._state['turn_id'] or 'manual-' + uuid.uuid4().hex
        if kind == 'turn_started' and (self.closed or self._state['status'] == 'DONE'):
            turn = 'manual-' + uuid.uuid4().hex
        return self.handle(dict(event_id=uuid.uuid4().hex, session_id=self.session_id,
                                turn_id=turn, kind=kind, source='manual', reason=reason))

    def set_working(self, reason='manual'):
        if self._state['status'] == 'WARNING':
            return self._manual('user_responded', reason)
        return self._manual('turn_started', reason)

    def set_warning(self, reason='manual'):
        if self._state['turn_id'] is None or self.closed:
            raise ControllerError('start_working_first')
        return self._manual('input_required', reason)

    def set_done(self, reason='explicit_task_complete'):
        if self._state['turn_id'] is None or (self.closed and self.closed_kind != 'turn_stopped'):
            raise ControllerError('start_working_first')
        return self._manual('task_completed', reason)

    def restore_clock(self, reason='manual'):
        return self._manual('restore_clock', reason)

    def heartbeat(self):
        return self._manual('activity', 'heartbeat')

    def tick(self):
        now = self.clock()
        if self._state['recovery_pending'] and now >= self.retry_at:
            self.closed = True
            self.closed_kind = 'recovery'
            return self._transition('CLOCK', 'recovery')
        if self.deadline is not None and now >= self.deadline:
            reason = 'done_timeout' if self._state['status'] == 'DONE' else 'stale'
            self.closed = True
            self.closed_kind = reason
            return self._transition('CLOCK', reason)
        return self.state
