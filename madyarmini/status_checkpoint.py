"""Validated, metadata-only state. A restart never blindly replays a display status."""
import json
import math
from pathlib import Path

from .notification import _atomic_json
from .status_controller import ControllerError, IDENTIFIER, REASONS


class StatusCheckpoint:
    LIMIT=8*1024*1024
    def __init__(self,path,registry):
        self.path,self.registry=Path(path),dict(registry)

    @staticmethod
    def _ids(value,limit):
        return (isinstance(value,list) and len(value)<=limit
                and all(isinstance(v,str) and IDENTIFIER.fullmatch(v) for v in value)
                and len(set(value))==len(value))

    def _validate(self,data):
        try:
            fields={'schema','saved_at','registry','participants','completed','wait_reasons','sessions','done_consumed','last_event_at'}
            if not isinstance(data,dict) or set(data)!=fields or type(data['schema']) is not int or data['schema']!=1:
                raise ValueError('schema')
            if data['registry']!=self.registry:
                raise ValueError('registry_changed')
            when=data['saved_at']
            if type(when) not in (int,float) or not math.isfinite(when) or when<=0:
                raise ValueError('timestamp')
            keys=set(self.registry)
            times=data['last_event_at']
            if (not isinstance(times,dict) or set(times)!=keys or
                    any(type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in times.values())):
                raise ValueError('event_watermarks')
            for name in ('participants','completed'):
                if not self._ids(data[name],8) or not set(data[name])<=keys:
                    raise ValueError('participants')
            if not set(data['completed'])<=set(data['participants']) or type(data['done_consumed']) is not bool:
                raise ValueError('completion')
            waits=data['wait_reasons']
            if (not isinstance(waits,dict) or not set(waits)<=keys
                    or any(v not in ('permission','question','error') for v in waits.values())):
                raise ValueError('waits')
            sessions=data['sessions']
            if not isinstance(sessions,dict) or set(sessions)!=keys:
                raise ValueError('sessions')
            reasons=REASONS|{'startup','stale','done_timeout','recovery','display_error'}
            closed_kinds={'turn_stopped','interrupted','session_ended','restore_clock','done_timeout','stale','recovery'}
            for row in sessions.values():
                if not isinstance(row,dict) or set(row)!={'state','closed','closed_kind','retired','seen'}:
                    raise ValueError('row')
                state=row['state']
                if (not isinstance(state,dict) or set(state)!={'status','reason','turn_id','recovery_pending','clock_restored'}
                        or state['status'] not in ('CLOCK','WORKING','WARNING','DONE')
                        or state['reason'] not in reasons or state['recovery_pending'] is not False
                        or type(state['clock_restored']) is not bool
                        or state['clock_restored']!=(state['status']=='CLOCK')):
                    raise ValueError('state')
                turn=state['turn_id']
                if turn is not None and (not isinstance(turn,str) or not IDENTIFIER.fullmatch(turn)):
                    raise ValueError('turn')
                if state['status']!='CLOCK' and turn is None:
                    raise ValueError('missing_turn')
                if (type(row['closed']) is not bool
                        or (row['closed'] and row['closed_kind'] not in closed_kinds)
                        or (not row['closed'] and row['closed_kind'] is not None)
                        or not self._ids(row['retired'],4096) or not self._ids(row['seen'],2048)
                        or turn in row['retired']):
                    raise ValueError('turn_history')
                if row['closed'] and state['status'] in ('WORKING','WARNING'):
                    raise ValueError('closed_active_turn')
            for key in data['participants']:
                if sessions[key]['state']['turn_id'] is None:
                    raise ValueError('participant_without_turn')
            for key in data['completed']:
                row=sessions[key]
                if not (row['state']['status']=='DONE' or
                        (row['state']['status']=='CLOCK' and row['closed_kind']=='done_timeout')):
                    raise ValueError('completion_without_marker')
            return data
        except (KeyError,TypeError,ValueError) as exc:
            raise ControllerError('checkpoint_invalid_or_registry_changed') from exc

    def load(self):
        if not self.path.exists(): return None
        try:
            with self.path.open('rb') as stream: raw=stream.read(self.LIMIT+1)
            if len(raw)>self.LIMIT: raise ValueError('limit')
            return self._validate(json.loads(raw))
        except (ValueError,UnicodeError,RecursionError) as exc:
            raise ControllerError('checkpoint_invalid_or_registry_changed') from exc

    def save(self,data):
        self._validate(data)
        _atomic_json(self.path,data)
