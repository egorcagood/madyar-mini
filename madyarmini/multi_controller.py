"""Per-chat state isolation and one aggregate display owner; no source discovery."""
import copy
import math
import re
import time
from collections import OrderedDict
from datetime import datetime,timezone
from pathlib import Path

from .notification import _atomic_json
from .status_controller import ControllerError, EventLog, IDENTIFIER, REASONS, StatusController, _hash, validate_event
from .status_checkpoint import StatusCheckpoint


def validate_sessions(sessions):
    if not isinstance(sessions,dict) or not 1 <= len(sessions) <= 8:
        raise ControllerError('sessions_count')
    result, ids, labels = {}, set(), set()
    for key,value in sessions.items():
        if (not isinstance(key,str) or not re.fullmatch('[a-z][a-z0-9_-]{0,23}',key)
                or not isinstance(value,dict) or set(value)!={'session_id','label'}):
            raise ControllerError('session_definition')
        session,label=value['session_id'],value['label']
        if (not isinstance(session,str) or not IDENTIFIER.fullmatch(session)
                or not isinstance(label,str) or not re.fullmatch('[A-Za-zА-Яа-яЁё0-9 ._-]{1,12}',label)
                or label.strip()!=label or not any(c.isalnum() for c in label)
                or session in ids or label.casefold() in labels):
            raise ControllerError('session_identity_or_label')
        ids.add(session)
        labels.add(label.casefold())
        result[key]={'session_id':session,'label':label}
    return result


class _SessionScreen:
    """Logical sink, not a device: only the aggregate may touch the real display."""
    def show(self,status): pass
    def restore_clock(self): pass


