"""Metadata-only registry journal. The committed controller checkpoint wins."""
import hashlib,json,math
from pathlib import Path
from .multi_controller import validate_sessions
from .notification import _atomic_json
from .status_controller import IDENTIFIER

def recover_registry(root):
 root=Path(root);journal=root/'registry-update.json'
 if not journal.exists():return
 raw=journal.read_bytes()
 if len(raw)>16384:raise ValueError('registry_journal_size')
 value=json.loads(raw)
 if set(value)!={'schema','old_registry','new_registry'} or value['schema']!=1:raise ValueError('registry_journal')
 rows=[validate_sessions(value[name]) for name in ('old_registry','new_registry')]
 path=root/'controller/checkpoint.json';raw=path.read_bytes()
 if len(raw)>8*1024*1024:raise ValueError('registry_checkpoint_size')
 committed=json.loads(raw).get('registry')
 for registry in rows:
  expected={k:hashlib.sha256(v['session_id'].encode()).hexdigest()[:16] for k,v in registry.items()}
  if expected==committed:_atomic_json(root/'chats.json',registry);journal.unlink();return
 raise ValueError('registry_journal_unmatched_checkpoint')

def validate_archive(value):
 if not isinstance(value,dict) or len(value)>64:raise ValueError('archive_count')
 for key,row in value.items():
  if not isinstance(row,dict) or set(row)!={'session_id','label','turn_id','retired_at'}:raise ValueError('archive_schema')
  validate_sessions({key:{n:row[n] for n in ('session_id','label')}})
  if row['turn_id'] is not None and (not isinstance(row['turn_id'],str) or not IDENTIFIER.fullmatch(row['turn_id'])):raise ValueError('archive_turn')
  if type(row['retired_at']) not in (int,float) or not math.isfinite(row['retired_at']) or row['retired_at']<=0:raise ValueError('archive_time')
 return value

def archived_turns(root,key):
 path=Path(root)/'archived-turns'/(key+'.json')
 if not path.exists():return set()
 raw=path.read_bytes()
 if len(raw)>1024*1024:raise ValueError('archived_turn_limit')
 value=json.loads(raw)
 if not isinstance(value,list) or len(value)>4096 or any(not isinstance(t,str) or not IDENTIFIER.fullmatch(t) for t in value):raise ValueError('archived_turns')
 return set(value)

def remember_archived_turns(root,key,turns):
 combined=archived_turns(root,key)|set(turns)
 if len(combined)>4096:raise ValueError('archived_turn_limit')
 _atomic_json(Path(root)/'archived-turns'/(key+'.json'),sorted(combined))
