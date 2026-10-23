"""Single-writer wrapper around the existing native client; no stock fallback."""
import copy
from firmware.tools.device_client import DeviceClient


class IntegratedDeviceClient:
    def __init__(self,host,key,*,transport=None,on_new_owner=None):
        self.extra={};self.suppressed_done=None;self.on_new_owner=on_new_owner
        self.core=DeviceClient(host,key)
        self.transport=transport or self.core._request
        self.core.transport=self._transport
        self.health=None
    def _transport(self,path,payload):
        if path=='/api/v1/health':
            value=self.transport(path,payload)
            if not isinstance(value,dict) or value.get('integrated_api')!=1:raise ValueError('integrated_firmware_required')
            self.health={k:value.get(k) for k in ('firmware','boot','free_heap','max_free_block','assets_verified','clock_valid','brightness_level')}
            return value
        if path=='/api/v1/claim':
            reply=self.transport(path,payload)
            if not isinstance(reply,dict):raise ValueError('invalid_claim_response')
            self.extra.pop('notice',None)
            self.extra.pop('reply_ready',None)
            self.suppressed_done=self.extra.get('done_id')
            if self.on_new_owner:self.on_new_owner()
            return reply
        if path=='/api/v1/state':
            packet=payload.copy()
            for k in ('source_connected','clock','city','country','utc_offset','display','weather','quota','notice','reply_ready','palette','show_ip','ip_text','animation','clock_style'):
                if k in self.extra:packet[k]=copy.deepcopy(self.extra[k])
            if packet['mode']=='DONE' and self.extra.get('done_id')==self.suppressed_done:
                packet.update(mode='HOME',active=0,waiting=0)
            reply=self.transport(path,packet)
            if not isinstance(reply,dict):raise ValueError('invalid_state_response')
            return reply
        raise ValueError('unexpected_device_method')
    def send(self,packet):
        self.extra=copy.deepcopy(packet)
        return self.core.send(packet['mode'],active=packet.get('active',0),waiting=packet.get('waiting',0),
            label=packet.get('label',''),done_seconds=packet.get('done_seconds',10))


class SimulationDevice:
    """Wire-compatible local target, not evidence of physical display behavior."""
    def __init__(self):self.latest=None;self.updates=0;self.boot=1
    def send(self,packet):
        self.latest=copy.deepcopy(packet);self.updates+=1
        return {'ok':True}
