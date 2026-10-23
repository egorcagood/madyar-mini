"""Local single-writer service. Stage 3 exposes SIMULATION ONLY."""
import hmac
import json
import re
import secrets
import socket
import threading
import tomllib
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from .device import _NoRedirect
from .notification import _atomic_json
from .status_controller import ControllerError, StatusController
from .status_display import CachedDisplay, make_simulated_device


def load_config(path):
    config = {'done_seconds': 8, 'lease_seconds': 600, 'port': 8765}
    if path is not None:
        with Path(path).open('rb') as stream:
            custom = tomllib.load(stream)
        if set(custom) - set(config):
            raise ControllerError('unknown_config_field')
        config.update(custom)
    if type(config['port']) is not int or not 1 <= config['port'] <= 65535:
        raise ControllerError('port_range')
    # Timer validation is shared with StatusController before the service starts.
    return config


class _LocalServer(HTTPServer):
    allow_reuse_address = False
    def __init__(self,*args,**kwargs):
        self.deadlines = {}
        super().__init__(*args,**kwargs)

    @staticmethod
    def _expire(connection):
        try:
            connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(.5)
        # An inactivity timeout alone does not bound a trickling header/body.
        # This timer only closes input; controller/device work remains single-writer.
        deadline = threading.Timer(1.0,self._expire,args=(connection,))
        deadline.daemon = True
        self.deadlines[connection] = deadline
        deadline.start()
        return connection, address

    def close_request(self,connection):
        self.release_input_deadline(connection)
        super().close_request(connection)

    def release_input_deadline(self,connection):
        deadline = self.deadlines.pop(connection,None)
        if deadline:
            deadline.cancel()

    def handle_error(self,request,client_address):
        # Aborted local HTTP input must not print request text or kill the timer loop.
        pass


class _Handler(BaseHTTPRequestHandler):
    server_version = 'MadyarMini'
    sys_version = ''
    protocol_version = 'HTTP/1.0'

    def log_message(self, *args):
        pass

    def _reply(self, code, payload):
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        try:
            self.send_response(code)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store')
            self.send_header('Connection','close')
            self.end_headers()
            self.wfile.write(data)
        except (OSError, socket.timeout):
            pass

    def _allowed(self):
        service = self.server.service
        expected_host = f'127.0.0.1:{service.port}'
        if (self.headers.get('Origin') is not None
                or self.headers.get('Host') != expected_host):
            self._reply(403, {'error':'local_client_only'})
            return False
        auth = self.headers.get('Authorization','')
        if not hmac.compare_digest(auth.encode(), ('Bearer '+service.token).encode()):
            self._reply(401, {'error':'unauthorized'})
            return False
        return True

    def do_GET(self):
        if not self._allowed():
            return
        if self.path not in ('/state','/health'):
            self._reply(404, {'error':'not_found'})
            return
        self._reply(200,self.server.service.snapshot())

    def _command(self,data):
        if set(data) != {'command'} or not isinstance(data['command'],str):
            raise ControllerError('command_fields')
        controller=self.server.service.controller
        commands={'working':controller.set_working,'warning':controller.set_warning,
                  'done':controller.set_done,'clock':controller.restore_clock,
                  'heartbeat':controller.heartbeat}
        if data['command'] not in commands:
            raise ControllerError('unknown_command')
        commands[data['command']]()

    def do_POST(self):
        if not self._allowed():
            return
        service = self.server.service
        try:
            if self.headers.get('Transfer-Encoding') is not None:
                raise ControllerError('transfer_encoding')
            length = self.headers.get('Content-Length','')
            if not re.fullmatch(r'[0-9]{1,5}',length) or not 0 < int(length) <= 8192:
                raise ControllerError('body_limit')
            if self.headers.get_content_type() != 'application/json':
                raise ControllerError('content_type')
            raw = self.rfile.read(int(length))
            if len(raw) != int(length):
                raise ControllerError('incomplete_body')
            data = json.loads(raw)
            if not isinstance(data,dict):
                raise ControllerError('object_required')
            # Input is complete. Device work may legitimately take longer than
            # the one-second slow-input limit; never abort its response timer.
            self.server.release_input_deadline(self.connection)
            if self.path == '/command':
                self._command(data)
            elif self.path == '/event':
                service.controller.handle(data)
            elif self.path == '/shutdown':
                if data:
                    raise ControllerError('shutdown_fields')
                service.stop_requested = True
            else:
                self._reply(404,{'error':'not_found'})
                return
            self._reply(503 if service.controller.state['recovery_pending'] else 200,service.snapshot())
        except (ControllerError, ValueError, UnicodeError, RecursionError):
            self._reply(400, {'error':'invalid_request'})
        except socket.timeout:
            self._reply(408, {'error':'request_timeout'})
        except (OSError, RuntimeError):
            # A persistence error must not leave the process accepting more shows.
            service.stop_requested = True
            self._reply(503, {'error':'service_stopping'})


