"""Explicit local operator commands; reads only our private runtime endpoint."""
import argparse
import json
import urllib.request
from pathlib import Path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=('state','test','shutdown'))
    parser.add_argument('mode',nargs='?',choices=('WORKING','WARNING','DONE'))
    args=parser.parse_args()
    runtime=json.loads((Path(__file__).resolve().parents[1]/'var/state/integrated/runtime.json').read_text())
    if runtime.get('host')!='127.0.0.1' or type(runtime.get('port')) is not int or not 1<=runtime['port']<=65535:
        raise ValueError('invalid_runtime')
    if args.command=='test' and args.mode is None:parser.error('test requires WORKING/WARNING/DONE')
    path={'state':'/api/state','test':'/api/test','shutdown':'/api/shutdown'}[args.command]
    payload=None if args.command=='state' else json.dumps({'mode':args.mode} if args.command=='test' else {}).encode()
    request=urllib.request.Request(f"http://127.0.0.1:{runtime['port']}"+path,data=payload,
        headers={'Authorization':'Bearer '+runtime['panel_token'],'Content-Type':'application/json'})
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request,timeout=3) as response:result=json.loads(response.read(16384))
    if args.command=='state':
        # No runtime tokens or notification contents in CLI output.
        result={key:result[key] for key in ('state','sources','simulation')}
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
