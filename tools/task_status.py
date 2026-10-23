"""Explicit local task status fallback. Never infer completion from a Stop or reply text."""
import argparse,hashlib,json,re,subprocess,sys,time,urllib.request
from pathlib import Path

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'integrations/codex-plugin/generated/plugins/madyar-mini/scripts'))
from bridge import IDENTIFIER,post_local
sys.path.insert(0,str(PROJECT))
from madyarmini.notification import _atomic_json

def session_key(sid):return 'c'+hashlib.sha256(sid.encode()).hexdigest()[:12]

def deliver(sid,turn,*,read,post=post_local,sleep=time.sleep):
    for _ in range(30):
        try:
            state=read()['state'];row=state['sessions'].get(session_key(sid))
            if not row or row['turn_id']!=turn:return dict(delivered=False,reason='stale_or_unknown_turn')
            if row.get('completed'):
                return dict(delivered=True,status=state['status'],reason='already_confirmed')
            if row['status'] in ('WORKING','CLOCK') and not row['fresh_required'] and row.get('can_complete'):
                reply=post('/api/task',dict(session_id=sid,turn_id=turn,state='done'),'mcp')
                if reply.get('status') not in ('DONE','CLOCK','WORKING','WARNING','DIAGNOSTIC','UNKNOWN'):
                    return dict(delivered=False,reason='invalid_reply')
                return dict(delivered=True,status=reply['status'],reason='explicit_completion')
        except (OSError,ValueError,KeyError,TypeError):
            return dict(delivered=False,reason='unavailable_or_rejected')
        sleep(1)
    return dict(delivered=False,reason='waiting_or_unverified_preserved')

def read_state():
    cfg=json.loads((PROJECT/'var/state/integrated/runtime.json').read_text(encoding='utf-8-sig'))
    if cfg.get('host')!='127.0.0.1' or cfg.get('port')!=8772:raise ValueError('local_runtime_required')
    req=urllib.request.Request('http://127.0.0.1:8772/api/state',headers={'Authorization':'Bearer '+cfg['panel_token']})
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req,timeout=3) as response:return json.load(response)

def main():
    parser=argparse.ArgumentParser(description='Explicit verified task completion for Madyar Mini')
    parser.add_argument('--session-id',required=True);parser.add_argument('--turn-id',required=True)
    parser.add_argument('--state',choices=('working','warning','done'),default='done')
    parser.add_argument('--receipt',action='store_true');parser.add_argument('--deliver',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args()
    if not IDENTIFIER.fullmatch(args.session_id) or not IDENTIFIER.fullmatch(args.turn_id):parser.error('Invalid identity')
    digest=hashlib.sha256((args.session_id+'\0'+args.turn_id+'\0'+args.state).encode()).hexdigest()[:24]
    root=PROJECT/'var/state/integrated/signals';receipt=root/(digest+'.json')
    if args.receipt:
        print(receipt.read_text(encoding='utf-8') if receipt.exists() else '{"delivered":false,"reason":"no_receipt"}')
        return 0
    root.mkdir(parents=True,exist_ok=True)
    if args.deliver:
        result=deliver(args.session_id,args.turn_id,read=read_state)
        _atomic_json(receipt,dict(result,at=time.time()))
        return 0 if result['delivered'] else 1
    if args.state!='done':
        try:
            response=post_local('/api/task',dict(session_id=args.session_id,turn_id=args.turn_id,state=args.state),'mcp')
            result=dict(delivered=True,status=response['status'])
        except (OSError,ValueError,KeyError,TypeError):result=dict(delivered=False,reason='unavailable_or_rejected')
        _atomic_json(receipt,dict(result,at=time.time()));print(json.dumps(result));return 0 if result['delivered'] else 1
    # The calling tool itself may be under automatic permission review. Let its
    # PostToolUse resolve that review before attempting genuine completion.
    if receipt.exists():
        previous=json.loads(receipt.read_text(encoding='utf-8'))
        if previous.get('delivered'):print(json.dumps(previous));return 0
    _atomic_json(receipt,dict(queued=True,delivered=False,at=time.time()))
    subprocess.Popen([sys.executable,'-I',str(Path(__file__).resolve()),'--session-id',args.session_id,
        '--turn-id',args.turn_id,'--state','done','--deliver'],cwd=PROJECT,
        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    # Retain only bounded, hash-named receipts in this module's own directory.
    entries=sorted((p for p in root.glob('*.json') if re.fullmatch(r'[a-f0-9]{24}\.json',p.name)),key=lambda p:p.stat().st_mtime,reverse=True)
    for old in entries[128:]:
        if old.parent.resolve()!=root.resolve():raise ValueError('receipt_path')
        old.unlink(missing_ok=True)
    print(json.dumps(dict(queued=True,delivered=False,receipt=str(receipt))))
    return 0

if __name__=='__main__':raise SystemExit(main())
