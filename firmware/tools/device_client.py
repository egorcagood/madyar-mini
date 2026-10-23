"""Client of our proposed firmware API; never falls back to stock endpoints."""
import ipaddress
import json
import time
import urllib.request

def validate_host(host):
    address=ipaddress.IPv4Address(host)
    if not any(address in ipaddress.ip_network(n) for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16')):
        raise ValueError('Only explicit LAN IPv4 is allowed')
    return str(address)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError('Redirect forbidden')

class DeviceClient:
    def __init__(self,host,key,*,transport=None):
        self.host=validate_host(host)
        if not isinstance(key,str) or len(key)!=48 or any(c not in '0123456789abcdef' for c in key):
            raise ValueError('Invalid management key')
        self.key=key;self.boot=self.owner=self.seq=0;self.seen=0
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        self.transport=transport or self._request
    def _request(self,path,payload):
        data=None if payload is None else json.dumps(payload,ensure_ascii=False).encode('utf-8')
        if data is not None and len(data)>2048:raise ValueError('Snapshot too large')
        req=urllib.request.Request('http://'+self.host+path,data=data,headers={
            'X-Madyar-Key':self.key,'Content-Type':'application/json','Connection':'close'})
        with self.opener.open(req,timeout=3) as response:
            raw=response.read(8193)
            if len(raw)>8192:raise ValueError('Response too large')
            return json.loads(raw)
    def send(self,mode,*,active=0,waiting=0,label='',done_seconds=10):
        if mode not in ('HOME','WORKING','WARNING','DONE','DIAGNOSTIC'):
            raise ValueError('Invalid mode')
        if any(type(x) is not int or not 0<=x<=8 for x in (active,waiting)) or active+waiting>8:
            raise ValueError('Invalid counters')
        if (mode=='WORKING' and (not active or waiting)) or (mode=='WARNING' and not waiting) or (mode in ('HOME','DONE') and (active or waiting)):
            raise ValueError('Counters contradict mode')
        if not isinstance(label,str) or len(label)>12 or any(ord(c)<32 or ord(c)==127 for c in label):
            raise ValueError('Invalid label')
        if type(done_seconds) is not int or not 1<=done_seconds<=60:raise ValueError('Invalid DONE duration')
        health=self.transport('/api/v1/health',None)
        if not isinstance(health,dict) or not str(health.get('firmware','')).startswith('MadyarMini-') or health.get('protocol')!=1:
            raise ValueError('Device is not the supported Madyar Mini firmware')
        if not health.get('assets_verified'):raise ValueError('Device resources unverified')
        now=time.monotonic();boot=health.get('boot')
        if type(boot) is not int or not 1<=boot<2**32:raise ValueError('Invalid boot identity')
        if self.boot!=boot or now-self.seen>=30 or not self.owner:
            claim=self.transport('/api/v1/claim',{'boot':boot})
            owner=claim.get('owner')
            if claim.get('boot')!=boot or type(owner) is not int or not 1<=owner<2**32:
                raise ValueError('Invalid controller identity')
            self.boot,self.owner,self.seq=boot,owner,0
        self.seq+=1
        packet=dict(boot=self.boot,owner=self.owner,seq=self.seq,mode=mode,active=active,waiting=waiting,
                    label=label,lease_ms=30000,done_ms=done_seconds*1000,
                    clock=int(time.time()),source_connected=mode not in ('HOME','DIAGNOSTIC'))
        result=self.transport('/api/v1/state',packet)
        if result.get('ok') is not True:raise ValueError('State not acknowledged')
        self.seen=now
        return result
