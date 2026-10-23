"""Bounded notification transaction, verified cache, durable recovery journal."""
import hashlib
import io
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from PIL import Image
from .device import DeviceError


class NotificationError(RuntimeError):
    pass


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _validate_snapshot(snapshot):
    try:
        if snapshot['version'] != {'m': 'SmallTV-Ultra', 'v': 'Ultra-V9.0.43'}:
            raise ValueError('Unsupported model/firmware')
        theme = snapshot['app']['theme']
        album = snapshot['album']
        if type(theme) is not int or theme not in range(1, 8):
            raise ValueError('Invalid theme')
        if (type(album['autoplay']) is not int or album['autoplay'] not in (0, 1)
                or type(album['i_i']) is not int or not 1 <= album['i_i'] <= 86400):
            raise ValueError('Invalid album')
        for key in ('brightness', 'night', 'delay'):
            if not isinstance(snapshot[key], dict) or not snapshot[key]:
                raise ValueError('Missing settings')
    except (KeyError, TypeError, ValueError) as exc:
        raise NotificationError('Неподдерживаемая модель/прошивка или неполные настройки') from exc


class Notifier:
    def __init__(self, device, state_dir, sleep=time.sleep):
        self.device = device
        self.state_dir = Path(state_dir)
        self.journal = self.state_dir / 'pending-restore.json'
        self.sleep = sleep

    @contextmanager
    def lock(self):
        import msvcrt
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with (self.state_dir / 'notification.lock').open('a+b') as stream:
            stream.seek(0, 2)
            if stream.tell() == 0:
                stream.write(b'0')
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise NotificationError('Другой процесс уже управляет уведомлением') from exc
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)

    def _restore(self, original):
        # Try the clock first, even if an album setting later fails.
        self.device.set_theme(original['app']['theme'])
        album = original['album']
        self.device.set_album(album['autoplay'], album['i_i'])
        after = self.device.snapshot()
        if after != original:
            changed = [key for key in original if original[key] != after.get(key)]
            raise NotificationError('Настройки не совпали после возврата: ' + ', '.join(changed))
        self.journal.unlink()
        return after

    def recover(self, *, approved=False):
        if not approved:
            raise NotificationError('Восстановление требует явного разрешения')
        with self.lock():
            if not self.journal.exists():
                return {'pending': False}
            try:
                record = json.loads(self.journal.read_text(encoding='utf-8'))
                if record['schema'] != 1 or record['device'] != self.device.identity:
                    raise ValueError('Wrong journal identity')
                original = record['before']
                _validate_snapshot(original)
                current = self.device.snapshot()
                if current['version'] != original['version']:
                    raise ValueError('Different device firmware')
                return {'pending': False, 'after': self._restore(original)}
            except (KeyError, ValueError, TypeError, DeviceError) as exc:
                raise NotificationError('Не удалось восстановить; журнал сохранён') from exc

    def show(self, image_path, *, approved=False, duration=5,
             expected_sha256=None, allow_upload=True):
        if not approved:
            raise NotificationError('Сначала подтвердите изображение и показ')
        data = Path(image_path).read_bytes()
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.size != (240, 240):
                    raise ValueError('wrong dimensions')
                if image.format == 'JPEG':
                    if len(data) > 65536 or duration != 5:
                        raise ValueError('JPEG requires 5 seconds and <=64 KiB')
                    image.verify()
                    suffix = '.jpg'
                elif image.format == 'GIF':
                    if (len(data) > 524288 or not 2 <= image.n_frames <= 96
                            or image.info.get('loop') != 0
                            or type(duration) is not int or not 1 <= duration <= 30):
                        raise ValueError('unsafe GIF limits')
                    for index in range(image.n_frames):
                        image.seek(index)
                        if not 80 <= image.info.get('duration', 0) <= 1000:
                            raise ValueError('unsafe GIF timing')
                        image.load()
                    suffix = '.gif'
                else:
                    raise ValueError('only JPEG and GIF can be sent')
        except (OSError, ValueError) as exc:
            raise NotificationError('Нужен JPEG 240×240 до 64 KiB или циклический GIF до 512 KiB; PNG не отправляется') from exc
        digest = hashlib.sha256(data).hexdigest()
        if expected_sha256 is not None and digest != expected_sha256:
            raise NotificationError('Файл изменился после подтверждения превью')
        device_path = '/image/mm_' + digest[:20] + suffix
        with self.lock():
            if self.journal.exists():
                raise NotificationError('Сначала восстановите незавершённое уведомление')
            try:
                before = self.device.snapshot()
                _validate_snapshot(before)
                if before['app']['theme'] != 1:
                    raise NotificationError('Перед этим тестом ожидаются заводские часы с погодой, тема 1')
                files = self.device.list_files()
                uploads = 0
                if device_path not in files:
                    if not allow_upload:
                        raise NotificationError('Файл кеша отсутствует; повторная загрузка запрещена')
                    space = self.device.space()
                    if type(space.get('free')) is not int or space['free'] < len(data) + 262144:
                        raise NotificationError('Недостаточно свободного места с учётом резерва')
                    self.device.upload(device_path, data)
                    uploads = 1
                    if device_path not in self.device.list_files():
                        raise NotificationError('Загруженный файл не появился в каталоге')
                if hashlib.sha256(self.device.download(device_path)).hexdigest() != digest:
                    raise NotificationError('Хеш файла на устройстве не совпал; перезапись запрещена')
                # No settings changed before a durable recovery journal exists.
                _atomic_json(self.journal, {'schema': 1, 'device': self.device.identity,
                                           'before': before, 'asset': device_path,
                                           'created_at': time.time()})
                try:
                    album = before['album']
                    if album['autoplay']:
                        self.device.set_album(0, album['i_i'])
                    paused = self.device.snapshot()
                    expected = dict(before)
                    expected['album'] = dict(album, autoplay=0)
                    if paused != expected:
                        raise NotificationError('Не удалось безопасно приостановить альбом')
                    self.device.set_theme(3)
                    if self.device.theme() != 3:
                        raise NotificationError('Не подтверждён режим Photo Album')
                    self.device.show_image(device_path)
                    deadline = time.monotonic() + duration
                    self.sleep(max(0, deadline - time.monotonic()))
                finally:
                    try:
                        after = self._restore(before)
                    except (DeviceError, NotificationError, OSError) as exc:
                        raise NotificationError('Возврат не подтверждён; журнал сохранён, выполните restore') from exc
                return {'device_path': device_path, 'sha256': digest, 'uploads': uploads,
                        'duration_seconds': duration, 'before': before, 'after': after,
                        'unverified_settings': [key for key, value in before.items()
                                                if value == {'unavailable': 'HTTP404'}],
                        'observed_settings_preserved': before == after}
            except DeviceError as exc:
                raise NotificationError(str(exc)) from exc
