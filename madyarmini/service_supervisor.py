"""Single local supervisor. Unexpected exits retry; normal shutdown stays stopped."""
import argparse,json,os,subprocess,sys,threading,time,urllib.request
from pathlib import Path
from .integrated_runtime import StateLock
from .service_recovery import EventJournal

def supervise(spawn,wait,log,clock=time.monotonic):
    failures=0
    while True:
        started=clock()
        try:
            child=spawn();log('child_started',pid=getattr(child,'pid',0));code=child.wait()
            log('child_exited',exit_code=code)
            if code==0:return 0
        except OSError as exc:log('child_launch_failed',error_type=type(exc).__name__)
        if clock()-started>=120:failures=0
        delay=min(60,2**min(failures+1,6));failures+=1
        log('restart_scheduled',delay_seconds=delay)
        if wait(delay):return 0

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def existing_service(root,live,port):
    try:
        path=Path(root)/'runtime.json'
        if path.stat().st_size>32768:return False
        r=json.loads(path.read_text(encoding='utf-8-sig'))
        if r.get('host')!='127.0.0.1' or r.get('port')!=port or r.get('mode')!=('live' if live else 'simulation'):return False
        req=urllib.request.Request(f'http://127.0.0.1:{port}/api/state',headers={'Authorization':'Bearer '+r['panel_token']})
        op=urllib.request.build_opener(urllib.request.ProxyHandler({}),_NoRedirect())
        with op.open(req,timeout=2) as response:
            data=json.loads(response.read(262145))
        return isinstance(data.get('state'),dict) and data.get('simulation') is (not live)
    except (OSError,ValueError,TypeError,KeyError):return False

def main():
    parser=argparse.ArgumentParser(description='Madyar Mini reliable local launcher')
    parser.add_argument('--live',action='store_true');parser.add_argument('--port',type=int,default=8772)
    args=parser.parse_args();project=Path(__file__).resolve().parents[1];root=project/'var/state/integrated'
    if not 1<=args.port<=65535:parser.error('invalid port')
    try:lock=StateLock(root/'supervisor')
    except OSError:return 0
    log=EventJournal(root/'supervisor').write
    try:
        if existing_service(root,args.live,args.port):log('existing_service_running');return 0
        command=[sys.executable,'-m','madyarmini.integrated_service','--port',str(args.port)]
        if args.live:command.append('--live')
        def spawn():
            return subprocess.Popen(command,cwd=project,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),env=os.environ.copy())
        log('supervisor_started',pid=os.getpid())
        return supervise(spawn,threading.Event().wait,log)
    finally:lock.close()

if __name__=='__main__':raise SystemExit(main())
