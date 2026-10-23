"""Local integrated service. Simulation is default; live hardware is explicit."""
import copy
import hmac
import json
import re
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn

from .integrated_hub import IntegratedHub
from .integrated_runtime import StateLock,secure_state,play_sound
from .integrated_transport import IntegratedDeviceClient,SimulationDevice
from .integration_sources import WeatherClient
from .notification import _atomic_json
from .status_service import _LocalServer
from .usage_reader import read_usage,UsageUnavailable
from .service_recovery import EventJournal,ResumeDetector,wait_refresh


SERVICE_VERSION='0.3.1'


class _Server(ThreadingMixIn,_LocalServer):
    daemon_threads=True
    def __init__(self,*args,**kw):
        self.slots=threading.BoundedSemaphore(12)
        super().__init__(*args,**kw)
    def process_request(self,request,address):
        if not self.slots.acquire(False):self.shutdown_request(request);return
        try:super().process_request(request,address)
        except BaseException:self.slots.release();raise
    def process_request_thread(self,request,address):
        try:super().process_request_thread(request,address)
        finally:self.slots.release()


class IntegratedService:
    def __init__(self,project,state_dir,*,port=8772,sources=True,live=False,runtime=None):
        self.project=Path(project);self.state_lock=StateLock(state_dir)
        try:self.hub=IntegratedHub(state_dir)
        except BaseException:self.state_lock.close();raise
        self.hub.sound=play_sound
        self.stop=threading.Event();self.sources_enabled=sources;self.live=live
        self.journal=EventJournal(state_dir);self.worker_failure=None
        self.quota_refresh=threading.Event();self.weather_refresh=threading.Event();self.device_refresh=threading.Event()
        self.explicit_runtime=runtime is not None;self.quota_failures=0
        self.tokens={role:secrets.token_hex(32) for role in ('panel','hook','notification','mcp')}
        self.runtime=Path(runtime) if runtime else self._find_runtime()
        self.weather=WeatherClient();self.workers=[]
        self.device=SimulationDevice()
        if live:
            cfg=json.loads((self.project/'firmware/private/device.json').read_text(encoding='utf-8'))
            if not self.hub.settings['device_host']:self.hub.settings['device_host']=cfg['host']
            if cfg['host']!=self.hub.settings['device_host']:raise ValueError('device_host_requires_matching_private_config')
            self.device=IntegratedDeviceClient(cfg['host'],cfg['key'],on_new_owner=self.hub.clear_notices)
            self.hub.sources['device']={'status':'connecting'}
        try:self.server=_Server(('127.0.0.1',port),_Handler)
        except BaseException:self.state_lock.close();raise
        self.server.service=self;self.server.timeout=.15;self.port=self.server.server_address[1]
        _atomic_json(self.hub.root/'runtime.json',dict(host='127.0.0.1',port=self.port,
            **{role+'_token':token for role,token in self.tokens.items()},mode='live' if live else 'simulation'))
    @staticmethod
    def _find_runtime():
        import os
        root=Path(os.environ.get('LOCALAPPDATA',''))/'OpenAI/Codex/bin'
        paths=list(root.glob('*/codex.exe')) if root.is_dir() else []
        return max(paths,key=lambda p:p.stat().st_mtime) if paths else None
    def _set_source(self,name,status,**extra):
        with self.hub.lock:self.hub.sources[name]={'status':status,'updated_at':time.time(),**extra}
    def _quota_once(self):
        try:
            if not self.explicit_runtime:self.runtime=self._find_runtime()
            if self.runtime is None:raise UsageUnavailable('runtime_missing')
            usage=read_usage(self.runtime)
            with self.hub.lock:self.hub.usage=usage
            self._set_source('quota','available');self.quota_failures=0
            return 300
        except (UsageUnavailable,OSError,ValueError) as exc:
            self.quota_failures+=1;delay=min(60,15*2**min(self.quota_failures-1,2))
            self._set_source('quota','unavailable',error_type=type(exc).__name__,retry_seconds=delay)
            self.journal.write('quota_unavailable',error_type=type(exc).__name__,delay_seconds=delay)
            return delay
    def _quota_loop(self):
        while not self.stop.is_set():
            self.quota_refresh.clear();delay=self._quota_once()
            wait_refresh(self.stop,self.quota_refresh,delay)
    def _on_resume(self):
        self.quota_refresh.set();self.weather_refresh.set();self.device_refresh.set()
        self.journal.write('resume_refresh_requested')
    def _resume_loop(self):
        detector=ResumeDetector(time.time(),time.monotonic())
        while not self.stop.wait(1):
            if detector.update(time.time(),time.monotonic()):self._on_resume()
    def _guard_worker(self,name,target):
        try:
            target()
            if not self.stop.is_set():raise RuntimeError('worker_stopped')
        except Exception as exc:
            self.worker_failure=name;self.journal.write('worker_failed',worker=name,error_type=type(exc).__name__)
            self.stop.set()
    def _weather_loop(self):
        revision=None;next_fetch=0
        while not self.stop.is_set():
            if self.weather_refresh.is_set():self.weather_refresh.clear();next_fetch=0
            with self.hub.lock:place=copy.deepcopy(self.hub.settings['place']);current_revision=self.hub.weather_revision
            if current_revision!=revision or time.monotonic()>=next_fetch:
                revision=current_revision;next_fetch=time.monotonic()+600
                try:
                    weather=self.weather.current(place)
                    with self.hub.lock:
                        if revision==self.hub.weather_revision:self.hub.weather=weather;self._set_source('weather','available')
                except (OSError,RuntimeError,ValueError) as exc:
                    next_fetch=time.monotonic()+30
                    self._set_source('weather','unavailable',error_type=type(exc).__name__)
                    self.journal.write('weather_unavailable',error_type=type(exc).__name__)
            if self.stop.wait(1):break
    @staticmethod
    def _signature(packet):
        data=copy.deepcopy(packet);data.pop('clock',None)
        for field in ('notice','weather','quota','reply_ready'):
            if field in data:data[field].pop('ttl_ms',None)
        return json.dumps(data,sort_keys=True,ensure_ascii=False)
    def _device_loop(self):
        last_signature=None;last_sent=0;retry=0
        while not self.stop.is_set():
            packet=self.hub.packet();signature=self._signature(packet);now=time.monotonic()
            if now>=retry and (signature!=last_signature or now-last_sent>=10 or self.device_refresh.is_set()):
                self.device_refresh.clear()
                try:
                    self.device.send(packet);last_signature=signature;last_sent=time.monotonic()
                    self._set_source('device','connected' if self.live else 'simulation',health=getattr(self.device,'health',None))
                except (OSError,RuntimeError,ValueError,KeyError) as exc:
                    retry=time.monotonic()+5;self._set_source('device','offline',error_type=type(exc).__name__,http_status=getattr(exc,'code',None))
                    self.journal.write('device_unavailable',error_type=type(exc).__name__)
            if self.stop.wait(.25):break
    def run(self):
        try:
            self.journal.write('service_started')
            targets=[self._device_loop,self._resume_loop]
            if self.sources_enabled:targets.extend([self._quota_loop,self._weather_loop])
            for target in targets:
                t=threading.Thread(target=self._guard_worker,args=(target.__name__.strip('_'),target),daemon=True);self.workers.append(t);t.start()
            while not self.stop.is_set():self.server.handle_request()
        except KeyboardInterrupt:pass
        finally:
            self.stop.set();self.server.server_close()
            for t in self.workers:t.join(14)
            self.state_lock.close()
            self.journal.write('service_stopped')
        if self.worker_failure:raise RuntimeError('background_worker_failed')
    def home_data(self):
        with self.hub.lock:snapshot=self.hub.snapshot();p=self.hub.packet()
        state=snapshot['state'];weather=snapshot['weather_raw']
        fresh=snapshot['weather'] is not None
        return dict(schema=1,server_time_ms=int(time.time()*1000),timezone=snapshot['settings']['place']['timezone'],
            city=p['city'],country=p['country'],mode=p['mode'],notice=p.get('notice'),device_ip=snapshot['settings']['device_host'],
            palette=p['palette'],show_ip=p['show_ip'],ip_text=p['ip_text'],animation=p['animation'],clock_style=p['clock_style'],
            usage=snapshot['usage'],reply_ready=p.get('reply_ready'),weather=dict(available=fresh,description=weather['description'] if fresh else 'Нет данных',
                kind=weather['kind'] if fresh else 'unknown',temperature_c=weather['temperature_c'] if fresh else None,
                feels_c=weather['feels_c'] if fresh else None,wind_ms=weather['wind_ms'] if fresh else None),
            codex=dict(status=p['mode'] if p['mode']!='HOME' else ('CLOCK' if p['source_connected'] else 'DISCONNECTED'),
                working_count=p['active'],waiting_count=p['waiting'],
                label=p['label'],simulation=not self.live),physical_output_enabled=self.live)