class MultiStatusController:
    def __init__(self,display,state_dir,*,sessions,done_seconds=10,lease_seconds=600,
                 rotate_seconds=4,clock=time.monotonic,on_view=None,strict_events=False,
                 wall_clock=time.time,max_event_age=30):
        self.sessions=validate_sessions(sessions)
        if (type(rotate_seconds) not in (int,float) or not math.isfinite(rotate_seconds)
                or not 2 <= rotate_seconds <= 30):
            raise ControllerError('rotation_range')
        self.display,self.clock,self.on_view=display,clock,on_view
        if (type(strict_events) is not bool or type(max_event_age) not in (int,float)
                or not math.isfinite(max_event_age) or not 1<=max_event_age<=300):
            raise ControllerError('event_age_config')
        self.strict_events,self.wall_clock,self.max_event_age=strict_events,wall_clock,max_event_age
        self.started_at=wall_clock()
        self.root=Path(state_dir)
        self.log=EventLog(self.root)
        self.checkpoint=StatusCheckpoint(self.root/'checkpoint.json',
                                        {k:_hash(v['session_id']) for k,v in self.sessions.items()})
        recovered=self.checkpoint.load()
        self.controllers={key:StatusController(_SessionScreen(),self.root/'sessions'/_hash(v['session_id']),
                       session_id=v['session_id'],done_seconds=done_seconds,
                       lease_seconds=lease_seconds,clock=clock) for key,v in self.sessions.items()}
        self.by_id={value['session_id']:key for key,value in self.sessions.items()}
        self.done_seconds,self.rotate_seconds=done_seconds,rotate_seconds
        self.participants=set()
        self.completed=set()
        self.wait_reasons={}
        self.wait_order=()
        self.wait_since=clock()
        self.done_deadline=None
        self.done_consumed=False
        self.applied_status='CLOCK'
        self.recovery_pending=False
        self.await_fresh=False
        self.fresh_required=set()
        self.retry_at=None
        self.preview_key=None
        self._view={}
        self.restarted=bool(recovered)
        self.last_event_at={key:0 for key in self.sessions}
        if recovered:
            self._restore_checkpoint(recovered)
            self.applied_status=None  # Confirm the clock; never replay the old notification.
        self._publish('restart' if recovered else 'startup')

    def _restore_checkpoint(self,data):
        self.last_event_at=dict(data['last_event_at'])
        self.participants=set(data['participants'])
        self.completed=set(data['completed'])
        self.wait_reasons=dict(data['wait_reasons'])
        self.done_consumed=data['done_consumed']
        for key,row in data['sessions'].items():
            core=self.controllers[key]
            core._state=dict(row['state'])
            core.closed,core.closed_kind=row['closed'],row['closed_kind']
            core.retired=set(row['retired'])
            core.seen=OrderedDict.fromkeys(row['seen'])
            core.deadline=None
        if not (self.participants and self.participants<=self.completed and self.done_consumed):
            self.fresh_required=self._unverified_participants()
            self.await_fresh=bool(self.fresh_required)

    def _unverified_participants(self):
        # A recorded Stop/interrupt is already evidence of inactivity. Preserve
        # participants and completion markers, but never demand a new event from
        # a normally closed turn. Lease loss and recovery remain unverified.
        return {key for key in self.participants if key not in self.completed
                and (not self.controllers[key].closed
                     or self.controllers[key].closed_kind in ('stale','recovery'))}

    def _save_checkpoint(self):
        self.checkpoint.save({'schema':1,'saved_at':self.wall_clock(),'registry':self.checkpoint.registry,
            'participants':sorted(self.participants),'completed':sorted(self.completed),
            'wait_reasons':dict(self.wait_reasons),
            'last_event_at':dict(self.last_event_at),
            'done_consumed':self.done_consumed or self.done_deadline is not None,
            'sessions':{k:{'state':c.state,'closed':c.closed,'closed_kind':c.closed_kind,
                           'retired':sorted(c.retired),'seen':list(c.seen)} for k,c in self.controllers.items()}})

    @property
    def state(self):
        return copy.deepcopy(self._view)

    def register_session(self,key,session_id,label):
        """Add/rename one chat without rebuilding active controllers or turn IDs."""
        if key in self.sessions and self.sessions[key]['session_id']!=session_id:
            raise ValueError('cannot_rebind_active_registry_slot')
        proposed=dict(self.sessions,**{key:{'session_id':session_id,'label':label}})
        proposed=validate_sessions(proposed)
        if key not in self.controllers:
            lease=next(iter(self.controllers.values())).lease_seconds
            self.controllers[key]=StatusController(_SessionScreen(),self.root/'sessions'/_hash(session_id),
                session_id=session_id,done_seconds=self.done_seconds,lease_seconds=lease,clock=self.clock)
            self.last_event_at[key]=0
        self.sessions=proposed
        self.by_id={v['session_id']:k for k,v in proposed.items()}
        self.checkpoint=StatusCheckpoint(self.root/'checkpoint.json',{k:_hash(v['session_id']) for k,v in proposed.items()})
        self._publish('registry_updated')
        return self.state

    def _key(self,key):
        if not isinstance(key,str) or key not in self.controllers:
            raise ControllerError('unknown_chat')
        return key

    def inactive_session_keys(self):
        """Return proven inactive slots, oldest first, without changing a batch."""
        if self.recovery_pending:
            return []
        protected=self.participants | self.completed | self.fresh_required
        candidates=[]
        normal_closed={'turn_stopped','interrupted','session_ended','restore_clock','done_timeout'}
        for key,core in self.controllers.items():
            state=core.state
            if (key in protected or key in self.wait_reasons
                    or state['status'] not in ('CLOCK','DONE')
                    or state['recovery_pending'] or core.retry_at is not None
                    or state['reason'] in ('stale','recovery','display_error')
                    or core.closed_kind in ('stale','recovery')):
                continue
            closed=core.closed and core.closed_kind in normal_closed
            virgin=(state['status']=='CLOCK' and state['turn_id'] is None
                    and not core.closed and core.closed_kind is None
                    and not core.seen and not core.retired and core.deadline is None)
            if closed or virgin:
                candidates.append(key)
        return sorted(candidates,key=lambda key:self.last_event_at[key])

    def replace_inactive_session(self,old_key,new_key,session_id,label):
        """Replace one proven inactive slot and publish only the final registry."""
        old_key=self._key(old_key)
        if old_key not in self.inactive_session_keys():
            raise ControllerError('session_not_inactive')
        if session_id==self.sessions[old_key]['session_id']:
            raise ControllerError('replacement_requires_new_session')
        if not isinstance(new_key,str) or (new_key in self.sessions and new_key!=old_key):
            raise ControllerError('session_definition')
        proposed={key:value for key,value in self.sessions.items() if key!=old_key}
        proposed[new_key]={'session_id':session_id,'label':label}
        proposed=validate_sessions(proposed)
        lease=next(iter(self.controllers.values())).lease_seconds
        core=StatusController(_SessionScreen(),self.root/'sessions'/_hash(session_id),
            session_id=session_id,done_seconds=self.done_seconds,lease_seconds=lease,clock=self.clock)
        controllers={key:value for key,value in self.controllers.items() if key!=old_key}
        controllers[new_key]=core
        watermarks={key:value for key,value in self.last_event_at.items() if key!=old_key}
        watermarks[new_key]=0
        checkpoint=StatusCheckpoint(self.root/'checkpoint.json',
                                    {key:_hash(value['session_id']) for key,value in proposed.items()})
        self.sessions,self.controllers,self.last_event_at=proposed,controllers,watermarks
        self.by_id={value['session_id']:key for key,value in proposed.items()}
        self.checkpoint=checkpoint
        self._publish('registry_updated')
        return self.state

    def _new_batch_if_finished(self):
        # A new request after all previous turns have stopped begins a new
        # batch. Archive inactive participants without marking their tasks DONE.
        inactive=all(key in self.completed or (self.controllers[key].closed and
                     self.controllers[key].closed_kind not in ('stale','recovery'))
                     for key in self.participants)
        if self.participants and inactive and not (self.participants & self.fresh_required):
            self.participants.clear()
            self.completed.clear()
            self.fresh_required.clear()
            self.done_deadline=None
            self.done_consumed=False

    def _update_session(self,key,before,event_kind,*,completion=False,reason='manual'):
        controller=self.controllers[key]
        after=controller.state
        if after['turn_id']!=before['turn_id']:
            self.completed.discard(key)
            self.done_deadline=None
            self.done_consumed=False
        active=not controller.closed and after['status'] in ('WORKING','WARNING')
        if after['turn_id'] is not None and (after['turn_id']!=before['turn_id'] or active or completion):
            self.participants.add(key)
        if completion and after['status']=='DONE':
            self.completed.add(key)
        if after['status']=='WARNING':
            if event_kind=='permission_requested' or reason=='permission':
                self.wait_reasons[key]='permission'
            elif event_kind=='turn_failed' or reason=='error':
                self.wait_reasons[key]='error'
            elif event_kind=='input_required':
                self.wait_reasons[key]='question'
        else:
            self.wait_reasons.pop(key,None)

    def _manual(self,key,method,reason):
        key=self._key(key)
        if not isinstance(reason,str) or reason not in REASONS:
            raise ControllerError('event_values')
        controller=self.controllers[key]
        before=controller.state
        if method=='set_done' and key in self.completed:
            if not self.recovery_pending:
                self.fresh_required.discard(key)
                self.await_fresh=False
            self._publish('duplicate',actor=key)
            return self.state
        if method=='set_working' and (controller.closed or before['status'] in ('CLOCK','DONE')):
            self._new_batch_if_finished()
        if method=='heartbeat':
            controller.heartbeat()
        else:
            getattr(controller,method)(reason=reason)
        kind={'set_working':'user_responded','set_warning':'input_required',
              'set_done':'task_completed','restore_clock':'restore_clock','heartbeat':'activity'}[method]
        self._update_session(key,before,kind,completion=method=='set_done',reason=reason)
        if method=='restore_clock':
            self.completed.discard(key)
            self.fresh_required.discard(key)
        after=controller.state
        fresh=((method=='set_working' and after['status']=='WORKING')
               or (method=='set_warning' and after['status']=='WARNING')
               or (method=='set_done' and after['status']=='DONE')
               or (method=='heartbeat' and not controller.closed and after['status'] in ('WORKING','WARNING')))
        if not self.recovery_pending and fresh:
            self.fresh_required.discard(key)
            self.await_fresh=False
        if fresh and after['status'] in ('WORKING','WARNING'):
            controller.deadline=self.clock()+controller.lease_seconds
        if method!='heartbeat':
            self.last_event_at[key]=max(self.last_event_at[key],self.wall_clock())
        self._publish(reason,actor=key)
        return self.state

    def set_working(self,key,reason='manual'): return self._manual(key,'set_working',reason)
    def set_warning(self,key,reason='question'): return self._manual(key,'set_warning',reason)
    def set_done(self,key,reason='explicit_task_complete'): return self._manual(key,'set_done',reason)
    def heartbeat(self,key): return self._manual(key,'heartbeat','heartbeat')

    def restore_clock(self,key=None,reason='manual'):
        if key is not None:
            return self._manual(key,'restore_clock',reason)
        for controller in self.controllers.values():
            controller.restore_clock(reason=reason)
        for key in self.sessions:
            self.last_event_at[key]=max(self.last_event_at[key],self.wall_clock())
        self.participants.clear()
        self.completed.clear()
        self.wait_reasons.clear()
        self.fresh_required.clear()
        self.done_deadline=None
        self.done_consumed=False
        self.await_fresh=False
        if self.recovery_pending:
            self._recover()
        self._publish(reason)
        return self.state

    def accepts_event(self,event):
        """Side-effect-free gate for adapters which correlate tool IDs."""
        event=validate_event(event);key=self.by_id.get(event['session_id'])
        if key is None:return False
        core=self.controllers[key];stamp=event.get('emitted_at');automatic=event['source']!='manual'
        if event['event_id'] in core.seen or core.state['recovery_pending']:return False
        if automatic and (self.strict_events or (self.restarted and key in self.fresh_required)) and stamp is None:return False
        if stamp is not None and (not -5<=self.wall_clock()-stamp<=self.max_event_age or
            (automatic and (stamp<self.last_event_at[key] or (self.restarted and stamp<self.started_at)))):return False
        turn,kind=event['turn_id'],event['kind']
        if kind=='turn_started':return turn not in core.retired and not (turn==core.state['turn_id'] and core.closed)
        if turn!=core.state['turn_id']:return False
        if core.closed and not (kind=='task_completed' and event['source']=='manual' and core.closed_kind=='turn_stopped'):return False
        return core.state['status']!='DONE' or kind=='task_completed'

    def handle(self,event):
        event=validate_event(event)
        key=self.by_id.get(event['session_id'])
        if key is None:
            self._record('unselected_session',event=event)
            return self.state
        controller=self.controllers[key]
        timestamp=event.get('emitted_at')
        automatic=event['source']!='manual'
        if (automatic and (self.strict_events or (self.restarted and key in self.fresh_required))
                and timestamp is None):
            self._record('undated_event',actor=key,event=event)
            return self.state
        if timestamp is not None:
            age=self.wall_clock()-timestamp
            if (age>self.max_event_age or age < -5 or
                    (automatic and (timestamp<self.last_event_at[key] or
                                    (self.restarted and timestamp<self.started_at)))):
                self._record('stale_event',actor=key,event=event)
                return self.state
        before=controller.state
        was_closed=controller.closed
        duplicate=event['event_id'] in controller.seen
        if event['kind']=='restore_clock' and event['turn_id']!=before['turn_id']:
            self._record('stale_turn',actor=key,event=event)
            return self.state
        if (not duplicate and event['kind']=='turn_started' and event['turn_id'] not in controller.retired
                and event['turn_id']!=before['turn_id']):
            self._new_batch_if_finished()
        controller.handle(event)
        after=controller.state
        if (not duplicate and event['kind']=='restore_clock' and event['source']=='manual'):
            self.completed.discard(key)
            self.fresh_required.discard(key)
        accepted=(not duplicate and event['turn_id']==after['turn_id']
                  and (not controller.closed or
                       (event['kind']=='task_completed' and controller.closed_kind=='turn_stopped')))
        # Tool activity says nothing about a pending human decision. Ignored events
        # from a closed turn must not block the next turn either.
        relevant=(event['kind']!='activity' and
                  not (event['kind']=='task_completed' and automatic))
        if (not duplicate and relevant and event['turn_id']==after['turn_id'] and
                (accepted or (not was_closed and controller.closed))):
            observed=timestamp if automatic and timestamp is not None else self.wall_clock()
            self.last_event_at[key]=max(self.last_event_at[key],observed)
        if accepted:
            self._update_session(key,before,event['kind'],
                 completion=event['kind']=='task_completed' and event['source']=='manual',reason=event['reason'])
            if (after['status'] in ('WORKING','WARNING') and event['kind'] in
                    ('turn_started','user_responded','permission_requested','input_required','turn_failed')):
                controller.deadline=self.clock()+controller.lease_seconds
            fresh=(event['kind'] in ('turn_started','user_responded','permission_requested','input_required','turn_failed')
                   or (event['kind']=='activity' and after['status'] in ('WORKING','WARNING'))
                   or (event['kind']=='task_completed' and event['source']=='manual' and after['status']=='DONE'))
            if fresh and not self.recovery_pending:
                self.fresh_required.discard(key)
                self.await_fresh=False
        # A fresh Stop may confirm a recovered active turn without proving task
        # completion. A Stop while WARNING is preserved by the core and cannot
        # clear its pending human decision.
        if (not duplicate and not was_closed and controller.closed and
                event['turn_id']==after['turn_id'] and
                controller.closed_kind in ('turn_stopped','interrupted','session_ended') and
                not self.recovery_pending):
            self.fresh_required.discard(key)
            self.await_fresh=False
        # A closed Stop still changes the aggregate, but cannot claim task completion.
        self._publish('duplicate' if duplicate else event['reason'],actor=key,event=event)
        return self.state

    def _desired(self):
        if self.recovery_pending:
            self.fresh_required.update(self._unverified_participants())
        working=[k for k in self.sessions if k in self.participants and k not in self.fresh_required
                 and self.controllers[k].state['status']=='WORKING']
        waiting=[k for k in self.sessions if k in self.participants and k not in self.fresh_required
                 and self.controllers[k].state['status']=='WARNING' and self.wait_reasons.get(k)!='error']
        errors=[k for k in self.participants if k not in self.fresh_required
                and self.controllers[k].state['status']=='WARNING' and self.wait_reasons.get(k)=='error']
        stale=[k for k in self.participants if k in self.fresh_required
               or (k not in self.completed and self.controllers[k].state['reason']=='stale')]
        order=tuple(waiting)
        if order!=self.wait_order:
            self.wait_order,self.wait_since=order,self.clock()
        focus=None
        if waiting:
            key=waiting[int(max(0,self.clock()-self.wait_since)//self.rotate_seconds)%len(waiting)]
            focus={'key':key,'label':self.sessions[key]['label'],
                   'reason':self.wait_reasons.get(key,'question')}
        elif len(working)==1:
            key=working[0]
            focus={'key':key,'label':self.sessions[key]['label'],'reason':'activity'}
        if self.recovery_pending:
            status='UNKNOWN'
        elif self.await_fresh:
            status='DIAGNOSTIC'
        elif waiting:
            status='WARNING'
        elif working:
            status='WORKING'
        elif errors or stale:
            status='DIAGNOSTIC'
        elif self.participants and self.participants <= self.completed and not (self.participants & self.fresh_required):
            if self.done_deadline is None and not self.done_consumed:
                self.done_deadline=self.clock()+self.done_seconds
            status='DONE' if self.done_deadline is not None else 'CLOCK'
        else:
            status='CLOCK'
        diagnostic=('device_offline' if self.recovery_pending else 'task_error' if errors
                    else 'restart_unverified' if stale and self.restarted else 'stale' if stale else None)
        return {'status':status,'working_count':len(working),'waiting_count':len(waiting),
                'diagnostic_count':len(set(errors)|set(stale)),'diagnostic':diagnostic,
                'completed_count':len(self.completed & self.participants),'stale_count':len(stale),
                'participating_count':len(self.participants),'focus':focus,
                'working_labels':[self.sessions[k]['label'] for k in working],
                'recovery_pending':self.recovery_pending,'clock_restored':status in ('CLOCK','DIAGNOSTIC') and not self.recovery_pending,
                'awaiting_fresh_event':self.await_fresh,
                'sessions':{k:dict(c.state,label=self.sessions[k]['label'],completed=k in self.completed,
                                   fresh_required=k in self.fresh_required,
                                   can_complete=k not in self.fresh_required and
                                     (c.state['status']=='WORKING' or
                                      (c.state['status']=='CLOCK' and c.closed_kind=='turn_stopped')))
                            for k,c in self.controllers.items()}}

    def _record(self,reason,actor=None,event=None):
        record={'at':datetime.now(timezone.utc).isoformat(),
                'reason':reason,'status':self._view.get('status','CLOCK'),
                'working_count':self._view.get('working_count',0),
                'waiting_count':self._view.get('waiting_count',0),
                'completed_count':self._view.get('completed_count',0),
                'diagnostic_count':self._view.get('diagnostic_count',0),
                'clock_restored':self._view.get('clock_restored',True),
                'recovery_pending':self.recovery_pending}
        if actor: record['session_hash']=_hash(self.sessions[actor]['session_id'])
        if event: record.update(event_hash=_hash(event['event_id']),kind=event['kind'],source=event['source'])
        self.log.write(record)
        _atomic_json(self.root/'aggregate.json',record)
        self._save_checkpoint()

    def _publish(self,reason,actor=None,event=None):
        view=self._desired()
        target=view['status']
        if target=='DIAGNOSTIC': target='CLOCK'
        if target!='UNKNOWN' and target!=self.applied_status:
            try:
                if target=='CLOCK': self.display.restore_clock()
                else: self.display.show(target)
                # Physical display operations are synchronous and can be slow.
                # Count the full notification interval from acknowledged display,
                # and never renew it for a duplicate DONE or a failed write.
                if target=='DONE':
                    self.done_deadline=self.clock()+self.done_seconds
                self.applied_status=target
            except (OSError,RuntimeError):
                self.recovery_pending,self.await_fresh=True,True
                self.retry_at=self.clock()+5
                if target=='DONE': self.done_consumed=True
                self.done_deadline=None
                view=self._desired()
                reason='display_error'
        self._view=view
        visual={k:v for k,v in view.items() if k!='sessions'}
        if visual!=self.preview_key:
            if self.on_view: self.on_view(copy.deepcopy(view))
            self.preview_key=copy.deepcopy(visual)
        self._record(reason,actor,event)

    def _recover(self):
        try:
            self.display.restore_clock()
        except (OSError,RuntimeError):
            self.retry_at=self.clock()+5
            return
        self.recovery_pending=False
        self.retry_at=None
        self.applied_status='CLOCK'
        self.await_fresh=bool(self.fresh_required)

    def tick(self):
        for controller in self.controllers.values(): controller.tick()
        if self.done_deadline is not None and self.clock()>=self.done_deadline:
            self.done_deadline=None
            self.done_consumed=True
        if self.recovery_pending and self.clock()>=self.retry_at:
            self._recover()
        before=self.state
        desired=self._desired()
        if desired!=before:
            self._publish('timer')
        return self.state
