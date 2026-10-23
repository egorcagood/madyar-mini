"""In-memory driver: it cannot contact a physical device."""
import copy
from .device import DeviceError


class SimulatedDevice:
    identity = 'simulation'

    def __init__(self):
        self.settings = {
            'version': {'m': 'SmallTV-Ultra', 'v': 'Ultra-V9.0.43'},
            'app': {'theme': 1}, 'album': {'autoplay': 1, 'i_i': 5},
            'brightness': {'brt': 70},
            'night': {'t1': 22, 't2': 7, 'b1': 50, 'b2': 20, 'en': 1},
            'delay': {'delay': 0},
        }
        self.assets = {}
        self.upload_count = 0
        self.offline = False
        self.fail_upload_after_save = False
        self.shown = None

    def _online(self):
        if self.offline:
            raise DeviceError('Симуляция: соединение недоступно')

    def snapshot(self):
        self._online()
        return copy.deepcopy(self.settings)

    def space(self):
        self._online()
        return {'total': 3121152, 'free': 1228300}

    def theme(self):
        self._online()
        return self.settings['app']['theme']

    def list_files(self):
        self._online()
        return set(self.assets)

    def download(self, path):
        self._online()
        return self.assets[path]

    def upload(self, path, data):
        self._online()
        self.upload_count += 1
        self.assets[path] = data
        if self.fail_upload_after_save:
            raise DeviceError('Симуляция: таймаут после загрузки')

    def set_theme(self, theme):
        self._online()
        self.settings['app']['theme'] = theme

    def set_album(self, autoplay, interval):
        self._online()
        self.settings['album'] = {'autoplay': autoplay, 'i_i': interval}

    def show_image(self, path):
        self._online()
        if path not in self.assets:
            raise DeviceError('Симуляция: файл отсутствует')
        self.shown = path
