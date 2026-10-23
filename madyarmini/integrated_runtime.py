"""One writer per state directory; Windows ACLs protect local role tokens."""
import os
import subprocess
from pathlib import Path


class StateLock:
    def __init__(self,root):
        root=Path(root);root.mkdir(parents=True,exist_ok=True)
        self.stream=(root/'service.lock').open('a+b')
        try:
            if self.stream.seek(0,2)==0:self.stream.write(b'0');self.stream.flush()
            self.stream.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BaseException:self.stream.close();raise
    def close(self):
        if self.stream.closed:return
        self.stream.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(self.stream.fileno(),msvcrt.LK_UNLCK,1)
        self.stream.close()


def secure_state(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    if os.name!='nt':root.chmod(0o700);return
    system=Path(os.environ['SystemRoot'])/'System32'
    result=subprocess.run([str(system/'WindowsPowerShell/v1.0/powershell.exe'),'-NoProfile','-NonInteractive','-Command',
        '[Security.Principal.WindowsIdentity]::GetCurrent().User.Value'],capture_output=True,text=True,check=True,timeout=10)
    sid=result.stdout.strip()
    import re
    if not re.fullmatch(r'S-1-5-[0-9-]+',sid):raise RuntimeError('current_user_sid_unavailable')
    result=subprocess.run([str(system/'icacls.exe'),str(root),'/inheritance:r','/grant:r',
        '*'+sid+':(OI)(CI)F','*S-1-5-18:(OI)(CI)F','*S-1-5-32-544:(OI)(CI)F'],capture_output=True,timeout=10)
    if result.returncode:raise RuntimeError('private_state_acl_failed')


def play_sound(kind):
    if os.name=='nt':
        import winsound
        try:winsound.PlaySound('SystemExclamation' if kind=='warning' else 'SystemNotification',
                              winsound.SND_ALIAS|winsound.SND_ASYNC|winsound.SND_NODEFAULT)
        except RuntimeError:pass
