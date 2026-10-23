"""Explicit manual tests, not an automatic Codex Windows App event source."""
import argparse
import json
from pathlib import Path
import time
from device_client import DeviceClient

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=('WORKING','WARNING','DONE','HOME','DIAGNOSTIC'))
    p.add_argument('--active',type=int,default=None);p.add_argument('--waiting',type=int,default=None)
    p.add_argument('--label',default='');p.add_argument('--seconds',type=int,default=5)
    p.add_argument('--done-seconds',type=int,default=10)
    p.add_argument('--config',type=Path,default=Path(__file__).resolve().parents[1]/'private/device.json')
    a=p.parse_args()
    if not 1<=a.seconds<=3600:p.error('--seconds must be 1..3600')
    cfg=json.loads(a.config.read_text(encoding='utf-8'));c=DeviceClient(cfg['host'],cfg['key'])
    active=a.active if a.active is not None else (1 if a.mode=='WORKING' else 0)
    waiting=a.waiting if a.waiting is not None else (1 if a.mode=='WARNING' else 0)
    sent=False
    try:
        c.send(a.mode,active=active,waiting=waiting,label=a.label,done_seconds=a.done_seconds);sent=True
        print('Manual test acknowledged. This does not establish automatic GUI event delivery.')
        deadline=time.monotonic()+a.seconds
        while time.monotonic()<deadline:
            time.sleep(min(1,max(0,deadline-time.monotonic())))
            if deadline-time.monotonic()>0 and time.monotonic()-c.seen>10:
                c.send(a.mode,active=active,waiting=waiting,label=a.label,done_seconds=a.done_seconds)
    finally:
        if sent:c.send('HOME')

if __name__=='__main__':
    try:main()
    except (OSError,ValueError,KeyError):raise SystemExit('Test not completed; check firmware, key, resources and controller ownership. No stock fallback was used.')