class SimulationService:
    handler_class=_Handler
    mode='simulation'
    physical_device_contacted=False

    def _make_display(self,project_root):
        self.device = make_simulated_device(project_root)
        return CachedDisplay(self.device,self.root/'display',project_root)

    def _make_controller(self,**options):
        return StatusController(self.display,self.root,**options)

    def __init__(self, project_root, state_dir, *, port=8765, session_id='manual',
                 done_seconds=8, lease_seconds=600):
        if type(port) is not int or not 0 <= port <= 65535:
            raise ControllerError('port_range')
        self.root = Path(state_dir)
        self.display = self._make_display(project_root)
        self.server = None
        self.stop_requested = False
        self.token = secrets.token_hex(32)
        self.display.start()
        try:
            self.controller = self._make_controller(session_id=session_id,
                          done_seconds=done_seconds,lease_seconds=lease_seconds)
            self.server = _LocalServer(('127.0.0.1',port),self.handler_class)
            self.server.timeout = .1
            self.server.service = self
            self.port = self.server.server_address[1]
            _atomic_json(self.root/'runtime.json',{'host':'127.0.0.1','port':self.port,
                                                  'token':self.token,'mode':self.mode})
        except BaseException:
            if self.server:
                self.server.server_close()
            self.display.close()
            raise

    def snapshot(self):
        return {'mode':self.mode,'physical_device_contacted':self.physical_device_contacted,
                'state':self.controller.state,'uploads':self.device.upload_count,
                'last_confirmed_display':self.display.current}

    def run(self):
        try:
            while not self.stop_requested:
                self.server.handle_request()
                self.controller.tick()
        except KeyboardInterrupt:
            pass
        finally:
            try:
                self.controller.restore_clock(reason='shutdown')
            finally:
                try:
                    self.display.close()
                finally:
                    self.server.server_close()
                    runtime = self.root/'runtime.json'
                    if runtime.exists():
                        data = json.loads(runtime.read_text(encoding='utf-8'))
                        if data.get('token') == self.token:
                            runtime.unlink()


class StatusClient:
    mode='simulation'
    request_timeout=2
    def __init__(self,state_dir):
        self.root = Path(state_dir)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),_NoRedirect())

    def request(self,path,payload=None):
        try:
            runtime = json.loads((self.root/'runtime.json').read_text(encoding='utf-8'))
            port, token = runtime['port'], runtime['token']
            if (runtime.get('host') != '127.0.0.1' or runtime.get('mode') != self.mode
                    or type(port) is not int or not 1 <= port <= 65535
                    or not isinstance(token,str) or not re.fullmatch('[a-f0-9]{64}',token)):
                raise ControllerError('invalid_runtime')
            if path not in ('/command','/state','/health','/event','/shutdown'):
                raise ControllerError('invalid_path')
            body = json.dumps(payload).encode('utf-8') if payload is not None else None
            req = urllib.request.Request(f'http://127.0.0.1:{port}'+path,data=body,
                       headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
            with self.opener.open(req,timeout=self.request_timeout) as response:
                raw = response.read(65537)
            if len(raw)>65536:
                raise ControllerError('response_limit')
            return json.loads(raw)
        except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError) as exc:
            raise ControllerError('local_service_unavailable_or_rejected') from exc

    def command(self,command):
        return self.request('/command',{'command':command})

    def state(self):
        return self.request('/state')

    def event(self,event):
        return self.request('/event',event)

    def shutdown(self):
        return self.request('/shutdown',{})
