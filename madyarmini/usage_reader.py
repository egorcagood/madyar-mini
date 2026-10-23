"""Official read-only stdio quota reader; never opens threads or credential files."""
import json
import queue
import subprocess
import threading
import time
from pathlib import Path

from .home_state import normalise_limits


class UsageUnavailable(RuntimeError): pass


def read_usage(runtime,*,timeout=12,popen=subprocess.Popen,clock=time.time):
    if type(timeout) not in (int,float) or not .05<=timeout<=30: raise ValueError('usage_timeout')
    child=None
    worker=None
    messages=queue.Queue(maxsize=8)
    finished=threading.Event()
    end=time.monotonic()+timeout
    def receive():
        try:
            while not finished.is_set():
                raw=child.stdout.readline(262145)
                if not raw or len(raw)>262144: break
                try:
                    row=json.loads(raw)
                    if isinstance(row,dict) and row.get('id') in (1,2): messages.put_nowait(row)
                except (ValueError,queue.Full): pass
        except (OSError,ValueError): pass
        finally:
            try: messages.put_nowait(None)
            except queue.Full: pass
    def send(value):
        child.stdin.write((json.dumps(value)+'\n').encode('utf-8'));child.stdin.flush()
    def reply(expected):
        while True:
            remaining=end-time.monotonic()
            if remaining<=0: raise UsageUnavailable('usage_timeout')
            try: row=messages.get(timeout=remaining)
            except queue.Empty: raise UsageUnavailable('usage_timeout') from None
            if row is None: raise UsageUnavailable('usage_closed')
            if row.get('id')==expected:
                if 'error' in row or not isinstance(row.get('result'),dict):
                    raise UsageUnavailable('usage_rejected')
                return row['result']
    try:
        child=popen([str(runtime),'app-server','--listen','stdio://'],stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                    cwd=Path(__file__).resolve().parents[1],
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        worker=threading.Thread(target=receive,daemon=True)
        worker.start()
        send({'id':1,'method':'initialize','params':{'clientInfo':{'name':'madyar_mini','title':'Madyar Mini','version':'0.1'}}})
        reply(1)
        send({'method':'initialized','params':{}})
        send({'id':2,'method':'account/rateLimits/read'})
        result=normalise_limits(reply(2),clock())
        if result is None: raise UsageUnavailable('usage_missing')
        return result
    except (OSError,ValueError,TypeError) as exc:
        raise UsageUnavailable('usage_unavailable') from None
    finally:
        finished.set()
        if child is not None:
            try:
                if child.poll() is None: child.kill()
                child.wait(timeout=3)
            except (OSError,subprocess.TimeoutExpired): pass
            if worker: worker.join(1)
            # Real pipes are closed; test BytesIO buffers remain inspectable.
            for pipe in (child.stdin,child.stdout):
                if pipe is not None and hasattr(pipe,'fileno'):
                    try:
                        pipe.fileno();pipe.close()
                    except (OSError,ValueError): pass
