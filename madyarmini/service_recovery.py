"""Bounded technical diagnostics and recovery after a suspend/time discontinuity."""
import json,re,threading,time
from pathlib import Path

class EventJournal:
    FIELDS={'worker','error_type','exit_code','delay_seconds','pid'}
    def __init__(self,root,*,max_bytes=262144):
        self.path=Path(root)/'service-events.jsonl';self.max_bytes=max_bytes;self.lock=threading.Lock()
    def write(self,event,**fields):
        if not re.fullmatch(r'[a-z_]{1,48}',event) or set(fields)-self.FIELDS:raise ValueError('diagnostic_fields')
        if any(not isinstance(v,(str,int,float,bool)) or isinstance(v,str) and not re.fullmatch(r'[A-Za-z0-9_]{1,64}',v) for v in fields.values()):raise ValueError('diagnostic_value')
        row=json.dumps(dict(at=time.time(),event=event,**fields),ensure_ascii=True)+'\n'
        with self.lock:
            try:
                self.path.parent.mkdir(parents=True,exist_ok=True)
                if self.path.exists() and self.path.stat().st_size>=self.max_bytes:
                    one=self.path.with_name(self.path.name+'.1');two=self.path.with_name(self.path.name+'.2')
                    if one.exists():one.replace(two)
                    self.path.replace(one)
                with self.path.open('a',encoding='utf-8') as f:f.write(row)
                return True
            except OSError:return False

class ResumeDetector:
    def __init__(self,wall,mono):self.wall=wall;self.mono=mono
    def update(self,wall,mono):
        w,m=wall-self.wall,mono-self.mono;self.wall,self.mono=wall,mono
        return w>20 or m>20 or abs(w-m)>5

def wait_refresh(stop,refresh,seconds):
    end=time.monotonic()+seconds
    while not stop.is_set():
        remaining=end-time.monotonic()
        if remaining<=0 or refresh.wait(min(1,remaining)):return
