"""Long-lived, cache-only display lease. Uses the stage-2 lock/recovery format."""
import hashlib
import io
import json
import time
from pathlib import Path

from PIL import Image

from .notification import Notifier, NotificationError, _atomic_json, _validate_snapshot
from .simulated import SimulatedDevice


ASSETS = {
    'WORKING': ('assets/working-animation/working.gif',
                '34dd49fb2891a7f979d9f892a80deae7ebb9877cf0c751adfda0281a9a4e0fd3'),
    'WARNING': ('assets/warning-animation/warning.gif',
                'de9324f72fa4cd2155521bf55c2e1917830c56a79cd49808b27729cb86244738'),
    'DONE': ('assets/done-animation/done.gif',
             'fc6eb7d37279896162f955131a7c50532aa9f3e34d08c4df8b0a467714627fd0'),
}


def _load_assets(project_root):
    result = {}
    for status, (relative, digest) in ASSETS.items():
        data = (Path(project_root)/relative).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise NotificationError('Local GIF differs from approved SHA256')
        with Image.open(io.BytesIO(data)) as image:
            if (image.format != 'GIF' or image.size != (240, 240)
                    or image.n_frames != 48 or image.info.get('loop') != 0
                    or len(data) > 524288):
                raise NotificationError('Unsupported GIF')
            for index in range(image.n_frames):
                image.seek(index)
                if image.info.get('duration') != 160:
                    raise NotificationError('Unsupported GIF timing')
                image.load()
        result[status] = ('/image/mm_' + digest[:20] + '.gif', digest, data)
    return result


def make_simulated_device(project_root):
    """Seed RAM of the simulator with the already-approved assets; no upload call."""
    device = SimulatedDevice()
    for path, _, data in _load_assets(project_root).values():
        device.assets[path] = data
    return device


class CachedDisplay:
    def __init__(self, device, state_dir, project_root):
        self.device, self.project_root = device, Path(project_root)
        self.notifier = Notifier(device, state_dir)
        self._lock_context = None
        self.started = False
        self.failed = False
        self.current = 'CLOCK'
        self.original = None
        self.assets = None

    def start(self):
        if self.started:
            raise NotificationError('Display already started')
        lock = self.notifier.lock()
        lock.__enter__()
        self._lock_context = lock
        try:
            if self.notifier.journal.exists():
                self._recover_journal()
            assets = _load_assets(self.project_root)
            snapshot = self.device.snapshot()
            _validate_snapshot(snapshot)
            if snapshot['app']['theme'] != 1:
                raise NotificationError('Factory weather clock (theme 1) required')
            files = self.device.list_files()
            for path, digest, _ in assets.values():
                if path not in files or hashlib.sha256(self.device.download(path)).hexdigest() != digest:
                    raise NotificationError('Approved GIF missing or altered; upload forbidden')
            self.assets = assets
            self.started = True
        except BaseException:
            self._lock_context = None
            lock.__exit__(None, None, None)
            raise

    def _recover_journal(self):
        try:
            record = json.loads(self.notifier.journal.read_text(encoding='utf-8'))
            if record['schema'] != 1 or record['device'] != self.device.identity:
                raise ValueError('journal_identity')
            original = record['before']
            _validate_snapshot(original)
            if original['app']['theme'] != 1:
                raise ValueError('journal_theme')
            if self.device.snapshot()['version'] != original['version']:
                raise ValueError('firmware_changed')
            self.notifier._restore(original)
        except (ValueError, KeyError, TypeError) as exc:
            raise NotificationError('Invalid recovery journal; kept for inspection') from exc

    def show(self, status):
        if not self.started or self.failed:
            raise NotificationError('Display not ready; restore first')
        if status not in self.assets:
            raise NotificationError('Unknown status')
        if self.current == status:
            return
        try:
            if not self.notifier.journal.exists():
                before = self.device.snapshot()
                _validate_snapshot(before)
                if before['app']['theme'] != 1:
                    raise NotificationError('External display change detected')
                _atomic_json(self.notifier.journal, {'schema': 1, 'device': self.device.identity,
                            'before': before, 'asset': self.assets[status][0], 'created_at': time.time()})
                self.original = before
                album = before['album']
                if album['autoplay']:
                    self.device.set_album(0, album['i_i'])
                paused = self.device.snapshot()
                expected = dict(before, album=dict(album, autoplay=0))
                if paused != expected:
                    raise NotificationError('Could not pause album safely')
                self.device.set_theme(3)
            if self.device.theme() != 3:
                raise NotificationError('Photo Album mode not confirmed')
            self.device.show_image(self.assets[status][0])
            self.current = status
        except BaseException:
            self.failed = True
            raise

    def restore_clock(self):
        if not self.started:
            return
        try:
            if self.notifier.journal.exists():
                self._recover_journal()
            elif self.failed:
                # A failure before a journal was written must not change unknown settings.
                snapshot = self.device.snapshot()
                _validate_snapshot(snapshot)
                if snapshot['app']['theme'] != 1:
                    raise NotificationError('Clock restoration not confirmed')
            self.original = None
            self.current, self.failed = 'CLOCK', False
        except BaseException:
            self.failed = True
            raise

    def close(self):
        if self._lock_context is None:
            return
        try:
            self.restore_clock()
        finally:
            self.started = False
            lock, self._lock_context = self._lock_context, None
            lock.__exit__(None, None, None)