class _Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.0';server_version='MadyarMini';sys_version=''
    def log_message(self,*args):pass
    def send(self,code,value,kind='application/json; charset=utf-8'):
        body=value if isinstance(value,bytes) else json.dumps(value,ensure_ascii=False).encode()
        try:
            self.send_response(code)
            for k,v in {'Content-Type':kind,'Content-Length':str(len(body)),'Cache-Control':'no-store',
                'X-Madyar-Service-Version':SERVICE_VERSION,'X-Content-Type-Options':'nosniff','X-Frame-Options':'SAMEORIGIN','Referrer-Policy':'no-referrer',
                'Content-Security-Policy':"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'self'; base-uri 'none'"}.items():self.send_header(k,v)
            self.end_headers();self.wfile.write(body)
        except OSError:pass
    def allowed(self,role='panel',public=False):
        s=self.server.service;origin='http://127.0.0.1:'+str(s.port)
        if self.headers.get('Host')!=f'127.0.0.1:{s.port}' or self.headers.get('Origin') not in (None,origin):
            self.send(403,{'error':'local_only'});return False
        if not public and not hmac.compare_digest(self.headers.get('Authorization','').encode(),('Bearer '+s.tokens[role]).encode()):
            self.send(401,{'error':'unauthorized'});return False
        return True
    def do_GET(self):
        if not self.path.startswith('/') or self.path.startswith('//'):self.send(400,{'error':'path'});return
        s=self.server.service;url=urllib.parse.urlsplit(self.path);path=url.path
        asset=path in tuple('/assets/status/'+n+'.gif' for n in ('working','warning','done'))
        public=path in ('/','/renderer.js','/preview') or asset
        if not self.allowed(public=public):return
        self.server.release_input_deadline(self.connection)
        try:
            if path=='/':
                html=(s.project/'web/control.html').read_text(encoding='utf-8').replace('/* PANEL_TOKEN */',json.dumps(s.tokens['panel']))
                self.send(200,html.encode(),'text/html; charset=utf-8')
            elif path=='/renderer.js':self.send(200,(s.project/'web/home-renderer.js').read_bytes(),'text/javascript; charset=utf-8')
            elif path=='/preview':
                html=(s.project/'web/integrated-preview.html').read_text(encoding='utf-8')
                html=html.replace('/* INITIAL */',json.dumps(s.home_data(),ensure_ascii=False).replace('<','\\u003c'))
                self.send(200,html.encode(),'text/html; charset=utf-8')
            elif asset:
                name=path.rsplit('/',1)[1][:-4]
                self.send(200,(s.project/f'assets/{name}-animation/{name}.gif').read_bytes(),'image/gif')
            elif path=='/api/state':self.send(200,s.hub.snapshot())
            elif path=='/api/home':self.send(200,s.home_data())
            elif path=='/api/geocode':
                q=urllib.parse.parse_qs(url.query).get('q',[''])[0]
                self.send(200,{'places':s.weather.search(q)})
            else:self.send(404,{'error':'not_found'})
        except (OSError,ValueError,RuntimeError):self.send(400,{'error':'request_failed'})
    def do_POST(self):
        s=self.server.service;path=self.path
        role={'/api/hook':'hook','/api/task':'mcp','/api/telegram':'notification','/api/desktop-signal':'notification'}.get(path,'panel')
        if not self.allowed(role):return
        try:
            length=self.headers.get('Content-Length','')
            if self.headers.get('Transfer-Encoding') or not re.fullmatch('[0-9]{1,5}',length) or not 1<=int(length)<=16384:
                raise ValueError('body_size')
            raw=self.rfile.read(int(length))
            if len(raw)!=int(length):raise ValueError('body_incomplete')
            data=json.loads(raw)
            if not isinstance(data,dict):raise ValueError('object_required')
            self.server.release_input_deadline(self.connection)
            if path=='/api/hook':reply=s.hub.hook(data)
            elif path=='/api/task':
                if set(data)!={'session_id','turn_id','state'}:raise ValueError('task_fields')
                reply=s.hub.task(data['session_id'],data['turn_id'],data['state'])
            elif path=='/api/telegram':reply={'accepted':s.hub.telegram(data)}
            elif path=='/api/desktop-signal':reply={'accepted':s.hub.desktop_signal(data)}
            elif path=='/api/settings':
                if s.live and data.get('device_host')!=s.device.core.host:raise ValueError('restart_required_for_host')
                s.hub.configure(data);reply={'ok':True}
            elif path=='/api/track':
                if set(data)!={'session_id','label'}:raise ValueError('track_fields')
                s.hub.track(data['session_id'],data['label']);reply={'ok':True}
            elif path=='/api/rename':
                if set(data)!={'key','label'}:raise ValueError('rename_fields')
                s.hub.rename(data['key'],data['label']);reply={'ok':True}
            elif path=='/api/complete':
                if set(data)!={'key','turn_id'}:raise ValueError('complete_fields')
                reply=s.hub.task(s.hub.registry[data['key']]['session_id'],data['turn_id'],'done')
            elif path=='/api/test':
                if set(data)!={'mode'}:raise ValueError('test_fields')
                s.hub.test_animation(data['mode']);reply={'ok':True}
            elif path=='/api/shutdown':self.send(200,{'ok':True});s.stop.set();return
            else:self.send(404,{'error':'not_found'});return
            self.send(200,reply)
        except (OSError,ValueError,RuntimeError,KeyError,TypeError):self.send(400,{'error':'request_rejected'})


def main():
    import argparse
    parser=argparse.ArgumentParser(description='Madyar Mini integrated localhost service')
    parser.add_argument('--live',action='store_true',help='Only for an installed MadyarMini-0.2.0 device')
    parser.add_argument('--port',type=int,default=8772)
    args=parser.parse_args();project=Path(__file__).resolve().parents[1]
    root=project/'var/state/integrated';secure_state(root)
    journal=EventJournal(root)
    try:service=IntegratedService(project,root,port=args.port,live=args.live)
    except Exception as exc:
        journal.write('startup_failed',error_type=type(exc).__name__);raise
    print(f'Madyar Mini: http://127.0.0.1:{service.port} / '+('LIVE' if args.live else 'SIMULATION'),flush=True)
    try:service.run()
    except Exception as exc:
        journal.write('service_failed',error_type=type(exc).__name__);raise


if __name__=='__main__':main()


