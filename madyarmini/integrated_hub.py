"""Thread-safe coordinator; private notice content never enters persistent state."""
import copy
import hashlib
import json
import math
import re
import threading
import time
import uuid
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

from .integration_data import default_settings,validate_settings,NoticeQueue,weather_packet,clean_text
from .home_state import usage_view
from .multi_controller import MultiStatusController,validate_sessions
from .notification import _atomic_json
from .status_controller import validate_event
from .tracking_registry import recover_registry,validate_archive,archived_turns,remember_archived_turns


class _LogicalDisplay:
    def show(self,status):pass
    def restore_clock(self):pass


class IntegratedHub:
    def __init__(self,root,*,clock=time.time,timer=time.monotonic):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.clock,self.timer=clock,timer;self.lock=threading.RLock()
        self.settings=validate_settings(self._load('settings.json',default_settings()))
        recover_registry(self.root)
        self.registry=self._load('chats.json',{})
        self.archived_chats=validate_archive(self._load('archived_chats.json',{}))
        if self.registry:validate_sessions(self.registry)
        self.controller=None
        if self.registry:self._make_controller()
        self.notices=NoticeQueue(clock=clock,seconds=self.settings['notice_seconds'])
        self.usage=self.weather=None;self.weather_revision=0
        self.pending={};self.inflight={};self.anonymous_permissions={}
        self.input_waiting=set();self.candidates={};self.done_id=None;self.last_mode=None;self.hidden_done=None
        self.candidate_active=set();self.tracking_uncertain=False;self.registry_failed=False;self.candidate_overflow=False
        self.reply_notice=None;self.reply_until=0;self.reply_manual=False
        pending=self._load('pending_chats.json',{'packets':[],'uncertain':False})
        if set(pending) not in ({'packets','uncertain'},{'packets','uncertain','overflow'}) or type(pending.get('overflow',False)) is not bool or type(pending['uncertain']) is not bool or not isinstance(pending['packets'],list) or len(pending['packets'])>16:raise ValueError('pending_chats')
        self.tracking_uncertain=pending['uncertain'];self.candidate_overflow=pending.get('overflow',False)
        for packet in pending['packets']:
            event=validate_event(packet['event']);sid=event['session_id']
            if event['source']!='hook' or set(packet)!={'event','hook','tool_name','tool_use_id','blocking_input'}:raise ValueError('pending_chat_packet')
            if sid not in {v['session_id'] for v in self.registry.values()}:self.candidates[sid]=packet;self.candidate_active.add(sid)
        if self.controller:
            for key,core in self.controller.controllers.items():
                if (core.state['status']=='WARNING' and core.state['turn_id']
                        and self.controller.wait_reasons.get(key)=='question'):
                    self.input_waiting.add((self.registry[key]['session_id'],core.state['turn_id']))
        self.sources={'codex':{'status':'not_connected','last_received':None,'windows_app_verified':False},
                      'quota':{'status':'not_connected'},'weather':{'status':'not_connected'},
                      'telegram':{'status':'permission_required'},'device':{'status':'simulation'}}
        self.preview=None;self.preview_until=0
        self.desktop_seen={}
        self.sources['desktop_notifications']={'status':'not_connected','received_count':0,'classification':'unverified'}
        self.sound=lambda kind:None;self.preview_id=None
    def _load(self,name,default):
        path=self.root/name
        if not path.exists():return copy.deepcopy(default)
        raw=path.read_bytes()
        if len(raw)>32768:raise ValueError('state_file_limit')
        return json.loads(raw)
    def _make_controller(self):
        self.controller=MultiStatusController(_LogicalDisplay(),self.root/'controller',sessions=self.registry,
            done_seconds=self.settings['done_seconds'],clock=self.timer,wall_clock=self.clock,strict_events=True)
    def _register(self,sid,label=None):
        for key,row in self.registry.items():
            if row['session_id']==sid:return key
        key='c'+hashlib.sha256(sid.encode()).hexdigest()[:12]
        archived=self.archived_chats.get(key)
        label=label or (archived['label'] if archived else None)
        labels={r['label'].casefold() for r in self.registry.values()}
        if not label or label.casefold() in labels:
            n=len(self.registry)+len(self.archived_chats)+1
            while f'чат {n}' in labels:n+=1
            label=f'Чат {n}'
        row={'session_id':sid,'label':label}
        if len(self.registry)>=8:
            slots=self.controller.inactive_session_keys()
            if not slots:raise ValueError('tracked_chat_limit')
            old=slots[0];proposed={k:v for k,v in self.registry.items() if k!=old};proposed[key]=row
            validate_sessions(proposed)
            old_row=self.registry[old];core=self.controller.controllers[old]
            try:
                remember_archived_turns(self.root,old,core.retired|({core.state['turn_id']} if core.state['turn_id'] else set()))
                self.archived_chats[old]=dict(old_row,turn_id=core.state['turn_id'],retired_at=self.clock())
                self.archived_chats=dict(sorted(self.archived_chats.items(),key=lambda p:p[1]['retired_at'],reverse=True)[:64])
                _atomic_json(self.root/'archived_chats.json',self.archived_chats)
                journal=self.root/'registry-update.json';_atomic_json(journal,dict(schema=1,old_registry=self.registry,new_registry=proposed))
                self.controller.replace_inactive_session(old,key,sid,label)
                self.registry=proposed;_atomic_json(self.root/'chats.json',self.registry);journal.unlink()
            except BaseException:self.registry_failed=True;raise
            old_sid=old_row['session_id']
            for mapping in (self.pending,self.inflight,self.anonymous_permissions):
                for pair in list(mapping):
                    if pair[0]==old_sid:mapping.pop(pair,None)
            self.input_waiting={p for p in self.input_waiting if p[0]!=old_sid}
            return key
        validate_sessions(dict(self.registry,**{key:row}))
        if self.controller:self.controller.register_session(key,sid,label)
        self.registry[key]=row
        if self.controller is None:self._make_controller()
        _atomic_json(self.root/'chats.json',self.registry)
        return key
    def _save_candidates(self):
        _atomic_json(self.root/'pending_chats.json',dict(packets=[self.candidates[s] for s in self.candidate_active if s in self.candidates],uncertain=self.tracking_uncertain,overflow=self.candidate_overflow))
    def _remember_candidate(self,packet):
        event=packet['event'];sid=event['session_id'];old=self.candidates.get(sid)
        if old and event['turn_id']!=old['event']['turn_id'] and event['kind']!='turn_started':return
        if old and event['emitted_at']<old['event']['emitted_at']:return
        if sid not in self.candidates and len(self.candidates)>=16:
            inactive=next((s for s in self.candidates if s not in self.candidate_active),None)
            if inactive is not None:self.candidates.pop(inactive)
            else:
                self.candidate_overflow=True;self.tracking_uncertain=True;self._save_candidates();return
        self.candidates[sid]=copy.deepcopy(packet)
        if event['kind'] in ('turn_stopped','interrupted','session_ended'):self.candidate_active.discard(sid)
        else:self.candidate_active.add(sid)
        if self.settings['auto_track'] and self.candidate_active:self.tracking_uncertain=True
        self._save_candidates()
    def _reply_ready_view(self):
        remaining=self.reply_until-self.timer()
        if not self.reply_notice or remaining<=0 or self.settings['paused']:return {'active':False}
        return dict(active=True,id=self.reply_notice,ttl_ms=max(1,min(5000,int(remaining*1000))),manual_preview=self.reply_manual)
    def rename(self,key,label):
        with self.lock:
            if key not in self.registry:raise ValueError('unknown_chat')
            self.controller.register_session(key,self.registry[key]['session_id'],label)
            self.registry[key]['label']=label;_atomic_json(self.root/'chats.json',self.registry)
    def track(self,sid,label):
        with self.lock:
            if sid not in self.candidates:raise ValueError('unknown_candidate')
            packet=self.candidates[sid]
            if (packet['event']['kind']!='turn_started' or not -5<=self.clock()-packet['event']['emitted_at']<=30 or (self.controller and self.controller.restarted and packet['event']['emitted_at']<self.controller.started_at)):raise ValueError('fresh_start_required')
            if (self.controller and packet['event']['kind']=='turn_started' and
                    -5<=self.clock()-packet['event']['emitted_at']<=30):self.controller._new_batch_if_finished()
            self._register(sid,label);self.candidates.pop(sid);self.candidate_active.discard(sid);self._save_candidates()
            # Only a fresh real start can reconstruct a newly selected chat.
            if packet['event']['kind']=='turn_started':self.hook(packet)
    def hook(self,packet):
        with self.lock:
            if not isinstance(packet,dict) or set(packet)!={'event','hook','tool_name','tool_use_id','blocking_input'}:
                raise ValueError('hook_fields')
            event=validate_event(packet['event'])
            if event['source']!='hook' or event['kind']=='task_completed':raise ValueError('hook_source')
            now=self.clock();at=event.get('emitted_at')
            if at is None or not -5<=now-at<=30:return self.reply()
            sid,turn=event['session_id'],event['turn_id']
            archive_key='c'+hashlib.sha256(sid.encode()).hexdigest()[:12]
            if event['kind']=='turn_started' and event['turn_id'] in archived_turns(self.root,archive_key):return self.reply()
            self.sources['codex'].update(status='receiving_hooks',last_received=now)
            if sid not in {r['session_id'] for r in self.registry.values()}:
                archived=self.archived_chats.get('c'+hashlib.sha256(sid.encode()).hexdigest()[:12])
                if archived and (event['kind']!='turn_started' or event['turn_id']==archived['turn_id']):return self.reply()
                if not self.settings['auto_track'] or event['kind']!='turn_started':
                    self._remember_candidate(packet);return self.reply()
                if self.controller and self.controller.restarted and at<self.controller.started_at:
                    self._remember_candidate(packet);return self.reply()
                if len(self.registry)>=8:self.controller._new_batch_if_finished()
                if len(self.registry)>=8 and not self.controller.inactive_session_keys():
                    self._remember_candidate(packet);return self.reply()
            key=self._register(sid)
            if sid in self.candidates:
                self.candidates.pop(sid,None);self.candidate_active.discard(sid);self._save_candidates()
            if not self.controller.accepts_event(event):
                self.controller.handle(event)
                return self.reply()
            pair=(sid,turn);use=packet.get('tool_use_id')
            if event['kind']=='turn_started':
                self.controller._new_batch_if_finished()
                self.reply_notice=None;self.reply_until=0
                if not self.candidate_active and not self.controller.participants:
                    self.tracking_uncertain=False;self._save_candidates()
                self.pending={k:v for k,v in self.pending.items() if k[0]!=sid}
                self.inflight={k:v for k,v in self.inflight.items() if k[0]!=sid}
                self.anonymous_permissions={k:v for k,v in self.anonymous_permissions.items() if k[0]!=sid}
                self.input_waiting={k for k in self.input_waiting if k[0]!=sid}
            if packet.get('hook')=='PreToolUse' and use:
                self.inflight.setdefault(pair,{}).setdefault(packet['tool_name'],set()).add(use)
            if event['kind']=='permission_requested' or (event['kind']=='input_required' and packet.get('blocking_input')):
                if use:
                    self.pending.setdefault(pair,set()).add(use)
                else:
                    # PermissionRequest need not include tool_use_id. Correlate only
                    # with calls already in flight; absent evidence stays WARNING.
                    calls=self.inflight.get(pair,{}).get(packet['tool_name'],set())
                    self.anonymous_permissions.setdefault(pair,[]).append(set(calls) if calls else None)
            elif event['kind']=='input_required':self.input_waiting.add(pair)
            elif packet.get('hook')=='PostToolUse':
                resolved=False
                pending=self.pending.get(pair)
                if use and pending and use in pending:
                    pending.remove(use)
                    if not pending:
                        self.pending.pop(pair,None)
                        resolved=True
                calls=self.inflight.get(pair,{}).get(packet['tool_name'])
                if use and calls and use in calls:
                    calls.remove(use)
                    if not calls:self.inflight[pair].pop(packet['tool_name'])
                    if not self.inflight[pair]:self.inflight.pop(pair)
                    anonymous=self.anonymous_permissions.get(pair)
                    if anonymous:
                        for group in anonymous:
                            if group is not None:group.discard(use)
                        if any(group is not None and not group for group in anonymous):resolved=True
                        anonymous[:]=[group for group in anonymous if group is None or group]
                        if not anonymous:self.anonymous_permissions.pop(pair,None)
                if resolved and pair not in self.pending and pair not in self.anonymous_permissions and pair not in self.input_waiting:
                    event=dict(event,kind='user_responded')
            core=self.controller.controllers[key]
            eligible_reply=(event['kind']=='turn_stopped' and not core.closed and core.state['status']=='WORKING' and pair not in self.input_waiting)
            self.controller.handle(event)
            if (eligible_reply and core.closed and core.closed_kind=='turn_stopped'
                    and core.state['status']=='CLOCK' and key not in self.controller.completed):
                self.reply_notice=int(uuid.uuid4().hex[:8],16) or 1;self.reply_until=self.timer()+5;self.reply_manual=False
            return self.reply()
    def task(self,sid,turn,state):
        with self.lock:
            if state not in ('working','warning','done'):raise ValueError('task_state')
            key=next((k for k,v in self.registry.items() if v['session_id']==sid),None)
            if key is None or self.controller.state['sessions'][key]['turn_id']!=turn:raise ValueError('task_identity')
            if state=='done':self.controller.set_done(key)
            elif state=='warning':
                self.controller.set_warning(key,reason='question');self.input_waiting.add((sid,turn))
            else:
                if self.controller.controllers[key].closed:raise ValueError('closed_turn_requires_new_start')
                self.input_waiting.discard((sid,turn))
                if not self.pending.get((sid,turn)) and not self.anonymous_permissions.get((sid,turn)):
                    self.controller.set_working(key)
            return self.reply()
    def configure(self,settings):
        settings=validate_settings(settings)
        with self.lock:
            if settings['place']!=self.settings['place']:
                self.weather=None;self.weather_revision+=1;self.sources['weather']['status']='loading'
            if settings['paused'] or not settings['telegram_enabled']:self.notices.clear()
            if settings['paused'] and self._state()['status']=='DONE':self.hidden_done=self.done_id
            self.settings=settings;self.notices.seconds=settings['notice_seconds']
            if self.controller:
                self.controller.done_seconds=settings['done_seconds']
                for c in self.controller.controllers.values():c.done_seconds=settings['done_seconds']
            _atomic_json(self.root/'settings.json',settings)
    def desktop_signal(self,payload):
        with self.lock:
            source=self.sources['desktop_notifications'];now=self.clock()
            if set(payload)=={'status'}:
                if payload['status'] not in ('permission_required','allowed','denied','capability_missing','collector_stopped','unknown'):raise ValueError('desktop_status')
                source.update(status=payload['status'],updated_at=now);return False
            if set(payload)!={'source_id','created_at','origin'} or payload['origin']!='chatgpt_desktop':raise ValueError('desktop_fields')
            sid=payload['source_id'];at=payload['created_at']
            if not isinstance(sid,str) or not re.fullmatch(r'win:[0-9]{1,10}:[0-9]{1,20}',sid):raise ValueError('desktop_id')
            if type(at) not in (int,float) or not math.isfinite(at):raise ValueError('desktop_time')
            if not -5<=now-at<=120:return False
            self.desktop_seen={k:v for k,v in self.desktop_seen.items() if now-v<=120}
            if sid in self.desktop_seen or len(self.desktop_seen)>=4096:return False
            self.desktop_seen[sid]=at
            source.update(received_count=source['received_count']+1,last_notice_at=now)
            return True
    def telegram(self,payload):
        with self.lock:
            if set(payload)=={'status'}:
                if payload['status'] not in ('permission_required','allowed','denied','capability_missing','collector_stopped','unknown'):
                    raise ValueError('notification_source_status')
                self.sources['telegram'].update(status=payload['status'],updated_at=self.clock())
                return False
            if set(payload)!={'app','source_id','title','body','created_at'}:raise ValueError('notification_fields')
            if self.settings['paused'] or not self.settings['telegram_enabled']:return False
            accepted=self.notices.add(payload['source_id'],payload['title'],payload['body'],created_at=payload['created_at'],app=payload['app'])
            if accepted:
                source=self.sources['telegram']
                source.update(received_count=source.get('received_count',0)+1,last_notice_at=self.clock())
            if accepted and self.settings['sound']:self.sound('telegram')
            return accepted
    def clear_notices(self):
        with self.lock:self.notices.clear();self.reply_notice=None;self.reply_until=0
    def _state(self):
        if self.controller:self.controller.tick();state=self.controller.state
        else:state=dict(status='CLOCK',working_count=0,waiting_count=0,completed_count=0,diagnostic_count=0,sessions={},focus=None)
        if self.registry_failed or (self.settings['auto_track'] and (self.candidate_active or self.candidate_overflow)):
            if state['status']!='WARNING':state['status']='DIAGNOSTIC'
            state['diagnostic_count']+=max(1,len(self.candidate_active))
        elif self.tracking_uncertain and state['status']=='DONE':state['status']='CLOCK'
        if state['status']=='DONE' and self.last_mode!='DONE':self.done_id=uuid.uuid4().hex
        if state['status']=='WARNING' and self.last_mode!='WARNING' and self.settings['sound'] and not self.settings['paused']:
            self.sound('warning')
        self.last_mode=state['status']
        if self.settings['paused'] and state['status']=='DONE':self.hidden_done=self.done_id
        return state
    def reply(self):
        state=self._state()
        return dict(status=state['status'],counters={k:state.get(k+'_count',0) for k in ('working','waiting','completed','diagnostic')})
    def snapshot(self):
        with self.lock:
            state=self._state()
            sources=copy.deepcopy(self.sources)
            last=sources['codex']['last_received']
            if last is not None and self.clock()-last>600:
                sources['codex']['status']='idle' if state['status']=='CLOCK' else 'stale'
            return dict(settings=copy.deepcopy(self.settings),state=state,sources=sources,
                        usage=usage_view(self.usage,self.clock(),prefer_weekly=True),weather=weather_packet(self.weather,self.clock()),
                        weather_raw=copy.deepcopy(self.weather),
                        reply_ready=self._reply_ready_view(),tracking=dict(archived_count=len(self.archived_chats),capacity=8,untracked_active=len(self.candidate_active)+int(self.candidate_overflow),overflow=self.candidate_overflow,registry_error=self.registry_failed),
                        notification_count=self.notices.pending_count,candidates=list(self.candidates),
                        simulation=self.sources['device']['status']=='simulation')
    def packet(self):
        with self.lock:
            state=self._state();now=self.clock();settings=self.settings
            mode={'CLOCK':'HOME','UNKNOWN':'DIAGNOSTIC','DIAGNOSTIC':'DIAGNOSTIC'}.get(state['status'],state['status'])
            if settings['paused'] or (mode=='DONE' and self.done_id==self.hidden_done):mode='HOME'
            active=state['working_count'] if mode in ('WORKING','WARNING','DIAGNOSTIC') else 0
            waiting=state['waiting_count'] if mode in ('WARNING','DIAGNOSTIC') else 0
            focus=state.get('focus') or {}
            last=self.sources['codex']['last_received']
            # Hooks are events, not an application heartbeat. A confirmed idle
            # controller remains ready to receive work during quiet periods;
            # silence alone cannot prove the Windows App disconnected.
            connected=last is not None and now-last>=-5 and (now-last<=600 or state['status']=='CLOCK')
            result=dict(mode=mode,active=active,waiting=waiting,label=focus.get('label',''),
                clock=int(now),source_connected=connected,done_seconds=settings['done_seconds'],done_id=self.done_id,
                city=clean_text(settings['place']['name'],24,64),country=settings['place']['country'],
                palette=copy.deepcopy(settings['palette']),show_ip=settings['show_ip'],ip_text=settings['ip_text'],clock_style=settings['clock_style'],
                animation=dict(enabled=settings['animations_enabled'],seconds=settings['animation_seconds'],
                               interval_seconds=settings['animation_interval_seconds']),
                utc_offset=int(datetime.fromtimestamp(now,ZoneInfo(settings['place']['timezone'])).utcoffset().total_seconds()),
                display=dict(brightness=settings['brightness'],night_enabled=settings['night_enabled'],
                             night_start=settings['night_start'],night_end=settings['night_end'],night_brightness=settings['night_brightness']))
            if settings['background_solid']:result['palette']['background_to']=result['palette']['background_from']
            # Manual/MCP states still have a real local controller as their source.
            if mode in ('WORKING','WARNING','DONE'):result['source_connected']=True
            weather=weather_packet(self.weather,now)
            if weather:result['weather']=weather
            quota=usage_view(self.usage,now,prefer_weekly=True)
            if quota:
                remaining=max(1,int((600-(now-quota['observed_at']))*1000))
                result['quota']=dict(remaining=int(quota['remaining_percent']),window_minutes=quota['window_minutes'],ttl_ms=min(600000,remaining),resets_at=quota['resets_at'])
            ready=self._reply_ready_view()
            if ready['active'] and mode in ('HOME','WORKING') and not settings['paused']:
                result['reply_ready']={k:ready[k] for k in ('id','ttl_ms')}
            if not settings['paused'] and mode not in ('WARNING','DIAGNOSTIC'):
                notice=self.notices.current()
                if notice:result['notice']=notice
            if self.preview and self.timer()<self.preview_until and not active and not waiting:
                result.update(mode=self.preview,active=1 if self.preview=='WORKING' else 0,
                              waiting=1 if self.preview=='WARNING' else 0,label='Тест',source_connected=True,
                              done_id=self.preview_id if self.preview=='DONE' else self.done_id)
                result.pop('notice',None)
            return result
    def test_animation(self,mode,seconds=5):
        with self.lock:
            state=self._state()
            if mode=='REPLY':
                if self.settings['paused'] or state['status'] not in ('CLOCK','WORKING'):raise ValueError('reply_test_requires_available_state')
                self.reply_notice=int(uuid.uuid4().hex[:8],16) or 1;self.reply_until=self.timer()+5;self.reply_manual=True
                return
            if mode not in ('WORKING','WARNING','DONE') or state['working_count'] or state['waiting_count']:
                raise ValueError('animation_test_requires_idle')
            self.preview=mode;self.preview_until=self.timer()+seconds
            self.preview_id=uuid.uuid4().hex
